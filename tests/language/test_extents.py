"""A call may leave out the extent parameters its views already carry: `checksum(frame)` is
`checksum(len(frame), frame)`. The checker writes the omitted arguments in, so the emitted code, the effect rows
and every later stage see the explicit call; a view of another length is refused or trapped exactly as it would be
if the length had been written by hand.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from emitted import refused, run, sanitized

HELPERS = """
import std.sort as sort;
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + xs[i]; } return t; }
fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + xs[i] * ys[i]; } return t; }
fn outer(n:usize, xs:ro<u64>[n]) -> u64 = total(xs);
fn largest[T:numeric](n:usize, xs:ro<T>[n]) -> T { let mut m = xs[0]; for i in 0..n { m = max(m, xs[i]); } return m; }
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 { let mut s:u32 = 0; for i in 0..n { s = add_wrap(s, u32(bytes[i])); } return s; }
"""

SHORT = (
    HELPERS
    + """
fn main() -> i32 {
  let n:usize = 900;
  let mut data = Buf[u64](n);
  let left = spawn fill(data[0..450], 0);                  // n is 450 - 0
  let right = spawn fill(data[450..n], 450);
  wait(left);
  wait(right);
  let g = Group[u64](2);
  spawn total(data) into g;
  let first = collect(g);
  wait(g);
  stack cells:u64[4] = zeroed;
  fill(cells, 5);
  let mut tag = Array[u64, 3]();
  fill(tag, 1);
  sort.sort(data);
  if first != 404550 || outer(data) != 404550 || largest(cells) != 8 || total(tag) != 6 { return 1; }
  if dot(data[0..3], data[3..6]) != 0 * 3 + 1 * 4 + 2 * 5 || checksum("abc") != 294 { return 2; }
  return 0;
}
"""
)

EXPLICIT = (
    SHORT.replace("fill(data[0..450], 0)", "fill(450 - 0, data[0..450], 0)")
    .replace("fill(data[450..n], 450)", "fill(n - 450, data[450..n], 450)")
    .replace("total(data) into", "total(len(data), data) into")
    .replace("fill(cells, 5)", "fill(len(cells), cells, 5)")
    .replace("fill(tag, 1)", "fill(len(tag), tag, 1)")
    .replace("sort.sort(data)", "sort.sort(len(data), data)")
    .replace("outer(data)", "outer(len(data), data)")
    .replace("largest(cells)", "largest(len(cells), cells)")
    .replace("total(tag)", "total(len(tag), tag)")
    .replace("dot(data[0..3], data[3..6])", "dot(3 - 0, data[0..3], data[3..6])")
    .replace('checksum("abc")', 'checksum(len("abc"), "abc")')
)


def test_a_left_out_extent_is_the_call_written_out():
    """The short call and the explicit one are the same program: same C++, same effect rows."""
    short, explicit = compile_source(SHORT), compile_source(EXPLICIT)
    assert "len(data), data" not in SHORT and short[0] == explicit[0]
    assert short[1]["functions"] == explicit[1]["functions"]
    assert compile_source(canonical_source(SHORT))[0] == short[0]  # The projection keeps what was written.
    assert "fill(cells, 5);" in canonical_source(SHORT)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_left_out_extent_runs(tmp_path, cxx):
    assert run(tmp_path, compile_source(SHORT)[0], *sanitized(cxx), "-pthread", cxx=cxx).returncode == 0


def test_parts_of_different_lengths_still_trap(tmp_path):
    """The first view sets the extent; a part of another length fails its guard, as with a written length."""
    source = HELPERS + "fn main() -> i32 { let mut d = Buf[u64](8); return i32(dot(d[0..4], d[4..7])); }"
    assert run(tmp_path, compile_source(source)[0], "-std=c++20", "-O1").returncode == -6


@pytest.mark.parametrize(
    ("code", "call"),
    [
        ("E-TYPE-MISMATCH", "let a = Buf[u64](8); let b = Buf[u64](8); return i32(dot(a, b));"),  # two identities
        ("E-ARITY", "let a = Buf[u64](8); return i32(dot(len(a), a));"),  # all extents or none
        ("E-ARITY", 'let s = "hi"; unsafe { let w = write(1, s); } return 0;'),  # C puts the length where it likes
        ("E-CALL-SHAPE", "let a = Buf[u64](8); return i32(total(a[0..pick()]));"),  # a part's bounds, as before
    ],
)
def test_what_a_left_out_extent_still_refuses(code, call):
    extra = "extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);\nfn pick() -> usize = 4;\n"
    refused(code, HELPERS + extra + "fn main() -> i32 { " + call + " }")

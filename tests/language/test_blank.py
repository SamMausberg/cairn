"""`_` binds nothing: as the binder of a payload arm it drops the payload, releasing an owner where the arm ends and
refusing a linear one, and as a loop's binder it only counts. Nothing reads it, so it repeats in nested arms and
loops, and its arm declares no C++ name. The accepted programs run natively under both compilers, an owner dropped
by `_` is released exactly once under AddressSanitizer's leak check, and every refusal names its code. A device
program that leaves `_` and every other kind of local unread builds for sm_120, which nvcc refused, and runs emulated.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import WARNINGS, device_build, ran_emulated, refused, run, sanitized, watched

BLANK = """
import std.core (Option, Result);

fn depth(a:Option[u64], b:Option[Buf[u8]]) -> u64 {
  let mut n:u64 = 0;
  match a {
    Some(_) => { match b { Some(_) => n = 2; None => n = 1; } }
    None => return 0;
  }
  for _ in 0..3 { for _ in 0..2 { n += 1; } }
  return n;
}

fn code(r:Result[u64, u8]) -> u64 {
  match r {
    Ok(v) => return v;
    Err(_) => return 0;
  }
}

fn main() -> i32 {
  for _ in 0..1000 {
    if depth(Some(7), Some(Buf[u8](64))) != 8 { return 1; }
  }
  if depth(None, None) != 0 || code(Ok(4)) != 4 || code(Err(9)) != 0 { return 2; }
  return 0;
}
"""

# The shape of the device program a probe could not build, `Err(_)` in host code beside device work, and a local left
# unread wherever one binds: a payload arm of an enum and of a generic instance, an element loop, `try`, a lane and a
# block. nvcc's front end refused each (#177-D declared and never read, #550-D set and never read), as g++ and clang++
# would without the -Wno-unused-variable and -Wno-unused-but-set-variable every native build passes them.
UNREAD = """
import std.core (Option, Result);

enum Shape { Dot; Line(u64); Box(u64); }

fn fill(n:usize, a:rw<u64>[n]@device) {
  parallel i in n { let spare:u64 = 7; a[i] = 1; }
}

fn sums(g:usize, out:rw<u64>[g]@device) {
  blocks b in g threads t in 32 {
    let mut spare:u64 = 0;
    spare = 2;
    let total = reduce + warp yield u64(t);
    if t == 0 { out[b] = total; }
  }
}

fn parsed(x:u64) -> Result[u64, u8] {
  if x > 9 { return Err(1); }
  return Ok(x);
}

fn either[T:copy](a:Option[T], b:T) -> T {
  match a {
    Some(_) => return b;
    None => return b;
  }
}

fn pick(s:Shape) -> u64 {
  match s {
    Dot => return 0;
    Line(length) => return 1;
    Box(_) => return 2;
  }
}

fn tried(x:u64) -> Result[u64, u8] {
  let got = try parsed(x);
  return Ok(0);
}

fn main() -> i32 {
  let unread:u64 = 3;
  let mut overwritten:u64 = 0;
  overwritten = 5;
  match parsed(4) {
    Ok(got) => println("parsed");
    Err(_) => return 1;
  }
  stack xs:u64[4] = zeroed;
  for i, x in xs { println("element"); }
  if pick(Shape.Box(4)) != 2 || either(Option.Some(u64(1)), u64(2)) != 2 { return 2; }
  match tried(12) {
    Ok(_) => return 3;
    Err(code) => println("refused");
  }
  buffer d:u64[64]@device = zeroed;
  fill(d);
  buffer s:u64[2]@device = zeroed;
  sums(s);
  return 0;
}
"""


def test_a_blank_binder_repeats_and_drops_an_owner_where_its_arm_ends():
    receipt = compile_source(BLANK)[1]
    assert "free" in receipt["functions"]["depth"]["effects"]  # the Buf `Some(_)` dropped is released
    assert "free" not in receipt["functions"]["code"]["effects"]
    assert compile_source(canonical_source(BLANK))[0] == compile_source(BLANK)[0]
    assert "Some(_) => n = 2;" in canonical_source(BLANK) and format_source(BLANK) == format_source(
        format_source(BLANK)
    )


@pytest.mark.parametrize(
    ("code", "source", "said"),
    [
        ("E-UNBOUND", "import std.core (Option);\nfn f(x:Option[u64]) -> u64 { match x { Some(_) => return _; None => "
         "return 0; } }", "_ binds nothing"),
        ("E-UNBOUND", "fn f() -> u64 { let mut t:u64 = 0; for _ in 0..3 { t += u64(_); } return t; }", "_ binds nothing"),
        ("E-LINEAR-LEAK", "import std.core (Option);\nlinear struct Lease { id:u64; }\nfn f(l:Option[Lease]) -> u64 { "
         "match l { Some(_) => return 1; None => return 0; } }", "Some(_) would drop a linear Lease"),
    ],
)  # fmt: skip
def test_what_a_blank_binder_refuses(code, source, said):
    assert said in refused(code, source)["message"]


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_blank_binders_run_natively(tmp_path, cxx):
    assert run(tmp_path, compile_source(BLANK)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


def test_an_owner_dropped_by_a_blank_binder_is_released_once(tmp_path):
    done = watched(tmp_path, compile_source(BLANK)[0], "clang++", "address")
    assert done.returncode == 0 and "LeakSanitizer" not in done.stderr and "AddressSanitizer" not in done.stderr


def test_a_blank_binder_declares_no_name_and_reads_no_payload():
    cpp = compile_source(BLANK)[0]
    assert "payload.v_Ok" in cpp and "payload.v_Some" not in cpp and "payload.v_Err" not in cpp
    assert " v__ = " not in cpp.replace("std::size_t v__ = cr_begin_", "")  # only `for _` loops count with it


def test_locals_the_program_never_reads_build_for_the_device(tmp_path):
    """Compiled for sm_120 by the project's own device command line; nothing runs on a device."""
    device_build(tmp_path, compile_source(UNREAD)[0], entry="main")


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_locals_the_program_never_reads_run_emulated(tmp_path, cxx):
    done = ran_emulated(tmp_path, compile_source(UNREAD)[0], cxx)
    assert done.stdout.split() == ["parsed", *["element"] * 4, "refused"]

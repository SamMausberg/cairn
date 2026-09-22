"""A lane may own a block of what lanes write: `out[b * S + j]` with `j` below one constant `S`, or anything the
facts place in `[b * S, b * S + S)`. Two lanes' blocks of one stride never meet, so the region stays race free.

The rule extends the lane rule rather than loosening it: `out[i]` is the block of stride 1, every access to a
written array must stay inside the lane's block of one stride, and an access the checker cannot place is still
`E-PARALLEL-RACE`. A block costs the lane pool what its elements cost, so a region of few heavy lanes still spreads.
"""

import os
import re
import shutil

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import contract, refused, watched

HEAD = (
    "const BLOCK:usize = 64;\nfn fill(n:usize, out:rw<u64>[n], v:u64) { for i in 0..n { out[i] = v; } }\n"
    "fn bump(v:rw<u64>) { v = add_wrap(v, 1); }\n"
)


def region(body: str) -> str:
    return (
        HEAD
        + "fn f(n:usize, k:usize, m:usize, out:rw<u64>[n], x:ro<u64>[n]) {\n  parallel b in k { "
        + body
        + " }\n}\n"
    )


ACCEPTED = {
    "row": "for j in 0..BLOCK { out[b * BLOCK + j] = x[j]; }",
    "row_by_literal": "for j in 0..4 { out[4 * b + j] = 1; }",
    "masked_offset": "let v = usize(x[b] & 7); out[b * 8 + v] = add_wrap(out[b * 8 + v], 1);",
    "named_bounds": "let lo = b * BLOCK; let hi = min(lo + BLOCK, n); for i in lo..hi { out[i] = x[i] * 2; }",
    "part_to_a_call": "let lo = b * BLOCK; let hi = min(lo + BLOCK, n); if lo < hi { fill(hi - lo, out[lo..hi], 1); }",
    "first_of_block": "out[b * BLOCK] = 1;",
    "own_element": "out[b] = x[b];",
    "own_element_to_a_call": "bump(out[b]); bump(out[b * 1]);",
    "row_element_to_a_call": "for j in 0..BLOCK { bump(out[b * BLOCK + j]); }",
}


@pytest.mark.parametrize("name", ACCEPTED)
def test_a_lane_may_own_its_block(name):
    compile_source(region(ACCEPTED[name]))


REFUSED = {
    "one_past_the_block": "for j in 0..BLOCK + 1 { out[b * BLOCK + j] = 1; }",
    "the_next_block": "out[b * BLOCK + BLOCK] = 1;",
    "two_strides": "out[b] = 1; out[b * BLOCK] = 2;",
    "an_unbounded_offset": "out[b * BLOCK + m] = 1;",
    "a_stride_that_is_not_constant": "for j in 0..m { out[b * m + j] = 1; }",
    "a_part_past_the_block": "let lo = b * BLOCK; fill(BLOCK + 1, out[lo..lo + BLOCK + 1], 1);",
    "a_moved_base": "let mut lo = b * BLOCK; lo = lo + 1; out[lo] = 1;",
    "a_read_outside": "out[b * BLOCK] = out[0];",
    "the_whole_array": "fill(n, out, 1);",
    "another_element_to_a_call": "bump(out[0]);",
    "a_part_of_another_lane": "let lo = b * BLOCK; fill(BLOCK, out[lo + BLOCK..lo + 2 * BLOCK], 1);",
}


@pytest.mark.parametrize("name", REFUSED)
def test_an_access_outside_the_block_is_a_race(name):
    refused("E-PARALLEL-RACE", region(REFUSED[name]))


# Per-block histograms, then a sequential merge: the shape the lane rule used to force into one thread.
HISTOGRAM = """
const BLOCK:usize = 4096;
fn blocked(n:usize, bins:rw<u64>[256], x:ro<u32>[n], k:usize, rows:usize, partial:rw<u64>[rows]) {
  parallel b in k {
    let lo = b * BLOCK;
    let hi = min(lo + BLOCK, n);
    let row = b * 256;
    for i in lo..hi {
      let v = usize(x[i] & 255);
      partial[row + v] = add_wrap(partial[row + v], 1);
    }
  }
  for v in 0..256 {
    let mut t:u64 = 0;
    for b in 0..k { t = add_wrap(t, partial[b * 256 + v]); }
    bins[v] = t;
  }
}

fn stamp(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn stamped(n:usize, k:usize, out:rw<u64>[n]) {
  parallel b in k {
    let lo = b * BLOCK;
    let hi = min(lo + BLOCK, n);
    if lo < hi { stamp(hi - lo, out[lo..hi], u64(lo)); }   // a helper writes the lane's own block
  }
}

fn plain(n:usize, bins:rw<u64>[256], x:ro<u32>[n]) {
  for i in 0..n { let v = usize(x[i] & 255); bins[v] = add_wrap(bins[v], 1); }
}

fn main() -> i32 {
  let n:usize = 1000003;
  let k = (n + BLOCK - 1) / BLOCK;
  buffer x:u32[n] = zeroed;
  for i in 0..n { x[i] = u32(shr(mul_wrap(u64(i), 0x9e3779b97f4a7c15), 40)); }
  let rows = k * 256;
  buffer partial:u64[rows] = zeroed;
  buffer a:u64[256] = zeroed;
  buffer z:u64[256] = zeroed;
  blocked(n, a, x, k, rows, partial);
  plain(n, z, x);
  for v in 0..256 { if a[v] != z[v] { return 1; } }
  buffer y:u64[n] = zeroed;
  stamped(n, k, y);
  for i in 0..n { if y[i] != u64(i) { return 2; } }
  return 0;
}
"""


def test_a_region_of_blocks_is_costed_as_its_elements():
    cpp = compile_source(HISTOGRAM)[0]
    assert re.search(r"cr::par::run\(v_k, \[&\]\(std::size_t v_b\) noexcept \{.*?\}, 4096\);", cpp, re.S)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("lanes", ["1", "5", "16"])
def test_a_blocked_histogram_counts_what_the_sequential_one_counts(tmp_path, cxx, lanes):
    done = contract(tmp_path, compile_source(HISTOGRAM)[0], cxx, env={**os.environ, "CAIRN_LANES": lanes})
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
@pytest.mark.parametrize("sanitizer", ["thread", "address"])
def test_a_blocked_histogram_is_sanitizer_clean(tmp_path, sanitizer):
    done = watched(tmp_path, compile_source(HISTOGRAM)[0], "clang++", sanitizer)
    assert done.returncode == 0, done.stderr[-4000:]
    assert "WARNING: ThreadSanitizer" not in done.stderr

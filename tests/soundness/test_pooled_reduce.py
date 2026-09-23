"""`reduce op parallel i in n yield e` folds on the host lane pool, in blocks that the count alone fixes.

Each block folds in index order and the block totals fold in block order, so every operator the form admits
gives exactly what the in-order fold gives, on any number of lanes. Checked unsigned `+` traps exactly when the
in-order fold does: every block total, and every running total of them, is at most the whole sum. Floats are
refused, because a sum in blocks is a different function of the same inputs.
"""

import os
import shutil
import signal

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import Diagnostic, compile_source
from emitted import contract, watched

VIEWS = "fn f(n:usize, x:ro<u64>[n], s:ro<i32>[n], d:ro<f32>[n]@device) -> u64 {\n  "


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-REDUCE-ORDER", "let t = reduce + parallel i in n yield f64(x[i]); return 0;"),
        ("E-REDUCE-ORDER", "let t = reduce * parallel i in n yield f32(x[i]); return 0;"),
        (
            "E-REDUCE-OP",
            "let t = reduce + parallel i in n yield s[i]; return 0;",
        ),  # A signed block total overflows alone.
        ("E-REDUCE-OP", "let t = reduce * parallel i in n yield x[i]; return 0;"),
        ("E-PARALLEL-NEST", "parallel j in n { let t = reduce add_wrap parallel i in n yield x[i]; } return 0;"),
        ("E-PARSE", "let t = compact x parallel i in n where true yield x[i]; return 0;"),
        ("E-PARSE", "let t = reduce add_wrap parallel i in 0..n yield x[i]; return 0;"),
    ],
)
def test_rejections(code, body):
    with pytest.raises(Diagnostic) as e:
        compile_source(VIEWS + body + "\n}")
    assert e.value.data["code"] == code, e.value.data["message"]


def test_a_pooled_fold_says_it_runs_on_the_lane_pool_and_a_plain_one_does_not():
    source = (
        "fn pooled(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce add_wrap parallel i in n yield x[i]; return t; }\n"
        "fn plain(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce add_wrap for i in n yield x[i]; return t; }\n"
        "fn device(n:usize, d:ro<f32>[n]@device) -> f32 { let t = reduce + parallel i in n yield d[i]; return t; }\n"
    )
    cpp, receipt = compile_source(source)
    rows = receipt["functions"]
    assert "par:host" in rows["pooled"]["effects"] and rows["pooled"]["syntactic_check_sites"]["parallel_regions"] == 1
    assert not any(e.startswith("par:") for e in rows["plain"]["effects"])
    assert "par:device" in rows["device"]["effects"]  # A device reduction is a tree either way.
    assert "cr::par::reduce<std::uint64_t>(" in cpp and "cr::gpu::reduce_on<float>(cr::gpu::here(), " in cpp


def test_the_canonical_projection_keeps_the_form():
    source = "fn f(n:usize, x:ro<u64>[n]) -> u64 { let t = reduce max parallel i in n yield x[i]; return t; }\n"
    canonical = canonical_source(source)
    assert "reduce max parallel i in n yield" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical


# Every operator on the pool against the same operator folded in order, on a count that no block size divides,
# with an independent answer for the checked sum. The status is the index of the first disagreement.
AGREES = """
fn sums(n:usize, x:ro<u64>[n], y:ro<u32>[n], z:ro<i32>[n]) -> i32 {
  let a = reduce add_wrap parallel i in n yield x[i];
  let b = reduce add_wrap for i in n yield x[i];
  let c = reduce mul_wrap parallel i in n yield x[i] | 1;
  let d = reduce mul_wrap for i in n yield x[i] | 1;
  let e = reduce ^ parallel i in n yield x[i];
  let f = reduce ^ for i in n yield x[i];
  let g = reduce & parallel i in n yield x[i] | 0xff00;
  let h = reduce & for i in n yield x[i] | 0xff00;
  let k = reduce | parallel i in n yield x[i] & 0x0f0f;
  let l = reduce | for i in n yield x[i] & 0x0f0f;
  let m = reduce min parallel i in n yield z[i];
  let o = reduce min for i in n yield z[i];
  let p = reduce max parallel i in n yield z[i];
  let q = reduce max for i in n yield z[i];
  let r = reduce + parallel i in n yield u64(y[i]);
  let s = reduce + for i in n yield u64(y[i]);
  if a != b { return 1; }
  if c != d { return 2; }
  if e != f { return 3; }
  if g != h { return 4; }
  if k != l { return 5; }
  if m != o || m != -500000 { return 6; }
  if p != q || p != 500000 { return 7; }
  if r != s || r != 1000003 * 1000002 / 2 { return 8; }
  return 0;
}

fn main() -> i32 {
  let n:usize = 1000003;
  buffer x:u64[n] = zeroed;
  buffer y:u32[n] = zeroed;
  buffer z:i32[n] = zeroed;
  for i in 0..n {
    x[i] = mul_wrap(u64(i), 0x9e3779b97f4a7c15);
    y[i] = u32(i);
    z[i] = i32(i % 1000001) - 500000;
  }
  return sums(n, x, y, z);
}
"""

# A checked sum whose every block total fits and whose whole does not: only the fold of the block totals traps.
OVERFLOWS = """
fn total(n:usize, x:ro<u32>[n]) -> u32 { let s = reduce + parallel i in n yield x[i]; return s; }
fn main() -> i32 {
  let n:usize = 100000;
  buffer x:u32[n] = zeroed;
  for i in 0..n { x[i] = EACH; }
  let t = total(n, x);
  if u64(t) != u64(n) * u64(EACH) { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("lanes", ["1", "3", "16"])
def test_every_operator_on_the_pool_is_the_in_order_fold(tmp_path, cxx, lanes):
    done = contract(tmp_path, compile_source(AGREES)[0], cxx, env={**os.environ, "CAIRN_LANES": lanes})
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_checked_sum_traps_when_only_the_whole_overflows(tmp_path, cxx):
    (tmp_path / "fits").mkdir()
    (tmp_path / "over").mkdir()
    fits = contract(tmp_path / "fits", compile_source(OVERFLOWS.replace("EACH", "40000"))[0], cxx)
    assert fits.returncode == 0, fits.stderr[-2000:]
    over = contract(tmp_path / "over", compile_source(OVERFLOWS.replace("EACH", "50000"))[0], cxx)
    assert over.returncode == -signal.SIGABRT, (over.returncode, over.stderr[-2000:])


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_pooled_fold_is_clean_under_thread_sanitizer(tmp_path):
    done = watched(tmp_path, compile_source(AGREES)[0], "clang++", "thread")
    assert done.returncode == 0, done.stderr[-4000:]
    assert "WARNING: ThreadSanitizer" not in done.stderr

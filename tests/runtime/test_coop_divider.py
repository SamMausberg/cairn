"""A cooperative region of two or three grid dimensions finds its block names without a division by a run-time value.

cairn_coop.hpp's Divider is made once per launch from a grid extent, and each block divides its number by it with a
multiply-high and two shifts. coop_divider.cpp holds it to the host compiler's / and % on 28.7 million pairs, in the
branch a host or emulated region runs and in the branch a device runs (its intrinsic defined as its documented
result). The lowering is then held to the language's order of blocks, block (bx, by, bz) being block
bx + gx * (by + gy * bz), in a host build, in an emulated device build, and in a device kernel compiled for sm_120,
which calls no subroutine.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import build, device_build, ran_emulated, sanitized

ROOT = Path(__file__).resolve().parents[2]
RUNTIME, NATIVE = ROOT / "src/cairn/runtime", ROOT / "tests/runtime"
STRICT = ["-std=c++20", "-O3", "-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
STRICT += ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]


@pytest.mark.parametrize("branch", ["host", "device"])
@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_the_divider_agrees_with_division_in_both_branches(tmp_path, cxx, branch):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    exe = tmp_path / "coop_divider"
    extra = ["-DDEVICE"] if branch == "device" else []
    subprocess.run([cxx, *STRICT, *extra, f"-I{RUNTIME}", str(NATIVE / "coop_divider.cpp"), "-o", str(exe)], check=True)
    done = subprocess.run([str(exe), "20000000"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "divider ok: 28722291 cases, 0 mismatches" in done.stdout, done.stdout


NAMES = """
fn names(g:usize, h:usize, n:usize, out:rw<u64>[n]) {
  blocks bx, by, bz in g, h, 3 threads t in 32 {
    let i = ((bz * h + by) * g + bx) * 32 + t;
    if i < n { out[i] = u64(bx) + 1000 * u64(by) + 1000000 * u64(bz) + 1000000000 * u64(t); }
  }
}
"""
CHECK = """
fn main() -> i32 {
  for g in 1..6 {
    for h in 1..4 {
      let n = g * h * 96;
      buffer out:u64[n] = zeroed;
      FILL
      for bz in 0..3 { for by in 0..h { for bx in 0..g { for t in 0..32 {
        let want = u64(bx) + 1000 * u64(by) + 1000000 * u64(bz) + 1000000000 * u64(t);
        if out[((bz * h + by) * g + bx) * 32 + t] != want { return 1; }
      } } } }
    }
  }
  return 0;
}
"""
HOST = NAMES + CHECK.replace("FILL", "names(g, h, n, out);")
DEVICE = NAMES.replace("[n])", "[n]@device)") + CHECK.replace(
    "FILL", "buffer there:u64[n]@device = zeroed;\n      names(g, h, n, there);\n      transfer(out, there);"
)


def test_a_block_divides_by_the_launchs_divider_not_by_the_extent():
    cpp = compile_source(HOST)[0]
    assert "cr::coop::Divider cr_d0(cr_g0);" in cpp and "cr::coop::Divider cr_d1(cr_g1);" in cpp
    assert "cr_d0.div(cr_b)" in cpp and "cr_d1.div(cr_q0)" in cpp
    assert "% cr_g" not in cpp and "/ cr_g" not in cpp


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_block_has_the_names_the_language_gives_it_on_the_host(tmp_path, cxx):
    exe = build(tmp_path, compile_source(HOST)[0], *sanitized(cxx), cxx=cxx)
    assert subprocess.run([exe], capture_output=True, timeout=300).returncode == 0


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_block_has_the_names_the_language_gives_it_emulated(tmp_path, cxx):
    ran_emulated(tmp_path, compile_source(DEVICE)[0], cxx)


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_device_block_split_calls_no_division(tmp_path, cxx):
    """Built for sm_120 with each host compiler and not run: splitting the block number takes a multiply-high, where
    `/` and `%` by a grid extent called the 64-bit division subroutine."""
    if not shutil.which("cuobjdump"):
        pytest.skip("needs cuobjdump")
    obj = device_build(tmp_path, compile_source(DEVICE)[0], entry=None, cxx=cxx)
    sass = subprocess.run(["cuobjdump", "-sass", str(obj)], capture_output=True, text=True, check=True).stdout
    kernel = next(body for body in sass.split("Function : ")[1:] if "ci_names" in body.split("\n")[0])
    assert "IMAD.WIDE.U32" in kernel or "IMAD.HI" in kernel  # the multiply-high
    assert "CALL" not in kernel

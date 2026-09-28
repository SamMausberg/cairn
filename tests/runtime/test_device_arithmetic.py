"""A device lane's checked arithmetic: the same traps as the host's, and a multiply that costs no division.

cairn_runtime.hpp checks a multiply in a lane by the product's high half. Its device branches are compiled for the
host here, with the CUDA intrinsics they call defined as their documented results, and held to the host compiler's
__builtin_*_overflow on boundary and random operands of every integer type; tests/runtime/gpu_arithmetic.cu makes the
same comparison on a device, only under `make gpu`. A lane with checked multiplies compiles for sm_120 to no call, and
a loop of checked arithmetic keeps its exit on each target, which NVVM 7.0.1 deleted while cr::trap() was noreturn.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.target import parse, resolve
from emitted import device_build
from support import device_lock, device_reason

ROOT = Path(__file__).resolve().parents[2]
RUNTIME, NATIVE = ROOT / "src/cairn/runtime", ROOT / "tests/runtime"
STRICT = ["-std=c++20", "-O3", "-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
STRICT += ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_the_device_checks_agree_with_the_host_compiler(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    exe = tmp_path / "device_arithmetic"
    subprocess.run([cxx, *STRICT, f"-I{RUNTIME}", str(NATIVE / "device_arithmetic.cpp"), "-o", str(exe)], check=True)
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "ok after" in done.stdout, done.stdout + done.stderr[-2000:]


LANES = """
fn scaled(n:usize, out:rw<u64>[n]@device, x:ro<u64>[n]@device, k:u64, w:u32) {
  parallel i in n { out[i] = x[i] * 256 + (u64(i) + k) * 8 + u64(w * 4); }
}
"""


def test_a_checked_multiply_in_a_lane_calls_no_division(tmp_path):
    """Built for sm_120 and not run: the lane's checked multiplies of u64 and u32 compile to multiplies and a test of
    the high half, and the kernel calls no subroutine, which a 64-bit division would be."""
    if not shutil.which("cuobjdump"):
        pytest.skip("needs cuobjdump")
    cpp = compile_source(LANES)[0]
    assert cpp.count("cr::mul<") >= 3
    obj = device_build(tmp_path, cpp, entry=None)
    sass = subprocess.run(["cuobjdump", "-sass", str(obj)], capture_output=True, text=True, check=True).stdout
    lanes = sass.split("Function : ")[1:]
    kernel = next(body for body in lanes if "ci_scaled" in body.split("\n")[0])
    assert "BPT.TRAP" in kernel  # the checks are there
    assert "CALL" not in kernel  # and none divides


def nvcc(out: Path, device) -> list[str]:
    return ["nvcc", "-std=c++20", "-O3", "--fmad=false", *device.flags(), "--expt-relaxed-constexpr", "-Werror",
            "all-warnings", f"-I{RUNTIME}", str(NATIVE / "gpu_arithmetic.cu"), "-o", str(out)]  # fmt: skip


ROWS = """#include "cairn_runtime.hpp"
__global__ void rows(std::size_t m, float* out) {
  const std::size_t ty = threadIdx.x / 32;
  for(std::size_t i = 0; i < 16; ++i)
    for(std::size_t j = 0; j < 2; ++j)
      if(cr::add<std::size_t>(ty, cr::mul<std::size_t>(4, i)) < m) out[(ty + 4 * i) * 2 + j] = 1.0f;
  asm volatile("// after the loop");
}
"""


@pytest.mark.skipif(not shutil.which("nvcc"), reason="nvcc is not installed")
@pytest.mark.parametrize("target", ["sm_80", "sm_90", "sm_120"])
def test_a_loop_of_checked_arithmetic_keeps_its_exit(tmp_path, target):
    """The loop that tile64 starts with, reduced: the multiply and the add may trap, and a row past `m` is skipped.
    CUDA 12.8 and 13.0 compile sm_80 and sm_90 with NVVM 7.0.1, which, while cr::trap() was noreturn there, deleted
    the loop's exit, so the code after it vanished and a kernel ran on until a guard trapped where every guard of the
    source held. Compiled to PTX and read; nothing runs."""
    source = tmp_path / "rows.cu"
    source.write_text(ROWS)
    ptx = tmp_path / "rows.ptx"
    subprocess.run(["nvcc", "-std=c++20", "-O3", "--fmad=false", *parse(target).flags(), "--expt-relaxed-constexpr",
                    "-Werror", "all-warnings", f"-I{RUNTIME}", "-ptx", str(source), "-o", str(ptx)], check=True,
                   timeout=600)  # fmt: skip
    assert "after the loop" in ptx.read_text()


@pytest.mark.skipif(not shutil.which("nvcc"), reason="nvcc is not installed")
def test_the_device_comparison_compiles_for_sm_120(tmp_path):
    subprocess.run([*nvcc(tmp_path / "gpu_arithmetic.o", parse("sm_120")), "-c"], check=True, timeout=600)


def test_the_device_checks_agree_on_a_device(tmp_path):
    """Run only under `make gpu`: the kernel records every check's answer, and nothing traps."""
    if reason := device_reason():
        pytest.skip(reason)
    exe = tmp_path / "gpu_arithmetic"
    subprocess.run(nvcc(exe, resolve()), check=True, timeout=600)
    with device_lock():
        done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "gpu arithmetic: ok" in done.stdout, done.stdout + done.stderr[-2000:]

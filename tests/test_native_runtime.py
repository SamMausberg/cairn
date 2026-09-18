"""Compile and run the native parallel and device runtime tests.

The executables under tests/native are self checking: exit 0 is a pass. Each also runs one
named death case per invocation, which must abort the process, so those are driven here as
subprocesses. Device work is skipped with a reason when nvcc or a GPU is missing.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/cairn/runtime"
NATIVE = ROOT / "tests/native"

# The language contract: strict floating point, no unwinding, warnings are errors. nvcc takes
# the first two itself and the rest only through -Xcompiler.
STRICT = ["-std=c++20", "-O3"]
HOST = ["-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
HOST += ["-Wall", "-Wextra", "-Werror"]
HOST += ["-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-unused-but-set-variable"]
# nvcc adds the device half of the same contract; see the note at the top of cairn_gpu.hpp.
DEVICE = ["--fmad=false", "-arch=native", "--extended-lambda", "--expt-relaxed-constexpr"]
DEVICE += ["-Werror", "all-warnings"]
HOSTS = [cc for cc in ("g++", "g++-12", "clang++") if shutil.which(cc)]


def build(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-4000:]
    assert done.stderr == "", f"the runtime must build without a single warning:\n{done.stderr}"


def drop_core_limit() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_cases(exe: Path) -> None:
    """Every death case must abort: a guard failure ends the process, it does not return."""
    listed = subprocess.run([str(exe), "--list"], capture_output=True, text=True)
    assert listed.returncode == 0
    cases = listed.stdout.split()
    assert cases, "no death cases were declared"
    for case in cases:
        died = subprocess.run([str(exe), case], capture_output=True, text=True, preexec_fn=drop_core_limit)
        assert died.returncode == -signal.SIGABRT, (case, died.returncode, died.stdout, died.stderr)


def device_reason() -> str:
    if not shutil.which("nvcc"):
        return "nvcc is not installed: the device runtime cannot be compiled here"
    smi = shutil.which("nvidia-smi")
    if not smi:
        return "nvidia-smi is missing: no NVIDIA device is visible"
    found = subprocess.run([smi, "-L"], capture_output=True, text=True)
    if found.returncode != 0 or "GPU 0" not in found.stdout:
        return "no CUDA device is available on this host"
    return ""


@pytest.fixture(scope="session")
def parallel_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    exe = tmp_path_factory.mktemp("native") / "parallel_runtime"
    src = str(NATIVE / "parallel_runtime.cpp")
    build([HOSTS[0], *STRICT, *HOST, "-pthread", f"-I{RUNTIME}", src, "-o", str(exe)])
    return exe


@pytest.fixture(scope="session")
def gpu_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if reason := device_reason():
        pytest.skip(reason)
    exe = tmp_path_factory.mktemp("native") / "gpu_runtime"
    host = ["-Xcompiler", ",".join(HOST)]
    src = str(NATIVE / "gpu_runtime.cu")
    build(["nvcc", *STRICT, *DEVICE, *host, f"-I{RUNTIME}", src, "-o", str(exe)])
    return exe


@pytest.mark.parametrize("compiler", HOSTS)
def test_headers_build_without_cuda(compiler: str, tmp_path: Path) -> None:
    """cairn_runtime.hpp and cairn_parallel.hpp stay plain C++20 when no CUDA is present."""
    src = str(NATIVE / "parallel_runtime.cpp")
    out = str(tmp_path / "check")
    build([compiler, *STRICT, *HOST, "-pthread", f"-I{RUNTIME}", src, "-o", out])


def test_parallel_runtime(parallel_exe: Path) -> None:
    done = subprocess.run([str(parallel_exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout


def test_parallel_runtime_deaths(parallel_exe: Path) -> None:
    run_cases(parallel_exe)


def test_gpu_runtime(gpu_exe: Path) -> None:
    done = subprocess.run([str(gpu_exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout


def test_gpu_runtime_deaths(gpu_exe: Path) -> None:
    run_cases(gpu_exe)

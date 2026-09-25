"""Compile and run the native parallel, I/O ring, execution context and device runtime tests.

The executables beside this file are self checking: exit 0 is a pass. Each also runs one
named death case per invocation, which must abort the process, so those are driven here as
subprocesses. Device work runs only under `make gpu`, one run at a time (`support.device_reason`);
everywhere else a device test is compiled for a named architecture and never run.

The parallel test is run at several lane counts (CAIRN_LANES), under ThreadSanitizer and under
AddressSanitizer with UBSan, because the lane pool is shared, long lived and joined at exit.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from cairn.projects.target import DeviceTarget, parse, resolve
from emitted import NVCC_HOST
from support import device_lock, device_reason

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "src/cairn/runtime"
NATIVE = ROOT / "tests/runtime"

# The language contract: strict floating point, no unwinding, warnings are errors. nvcc takes
# the first two itself and the rest only through -Xcompiler.
STRICT = ["-std=c++20", "-O3"]
HOST = ["-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
HOST += ["-Wall", "-Wextra", "-Werror"]
HOST += ["-Wno-unused-parameter", "-Wno-unused-variable", "-Wno-unused-but-set-variable"]
# nvcc adds the device half of the same contract; see the note at the top of cairn_gpu.hpp. The device target is
# named per build (projects/target.py), never native.
DEVICE = ["--fmad=false", "--extended-lambda", "--expt-relaxed-constexpr"]
DEVICE += ["-Werror", "all-warnings"]
HOSTS = [cc for cc in ("g++", "g++-12", "clang++") if shutil.which(cc)]


def build(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-4000:]
    assert done.stderr == "", f"the runtime must build without a single warning:\n{done.stderr}"


def drop_core_limit() -> None:
    from cairn.verify.testing import no_core

    no_core()


def run_cases(exe: Path) -> None:
    """Every death case must abort: a guard failure ends the process, it does not return."""
    listed = subprocess.run([str(exe), "--list"], capture_output=True, text=True)
    assert listed.returncode == 0
    cases = listed.stdout.split()
    assert cases, "no death cases were declared"
    for case in cases:
        died = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=300, preexec_fn=drop_core_limit)
        assert died.returncode == -signal.SIGABRT, (case, died.returncode, died.stdout, died.stderr)


def lanes(count: str | None) -> dict[str, str]:
    """The environment for one lane count; a timeout here is a hung pool, which is a failure."""
    return {**os.environ, "CAIRN_LANES": count} if count else dict(os.environ)


@pytest.fixture(scope="session")
def parallel_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    exe = tmp_path_factory.mktemp("native") / "parallel_runtime"
    src = str(NATIVE / "parallel_runtime.cpp")
    build([HOSTS[0], *STRICT, *HOST, "-pthread", f"-I{RUNTIME}", src, "-o", str(exe)])
    return exe


@pytest.fixture(
    scope="session", params=[(test, cc) for test in ("io_runtime", "reuse_runtime") for cc in HOSTS], ids="-".join
)
def contract_exe(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The ring's own test, and the execution context's bookkeeping against a mock device, each under each host
    compiler with the contract flags; `test_the_runtime_is_clean_under_address_leak_and_ub` builds them once more."""
    test, compiler = request.param
    exe = tmp_path_factory.mktemp("native") / f"{test}_{compiler}"
    build([compiler, *STRICT, *HOST, f"-I{RUNTIME}", str(NATIVE / f"{test}.cpp"), "-o", str(exe)])
    return exe


@pytest.fixture(scope="session")
def sanitized_parallel(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One build per sanitizer, at -O1 with frames, since both cost too much to build twice."""
    made: dict[str, Path] = {}
    where = tmp_path_factory.mktemp("sanitized")
    for name, extra in (
        ("thread", ["-fsanitize=thread"]),
        ("address", ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"]),
    ):
        exe = where / f"parallel_{name}"
        line = [HOSTS[0], "-std=c++20", "-O1", "-g", *HOST, "-pthread", *extra]
        build([*line, f"-I{RUNTIME}", str(NATIVE / "parallel_runtime.cpp"), "-o", str(exe)])
        made[name] = exe
    return made


@pytest.fixture(scope="session")
def gpu_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if reason := device_reason():
        pytest.skip(reason)
    exe = tmp_path_factory.mktemp("native") / "gpu_runtime"
    # CCCL 3 (CUDA 13) needs the host pass to parse exceptions; see the note at the top of cairn_gpu.hpp.
    host = ["-Xcompiler", ",".join(f.replace("-fno-exceptions", "-fexceptions") for f in HOST)]
    src = str(NATIVE / "gpu_runtime.cu")
    build(["nvcc", *STRICT, *DEVICE, *host, f"-I{RUNTIME}", src, "-o", str(exe)])
    return exe


@pytest.mark.parametrize("compiler", HOSTS)
def test_headers_build_without_cuda(compiler: str, tmp_path: Path) -> None:
    """cairn_runtime.hpp and cairn_parallel.hpp stay plain C++20 when no CUDA is present."""
    src = str(NATIVE / "parallel_runtime.cpp")
    out = str(tmp_path / "check")
    build([compiler, *STRICT, *HOST, "-pthread", f"-I{RUNTIME}", src, "-o", out])


# One lane (no pool threads at all), fewer lanes than a region's chunks, an odd count, and the
# machine's own. A timeout is a hung pool and fails the test rather than hanging the session.
@pytest.mark.parametrize("count", [None, "1", "2", "3", "5", "64"])
def test_parallel_runtime(parallel_exe: Path, count: str | None) -> None:
    done = subprocess.run([str(parallel_exe)], capture_output=True, text=True, timeout=600, env=lanes(count))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout
    assert (f"on {count} lanes" if count else "lanes") in done.stdout  # what was asked for is what ran


def test_parallel_runtime_deaths(parallel_exe: Path) -> None:
    run_cases(parallel_exe)


@pytest.mark.parametrize("count", ["4", "16"])
def test_parallel_runtime_has_no_race(sanitized_parallel: dict[str, Path], count: str) -> None:
    """Regions from several threads, a blocking lane and the exit join, under ThreadSanitizer."""
    if not shutil.which("setarch"):
        pytest.skip("ThreadSanitizer needs setarch -R here")
    where = {**lanes(count), "TSAN_OPTIONS": "halt_on_error=1"}
    done = subprocess.run(
        ["setarch", "-R", str(sanitized_parallel["thread"])], capture_output=True, text=True, timeout=1800, env=where
    )
    assert done.returncode == 0, done.stdout + done.stderr[-4000:]
    assert "ThreadSanitizer" not in done.stderr, done.stderr[-4000:]


@pytest.mark.parametrize("count", ["4", "64"])
def test_parallel_runtime_is_clean_under_address_and_ub(sanitized_parallel: dict[str, Path], count: str) -> None:
    """The region descriptor lives on a caller's stack, so a late worker would be a use after free."""
    done = subprocess.run(
        [str(sanitized_parallel["address"])], capture_output=True, text=True, timeout=1800, env=lanes(count)
    )
    assert done.returncode == 0, done.stdout + done.stderr[-4000:]
    assert "Sanitizer" not in done.stderr, done.stderr[-4000:]


def test_the_io_and_reuse_runtimes(contract_exe: Path) -> None:
    done = subprocess.run([str(contract_exe)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout


def test_the_io_and_reuse_runtime_deaths(contract_exe: Path) -> None:
    run_cases(contract_exe)


# io_runtime: the kernel writes into storage the ring owns, so a Buf released before its operation finished shows here.
# reuse_runtime: every stream, event and arena a context made is gone when it ends, so a leak or a late free shows here.
@pytest.mark.parametrize("test", ["io_runtime", "reuse_runtime"])
def test_the_runtime_is_clean_under_address_leak_and_ub(test: str, tmp_path: Path) -> None:
    exe = tmp_path / f"{test}_asan"
    line = [HOSTS[-1], "-std=c++20", "-O1", "-g", *HOST, "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    build([*line, f"-I{RUNTIME}", str(NATIVE / f"{test}.cpp"), "-o", str(exe)])
    done = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=120, env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}
    )
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stdout + done.stderr[-4000:]


@pytest.mark.parametrize("compiler", HOSTS)
def test_the_ring_builds_against_an_io_uring_h_that_does_not_include_the_time_types(compiler: str, tmp_path: Path):
    """Linux 5.15's io_uring.h, which Ubuntu 22.04 ships, does not include linux/time_types.h, so the ring names
    __kernel_timespec only because it includes that header itself. Here the installed io_uring.h without that line
    stands in for the older one."""
    installed = Path("/usr/include/linux/io_uring.h")
    if not installed.is_file():
        pytest.skip("the kernel's io_uring.h is not installed")
    older = tmp_path / "include/linux/io_uring.h"
    older.parent.mkdir(parents=True)
    lines = installed.read_text(encoding="utf-8").splitlines(keepends=True)
    older.write_text("".join(line for line in lines if line.strip() != "#include <linux/time_types.h>"))
    probe = tmp_path / "ring.cpp"
    probe.write_text('#include "cairn_io.hpp"\nint main() { return 0; }\n')
    line = [compiler, *STRICT, *HOST, f"-I{tmp_path / 'include'}", f"-I{RUNTIME}", "-c", str(probe)]
    build([*line, "-o", str(tmp_path / "ring.o")])


def test_gpu_runtime(gpu_exe: Path) -> None:
    with device_lock():
        done = subprocess.run([str(gpu_exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout


def test_gpu_runtime_deaths(gpu_exe: Path) -> None:
    with device_lock():
        run_cases(gpu_exe)


def device_line(source: str, out: Path, device: DeviceTarget | None = None) -> list[str]:
    """nvcc's command for one device test, for `device`, or the target resolved here (the GPU make gpu runs on)."""
    host = ["-Xcompiler", ",".join(f.replace("-fno-exceptions", "-fexceptions") for f in HOST)]
    arch = (device or resolve()).flags()
    ccbin = ["-ccbin", NVCC_HOST]
    return ["nvcc", *STRICT, *DEVICE, *arch, *ccbin, *host, f"-I{RUNTIME}", str(NATIVE / source), "-o", str(out)]


@pytest.mark.skipif(not shutil.which("nvcc"), reason="nvcc is not installed")
@pytest.mark.parametrize("source", ["gpu_runtime.cu", "gpu_reuse.cu"])
def test_device_tests_compile_for_a_named_architecture(source: str, tmp_path: Path) -> None:
    """Compiled for sm_120 and never run: the device half is checked by `make gpu` alone."""
    build([*device_line(source, tmp_path / "device.o", parse("sm_120")), "-c"])


@pytest.fixture(scope="session")
def gpu_reuse_exe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if reason := device_reason():
        pytest.skip(reason)
    exe = tmp_path_factory.mktemp("native") / "gpu_reuse"
    build(device_line("gpu_reuse.cu", exe))
    return exe


def test_gpu_reuse(gpu_reuse_exe: Path) -> None:
    with device_lock():
        done = subprocess.run([str(gpu_reuse_exe)], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "ok after" in done.stdout


def test_gpu_reuse_deaths(gpu_reuse_exe: Path) -> None:
    with device_lock():
        run_cases(gpu_reuse_exe)

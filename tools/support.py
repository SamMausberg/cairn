#!/usr/bin/env python3
"""Shared host helpers for the tools and benchmarks: one owner for flags and headers.

Every command line here comes from cairn.projects.toolchain; no script carries its own copy.
The harnesses historically pinned -march=x86-64-v3. `best_profile` keeps that intent
without the architecture: it is the richest profile of THIS host family that the named
compilers accept and this CPU actually executes, and callers record which one ran.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.projects.toolchain import FAMILIES, command, find, flags, host_family

FAMILY = host_family()
ARCHS = ("baseline", *FAMILIES[FAMILY])
# A vectorizable reduction, so an unsupported instruction set fails here and not inside a harness.
PROBE = """#include <cstdint>
static std::uint64_t data[512];
int main() {
  std::uint64_t total = 0;
  for (int i = 0; i < 512; ++i) data[i] = std::uint64_t(i) * 2654435761u;
  for (int i = 0; i < 512; ++i) total += data[i] * 3 + 1;
  return total == 0;
}
"""


@functools.cache
def best_profile(*compilers: str) -> str:
    """The richest profile of this host family that every named compiler builds and this CPU runs."""
    with tempfile.TemporaryDirectory(prefix="cairn-profile-") as directory:
        source = Path(directory) / "probe.cpp"
        source.write_text(PROBE)
        for arch in reversed(FAMILIES[FAMILY]):
            if all(_runs(command(cxx, str(source), f"{directory}/probe", arch, "exe")) for cxx in compilers):
                return arch
    return FAMILIES[FAMILY][0]  # The family baseline is defined to build and run here.


def _runs(argv: list[str]) -> bool:
    for step in [argv, [argv[-1]]]:
        done = subprocess.run(step, capture_output=True, text=True)
        if done.returncode:
            return False
    return True


def profile_flags(kind: str = "library", arch: str | None = None, drop: tuple[str, ...] = (), add=()) -> list[str]:
    """Toolchain flags for one profile, minus every flag starting with a `drop` prefix, plus `add`.

    Deviations are named by the caller in its own receipt; nothing here invents a flag.
    """
    kept = [f for f in flags(arch or best_profile("clang++"), kind) if not f.startswith(drop or ("\0",))]
    return [*kept, *add]


def version(cxx: str) -> str:
    return subprocess.run([find(cxx), "--version"], text=True, capture_output=True, check=True).stdout


def environment(*compilers: str, arch: str | None = None) -> dict:
    """What actually ran, so no result reads as another machine's."""
    return {
        "host": f"{platform.system()} {platform.machine()}",
        "arch_family": FAMILY,
        "arch_profile": arch or best_profile(*(compilers or ("clang++",))),
        "compilers": {cxx: version(cxx).splitlines()[0] for cxx in compilers},
    }


def runtime_headers(directory: Path, transform=None) -> None:
    """Write EVERY runtime header beside generated C++; emitted code may include any of them."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in RUNTIME_FILES.items():
        (directory / name).write_text(transform(name, text) if transform else text)


def generate(source: Path, out: Path) -> dict:
    """Generated C++ for one .cairn file plus its runtime headers, in `out`; returns the receipt."""
    cpp, receipt = compile_source(source.read_text(encoding="utf-8"))
    runtime_headers(out)
    (out / (source.stem + ".cpp")).write_text(cpp)
    return receipt


DEVICE_LOCK = Path("/tmp/cairn-gpu.lock")  # one path for every checkout and worktree on the machine


def device_reason() -> str:
    """Why no code may run on a CUDA device here, or "" when it may.

    Device code runs only with CAIRN_GPU_TESTS=1, which `make gpu` sets. Under WSL2 and Windows the GPU also drives
    the display: a device run can make the driver reset its engine, and a morning of test runs that each did so ended
    in a host crash twice. So the everyday suite never touches the device, and `device_lock` serializes the rest.
    """
    if os.environ.get("CAIRN_GPU_TESTS") != "1":
        return "device code runs only under `make gpu` (CAIRN_GPU_TESTS=1)"
    if not shutil.which("nvcc"):
        return "nvcc is not installed"
    smi = shutil.which("nvidia-smi")
    found = subprocess.run([smi, "-L"], capture_output=True, text=True) if smi else None
    if found is None or found.returncode != 0 or "GPU 0" not in found.stdout:
        return "no CUDA device is visible"
    return ""


@contextlib.contextmanager
def device_lock():
    """Hold the machine-wide device lock, so that no two device runs overlap, from any process or checkout."""
    with open(DEVICE_LOCK, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

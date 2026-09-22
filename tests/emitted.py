"""Emitted C++ built beside the runtime headers and run: the one way a test does that by hand.

A test that wants the project's own build goes through `cairn.projects.build`; these helpers are for a test that
names its compiler flags, a sanitizer or its own `main`.
"""

import contextlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES
from cairn.compiler.codegen import mangle
from cairn.projects.toolchain import command
from support import device_lock, device_reason

SANITIZED = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]


def sanitized(cxx: str) -> list[str]:
    """`SANITIZED` under clang++; under g++ the same build without the sanitizers, run for its behaviour alone."""
    return SANITIZED if cxx == "clang++" else SANITIZED[:4]


def emit(tmp_path: Path, cpp: str, entry: str | None = "main") -> tuple[str, str]:
    """Write `cpp`, a C++ `main` returning what the CAIRN `entry` returns, and every runtime header; the source
    and executable paths. `entry=None` leaves `cpp` to bring its own `main`."""
    start = f"int main() {{ return static_cast<int>(cf_{mangle(entry)}()); }}\n" if entry else ""
    (tmp_path / "p.cpp").write_text(cpp + start)
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    return str(tmp_path / "p.cpp"), str(tmp_path / "p")


def build(tmp_path: Path, cpp: str, *flags: str, cxx: str = "clang++", entry: str | None = "main", timeout=180) -> str:
    """`cpp` compiled by `cxx` with exactly `flags`, skipping the test when `cxx` is absent; the executable."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    source, executable = emit(tmp_path, cpp, entry)
    subprocess.run([cxx, *flags, source, "-o", executable], check=True, timeout=timeout)
    return executable


def run(tmp_path: Path, cpp: str, *flags: str, cxx="clang++", entry="main", timeout=180, env=None):
    """`build`, then run it once; the finished process, with its status and its output as text."""
    executable = build(tmp_path, cpp, *flags, cxx=cxx, entry=entry, timeout=timeout)
    return subprocess.run([executable], capture_output=True, text=True, timeout=timeout, env=env)


def contract(tmp_path: Path, cpp: str, cxx: str, *extra: str, cuda=False, timeout=240, env=None, under=()):
    """`cpp` built by the project's own command line for `cxx` plus `extra`, then run once `under` a wrapper."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    if cuda and (reason := device_reason()):  # A device program runs inside `on_device` or not at all.
        pytest.skip(reason)
    source, executable = emit(tmp_path, cpp)
    subprocess.run([*command(cxx, source, executable, kind="exe", cuda=cuda), *extra], check=True, timeout=timeout)
    return subprocess.run([*under, executable], capture_output=True, text=True, timeout=timeout, env=env)


def watched(tmp_path: Path, cpp: str, cxx: str, sanitizer: str):
    """The project's own build under `sanitizer`, with leak detection, run without address randomization, which
    ThreadSanitizer needs on newer kernels."""
    if not shutil.which(cxx) or not shutil.which("setarch"):
        pytest.skip(f"needs {cxx} and setarch")
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=1"}
    return contract(tmp_path, cpp, cxx, "-g", f"-fsanitize={sanitizer}", timeout=300, env=env, under=("setarch", "-R"))


@contextlib.contextmanager
def on_device():
    """Skip the rest of a test unless device code may run here (`support.device_reason`); otherwise hold the
    machine-wide device lock while it runs."""
    if reason := device_reason():
        pytest.skip(reason)
    with device_lock():
        yield

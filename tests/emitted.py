"""What a test does by hand with a CAIRN program: require that it is refused with one code, build the C++ it
emits beside the runtime headers and run it, or build it as `cairn build` does and run that (`native`).

The C++ helpers are for a test that names its compiler flags, a sanitizer or its own `main`.
"""

import contextlib
import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic, compile_source
from cairn.compiler.codegen import mangle
from cairn.projects.build import build as build_project
from cairn.projects.project import load_project
from cairn.projects.target import parse
from cairn.projects.toolchain import command
from support import device_lock, device_reason

SANITIZED = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]


def refused(code: str, source: str, **options) -> dict:
    """Require that `source` is refused with exactly `code`; the diagnostic, for anything else a test checks."""
    with pytest.raises(Diagnostic) as error:
        compile_source(source, **options)
    assert error.value.data["code"] == code, error.value.data["message"]
    return error.value.data


def code_of(call) -> str:
    """The code of the diagnostic `call()` raises; the test fails when it raises none."""
    with pytest.raises(Diagnostic) as error:
        call()
    return error.value.data["code"]


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


def library(tmp_path: Path, cpp: str, cxx: str, *flags: str) -> ctypes.CDLL:
    """`cpp` built as a shared library and loaded: with exactly `flags` when the test names them, otherwise by the
    project's own command line for `cxx`. Skips the test when `cxx` is absent."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    source, artifact = emit(tmp_path, cpp, entry=None)
    shared = artifact + ".so"
    line = (
        [cxx, *flags, "-shared", "-fPIC", source, "-o", shared]
        if flags
        else command(cxx, source, shared, kind="library")
    )
    subprocess.run(line, check=True, timeout=240)
    return ctypes.CDLL(shared)


def artifact(project: Path, cxx: str = "clang++", timeout=240, **options) -> str:
    """A project directory or one `.cairn` file built as `cairn build` builds it; the executable. Skips the test
    when `cxx` is absent."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = build_project(load_project(project), cxx=cxx, timeout=timeout, **options)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    return record["artifact"]


def program(tmp_path: Path, source: str, cxx="clang++", timeout=180) -> str:
    """One CAIRN program built in its own directory; the executable."""
    path = tmp_path / "program.cairn"
    path.write_text(source, encoding="utf-8")
    return artifact(path, cxx, timeout, kind="exe")


def native(tmp_path: Path, source: str, cxx="clang++", timeout=180):
    """Build one CAIRN program in its own directory and run it; a nonzero exit is a failure."""
    done = subprocess.run([program(tmp_path, source, cxx, timeout)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
    return done


def contract(tmp_path: Path, cpp: str, cxx: str, *extra: str, cuda=False, timeout=240, env=None, under=()):
    """`cpp` built by the project's own command line for `cxx` plus `extra`, then run once `under` a wrapper."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    if cuda and (reason := device_reason()):  # A device program runs inside `on_device` or not at all.
        pytest.skip(reason)
    source, executable = emit(tmp_path, cpp)
    subprocess.run([*command(cxx, source, executable, kind="exe", cuda=cuda), *extra], check=True, timeout=timeout)
    return subprocess.run([*under, executable], capture_output=True, text=True, timeout=timeout, env=env)


def device_build(tmp_path: Path, cpp: str, entry: str | None = None, ptx=False, timeout=600, cxx="g++") -> Path:
    """`cpp` compiled by the project's own device command line for sm_120, a named architecture, so nothing asks the
    device and nothing runs, with `cxx` as nvcc's host compiler; the object, or with `ptx` the PTX. Skips the test
    when nvcc or `cxx` is absent."""
    if not shutil.which("nvcc") or not shutil.which(cxx):
        pytest.skip(f"needs nvcc and {cxx}")
    source, artifact = emit(tmp_path, cpp, entry)
    target = artifact + (".ptx" if ptx else ".o")
    line = [part for part in command(cxx, source, target, cuda=True, device=parse("sm_120")) if part != "-shared"]
    line.insert(line.index("-o"), "-ptx" if ptx else "-c")
    done = subprocess.run(line, capture_output=True, text=True, timeout=timeout)
    assert done.returncode == 0, done.stderr[-3000:]
    return Path(target)


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

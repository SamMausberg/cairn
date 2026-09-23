"""The hidden check: build one program the way its language is judged, and run it on every hidden case.

C++ and CAIRN are judged by the same sanitizer builds of the C++ each produces: AddressSanitizer with leak detection
and UndefinedBehaviorSanitizer for every task, and ThreadSanitizer as well for a task that must use threads. Rust is
judged by its debug build, whose overflow checks and bounds checks panic; no Rust sanitizer is installed here, so
Rust and CAIRN may not use `unsafe`. A case passes when the program exits 0 and its standard output is exactly the
oracle's. The same rules are stated to every subject in its TASK.md.
"""

from __future__ import annotations

import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tasks import Task, hidden_cases

LANGUAGES = ("cairn", "cpp", "rust")
SOURCE = {"cairn": "src/main.cairn", "cpp": "main.cpp", "rust": "src/main.rs"}
EXTENSION = {"cairn": "cairn", "cpp": "cpp", "rust": "rs"}
CXX = shutil.which("clang++") or "clang++"
SANITIZERS = {"address": ["-fsanitize=address,undefined"], "thread": ["-fsanitize=thread"]}
CXX_FLAGS = ["-std=c++20", "-O1", "-g", "-fno-omit-frame-pointer", "-fno-sanitize-recover=all", "-pthread"]
RUN_ENV = {"ASAN_OPTIONS": "detect_leaks=1", "TSAN_OPTIONS": "halt_on_error=1", "UBSAN_OPTIONS": "print_stacktrace=1"}
CARGO_TOML = """[package]
name = "solution"
version = "0.1.0"
edition = "2021"

[dependencies]

[profile.dev]
overflow-checks = true
debug-assertions = true
"""
CAIRN_TOML = """[project]
name = "solution"
sources = ["src/main.cairn"]

[build]
kind = "exe"
arch = "baseline"
"""
# What a thread task must contain, after comments are removed; the construct, not a count of threads.
THREADED = {
    "cpp": re.compile(r"std::(thread|jthread|async)\b|\bpthread_create\b"),
    "rust": re.compile(r"\bthread::(spawn|scope)\b"),
    "cairn": re.compile(r"\bspawn\b|\bparallel\b"),
}
UNSAFE = re.compile(r"\bunsafe\b")


def default_cairn() -> list[str]:
    """The compiler the subjects use: CAIRN_BENCH_CAIRN, or this checkout's bin/cairn."""
    if command := os.environ.get("CAIRN_BENCH_CAIRN"):
        return command.split()
    return [sys.executable, str(Path(__file__).resolve().parents[2] / "bin" / "cairn")]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


@dataclass
class Verdict:
    passed: bool
    reason: str  # "passed", or what failed first: construct, unsafe, build, output, exit, sanitizer, timeout
    builds: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)  # (build, case index, what happened), at most a few
    cases: int = 0

    def record(self) -> dict:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "cases": self.cases,
            "builds": self.builds,
            "failures": self.failures,
        }


def build(
    language: str, source: str, where: Path, sanitizer: str, cairn: list[str] | None = None
) -> tuple[Path | None, str]:
    """An executable of `source` for one judged build, or None and the log that says why."""
    where.mkdir(parents=True, exist_ok=True)
    if language == "cpp":
        (where / "main.cpp").write_text(source)
        exe = where / "program"
        done = run([CXX, *CXX_FLAGS, *SANITIZERS[sanitizer], "main.cpp", "-o", str(exe)], where, 600)
        return (exe if done.returncode == 0 else None), done.stdout + done.stderr
    if language == "rust":
        (where / "src").mkdir(exist_ok=True)
        (where / "Cargo.toml").write_text(CARGO_TOML)
        (where / "src" / "main.rs").write_text(source)
        done = run(["cargo", "build", "--offline", "--quiet"], where, 900, {"CARGO_TARGET_DIR": str(where / "target")})
        exe = where / "target" / "debug" / "solution"
        return (exe if done.returncode == 0 else None), done.stdout + done.stderr
    # CAIRN: the program must pass `cairn build` as the subject ran it; its generated C++ is then built again with
    # the sanitizers, from the same command line the receipt records, at -O1 instead of -O3.
    (where / "src").mkdir(exist_ok=True)
    (where / "cairn.toml").write_text(CAIRN_TOML)
    (where / "src" / "main.cairn").write_text(source)
    done = run([*(cairn or default_cairn()), "build", ".", "--format", "json"], where, 900)
    try:
        receipt = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None, done.stdout + done.stderr
    if receipt.get("status") != "native-built":
        return None, json.dumps({k: receipt.get(k) for k in ("status", "code", "message", "line", "column", "stderr")})
    exe = Path(receipt["directory"]) / "sanitized"
    line = [a for a in receipt["command"] if a not in ("-O3", "-Werror")]
    out = line.index("-o")
    line[out + 1] = str(exe)
    done = run([*line[:1], *CXX_FLAGS, *SANITIZERS[sanitizer], *line[1:]], where, 900)
    return (exe if done.returncode == 0 else None), done.stdout + done.stderr


def no_core() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(argv: list[str], cwd: Path, timeout: float, env: dict | None = None, stdin: bytes | None = None):
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            input=stdin,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
            preexec_fn=no_core,
            text=stdin is None,
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(argv, -9, e.stdout or ("" if stdin is None else b""), "timeout")


def judge(
    task: Task, language: str, source: str, scratch: Path | None = None, cairn: list[str] | None = None
) -> Verdict:
    """The hidden check of `source` as the answer to `task` in `language`."""
    code = strip_comments(source)
    if task.threads and not THREADED[language].search(code):
        return Verdict(False, "construct")
    if language in ("rust", "cairn") and UNSAFE.search(code):
        return Verdict(False, "unsafe")
    cases = hidden_cases(task)
    expected = [task.oracle(c) for c in cases]
    sanitizers = ["address", "thread"] if task.threads else ["address"]
    if language == "rust":
        sanitizers = ["debug"]
    owned = scratch is None
    root = Path(tempfile.mkdtemp(prefix=f"aibench-{task.name}-{language}-")) if owned else scratch
    verdict = Verdict(True, "passed", cases=len(cases))
    try:
        for sanitizer in sanitizers:
            exe, log = build(language, source, root / sanitizer, sanitizer, cairn)
            verdict.builds[sanitizer] = "built" if exe else log[-3000:]
            if exe is None:
                return Verdict(False, "build", verdict.builds, cases=len(cases))
            for i, (case, want) in enumerate(zip(cases, expected, strict=True)):
                done = run(["setarch", os.uname().machine, "-R", str(exe)], root, 120, RUN_ENV, stdin=case)
                what = classify(done, want)
                if what is not None:
                    verdict.passed = False
                    if verdict.reason == "passed":
                        verdict.reason = what
                    if len(verdict.failures) < 5:
                        verdict.failures.append([sanitizer, i, what, tail(done.stderr)])
        return verdict
    finally:
        if owned:
            shutil.rmtree(root, ignore_errors=True)


def classify(done: subprocess.CompletedProcess, want: bytes) -> str | None:
    err = done.stderr if isinstance(done.stderr, bytes) else (done.stderr or "").encode()
    if done.returncode == -9 and err == b"timeout":
        return "timeout"
    if b"Sanitizer" in err or b"runtime error:" in err:
        return "sanitizer"
    if done.returncode != 0:
        return "exit"
    if done.stdout != want:
        return "output"
    return None


def tail(err) -> str:
    text = err.decode("utf-8", "replace") if isinstance(err, bytes) else (err or "")
    return text[-600:]

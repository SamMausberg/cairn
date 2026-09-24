"""`test name { }` blocks, run: one executable holds every selected test, and each runs in a process of its own.

A test passes only when its process exits with status 0. A failed assert or guard, a signal, another status and a
timeout each fail that test and no other, whatever it printed first. The processes run under the limits `cairn run`
applies, which are protections against runaway programs and not a sandbox.
"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..compiler.syntax import Parser
from ..compiler.tree import Function, fail
from ..projects.build import build
from ..projects.project import Project
from .testing import limited


def label(f: Function) -> str:
    """How a test is named to a person: `sums`, or `store.sums` for a test of module `store`."""
    return f.name.replace("test$", "")


def written(project: Project, chosen: str = "", exact: bool = False) -> list[Function]:
    """The project's own tests, in source order, whose name contains `chosen`, or is `chosen` when `exact`; a
    dependency's tests are its own."""
    parsed = Parser(project.source).parse().functions
    picked = [f for f in parsed if f.test and project.wrote(f.line)]
    return [f for f in picked if (label(f) == chosen if exact else chosen in label(f))]


def reason(done: subprocess.CompletedProcess) -> str:
    """Why a test failed, in one line: the assert that failed when it said so, else how its process ended."""
    said = [line for line in done.stderr.splitlines() if line.startswith("assertion failed ")]
    if said:
        return said[-1]
    if done.returncode < 0 and -done.returncode in signal.valid_signals():
        name = signal.Signals(-done.returncode).name
        return f"stopped by {name}" + (": a guard failed" if name == "SIGABRT" else "")
    return f"exited with status {done.returncode}"


def run_tests(project: Project, *, cxx: str = "clang++", chosen: str = "", exact: bool = False, jobs: int = 0,
              timeout: int = 60, memory_mib: int = 1024, output: Path | None = None, device_target: str | None = None,
              emulate: bool = False) -> dict:  # fmt: skip
    """Build every selected test into one executable under `output` (the project's build/ by default), then run each
    in its own process, `jobs` at a time. With `emulate`, a device program's tests run their device work on host
    threads, judged against `device_target` (projects/emulation.py)."""
    if project.target != "hosted":
        fail("E-TEST", f"Tests run as host processes, and target {project.target} has no host to run them on.")
    tests = written(project, chosen, exact)
    record: dict = {"status": "no-test-blocks", "tests": [], "passed": 0, "failed": 0}
    if not tests:
        return record
    built = build(project, output=output, cxx=cxx, timeout=min(300, max(timeout, 60)),
                  tests=tuple(f.name for f in tests), device_target=device_target, emulate=emulate)  # fmt: skip
    record["build"] = {k: built.get(k) for k in ("status", "artifact", "directory", "exit_code", "stderr", "message")}
    record.update({"emulation": built["emulation"]} if "emulation" in built else {})
    if built["status"] != "native-built":
        return {**record, "status": built["status"]}
    # Unified addressing reserves far more than it uses; an emulated program's device memory is host memory.
    cuda = "cuda" in built["frontend"]["requires"] and "emulation" not in built
    limits = functools.partial(limited, timeout, None if cuda else memory_mib)

    def one(index: int) -> dict:
        started, (file, line) = time.monotonic(), project.site(tests[index].line)
        result: dict = {"name": label(tests[index]), "file": file, "line": line}
        try:
            done = subprocess.run([built["artifact"], str(index)], capture_output=True, text=True, timeout=timeout,
                                  cwd=project.root, stdin=subprocess.DEVNULL, preexec_fn=limits)  # fmt: skip
        except subprocess.TimeoutExpired as late:
            out, err = (
                x.decode(errors="replace") if isinstance(x, bytes) else x or "" for x in (late.stdout, late.stderr)
            )
            done = subprocess.CompletedProcess(late.cmd, -signal.SIGKILL, out, err)
            result["reason"] = f"timed out after {timeout} s"
        passed = done.returncode == 0 and "reason" not in result
        result.update(status="passed" if passed else "failed", exit_code=done.returncode)
        if not passed:
            result.setdefault("reason", reason(done))
        result.update(stdout=done.stdout[:8000], stderr=done.stderr[:8000], elapsed_seconds=time.monotonic() - started)
        return result

    workers = jobs or min(8, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        record["tests"] = list(pool.map(one, range(len(tests))))
    record["failed"] = sum(t["status"] != "passed" for t in record["tests"])
    record["passed"] = len(tests) - record["failed"]
    record.update(status="test-blocks-failed" if record["failed"] else "passed-test-blocks", workers=workers)
    return record

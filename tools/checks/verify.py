#!/usr/bin/env python3
"""Regenerate and test the native artifact. CPU-only, on this host's best CPU profile.

No downloads. Benchmarks are optional and overwrite timing result files.
A failed child command stops immediately; a success log is never fabricated.
The profile that ran is recorded: a result from one host is not another host's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.projects.toolchain import command
from support import best_profile, environment, profile_flags

SANITIZE = ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--gcc", action="store_true")
    ap.add_argument("--sanitize", action="store_true")
    ap.add_argument("--tests", default="tests", help="Pytest selection for the child suite; the default is all of it.")
    a = ap.parse_args()
    os.chdir(ROOT)
    (ROOT / "results/native").mkdir(parents=True, exist_ok=True)
    (ROOT / "results/checks").mkdir(parents=True, exist_ok=True)
    compilers = ["clang++"] + (["g++"] if a.gcc else [])
    arch = best_profile(*compilers)
    record = {"environment": environment(*compilers, arch=arch), "commands": []}

    def run(argv, output=None, env=None):
        cp = subprocess.run(argv, text=True, capture_output=True, env=env)
        record["commands"].append(
            {"command": argv, "exit_code": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}
        )
        if output:
            (ROOT / output).write_text(cp.stdout + (cp.stderr if cp.returncode else ""))
        (ROOT / "results/native/verification_run.json").write_text(json.dumps(record, indent=2) + "\n")
        if cp.returncode:
            raise SystemExit(cp.stderr or cp.stdout or f"Failed: {argv}")
        return cp.stdout

    py = sys.executable
    for stem in ["native", "family", "wire"]:
        run([py, "tools/release/build.py", f"examples/basics/{stem}.cairn", "--out", "results/native", "--arch", arch])
    run(command("clang++", "bench/cpu/family_template.cpp", "results/native/libtemplate.so", arch, "library"))
    # The suite's own smoke test runs this script; the flag stops it re-entering and rewriting results/.
    run([py, "-m", "pytest", a.tests, "-q"], "results/checks/compiler_tests.txt", dict(os.environ, CAIRN_VERIFY="1"))
    run([py, "tests/checks/native_checks.py"], "results/checks/native_tests.json")
    run([py, "tests/checks/collector_wire_checks.py"], "results/checks/collector_wire_tests.json")
    run([py, "tests/checks/template_checks.py"], "results/checks/template_tests.json")
    if a.gcc:
        for stem in ["native", "family"]:
            run(command("g++", f"results/native/{stem}.cpp", f"results/native/lib{stem}_gcc.so", arch, "library"))
        env = dict(os.environ, CAIRN_FAMILY_LIB=str(ROOT / "results/native/libfamily_gcc.so"))
        run(
            [py, "tests/checks/native_checks.py", str(ROOT / "results/native/libnative_gcc.so")],
            "results/checks/gcc_native_tests.json",
            env,
        )
    if a.sanitize:
        # Sanitizers need frame pointers and a light optimizer; every other flag is the shared contract.
        flags = profile_flags("exe", arch, drop=("-O3",), add=SANITIZE)
        run(["clang++", *flags, "tests/native/sanitize.cpp", "-o", "results/native/sanitize"])
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1", UBSAN_OPTIONS="halt_on_error=1")
        run(["results/native/sanitize"], "results/checks/sanitizer_tests.txt", env)
    if a.bench:
        run([py, "bench/cpu/run.py"], "results/checks/benchmark_run.txt")
    run([py, "tools/checks/density.py"], "results/checks/density_run.txt")
    summary = {"status": "all requested checks passed", "commands": len(record["commands"])}
    print(json.dumps({**summary, **record["environment"], "formal_status": "not-verified"}))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the release gates on this machine and record exactly what happened.

Writes evidence/<release>/summary.json. A failed or skipped gate is recorded as such; nothing is
retried, and a gate whose tool is absent is "unavailable", never "passed".
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
GATES = {
    "format": [PYTHON, "-m", "ruff", "format", "--check", "."],
    "lint": [PYTHON, "-m", "ruff", "check", "."],
    "types": [PYTHON, "-m", "mypy", "src/cairn/compiler", "src/cairn/projects/toolchain.py",
              "src/cairn/verify/scalar_semantics.py"],
    "cairn_format": [PYTHON, "bin/cairn", "fmt", "--check", "examples", "src/cairn/std"],
    "tests": [PYTHON, "-m", "pytest", "-q", "tests", "-n", "16", "-rs"],
    "certificates": [PYTHON, "bin/cairn", "certificates"],
    "lean_in_sync": [PYTHON, "tools/checks/export_lean_certificates.py", "--check"],
    "module_equivalence": [PYTHON, "bin/cairn", "verify", "examples/proof_scope/reference.cairn",
                           "examples/proof_scope/candidate.cairn", "--all"],
}  # fmt: skip


def run(command: list[str], timeout: int = 1800) -> dict:
    started = time.monotonic()
    try:
        done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        status = "passed" if done.returncode == 0 else "failed"
        tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
    except FileNotFoundError:
        status, tail, done = "unavailable", [], None
    except subprocess.TimeoutExpired:
        status, tail, done = "timed-out", [], None
    return {"command": [c.replace(PYTHON, "python") for c in command], "status": status,
            "exit_code": getattr(done, "returncode", None), "seconds": round(time.monotonic() - started, 1), "tail": tail}  # fmt: skip


def lines(*patterns: str) -> int:
    """Formatted source lines, blank and comment lines included: the honest upper bound."""
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for pattern in patterns for p in ROOT.glob(pattern))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="v1_0")
    args = parser.parse_args()
    out = ROOT / "evidence" / args.release
    out.mkdir(parents=True, exist_ok=True)
    gates = {name: run(command) for name, command in GATES.items()}
    counted = re.search(r"(\d+) passed(?:, (\d+) skipped)?", " ".join(gates["tests"]["tail"]))
    doctor = json.loads(
        subprocess.run([PYTHON, "bin/cairn", "doctor"], cwd=ROOT, capture_output=True, text=True).stdout
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(
        subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    )
    summary = {
        "schema": "cairn.evidence/1",
        "commit": commit,
        "worktree_dirty": dirty,
        "host": doctor,
        "gates": gates,
        "tests": {"passed": int(counted.group(1)) if counted else None, "skipped": int(counted.group(2) or 0) if counted else None},
        "source_lines": {
            "compiler_core": lines("src/cairn/compiler/syntax.py", "src/cairn/compiler/modules.py", "src/cairn/compiler/expansion.py",
                                   "src/cairn/compiler/checking.py", "src/cairn/compiler/traits.py", "src/cairn/compiler/constants.py", "src/cairn/compiler/effects.py",
                                   "src/cairn/compiler/builtins.py", "src/cairn/compiler/codegen.py",
                                   "src/cairn/compiler/cairnc.py", "src/cairn/projects/toolchain.py", "src/cairn/projects/build.py", "src/cairn/projects/project.py"),
            "compiler_package": lines("src/cairn/*.py"),
            "runtime_headers": lines("src/cairn/runtime/*.hpp"),
            "standard_library_cairn": lines("src/cairn/std/*.cairn"),
            "lean_proofs": lines("proofs/Cairn/*.lean", "proofs/Cairn.lean"),
            "tests": lines("tests/*.py", "tests/native/*"),
        },
        "separately_recorded": ["lean/summary.json", "gpu/benchmark.json", "embedded/summary.json"],
        "claims_not_made": ["whole-compiler proof", "native refinement", "tuned-baseline performance", "AI proficiency"],
    }  # fmt: skip
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({name: gate["status"] for name, gate in gates.items()} | {"tests": summary["tests"]}, indent=2))
    return 0 if all(gate["status"] == "passed" for gate in gates.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

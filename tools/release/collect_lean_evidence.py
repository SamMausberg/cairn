#!/usr/bin/env python3
"""Rebuild proofs/ from scratch and record exactly what the build and the axiom audit said.

Writes evidence/<release>/lean/: lake-build.log, print-axioms.txt, toolchain.txt and summary.json. A build that fails
is recorded as failed, an absent toolchain as unavailable, and nothing is retried. A summary is a record of one run on
one machine, never a claim about the tree at any other commit.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROOFS = ROOT / "proofs"
AXIOM = re.compile(r"'(?P<name>[^']+)' depends on axioms: \[(?P<axioms>[^\]]*)\]")
NO_AXIOM = re.compile(r"'(?P<name>[^']+)' does not depend on any axioms")


def run(command: list[str], env: dict[str, str]) -> tuple[int, str, float]:
    started = time.monotonic()
    done = subprocess.run(command, cwd=PROOFS, capture_output=True, text=True, env=env, timeout=1800)
    return done.returncode, done.stdout + done.stderr, round(time.monotonic() - started, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, help="the evidence directory, such as v0_8_3")
    args = parser.parse_args()
    out = ROOT / "evidence" / args.release / "lean"
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PATH=f"{Path.home() / '.elan/bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    lake = shutil.which("lake", path=env["PATH"])
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout)
    summary: dict = {"schema": "cairn.lean-evidence/1", "commit": commit, "worktree_dirty": dirty}
    if lake is None:
        summary["build"] = "unavailable"
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
        return 1
    shutil.rmtree(PROOFS / ".lake" / "build", ignore_errors=True)
    code, log, seconds = run([lake, "build"], env)
    (out / "lake-build.log").write_text(log)
    audit_code, audit, _ = run([lake, "env", "lean", "Cairn/Audit.lean"], env)
    (out / "print-axioms.txt").write_text(audit)
    versions = "\n".join(run([tool, "--version"], env)[1].strip() for tool in ("lean", "lake", "elan"))
    (out / "toolchain.txt").write_text(versions + "\n" + (PROOFS / "lean-toolchain").read_text())
    axioms = {m["name"]: [a.strip() for a in m["axioms"].split(",") if a.strip()] for m in AXIOM.finditer(audit)}
    axioms |= {m["name"]: [] for m in NO_AXIOM.finditer(audit)}
    summary |= {
        "build": "succeeded" if code == 0 and audit_code == 0 else "failed",
        "wall_seconds": seconds,
        "from_scratch": True,
        "declarations_audited": len(axioms),
        "axioms_used": sorted({a for used in axioms.values() for a in used}),
        "axioms_by_declaration": axioms,
        "ownership_regression": "pass" if "ownership-regression: pass" in log + audit else "absent",
        "sorry_or_native_decide": "sorryAx" in audit or "ofReduceBool" in audit,
        "lean_files": sorted(str(p.relative_to(PROOFS)) for p in PROOFS.glob("Cairn/**/*.lean")),
        "host": platform.platform(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps({k: summary[k] for k in ("build", "wall_seconds", "declarations_audited", "axioms_used")}, indent=2)
    )
    return 0 if summary["build"] == "succeeded" and not summary["sorry_or_native_decide"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

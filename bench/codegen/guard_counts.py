#!/usr/bin/env python3
"""Count the guards the emitter writes, with and without the facts the checker established.

Every example project, each single-file example in examples/basics and each preregistered bench kernel is compiled;
its C++ is emitted as it is, then again with every guard kept (`keep`), which is what the emitter of 0.8.3 wrote.
It also counts the call sites that reach a callee's lean body and the entry checks each of them no longer runs.
Nothing runs, so this measures emitted code only: how many guards a program pays at runtime is a separate
question, and so is what they cost.

    python3 bench/codegen/guard_counts.py [--out results/lowering/guard_counts.json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from guards import GUARDS, corpus, counts

from cairn.compiler.cairnc import Emitter, compile_program
from cairn.projects.project import load_project

ENTRY = re.compile(r"^[^\n]*\bcf_(\w+)\([^\n]*noexcept \{\n(.*?)^  return ci_\1\(", re.S | re.M)


def skipped(text: str) -> dict[str, int]:
    """Call sites from CAIRN code that reach a lean body `ci_`, and the entry checks those sites no longer run each
    time they execute: each site skips its callee's `cr::view` and `cr::disjoint` lines."""
    checks = {name: len(re.findall(GUARDS["entry"], body)) for name, body in ENTRY.findall(text)}
    sites = Counter()
    for line in text.splitlines():
        if "noexcept" in line:  # a prototype or a definition
            continue
        for name in re.findall(r"\bci_(\w+)\(", line):
            sites[name] += 1
    wrappers = Counter(name for name, _ in ENTRY.findall(text))  # each checked entry calls its own body once
    calls = {name: n - wrappers[name] for name, n in sites.items() if n > wrappers[name]}
    return {
        "lean_call_sites": sum(calls.values()),
        "entry_checks_skipped": sum(n * checks.get(f, 0) for f, n in calls.items()),
    }


def sources() -> list[tuple[str, str]]:
    found = []
    for name, path in corpus(ROOT):
        try:
            text = load_project(path.parent).source if path.name == "cairn.toml" else path.read_text(encoding="utf-8")
            compile_program(text)
        except Exception:  # A project that does not build here (a device or freestanding one) is not counted.
            continue
        found.append((name, text))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "results/lowering/guard_counts.json")
    args = ap.parse_args()
    rows, before, after, lean = [], Counter(), Counter(), Counter()
    for name, text in sources():
        p, checker, _ = compile_program(text)
        emitter = Emitter(p, checker)  # Only what verify/elision.py accepts is left out.
        now = emitter.emit()
        then = Emitter(p, checker, keep=True).emit()
        row = {"program": name, "before": counts(then), "after": counts(now), **skipped(now)}
        row["discharged_check_sites"] = dict(sum((Counter(v["accepted"]) for v in emitter.elision.values()), Counter()))
        before.update(row["before"])
        after.update(row["after"])
        lean.update({k: row[k] for k in ("lean_call_sites", "entry_checks_skipped")})
        rows.append(row)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    result = {"commit": commit, "programs": rows, "total_before": dict(before), "total_after": dict(after),
              "total_lean": dict(lean), "measures": "guard calls in emitted C++ text, not guards executed at runtime; "
              "entry_checks_skipped counts per execution of each call site that reaches a lean body"}  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"programs": len(rows), "before": dict(before), "after": dict(after), **lean}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

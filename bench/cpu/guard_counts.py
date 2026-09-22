#!/usr/bin/env python3
"""Count the guards the emitter writes, with and without the facts the checker established.

Every program under examples/, the standard library and the preregistered bench kernels is compiled once;
its C++ is emitted as it is, then again with every guard kept (`keep`), which is what the emitter of 1.3 wrote.
Nothing runs, so this measures emitted code only: how many guards a program pays at runtime is a separate
question, and so is what they cost.

    python3 bench/cpu/guard_counts.py [--out results/lowering/guard_counts.json]
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
from cairn.compiler.cairnc import Emitter, compile_program
from cairn.projects.project import load_project

GUARDS = {
    "bounds": r"\bcr::at\(",
    "overflow": r"\bcr::(?:add|sub|mul)<",
    "conversion": r"\bcr::(?:convert|truncate)<",
    "division": r"\bcr::(?:divide|remainder)<",
    "part": r"\bcr::part\(",
    "entry": r"\bcr::(?:view|disjoint)\(",
}


def counts(text: str) -> dict[str, int]:
    return {kind: len(re.findall(pattern, text)) for kind, pattern in GUARDS.items()}


def sources() -> list[tuple[str, str]]:
    found = []
    paths = [
        *ROOT.glob("examples/**/cairn.toml"),
        *ROOT.glob("examples/basics/*.cairn"),
        *ROOT.glob("bench/suite/kernels/*/kernel.cairn"),
    ]
    for path in sorted(paths):
        try:
            text = load_project(path.parent).source if path.name == "cairn.toml" else path.read_text(encoding="utf-8")
            compile_program(text)
        except Exception:  # A project that does not build here (a device or freestanding one) is not counted.
            continue
        found.append((str((path.parent if path.name == "cairn.toml" else path).relative_to(ROOT)), text))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "results/lowering/guard_counts.json")
    args = ap.parse_args()
    rows, before, after = [], Counter(), Counter()
    for name, text in sources():
        p, checker, _ = compile_program(text)
        emitter = Emitter(p, checker)  # Only what verify/elision.py accepts is left out.
        now = emitter.emit()
        then = Emitter(p, checker, keep=True).emit()
        row = {"program": name, "before": counts(then), "after": counts(now)}
        row["discharged_check_sites"] = dict(sum((Counter(v["accepted"]) for v in emitter.elision.values()), Counter()))
        before.update(row["before"])
        after.update(row["after"])
        rows.append(row)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    result = {"commit": commit, "programs": rows, "total_before": dict(before), "total_after": dict(after),
              "measures": "guard calls in emitted C++ text, not guards executed at runtime"}  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"programs": len(rows), "before": dict(before), "after": dict(after)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

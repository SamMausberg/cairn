#!/usr/bin/env python3
"""Count the guards two compilers write for the same programs, so a change to lowering is measured on fixed input.

    python3 bench/codegen/guard_delta.py SRC CORPUS [--out FILE]

SRC is a `src` directory holding a `cairn` package, and CORPUS a checkout whose example projects, single-file
examples and preregistered bench kernels are compiled with it, as `bench/codegen/guard_counts.py` finds them. Each
program's C++ is emitted as that compiler emits it, and its guard calls are counted by kind. Run it once with an
older compiler and once with a newer one on the same CORPUS; a program either one refuses, or that does not load, is
left out of both totals by the comparison in the run notes, which names it. Nothing runs, so this counts text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))  # holds no cairn package, so SRC still wins
from sources import cairn_sources

GUARDS = {
    "bounds": r"\bcr::at\(",
    "overflow": r"\bcr::(?:add|sub|mul)<",
    "conversion": r"\bcr::(?:convert|truncate)<",
    "division": r"\bcr::(?:divide|remainder)<",
    "part": r"\bcr::part\(",
    "entry": r"\bcr::(?:view|disjoint)\(",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    sys.path.insert(0, str(a.src.resolve()))
    from cairn.compiler.cairnc import compile_source
    from cairn.projects.project import load_project

    root = a.corpus.resolve()
    paths = [*root.glob("examples/**/cairn.toml"), *cairn_sources(root / "examples/basics"),
             *root.glob("bench/suite/kernels/*/kernel.cairn")]  # fmt: skip
    rows = {}
    for path in sorted(paths):
        name = str((path.parent if path.name == "cairn.toml" else path).relative_to(root))
        try:
            text = load_project(path.parent).source if path.name == "cairn.toml" else path.read_text(encoding="utf-8")
            cpp = compile_source(text)[0]
        except Exception as e:  # A device or freestanding project that does not build here, or a refusal.
            rows[name] = {"skipped": type(e).__name__}
            continue
        rows[name] = {kind: len(re.findall(pattern, cpp)) for kind, pattern in GUARDS.items()}
    total = sum((Counter(r) for r in rows.values() if "skipped" not in r), Counter())
    result = {"src": str(a.src), "corpus": str(a.corpus), "programs": rows, "total": dict(total)}
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"total": dict(total), "skipped": sorted(n for n, r in rows.items() if "skipped" in r)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

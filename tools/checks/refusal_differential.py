#!/usr/bin/env python3
"""Whether a check that reports every refusal still reports first the refusal a check that stops meets.

It takes every program `emission_identity.py` takes: each example, one program importing every std module, every
CAIRN program written into a test and every `cairn` block of the docs, including each `cairn rejects E-CODE` one. A
program the checker refuses is checked again with `every`, and its record must be the same record with at most
`further`, `further_omitted` and `not_judged` added. Each further refusal is a whole diagnostic, in source order,
never the first again; no check ends early on a fault; and an accepted program is accepted either way. It prints the
counts as JSON, names every program that breaks one of these, and exits 1 when there is one.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
from cairn.compiler.cairnc import Diagnostic, compile_program
from checks.emission_identity import programs

ADDED = ("further", "further_omitted", "not_judged")
WHERE = ("code", "message", "line", "column")


def order(d: dict) -> tuple:
    return bool(d.get("module")), d.get("module", ""), d["line"], d["column"]


def compare(source: str) -> dict:
    """One program checked both ways: whether it is refused, how many further refusals it has, and what breaks."""
    try:
        compile_program(source, every=True)
        every = None
    except Diagnostic as error:
        every = error
    try:
        compile_program(source)
        alone = None
    except Diagnostic as error:
        alone = error.data
    if every is None or alone is None:
        return {"refused": alone is not None, "broken": [] if (every is None) == (alone is None) else ["verdict"]}
    record, broken = every.data, []
    if {k: v for k, v in record.items() if k not in ADDED} != alone:
        broken.append("first")
    further = record.get("further", [])
    if any(not all(k in d for k in WHERE) for d in further):
        broken.append("shape")
    elif further != sorted(further, key=order) or any(
        [d[k] for k in WHERE] == [alone[k] for k in WHERE] for d in further
    ):
        broken.append("order")
    if len(further) > 20 or ("further_omitted" in record and len(further) != 20):
        broken.append("cap")
    if every.abandoned is not None:
        broken.append(f"fault: {type(every.abandoned).__name__}: {every.abandoned}")
    return {"refused": True, "further": len(further) + record.get("further_omitted", 0),
            "not_judged": record.get("not_judged", 0), "broken": broken}  # fmt: skip


def judged(item: tuple[str, str]) -> tuple[str, dict]:
    name, source = item
    return name, {"refused": False, "broken": []} if source.startswith("unloadable: ") else compare(source)


def census(items: dict[str, str], workers: int = 4) -> dict:
    if workers > 1:
        with ProcessPoolExecutor(workers) as pool:
            taken = dict(pool.map(judged, items.items(), chunksize=8))
    else:
        taken = dict(map(judged, items.items()))
    refused = [r for r in taken.values() if r["refused"]]
    return {
        "programs": len(taken),
        "refused": len(refused),
        "first_identical": sum(1 for r in refused if "first" not in r["broken"]),
        "with_further": sum(1 for r in refused if r["further"]),
        "further": sum(r["further"] for r in refused),
        "with_not_judged": sum(1 for r in refused if r["not_judged"]),
        "broken": {n: r["broken"] for n, r in sorted(taken.items()) if r["broken"]},
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workers", type=int, default=4)
    report = census(programs(), p.parse_args().workers)
    print(json.dumps(report, indent=1))
    return 1 if report["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

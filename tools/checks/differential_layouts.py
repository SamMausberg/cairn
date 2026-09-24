#!/usr/bin/env python3
"""Differential check: `compiler/layout_algebra.py` and `proofs/Cairn/Layout.lean` judge the same generated layouts alike.

A declared spread must give every element of its tile exactly one holder, and a declared storage layout every element
its own offset. `Layout.lean` writes both rules as their definitions and proves what a layout that passes promises;
`layout_algebra.py` counts holders in one pass and finds shared offsets with a table. This harness generates storage
layouts and spreads, among them the ones `spread`, `transpose`, `swizzle` and `inverse` make and the reads `stage` places, asks the real Python functions
(`cover`, `injective`) for their verdicts, renders the same layouts as Lean terms, and requires the verdicts to
match on every input: a coordinate outside the tile, the first element held twice, the first left to nobody, and
the first two elements that share an offset.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler import layout_algebra as L
from cairn.compiler.tree import Diagnostic
from checks.differential_ownership import find_lake, run_lean


def storage(rng: random.Random, rank: int) -> L.Layout:
    dims = []
    for _ in range(rank):
        modes = tuple(
            (rng.randint(1, 5), rng.choice((0, 1, 1, 2, 3, 4, 5, 8, 12))) for _ in range(rng.choice((1, 1, 2)))
        )
        dims.append(modes)
    swizzle = (0, 0, 0)
    if rng.random() < 0.3:
        swizzle = (rng.randint(1, 3), rng.randint(0, 2), rng.randint(0, 3))
    return L.Layout(tuple(dims), swizzle)


def modes(rng: random.Random, rank: int) -> tuple:
    count = rng.choice((1, 2, 2, 3))
    return tuple(
        (rng.randint(1, 5), tuple(rng.choice((0, 0, 1, 1, 2, 3, 4)) for _ in range(rank))) for _ in range(count)
    )


def made(rng: random.Random) -> L.Value:
    """A layout as the language makes one: a spread over a small tile, perhaps transposed, perhaps swizzled."""
    r, c = rng.choice((2, 4, 6, 8)), rng.choice((2, 4, 8))
    tile = rng.choice((L.rows(r, c), L.cols(r, c), L.strided(r, c, c + rng.randint(0, 3), 1)))
    if rng.random() < 0.3:
        tile = L.Layout(tile.dims, (rng.randint(1, 2), rng.randint(0, 1), rng.randint(0, 3)))
    if rng.random() < 0.3:
        return tile
    d = L.spread(tile, rng.randint(1, 4), rng.randint(1, 4), rng.randint(1, 2), rng.randint(1, 2))
    d = L.transposed(d) if rng.random() < 0.3 else d
    if rng.random() < 0.25:  # the owner of each element, as `inverse` makes it where it exists
        try:
            return L.inverse(d, None)
        except Diagnostic:
            return d
    return d


def halo(rng: random.Random) -> L.Spread:
    """What `stage R` asks of a block: its lanes' reads at [i + d], placed in a tile R either side of the block."""
    block, radius = rng.randint(1, 8), rng.randint(0, 3)
    low = rng.randint(-radius - 1, radius)
    high = rng.randint(low, radius + 1)
    tile = L.rows(1, block + 2 * radius)
    return L.Spread(tile, ((block, (0, 1)),), ((high - low + 1, (0, 1)),), False, (0, max(0, low + radius)))


def case(rng: random.Random) -> L.Value:
    choice = rng.random()
    if choice < 0.1:
        return halo(rng)
    if choice < 0.4:
        return made(rng)
    if choice < 0.6:
        return storage(rng, rng.randint(1, 3))
    rank = rng.choice((1, 2, 2))
    tile = storage(rng, rank)
    while tile.size > 64:
        tile = storage(rng, rank)
    d = L.Spread(tile, modes(rng, rank), modes(rng, rank), rng.random() < 0.6)
    while d.count * d.each > 128:
        d = L.Spread(tile, modes(rng, rank), modes(rng, rank), d.wrap)
    return d


def python_row(v: L.Value) -> str:
    """What the real Python functions say."""
    if isinstance(v, L.Layout):
        clash = L.injective(v)
        return "distinct" if clash is None else f"clash {clash[0]} {clash[1]}"
    try:
        found = L.cover(v)
    except Diagnostic:
        return "outside"
    if (e := found.overlap) is not None:
        return f"overlap {e}"
    return "ok" if (e := found.gap) is None else f"gap {e}"


def lean_storage(v: L.Layout) -> str:
    dims = ", ".join("[" + ", ".join(f"({e}, {s})" for e, s in m) + "]" for m in reversed(v.dims))
    return f"(Storage.mk [{dims}] ({v.swizzle[0]}, {v.swizzle[1]}, {v.swizzle[2]}))"


def lean_modes(ms: tuple) -> str:
    return "[" + ", ".join(f"({e}, [{', '.join(map(str, reversed(s)))}])" for e, s in ms) + "]"


def lean_row(v: L.Value) -> str:
    if isinstance(v, L.Layout):
        return f"storageRow {lean_storage(v)}"
    wrap = "true" if v.wrap else "false"
    origin = ", ".join(map(str, reversed(v.origin or (0,) * len(v.tile.shape))))
    parts = f"{lean_storage(v.tile)} {lean_modes(v.participants)} {lean_modes(v.values)} {wrap} [{origin}]"
    return f"spreadRow (Spread.mk {parts})"


def lean_source(rows: list[str], chunk: int = 50) -> str:
    lines = ["import Cairn.Layout", "", "open Cairn.Layout", ""]
    lines += [
        "def storageRow (L : Storage) : String :=",
        '  match L.clash with | none => "distinct" | some (a, b) => s!"clash {a} {b}"',
        "def spreadRow (d : Spread) : String :=",
        '  if d.outside then "outside" else match d.overlap with',
        '    | some e => s!"overlap {e}"',
        '    | none => match d.gap with | some e => s!"gap {e}" | none => "ok"',
    ]
    for start in range(0, len(rows), chunk):
        lines += ["", "#eval show IO Unit from do", f"  for r in [{', '.join(rows[start : start + chunk])}] do"]
        lines.append('    IO.println ("row " ++ r)')
    return "\n".join(lines) + "\n"


def compare(count: int, seed: int, lake: str, target: Path, timeout: int) -> dict:
    rng = random.Random(seed)
    cases = [case(rng) for _ in range(count)]
    printed = run_lean(lean_source([lean_row(v) for v in cases]), lake, target, timeout)
    lean = [line[4:].strip() for line in printed.splitlines() if line.startswith("row ")]
    if len(lean) != count:
        raise RuntimeError(f"the Lean run printed {len(lean)} rows for {count} inputs")
    disagreements, verdicts = [], {}
    for v, theirs in zip(cases, lean, strict=True):
        ours = python_row(v)
        kind = ours.split()[0]
        verdicts[kind] = verdicts.get(kind, 0) + 1
        if ours != theirs:
            disagreements.append({"python": ours, "lean": theirs, "input": repr(v), "lean_input": lean_row(v)})
    return {
        "status": "disagreed" if disagreements else "agreed",
        "inputs": count,
        "seed": seed,
        "verdicts": dict(sorted(verdicts.items())),
        "disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=int(os.environ.get("CAIRN_DIFFERENTIAL_N", "300")))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    options = parser.parse_args()
    lake = find_lake()
    if lake is None:
        print(json.dumps({"status": "unavailable", "reason": "no lake on PATH and no ~/.elan/bin/lake"}, indent=2))
        return 3
    with tempfile.TemporaryDirectory(prefix="cairn-layouts-") as scratch:
        report = compare(options.count, options.seed, lake, Path(scratch) / "Layouts.lean", options.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "agreed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

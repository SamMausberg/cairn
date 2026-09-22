#!/usr/bin/env python3
"""Differential check: `compiler/facts.py` and `proofs/Cairn/Facts.lean` decide the same generated inputs alike.

Lowering drops a guard when `facts.py` shows it cannot fail. `Facts.lean` transliterates the search, the bounds and the
five decisions lowering acts on, and proves each sound. This harness generates fact sets and usize expressions, asks
the real Python functions (`index`, `arithmetic` for `+` and `-`, `shift`, `conversion` to `u32`, `inside` for a part)
through a checker that holds only the facts and the bindings, renders the same inputs as Lean terms, and requires the
six answers to match on every input. It compares the rule on its inputs, not on whole programs: which facts are in
scope where is checked per site by `verify/elision.py` and tested in `tests/soundness/test_established.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler import facts as F
from cairn.compiler.scope import Binding
from cairn.compiler.tree import USIZE, Expr, Type
from checks.differential_ownership import PROOFS, find_lake, lean_environment

ATOMS = 4  # x0..x3 are immutable usize values; m0 is one that can change.
STRIDES = (2, 4, 8, 256)
CONSTANTS = (0, 1, 2, 3, 7, 8, 63, 64, 255, 256, 4096, 2**32 - 1, 2**32, F.MAX - 1, F.MAX)
DECISIONS = ("index", "add", "sub", "shift", "u32", "part")


def atom_name(rng: random.Random) -> str:
    choice = rng.random()
    if choice < 0.15:
        return F.ZERO
    base = "x" + str(rng.randrange(ATOMS))
    return base + "*" + str(rng.choice(STRIDES)) if choice < 0.3 else base


def expression(rng: random.Random, depth: int) -> tuple:
    """A usize expression as nested tuples: ("lit", k), ("atom", i), ("var", 0), (op, left, right)."""
    choice = rng.random()
    if depth == 0 or choice < 0.35:
        leaf = rng.random()
        return (
            ("lit", rng.choice(CONSTANTS[:11]))
            if leaf < 0.3
            else ("var", 0)
            if leaf < 0.4
            else ("atom", rng.randrange(ATOMS))
        )
    if choice < 0.45:  # A product atom: the one shape `exact` gives a stride.
        pair = [("atom", rng.randrange(ATOMS)), ("lit", rng.choice(STRIDES))]
        return ("mul", *(pair if rng.random() < 0.5 else pair[::-1]))
    op = rng.choice(("add", "add", "sub", "sub", "div", "mod", "band", "min"))
    return (op, expression(rng, depth - 1), expression(rng, depth - 1))


def case(rng: random.Random) -> dict:
    facts = [(atom_name(rng), atom_name(rng), rng.choice((-1, 0, 0, 1, 2, 5, 63, 255, 4096, F.MAX)) * rng.choice((1, 1, -1)))
             for _ in range(rng.randint(0, 6))]  # fmt: skip
    facts = [f for f in facts if f[0] != f[1]]  # `learn` records nothing between an atom and itself
    extent = (F.ZERO, rng.choice(CONSTANTS[:11])) if rng.random() < 0.3 else ("x" + str(rng.randrange(ATOMS)), 0)
    return {"facts": facts, "x": expression(rng, rng.choice((2, 3))), "y": expression(rng, 2), "extent": extent}


SYMBOLS = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "band": "&"}


def python_expr(e: tuple) -> Expr:
    if e[0] == "lit":
        return Expr("int", str(e[1]), ty=USIZE)
    if e[0] in {"atom", "var"}:
        return Expr("name", ("x" if e[0] == "atom" else "m") + str(e[1]), ty=USIZE)
    tag, val = ("call", "min") if e[0] == "min" else ("binary", SYMBOLS[e[0]])
    return Expr(tag, val, args=[python_expr(e[1]), python_expr(e[2])], ty=USIZE)


def python_row(c: dict) -> str:
    """The five decisions of the real `facts.py`, through a checker that holds only the facts and the bindings."""
    env = {"x" + str(i): Binding(USIZE) for i in range(ATOMS)} | {"m0": Binding(USIZE, mutable=True)}
    extent = str(c["extent"][1]) if c["extent"][0] == F.ZERO else c["extent"][0]
    env["xs"] = Binding(Type("u64", mode="ro", extent=extent))
    checker = SimpleNamespace(facts=list(c["facts"]), env=env, declared_extent=lambda e: "")
    x, y = python_expr(c["x"]), python_expr(c["y"])
    answers = (
        F.index(checker, Expr("index", args=[Expr("name", "xs", ty=env["xs"].ty), x])),
        F.arithmetic(checker, Expr("binary", "+", args=[x, y], ty=USIZE)),
        F.arithmetic(checker, Expr("binary", "-", args=[x, y], ty=USIZE)),
        F.shift(checker, Expr("binary", "<<", args=[Expr("int", "1", ty=Type("u64")), x], ty=Type("u64"))),
        F.conversion(checker, Expr("call", "u32", args=[x], ty=Type("u32"))),
        F.inside(checker, x, y, (c["extent"][0], c["extent"][1])),
    )
    return "".join("1" if a else "0" for a in answers)


def lean_atom(name: str) -> str:
    if name == F.ZERO:
        return "Atom.zero"
    base, _, stride = name.partition("*")
    return f"(Atom.prod {base[1:]} {stride})" if stride else f"(Atom.plain {base[1:]})"


def lean_expr(e: tuple) -> str:
    if e[0] in {"lit", "atom", "var"}:
        return f"(E.{e[0]} {e[1]})"
    return f"(E.{e[0]} {lean_expr(e[1])} {lean_expr(e[2])})"


def lean_row(c: dict) -> str:
    facts = "[" + ", ".join(f"({lean_atom(a)}, {lean_atom(b)}, ({k} : Int))" for a, b, k in c["facts"]) + "]"
    extent = f"({lean_atom(c['extent'][0])}, ({c['extent'][1]} : Int))"
    return f"row {facts} {lean_expr(c['x'])} {lean_expr(c['y'])} {extent}"


def lean_source(rows: list[str], chunk: int = 100) -> str:
    lines = ["import Cairn.Facts", "", "open Cairn.Facts", ""]
    lines.append('def bit (b : Bool) : String := if b then "1" else "0"')
    lines.append("def row (fs : List Fact) (x y : E) (ext : Term) : String :=")
    lines.append("  bit (index fs x ext) ++ bit (addOk fs x y) ++ bit (subOk fs x y) ++ bit (atMostConst fs 63 x)")
    lines.append("    ++ bit (atMostConst fs 4294967295 x) ++ bit (partOk fs x y ext)")
    for start in range(0, len(rows), chunk):
        lines += [
            "",
            "#eval show IO Unit from do",
            f"  for r in [{', '.join(rows[start : start + chunk])}] do",
            "    IO.println r",
        ]
    return "\n".join(lines) + "\n"


def compare(count: int, seed: int, lake: str, target: Path, timeout: int) -> dict:
    rng = random.Random(seed)
    cases = [case(rng) for _ in range(count)]
    target.write_text(lean_source([lean_row(c) for c in cases]), encoding="utf-8")
    command = [lake, "env", "lean", str(target)]
    done = subprocess.run(command, cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout)
    if done.returncode != 0:  # A cold `.lake`: build once and try again.
        subprocess.run(
            [lake, "build"], cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout
        )
        done = subprocess.run(
            command, cwd=PROOFS, capture_output=True, text=True, env=lean_environment(), timeout=timeout
        )
    if done.returncode != 0:
        raise RuntimeError("lake env lean failed:\n" + done.stdout[-4000:] + done.stderr[-4000:])
    lean = [line for line in done.stdout.split() if len(line) == len(DECISIONS) and set(line) <= {"0", "1"}]
    if len(lean) != count:
        raise RuntimeError(f"the Lean run printed {len(lean)} rows for {count} inputs")
    disagreements, held = [], dict.fromkeys(DECISIONS, 0)
    for c, theirs in zip(cases, lean, strict=True):
        ours = python_row(c)
        for name, bit in zip(DECISIONS, ours, strict=True):
            held[name] += bit == "1"
        if ours != theirs:
            disagreements.append({"python": ours, "lean": theirs, "input": repr(c), "lean_input": lean_row(c)})
    return {
        "status": "disagreed" if disagreements else "agreed",
        "inputs": count,
        "seed": seed,
        "discharged": held,
        "disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=int(os.environ.get("CAIRN_DIFFERENTIAL_N", "200")))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800)
    options = parser.parse_args()
    lake = find_lake()
    if lake is None:
        print(json.dumps({"status": "unavailable", "reason": "no lake on PATH and no ~/.elan/bin/lake"}, indent=2))
        return 3
    with tempfile.TemporaryDirectory(prefix="cairn-facts-") as scratch:
        report = compare(options.count, options.seed, lake, Path(scratch) / "Facts.lean", options.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "agreed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

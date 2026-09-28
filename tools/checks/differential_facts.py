#!/usr/bin/env python3
"""Differential check: `compiler/check/facts.py` and `proofs/Cairn/Facts.lean` decide the same generated inputs alike.

Lowering drops a guard when `facts.py` shows it cannot fail. `Facts.lean` transliterates the search, the bounds, the
six decisions lowering acts on and the facts `let q = a / C` adds, and proves each sound. This harness generates fact
sets and usize expressions, asks the real Python functions (`index`, `arithmetic` for `+` and `-`, `shift`,
`conversion` to `u32`, `inside` for a part, `product` for `x * C`, and `quotient`) through a checker that holds only
the facts and the bindings, renders the same inputs as Lean terms, and requires every answer to match on every input:
eight decisions, the last an index `x5 * C` with `x5 < x4` after `let x4 = a / C`, and the facts `quotient` adds, fact
by fact. It compares the rule on its inputs, not on whole programs: which facts are in scope where is checked per site
by `verify/elision.py` and tested in `tests/soundness/test_established.py`.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler.check import facts as F
from cairn.compiler.check.scope import Binding
from cairn.compiler.syntax.tree import USIZE, Expr, Type
from support import lean_differential, run_lean

ATOMS = 4  # x0..x3 are immutable usize values; m0 is one that can change.
STRIDES = (2, 4, 8, 256)
CONSTANTS = (0, 1, 2, 3, 7, 8, 63, 64, 255, 256, 4096, 2**32 - 1, 2**32, F.MAX - 1, F.MAX)
DECISIONS = ("index", "add", "sub", "shift", "u32", "part", "mul", "div")
DIVISORS = (0, 1, 2, 3, 4, 8, 256, 1024)


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
    return {"facts": facts, "x": expression(rng, rng.choice((2, 3))), "y": expression(rng, 2), "extent": extent,
            "product": factors(rng), "dividend": expression(rng, 2), "divisor": divisor(rng)}  # fmt: skip


def factors(rng: random.Random) -> tuple[tuple, tuple]:
    """The two sides of a `*`: mostly an atom and a constant in either order, sometimes two constants or anything."""
    choice = rng.random()
    if choice < 0.2:
        return expression(rng, 1), expression(rng, 1)
    if choice < 0.3:
        return ("lit", rng.choice(STRIDES)), ("lit", rng.choice(STRIDES))
    pair = (("atom", rng.randrange(ATOMS)), ("lit", rng.choice((*STRIDES, 0, 1, 3))))
    return pair if rng.random() < 0.5 else pair[::-1]


def divisor(rng: random.Random) -> tuple:
    return ("lit", rng.choice(DIVISORS)) if rng.random() < 0.85 else expression(rng, 1)


SYMBOLS = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "band": "&"}


def python_expr(e: tuple) -> Expr:
    if e[0] == "lit":
        return Expr("int", str(e[1]), ty=USIZE)
    if e[0] in {"atom", "var"}:
        return Expr("name", ("x" if e[0] == "atom" else "m") + str(e[1]), ty=USIZE)
    tag, val = ("call", "min") if e[0] == "min" else ("binary", SYMBOLS[e[0]])
    return Expr(tag, val, args=[python_expr(e[1]), python_expr(e[2])], ty=USIZE)


def python_row(c: dict) -> str:
    """The decisions and the quotient's facts of the real `facts.py`, through a checker that holds only the facts and
    the bindings: x0..x3, x4 (the quotient) and x5 (a name below it) are immutable, and m0 can change."""
    env = {"x" + str(i): Binding(USIZE) for i in range(ATOMS + 2)} | {"m0": Binding(USIZE, mutable=True)}
    extent = str(c["extent"][1]) if c["extent"][0] == F.ZERO else c["extent"][0]
    env["xs"] = Binding(Type("u64", mode="ro", extent=extent))
    checker = SimpleNamespace(facts=list(c["facts"]), env=env, declared_extent=lambda e: "")
    x, y, xs = python_expr(c["x"]), python_expr(c["y"]), Expr("name", "xs", ty=env["xs"].ty)
    d = python_expr(c["divisor"])
    added = F.quotient(checker, "x4", Expr("binary", "/", args=[python_expr(c["dividend"]), d], ty=USIZE))
    below = SimpleNamespace(**{**vars(checker), "facts": [*c["facts"], *added, ("x5", "x4", -1)]})
    scaled = Expr("binary", "*", args=[Expr("name", "x5", ty=USIZE), d], ty=USIZE)
    answers = (
        F.index(checker, Expr("index", args=[xs, x])),
        F.arithmetic(checker, Expr("binary", "+", args=[x, y], ty=USIZE)),
        F.arithmetic(checker, Expr("binary", "-", args=[x, y], ty=USIZE)),
        F.shift(checker, Expr("binary", "<<", args=[Expr("int", "1", ty=Type("u64")), x], ty=Type("u64"))),
        F.conversion(checker, Expr("call", "u32", args=[x], ty=Type("u32"))),
        F.inside(checker, x, y, (c["extent"][0], c["extent"][1])),
        F.product(checker, Expr("binary", "*", args=[python_expr(e) for e in c["product"]], ty=USIZE)),
        F.index(below, Expr("index", args=[xs, scaled])),
    )
    listed = ";".join(f"{a or '0'},{b or '0'},{k}" for a, b, k in added)
    return "row:" + "".join("1" if a else "0" for a in answers) + "|" + listed


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
    product = " ".join(lean_expr(e) for e in c["product"])
    quotient = f"{lean_expr(c['dividend'])} {lean_expr(c['divisor'])}"
    return f"row {facts} {lean_expr(c['x'])} {lean_expr(c['y'])} {extent} {product} {quotient}"


def lean_source(rows: list[str], chunk: int = 100) -> str:
    lines = ["import Cairn.Facts", "", "open Cairn.Facts", ""]
    lines.append('def bit (b : Bool) : String := if b then "1" else "0"')
    lines.append('def atom : Atom → String | .zero => "0" | .plain n => s!"x{n}" | .prod n S => s!"x{n}*{S}"')
    lines.append("def row (fs : List Fact) (x y : E) (ext : Term) (p q a d : E) : String :=")
    lines.append("  let added := divFacts fs 4 a d")
    lines.append('  "row:" ++ bit (index fs x ext) ++ bit (addOk fs x y) ++ bit (subOk fs x y)')
    lines.append("    ++ bit (atMostConst fs 63 x) ++ bit (atMostConst fs 4294967295 x) ++ bit (partOk fs x y ext)")
    lines.append(
        "    ++ bit (mulOk fs p q) ++ bit (index (fs ++ added ++ [(.plain 5, .plain 4, -1)]) (.mul (.atom 5) d) ext)"
    )
    lines.append('    ++ "|" ++ ";".intercalate (added.map fun f => s!"{atom f.1},{atom f.2.1},{f.2.2}")')
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
    printed = run_lean(lean_source([lean_row(c) for c in cases]), lake, target, timeout)
    lean = [line for line in printed.split() if line.startswith("row:")]
    if len(lean) != count:
        raise RuntimeError(f"the Lean run printed {len(lean)} rows for {count} inputs")
    disagreements, held = [], dict.fromkeys([*DECISIONS, "quotient_facts"], 0)
    for c, theirs in zip(cases, lean, strict=True):
        ours = python_row(c)
        bits, listed = ours[len("row:") :].split("|")
        for name, bit in zip(DECISIONS, bits, strict=True):
            held[name] += bit == "1"
        held["quotient_facts"] += len(listed.split(";")) if listed else 0
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
    return lean_differential(compare, "Facts", 200, __doc__)


if __name__ == "__main__":
    raise SystemExit(main())

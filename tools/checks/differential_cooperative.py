#!/usr/bin/env python3
"""Differential check: `compiler/phases.py` and `proofs/Cairn/Cooperative.lean` decide the same generated regions alike.

`Cooperative.lean` proves that a phase its rule accepts has no clashing steps in any interleaving and one result
whatever the threads' order, and its `program` runs a cut-down source (element reads and writes at indexes built from
the thread's number, loop counters and literals, `if` on the thread's number, loops of a known count, barriers) for
every thread and applies that rule to each phase. This harness generates such regions, renders each one as a CAIRN
function with one block and as a Lean term, and requires the real checker to accept exactly the regions `program`
accepts. The checker's refusals that count are the phase rule's three codes and a barrier under a condition;
anything else it says is a failure of the harness. A small run is part of `make test`; `CAIRN_DIFFERENTIAL_N` asks
for a large one.
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
from cairn.compiler.cairnc import Diagnostic, compile_source
from checks.differential_ownership import find_lake, run_lean

REFUSED = {"E-COOP-CONFLICT", "E-COOP-UNORDERED", "E-COOP-REUSE", "E-COOP-BARRIER"}
OPS = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "xor": "^"}


def index(rng: random.Random, depth: int, loops: int, size: int, top: bool = True) -> tuple:
    """A usize index as nested tuples: ("tid",), ("lit", k), ("var", i) or (op, a, b)."""
    if top and rng.random() < 0.6:  # most indexes stay inside the array, as a program's usually do
        return ("mod", index(rng, depth, loops, size, False), ("lit", size))
    choice = rng.random()
    if depth == 0 or choice < 0.35:
        leaf = rng.random()
        if loops and leaf < 0.25:
            return ("var", rng.randrange(loops))
        return ("tid",) if leaf < 0.7 else ("lit", rng.choice((0, 1, 2, 3, 5, 7, 16, 31, 32, 63)))
    op = rng.choice(("add", "add", "sub", "mul", "div", "mod", "xor", "xor"))
    right = ("lit", rng.choice((1, 2, 3, 4, 8, 16, 32))) if op in {"mul", "div", "mod"} else index(
        rng, depth - 1, loops, size, False)  # fmt: skip
    return (op, index(rng, depth - 1, loops, size, False), right)


def condition(rng: random.Random, loops: int) -> tuple:
    """A condition that depends on the thread's number: ("lt", a, b), ("eq", a, b), ("and", c, d), ("not", c)."""
    choice = rng.random()
    if choice < 0.15:
        return ("not", condition(rng, loops))
    if choice < 0.25:
        return ("and", condition(rng, loops), condition(rng, loops))
    if choice < 0.6:
        return ("lt", ("tid",), ("lit", rng.choice((1, 4, 8, 16, 31, 32, 48, 63))))
    if choice < 0.8:
        return ("eq", ("mod", ("tid",), ("lit", rng.choice((2, 3, 4)))), ("lit", rng.choice((0, 1))))
    # both sides hold the thread's number, so neither is a literal alone, which would be a u64 beside a usize
    return ("lt", ("add", ("tid",), index(rng, 1, loops, 64, False)), ("add", ("tid",), ("lit", rng.choice((0, 1, 2)))))


def statements(rng: random.Random, sizes: list[int], depth: int, loops: int, uniform: bool, budget: list[int]) -> list:
    out: list = []
    for _ in range(rng.randint(1, 4)):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        choice = rng.random()
        arr = rng.randrange(len(sizes))
        if choice < 0.3:
            out.append(("read", arr, index(rng, 2, loops, sizes[arr])))
        elif choice < 0.6:
            out.append(("write", arr, index(rng, 2, loops, sizes[arr])))
        elif choice < 0.75 and uniform:
            out.append(("barrier",))
        elif choice < 0.87 and depth:
            out.append(("when", condition(rng, loops), statements(rng, sizes, depth - 1, loops, False, budget)))
        elif depth:
            body = statements(rng, sizes, depth - 1, loops + 1, uniform, budget)
            out.append(("loop", rng.randint(1, 3), body))
    return out


def case(rng: random.Random) -> dict:
    sizes = [rng.choice((32, 64, 128)) for _ in range(rng.randint(1, 2))]
    body = statements(rng, sizes, 2, 0, True, [9])
    if rng.random() < 0.1:  # now and then a barrier some threads skip, which both must refuse
        body.append(("when", ("lt", ("tid",), ("lit", 16)), [("barrier",)]))
    return {"threads": rng.choice((32, 64)), "sizes": sizes, "body": body}


def cairn_expr(e: tuple, loops: int) -> str:
    if e[0] == "tid":
        return "t"
    if e[0] == "lit":
        return str(e[1])
    if e[0] == "var":
        return f"k{loops - 1 - e[1]}"
    return f"({cairn_expr(e[1], loops)} {OPS[e[0]]} {cairn_expr(e[2], loops)})"


def cairn_cond(c: tuple, loops: int) -> str:
    if c[0] == "not":
        return f"!({cairn_cond(c[1], loops)})"
    if c[0] == "and":
        return f"({cairn_cond(c[1], loops)} && {cairn_cond(c[2], loops)})"
    return f"({cairn_expr(c[1], loops)} {'<' if c[0] == 'lt' else '=='} {cairn_expr(c[2], loops)})"


def cairn_body(ss: list, loops: int, pad: str) -> list[str]:
    lines = []
    for s in ss:
        if s[0] == "read":
            lines.append(f"{pad}let _ = a{s[1]}[{cairn_expr(s[2], loops)}];")
        elif s[0] == "write":
            lines.append(f"{pad}a{s[1]}[{cairn_expr(s[2], loops)}] = 1;")
        elif s[0] == "barrier":
            lines.append(f"{pad}barrier;")
        elif s[0] == "when":
            lines += [f"{pad}if {cairn_cond(s[1], loops)} {{", *cairn_body(s[2], loops, pad + "  "), f"{pad}}}"]
        else:
            lines += [f"{pad}for k{loops} in 0..{s[1]} {{", *cairn_body(s[2], loops + 1, pad + "  "), f"{pad}}}"]
    return lines


def cairn_source(c: dict) -> str:
    arrays = [f"    shared a{k}:u64[{n}] = zeroed;" for k, n in enumerate(c["sizes"])]
    return "\n".join(["fn region() {", f"  blocks b in 1 threads t in {c['threads']} {{", *arrays,
                      *cairn_body(c["body"], 0, "    "), "  }", "}", ""])  # fmt: skip


def lean_expr(e: tuple) -> str:
    if e[0] == "tid":
        return "E.tid"
    if e[0] in {"lit", "var"}:
        return f"(E.{e[0]} {e[1]})"
    return f"(E.{e[0]} {lean_expr(e[1])} {lean_expr(e[2])})"


def lean_cond(c: tuple) -> str:
    if c[0] == "not":
        return f"(C.not {lean_cond(c[1])})"
    if c[0] == "and":
        return f"(C.and {lean_cond(c[1])} {lean_cond(c[2])})"
    return f"(C.{c[0]} {lean_expr(c[1])} {lean_expr(c[2])})"


def lean_body(ss: list) -> str:
    parts = []
    for s in ss:
        if s[0] in {"read", "write"}:
            parts.append(f"S.{s[0]} {s[1]} {lean_expr(s[2])}")
        elif s[0] == "barrier":
            parts.append("S.barrier")
        elif s[0] == "when":
            parts.append(f"S.when {lean_cond(s[1])} {lean_body(s[2])}")
        else:
            parts.append(f"S.loop {s[1]} {lean_body(s[2])}")
    return "[" + ", ".join(f"({p})" for p in parts) + "]"


def lean_row(c: dict) -> str:
    return f"program {c['threads']} [{', '.join(map(str, c['sizes']))}] {lean_body(c['body'])}"


def python_verdict(c: dict) -> bool:
    """Whether the real checker accepts the region; a refusal outside the phase rule's codes is the harness's fault."""
    try:
        compile_source(cairn_source(c))
        return True
    except Diagnostic as error:
        if error.data["code"] not in REFUSED:
            raise RuntimeError(f"{error.data['code']}: {error.data['message']}\n{cairn_source(c)}") from error
        return False


def lean_source(rows: list[str], chunk: int = 50) -> str:
    lines = ["import Cairn.Cooperative", "", "open Cairn.Cooperative", ""]
    for start in range(0, len(rows), chunk):
        lines += ["", "#eval show IO Unit from do", f"  for r in [{', '.join(rows[start : start + chunk])}] do",
                  '    IO.println (if r then "accept" else "reject")']  # fmt: skip
    return "\n".join(lines) + "\n"


def compare(count: int, seed: int, lake: str, target: Path, timeout: int) -> dict:
    rng = random.Random(seed)
    cases = [case(rng) for _ in range(count)]
    printed = run_lean(lean_source([lean_row(c) for c in cases]), lake, target, timeout)
    lean = [line == "accept" for line in printed.split() if line in {"accept", "reject"}]
    if len(lean) != count:
        raise RuntimeError(f"the Lean run printed {len(lean)} verdicts for {count} regions")
    disagreements, accepted = [], 0
    for c, theirs in zip(cases, lean, strict=True):
        ours = python_verdict(c)
        accepted += ours
        if ours != theirs:
            disagreements.append({"python": ours, "lean": theirs, "cairn": cairn_source(c), "lean_input": lean_row(c)})
    return {
        "status": "disagreed" if disagreements else "agreed",
        "inputs": count,
        "seed": seed,
        "accepted": accepted,
        "disagreements": disagreements[:10],
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
    with tempfile.TemporaryDirectory(prefix="cairn-cooperative-") as scratch:
        report = compare(options.count, options.seed, lake, Path(scratch) / "Cooperative.lean", options.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "agreed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

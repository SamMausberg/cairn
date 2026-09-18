#!/usr/bin/env python3
"""A small fresh-model pilot: can a model that has only the rule cards write correct CAIRN?

prepare DIR   one sandbox per task outside the repository: CARDS.md, TASK.md, check.py
check         (run by the subject inside a sandbox) compiler diagnostics and required constructs; no tests
score DIR     hidden finite tests for every solution.cairn, written to DIR/results.json

The protocol, the tasks and the metrics are fixed in evidence/v1_1/ai_pilot/PREREGISTRATION.md before
any subject runs. This measures proficiency with CAIRN, not an advantage over any other language.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cairn.cairnc import Diagnostic, Parser, compile_source  # noqa: E402
from cairn.syntax import lex  # noqa: E402
from cairn.teaching import CARDS  # noqa: E402
from cairn.testing import evaluate  # noqa: E402

MASK = 2**64 - 1
CHECKS = 6


def machine(code: list[int]) -> int:
    stack: list[int] = []
    i = 0
    while i < len(code):
        op = code[i]
        if op == 0 and i + 1 < len(code) and len(stack) < 64:
            stack.append(code[i + 1])
            i += 1
        elif op in (1, 2) and len(stack) >= 2:
            b, a = stack.pop(), stack.pop()
            stack.append(a + b if op == 1 else a * b)
        elif op == 3 and stack and len(stack) < 64:
            stack.append(stack[-1])
        else:
            return -1
        i += 1
    return stack[-1] if stack else -1


def programs(r: random.Random) -> list[int]:
    good = [0, r.randrange(9), 0, r.randrange(9), r.choice([1, 2]), 3, 1] * r.randrange(1, 4)
    return good if r.random() < 0.6 else good[: r.randrange(len(good))] + [r.choice([1, 2, 3, 7, 0])]


def views(r: random.Random, bits: int, most: int = 12) -> list[int]:
    return [r.randrange(2**bits) for _ in range(r.randrange(most))]


# name -> (statement, tokens the solution must contain, case generator)
TASKS = {
    "mean": (
        "fn mean(n:usize, xs:ro<u32>[n]) -> u32\nThe floor of the arithmetic mean of xs, and 0 when n is 0. "
        "The sum can exceed u32; nothing may trap for any input.",
        [],
        lambda r: (lambda xs: {"args": {"n": len(xs), "xs": xs}, "return": sum(xs) // len(xs) if xs else 0})(
            views(r, 32)
        ),
    ),
    "machine": (
        "fn run(n:usize, code:ro<u8>[n]) -> i64\nA stack machine over bytes: `0 v` pushes the next byte v, `1` adds "
        "the top two values, `2` multiplies them, `3` duplicates the top. Decode every instruction into "
        "`enum Op { Push(u8); Add; Mul; Dup; Bad; }` with a function of your own and execute it with `match` on a "
        "stack of 64 i64 values. Return the top of the stack at the end. Return -1 for any malformed program: an "
        "unknown opcode, a push with no operand, too few operands, more than 64 values, or an empty stack at the end.",
        ["enum", "match"],
        lambda r: (lambda c: {"args": {"n": len(c), "code": c}, "return": machine(c)})(programs(r)),
    ),
    "dedupe": (
        "fn dedupe(n:usize, xs:ro<u64>[n], out:rw<u64>[n]) -> usize\nxs is sorted ascending. Write its distinct "
        "values, in order, to the front of out and return how many there are. Elements of out past that count "
        "must keep the values they had. Use the `compact` form.",
        ["compact"],
        lambda r: (
            lambda xs, old: {
                "args": {"n": len(xs), "xs": xs, "out": old},
                "return": len(set(xs)),
                "after": {"out": sorted(set(xs)) + old[len(set(xs)) :]},
            }
        )(
            *(lambda xs: (xs, [r.randrange(100, 200) for _ in xs]))(
                sorted(r.randrange(6) for _ in range(r.randrange(10)))
            )
        ),
    ),
    "score": (
        "fn best(a:u64, b:u64, w:u64, h:u64) -> u64\nDeclare `trait Score { fn score(self:ro<Self>) -> u64; }`, a "
        "record Pair { a; b } whose score is a + 2*b, a record Rect { w; h } whose score is w*h, and a generic "
        "function with two type parameters, both bounded by Score, that returns the larger of two scores. `best` "
        "returns that function applied to Pair(a, b) and Rect(w, h). Inputs are below 2^20.",
        ["trait", "impl"],
        lambda r: (lambda a, b, w, h: {"args": {"a": a, "b": b, "w": w, "h": h}, "return": max(a + 2 * b, w * h)})(
            *(r.randrange(2**20) for _ in range(4))
        ),
    ),
    "rotate": (
        "fn rotate(n:usize, xs:rw<u64>[n], k:usize)\nRotate xs left by k positions in place (k may exceed n, and n "
        "may be 0). Use a heap scratch `Buf[u64]`.",
        ["Buf"],
        lambda r: (
            lambda xs, k: {
                "args": {"n": len(xs), "xs": xs, "k": k},
                "after": {"xs": xs[k % len(xs) :] + xs[: k % len(xs)] if xs else []},
            }
        )(views(r, 64), r.randrange(30)),
    ),
    "poly": (
        "fn poly(n:usize, xs:rw<i64>[n], a:i64, b:i64)\nReplace every x by a*x + b. Write a helper "
        "`fn map_in_place(n:usize, xs:rw<i64>[n], f:ro<fn(i64) -> i64>)` and call it with a closure that captures a "
        "and b. Inputs are small enough that nothing overflows.",
        ["|", "fn"],
        lambda r: (
            lambda xs, a, b: {
                "args": {"n": len(xs), "xs": xs, "a": a, "b": b},
                "after": {"xs": [a * x + b for x in xs]},
            }
        )([r.randrange(-1000, 1000) for _ in range(r.randrange(10))], r.randrange(-50, 50), r.randrange(-50, 50)),
    ),
    "dot": (
        "fn dot(n:usize, xs:ro<u32>[n], ys:ro<u32>[n]) -> u64\nThe sum of xs[i] * ys[i], wrapping modulo 2^64, "
        "computed with the `reduce` form.",
        ["reduce"],
        lambda r: (
            lambda xs, ys: {
                "args": {"n": len(xs), "xs": xs, "ys": ys},
                "return": sum(x * y for x, y in zip(xs, ys, strict=True)) & MASK,
            }
        )(
            *(lambda k: ([r.randrange(2**32) for _ in range(k)], [r.randrange(2**32) for _ in range(k)]))(
                r.randrange(12)
            )
        ),
    ),
    "halves": (
        "fn halves(n:usize, xs:ro<u64>[n]) -> u64\nThe wrapping sum of xs, computed by two tasks: one `spawn`ed over "
        "the first n/2 elements and one over the rest, both awaited.",
        ["spawn", "wait"],
        lambda r: (lambda xs: {"args": {"n": len(xs), "xs": xs}, "return": sum(xs) & MASK})(views(r, 64)),
    ),
    "recipe": (
        "fn zeros(a:u32, b:u32, c:u16) -> usize\nWrite a recipe `zero_fields for R` that generates "
        "`count_zero_$R(v:R) -> usize`, the number of fields of v that are zero, for any record of unsigned integer "
        "fields. Declare `struct Reading { a:u32; b:u32; c:u16; }`, derive the recipe for it, and let `zeros` return "
        "count_zero_Reading(Reading(a, b, c)).",
        ["recipe", "derive", "each"],
        lambda r: (lambda a, b, c: {"args": {"a": a, "b": b, "c": c}, "return": [a, b, c].count(0)})(
            *(r.choice([0, r.randrange(1, 9)]) for _ in range(3))
        ),
    ),
}


def contract(name: str) -> dict:
    r = random.Random("cairn-pilot-1:" + name)
    symbol = TASKS[name][0].split("(")[0].removeprefix("fn ")
    return {"schema": "cairn.task/1", "symbol": symbol, "cases": [TASKS[name][2](r) for _ in range(40)]}


def prepare(out: Path):
    cards = "\n\n".join(f"## {name}\n{text}" for name, text in CARDS.items())
    for name, (statement, needed, _) in TASKS.items():
        box = out / name
        box.mkdir(parents=True, exist_ok=True)
        (box / "CARDS.md").write_text("# CAIRN rule cards (the only documentation you have)\n\n" + cards + "\n")
        signature, _, prose = statement.partition("\n")
        must = f"\n\nYour source must use: {', '.join(needed)}." if needed else ""
        (box / "TASK.md").write_text(f"# Task: {name}\n\nWrite `solution.cairn` defining exactly this entry point:\n\n"
                                     f"    {signature}\n\n{prose}{must}\n\nCheck it with `python check.py` "
                                     f"(at most {CHECKS} runs; it reports compiler diagnostics, never test results).\n")  # fmt: skip
        (box / "check.py").write_text(f"import subprocess, sys\nsys.exit(subprocess.run([{sys.executable!r}, "
                                      f"{str(Path(__file__).resolve())!r}, 'check', {name!r}, 'solution.cairn']).returncode)\n")  # fmt: skip


def check(name: str, path: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    words = {t.s for t in lex(source)} if source.strip() else set()
    missing = [w for w in TASKS[name][1] if w not in words]
    try:
        compile_source(source)
        symbol = contract(name)["symbol"]
        declared = any(f.name == symbol for f in Parser(source).parse().functions)
        return {"status": "accepted" if declared and not missing else "incomplete", "missing_constructs": missing,
                "entry_point_declared": declared}  # fmt: skip
    except Diagnostic as e:
        return {**e.data, "missing_constructs": missing}


def score(out: Path) -> dict:
    results = {}
    for name in TASKS:
        path = out / name / "solution.cairn"
        if not path.is_file():
            results[name] = {"status": "no-solution"}
            continue
        structure = check(name, path)
        verdict = evaluate(path.read_text(encoding="utf-8"), contract(name))
        results[name] = {"structure": structure["status"], "tests": verdict["status"],
                         "solved": structure["status"] == "accepted" and verdict["status"] == "passed-finite-tests",
                         "source_bytes": path.stat().st_size}  # fmt: skip
    summary = {"schema": "cairn.ai-pilot/1", "tasks": results, "solved": sum(r.get("solved", False) for r in results.values()),
               "of": len(TASKS)}  # fmt: skip
    (out / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "prepare":
        prepare(Path(rest[0]))
    elif command == "check":
        print(json.dumps(check(rest[0], Path(rest[1])), indent=2))
    else:
        print(json.dumps(score(Path(rest[0])), indent=2))

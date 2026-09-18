#!/usr/bin/env python3
"""Differential validation of the new translator and trap-aware interpreter.

Python arithmetic oracles, the concrete CAIRN interpreter, instrumented native
Clang/GCC, and input-pinned SMT formulas are separate comparison points. This
is finite testing, not a proof of the translation or C++ backend.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / "src"), str(R / "tools")]
from cairn.scalar_semantics import (
    Concrete,
    Formula,
    Symbolic,
    bounds,
    conj,
    constant,
    disj,
    neg,
    outcome_key,
    prepared,
    same,
)
from cairn.smt_bridge import Solver
from native_scalar import NativeScalar


def fixtures():
    rows = []

    def add(name, ty, expr, oracle, params=None, ret=None):
        pars = params or [("x", ty), ("y", ty)]
        rows.append(
            {
                "name": name,
                "source": f"fn {name}(" + ",".join(n + ":" + t for n, t in pars) + f")->{ret or ty} {{return {expr};}}",
                "params": pars,
                "ret": ret or ty,
                "oracle": oracle,
            }
        )

    def checked(v, ty):
        lo, hi = bounds(ty)
        return {"defined": True, "return": v} if lo <= v <= hi else {"defined": False}

    def division(x, y, ty, rem=False):
        if y == 0 or (x == bounds(ty)[0] and y == -1):
            return {"defined": False}
        q = abs(x) // abs(y)
        if (x < 0) != (y < 0):
            q = -q
        return checked(x - q * y if rem else q, ty)

    def wrapped(v, ty):
        return checked(v % (bounds(ty)[1] + 1), ty)

    for ty in ["u8", "u32", "u64", "i32", "i64"]:
        for op, label, fun in [
            ("+", "add", lambda x, y: x + y),
            ("-", "sub", lambda x, y: x - y),
            ("*", "mul", lambda x, y: x * y),
        ]:
            add(label + "_" + ty, ty, f"x{op}y", lambda a, t=ty, f=fun: checked(f(a["x"], a["y"]), t))
        add("div_" + ty, ty, "x/y", lambda a, t=ty: division(a["x"], a["y"], t))
        add("rem_" + ty, ty, "x%y", lambda a, t=ty: division(a["x"], a["y"], t, True))
        add("min_" + ty, ty, "min(x,y)", lambda a, t=ty: checked(min(a.values()), t))
        if ty.startswith("u"):
            add("wrap_" + ty, ty, "add_wrap(x,y)", lambda a, t=ty: wrapped(a["x"] + a["y"], t))
            for expr, label, fun in [
                ("shl_wrap(x,k)", "left", lambda x, k: x << k),
                ("shr(x,k)", "right", lambda x, k: x >> k),
            ]:
                w = int(ty[1:])
                add(
                    label + "_" + ty,
                    ty,
                    expr,
                    lambda a, w=w, t=ty, f=fun: wrapped(f(a["x"], a["k"]), t) if a["k"] < w else {"defined": False},
                    [("x", ty), ("k", "usize")],
                )
        else:
            add("neg_" + ty, ty, "-x", lambda a, t=ty: checked(-a["x"], t), [("x", ty)])
    add("narrow", "u64", "u8(x)", lambda a: checked(a["x"], "u8"), [("x", "u64")], "u8")
    add("signed_cast", "u64", "i64(x)", lambda a: checked(a["x"], "i64"), [("x", "u64")], "i64")
    add("unsigned_cast", "i64", "u64(x)", lambda a: checked(a["x"], "u64"), [("x", "i64")], "u64")
    add("lazy", "u64", "x==0 || x/x==1", lambda a: {"defined": True, "return": True}, [("x", "u64")], "bool")
    add("bool_order", "bool", "x<y", lambda a: {"defined": True, "return": not a["x"] and a["y"]}, ret="bool")
    return rows


def inputs(row, rng, exhaustive=True):
    pars = row["params"]
    if exhaustive and pars == [("x", "u8"), ("y", "u8")]:
        return [dict(zip(["x", "y"], v)) for v in itertools.product(range(256), repeat=2)]
    axes = []
    for n, t in pars:
        if t == "bool":
            vals = [False, True]
        else:
            lo, hi = bounds(t)
            vals = sorted(
                set(
                    x
                    for x in [
                        lo,
                        lo + 1,
                        -2,
                        -1,
                        0,
                        1,
                        2,
                        7,
                        8,
                        31,
                        32,
                        63,
                        64,
                        127,
                        128,
                        255,
                        256,
                        hi // 2,
                        hi - 1,
                        hi,
                    ]
                    if lo <= x <= hi
                )
            )
        axes.append(vals)
    cases = [dict(zip([n for n, _ in pars], v)) for v in itertools.product(*axes)]
    for _ in range(100):
        cases.append({n: bool(rng.randrange(2)) if t == "bool" else rng.randint(*bounds(t)) for n, t in pars})
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gcc", action="store_true")
    a = ap.parse_args()
    start = time.perf_counter()
    rows = fixtures()
    source = "\n".join(r["source"] for r in rows)
    (R / "results").mkdir(exist_ok=True)
    (R / "results/scalar_validation.cairn").write_text(source)
    refs = prepared(source)
    concrete = Concrete(refs)
    rng = random.Random(1709202604)
    cases = {r["name"]: inputs(r, rng) for r in rows}
    checks = []
    with Solver(10000) as solver:
        for r in rows:
            name = r["name"]
            all_inputs = cases[name]
            selected = (
                all_inputs if len(all_inputs) < 150 else all_inputs[:30] + random.Random(42).sample(all_inputs, 120)
            )
            q = Formula(refs[name].params)
            sym = Symbolic(q, refs).invoke(name, list(q.inputs.values()))
            mismatches = []
            for args in selected:
                expected = r["oracle"](args)
                equal_inputs = conj(
                    *(
                        same(
                            q.inputs[n].value, ("true" if args[n] else "false") if t == "bool" else constant(args[n], t)
                        )
                        for n, t in r["params"]
                    )
                )
                mismatch = neg(sym.defined) if expected["defined"] else sym.defined
                if expected["defined"]:
                    v = (
                        ("true" if expected["return"] else "false")
                        if r["ret"] == "bool"
                        else constant(expected["return"], r["ret"])
                    )
                    mismatch = disj(mismatch, conj(sym.defined, neg(same(sym.value, v))))
                mismatches.append(conj(equal_inputs, mismatch))
            smt = q.text(disj(*mismatches))
            result = solver.check(smt, q.variables)
            if result["status"] != "unsat":
                raise AssertionError((name, result))
            checks.append({"function": name, "smt_pinned_cases": len(selected), "solver": result})
        version = solver.version
    native_rows = []
    for cxx in ["clang++"] + (["g++"] if a.gcc else []):
        with NativeScalar(source, cxx) as native:
            count = 0
            traps = 0
            for r in rows:
                for args in cases[r["name"]]:
                    expected = r["oracle"](args)
                    actual = concrete.outcome(r["name"], args)
                    machine = native.outcome(r["name"], args)
                    if not outcome_key(expected) == outcome_key(actual) == outcome_key(machine):
                        raise AssertionError((r["name"], args, expected, actual, machine))
                    count += 1
                    traps += not expected["defined"]
            native_rows.append(
                {
                    "compiler": native.compiler,
                    "cases": count,
                    "trap_cases": traps,
                    "generated_sha256": native.generated_sha256,
                    "instrumented_runtime_sha256": native.runtime_sha256,
                }
            )
    result = {
        "status": "passed",
        "seed": 1709202604,
        "functions": len(rows),
        "native": native_rows,
        "smt_pinned_cases": sum(x["smt_pinned_cases"] for x in checks),
        "smt_checks": checks,
        "z3_version": version,
        "seconds": time.perf_counter() - start,
        "boundary": "Finite differential tests. Native traps instrumented as exceptions only in this test. No universal translation/native/Lean proof.",
    }
    (R / "results/semantic_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "smt_checks"}, indent=2))


if __name__ == "__main__":
    main()

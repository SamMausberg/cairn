"""Closed, bounded AST generators. No source evaluation or external inputs."""

from __future__ import annotations

import copy
from functools import reduce
from typing import Any

from .syntax import MAX_FAMILY, MAX_FUNCTIONS, MAX_NODES, VOID, WIDTH, Arm, Expr, Function, Program, Stmt, Type, fail


def declared(p: Program) -> set[str]:
    return {f.name for f in p.functions} | set(p.records) | set(p.enums) | set(p.sums)


def derive_wire(p: Program) -> Program:
    """Closed AST-to-AST generator; field names cannot inject generated source."""
    names = declared(p)

    def var(n: str) -> Expr:
        return Expr("name", n)

    def lit(n: int) -> Expr:
        return Expr("int", str(n))

    def call(n: str, *args: Expr) -> Expr:
        return Expr("call", n, list(args))

    for record in p.derivations:
        if record not in p.records:
            fail("E-DERIVE-TYPE", f"Unknown record {record}.")
        fields = p.records[record]
        if any(t.mode != "value" or t.name not in {"u8", "u16", "u32", "u64"} for _, t in fields):
            fail("E-DERIVE-FIELD", "wire/1 supports only fixed-width unsigned scalar fields.")
        encode, decoded, offset = [], [], 0
        for name, t in fields:
            parts = []
            for byte in range(WIDTH[t.name] // 8):
                shifted = call("shr", Expr("field", name, [var("value")]), lit(8 * byte))
                low = call("u8", Expr("binary", "&", [shifted, lit(255)]))
                encode.append(Stmt("assign", exprs=[Expr("index", args=[var("out"), lit(offset)]), low]))
                wide = call(t.name, Expr("index", args=[var("input"), lit(offset)]))
                parts.append(call("shl_wrap", wide, lit(8 * byte)))
                offset += 1
            decoded.append(reduce(lambda a, b: Expr("binary", "|", [a, b]), parts))
        size = str(offset)
        for f in [
            Function("encode_" + record, [("out", Type("u8", "rw", size)), ("value", Type(record))], VOID, encode),
            Function("decode_" + record, [("input", Type("u8", "ro", size))], Type(record),
                     [Stmt("return", exprs=[call(record, *decoded)])]),
            Function("wire_size_" + record, [], Type("usize"), [Stmt("return", exprs=[lit(offset)])]),
        ]:  # fmt: skip
            if f.name in names:
                fail("E-DERIVE-COLLISION", f"Derived name {f.name} already exists.")
            f.source_name = "derive wire for " + record
            names.add(f.name)
            p.functions.append(f)
    return p


def node_count(f: Function) -> int:
    total = 0
    todo: list[Any] = list(f.body)
    while todo:
        node = todo.pop()
        total += 1
        if isinstance(node, Stmt):
            todo.extend(node.body + node.other + node.exprs + node.arms)
        elif isinstance(node, Arm):
            todo.extend(node.body)
        elif isinstance(node, Expr):
            todo.extend(node.args)
    return total


def specialize(p: Program) -> Program:
    """Instantiate each family over its bounded natural range; templates stay for the checker."""
    templates = {f.name: f for f in p.functions if f.generics}
    concrete = [f for f in p.functions if not f.generics]
    names = declared(p) - set(templates)
    estimated = sum(node_count(f) for f in concrete)
    if estimated > MAX_NODES or len(concrete) > MAX_FUNCTIONS:
        fail("E-EXPANSION-LIMIT", "Program exceeds the pre-expansion budget.")
    for prefix, name, lo, hi in p.families:
        base = templates.get(name)
        if base is None or [k for _, k in base.generics] != ["nat"]:
            fail("E-FAMILY-TARGET", f"{name} is not a static function template.")
        if not 0 <= lo < hi <= 2**32 or hi - lo > MAX_FAMILY:
            fail("E-FAMILY-LIMIT", "Family must be a nonempty half-open range, at most 1024 variants, below 2^32.")
        estimated += (hi - lo) * node_count(base)
        if estimated > MAX_NODES or len(concrete) + hi - lo > MAX_FUNCTIONS:
            fail("E-EXPANSION-LIMIT", "Family exceeds the remaining AST/function budget.")
        for k in range(lo, hi):
            f = copy.deepcopy(base)
            f.name, f.bindings = f"{prefix}_{k}", {base.generics[0][0]: k}
            if f.name in names:
                fail("E-DUPLICATE", f"Family emits duplicate name {f.name}.")
            names.add(f.name)
            concrete.append(f)
    p.functions = [*concrete, *templates.values()]
    return p

"""Names for a function's parallel regions that survive edits which do not touch them.

A region's line moves whenever anything above it does, so a record that says "the region at line 12" goes wrong on
the next unrelated edit. A region is named instead by its function and a digest of what it is as written: the
statement's syntax tree with every position, comment and layout left out, and nothing the checker or a plan adds.
An edit to another function, to another statement of the same function, a comment or a reformat leaves the name as
it was; an edit to the region itself gives it a new one, because it is a different region. Two regions of one
function written alike are told apart by their order (`f@1a2b3c4d`, `f@1a2b3c4d.2`).

A plan's items apply to every region of their kind in the function, so a transformation names its targets with
these: which regions a candidate's `vector` chunked, which its `stage` tiled, which its `fuse` joined.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..compiler.cairnc import Parser, compile_program
from ..compiler.tree import Arm, Expr, Stmt, Type

SYNTAX = ("tag", "name", "op", "binder", "pooled", "exclusive")  # what a Stmt says as written, positions aside


def shape(node: Any) -> Any:
    """A syntax node as plain data: its written fields and its children, nothing the checker or a plan sets."""
    if isinstance(node, Expr):
        return ["e", node.tag, node.val, [shape(a) for a in node.args if isinstance(a, Expr)]]
    if isinstance(node, Stmt):
        return ["s", *(getattr(node, k) for k in SYNTAX), node.ty.display() if isinstance(node.ty, Type) else "",
                [shape(e) for e in node.exprs], [shape(s) for s in node.body], [shape(s) for s in node.other],
                [shape(a) for a in node.arms], [shape(e) for e in node.other_names]]  # fmt: skip
    if isinstance(node, Arm):
        return ["a", node.variant, node.binder, [shape(s) for s in node.body]]
    return repr(node)


def walked(ss: list[Stmt]) -> list[Stmt]:
    out = []
    for s in ss:
        out.append(s)
        for arm in s.arms:
            out += walked(arm.body)
        out += walked(s.body) + walked(s.other)
    return out


def named(function: str, statements: list[Stmt]) -> list[str]:
    """The name of each region of `function`, in source order."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for s in statements:
        key = hashlib.sha256(json.dumps(shape(s)).encode()).hexdigest()[:8]
        seen[key] = seen.get(key, 0) + 1
        out.append(f"{function}@{key}" + (f".{seen[key]}" if seen[key] > 1 else ""))
    return out


def identified(source: str, function: str) -> list[dict[str, Any]]:
    """Each parallel region of `function` (its qualified name): its name, kind, line and binder, in source order."""
    parsed = next((f for f in Parser(source).parse().functions if f.name == function), None)
    checked = next((f for f in compile_program(source)[0].functions if f.name == function and not f.bindings), None)
    if parsed is None or checked is None:
        raise ValueError(f"No function {function} whose regions to name.")
    written = [s for s in walked(parsed.body) if s.tag == "parallel"]
    regions = [s for s in walked(checked.body) if s.tag == "parallel"]
    return [{"id": name, "kind": s.ref if s.ref in {"host", "device"} else "host", "line": s.line,
             "binder": s.binder or s.name} for name, s in zip(named(function, written), regions, strict=True)]  # fmt: skip


def applied(source: str, function: str, checked: tuple[Any, Any] | None = None,
            names: list[str] | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
    """What the plan in `source` does to each named region of `function`: its launch or claim, and which arrays its
    vector chunks and its stage tiles. A fuse is named on every region of the chain it heads or joins. `checked` is
    the program and checker of `source`, and `names` its regions' names in order, when the caller has them: a plan
    changes neither, so a search names the regions once for all its candidates."""
    from ..compiler import fusion

    p, checker = checked or compile_program(source)[:2]
    f = next(f for f in p.functions if f.name == function and not f.bindings)
    if names is None:
        parsed = next(g for g in Parser(source).parse().functions if g.name == function)
        names = named(function, [s for s in walked(parsed.body) if s.tag == "parallel"])
    regions = [s for s in walked(f.body) if s.tag == "parallel"]
    ids = dict(zip(map(id, regions), names, strict=True))
    out: dict[str, dict[str, Any]] = {}
    for s in regions:
        done: dict[str, Any] = {}
        if s.ref == "device":
            done |= {k: v for k, v in zip(("block", "per_lane", "unroll"), s.launch, strict=True) if v}
            if s.vector:
                done["vector"] = {"width": s.vector, "arrays": [c[0] for c in s.chunked]}
            if s.stage:
                done["stage"] = {"radius": s.stage, "arrays": [c[0] for c in s.staged]}
        else:
            done |= {k: v for k, v in zip(("grain", "lanes"), s.plan, strict=True) if v}
        out[ids[id(s)]] = done
    for ss in fusion.lists(f.body):
        for chain in fusion.chains(ss, checker.rows, f):
            joined = [ids[id(r)] for r in chain.regions if id(r) in ids]
            for name in joined:
                out[name]["fuse"] = joined
    return out

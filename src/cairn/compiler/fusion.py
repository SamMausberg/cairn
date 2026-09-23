"""Fusion: which adjacent parallel regions a plan may run as one traversal, and the scratch they need not keep.

`plan f { fuse K; }` lets up to K adjacent `parallel` statements of f run as one region, each lane running the
bodies one after another at its own index. The plan asks; this module decides, from the checked tree:

- the regions are statements side by side in one block, with one placement, extents spelled alike from names,
  literals, fields and `len`, and lanes that own element `[i]` rather than a block;
- whatever one region writes and another touches, both touch only at their own index, so lane `i` of a later body
  reads nothing that another lane of an earlier body writes, and writes nothing an earlier lane read;
- no body can trap, loop without end or be observed from outside: every guard in it was discharged and kept by
  the elision audit, and every function it calls has a row of reads, writes of what it was lent and local work.

The last rule is the strict one. A failed guard aborts the process, and which lane of a region fails first is
already open; fusing two trapping bodies would let a later body's guard fail before an earlier body's, so the
set of ways the process can end would grow. Fusion therefore waits for bodies that cannot trap at all.

A local `buffer` or `stack` array that only the fused bodies touch, each at its own index as a plain value, is
kept in one lane-local value instead of memory: nothing else can observe it, so it is never allocated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tree import FLOAT, INT, SIGNED, STORAGE, Expr, Function, Stmt

# What a function a fused body calls may do: read and write what it was lent, and work on its own storage. Its row
# may also say trap and ffi_precondition, for guards the checker discharged and for views an entry would check;
# the callee's own body then has to be quiet as it is emitted, and a call from CAIRN reaches its lean body.
QUIET = {"local_read", "local_write", "stack_storage", "zero_init"}
SCALAR = {"bool", *INT, *FLOAT}
# Builtins that compute without a guard; a narrowing or a shift counts only where the checker discharged it.
COMPUTES = {"len", "min", "max", "add_wrap", "sub_wrap", "mul_wrap", "sqrt", "floor", "ceil", "trunc", "to_bits"}
ALIKE = {"name", "int", "field", "call", "binary"}  # what an extent both regions agree on may be spelled with


@dataclass
class Chain:
    """Regions that run as one: the first carries the extent and the plan, the rest run inside its lanes."""

    regions: list[Stmt]
    scratch: list[str] = field(default_factory=list)  # local arrays held in one lane-local value each


def quiet_callee(f: Function, rows: dict[str, set[str]], open_: frozenset[str] = frozenset()) -> bool:
    """A function a fused body may call: nothing in its row but reads, writes of what it was lent, local work and
    guards, every one of which its body, as emitted, has discharged. A sum parameter has its tag checked on entry,
    so only scalar values and views are passed."""
    row = rows.get(f.name)
    if row is None or f.extern or f.name in open_ or f.kernel:
        return False
    if not all(x in QUIET | {"trap", "ffi_precondition"} or x.startswith(("read:", "write:")) for x in row):
        return False
    if any(t.mode == "value" and t.name not in SCALAR for _, t in f.params):
        return False
    return "trap" not in row or quiet_block(f.body, rows, open_ | {f.name}, returns=True)


def quiet(e: Any, rows: dict[str, set[str]], open_: frozenset[str] = frozenset()) -> bool:
    """e cannot trap, loop without end, or be seen from outside the lane that evaluates it."""
    if not isinstance(e, Expr):
        return True
    below = all(quiet(a, rows, open_) for a in e.args)
    if e.tag in {"name", "int", "float", "bool"}:
        return below
    if e.tag == "field":
        return below and not isinstance(e.ref, tuple)
    if e.tag == "index":
        return below and bool(e.established)
    if e.tag == "unary":
        return below and (e.val in {"!", "~"} or (e.ty is not None and e.ty.name in FLOAT))
    if e.tag == "binary":
        if e.val in {"==", "!=", "<", "<=", ">", ">=", "&&", "||", "&", "|", "^"}:
            return below
        return below and ((e.ty is not None and e.ty.name in FLOAT) or bool(e.established))
    if e.tag == "call" and isinstance(e.ref, Function):
        return below and quiet_callee(e.ref, rows, open_)
    if e.tag == "call" and isinstance(e.ref, tuple) and e.ref and e.ref[0] == "builtin":
        name, ty = e.val, e.ty.name if e.ty is not None else ""
        if name in COMPUTES or (name == "abs" and ty not in SIGNED):
            return below
        if name in FLOAT or name in STORAGE:  # IEEE's conversions; only f8e4m3, without an infinity, traps
            return below and STORAGE.get(name, (0, 0, True))[2]
        if name in INT or name in {"shl_wrap", "shr"}:  # narrowing, float to integer and shifts, where discharged
            return below and bool(e.established)
    if e.tag == "call" and isinstance(e.ref, tuple) and e.ref and e.ref[0] in {"record", "variant"}:
        return below  # a value built from quiet parts
    return False


def quiet_block(ss: list[Stmt], rows: dict[str, set[str]], open_: frozenset[str] = frozenset(),
                returns: bool = False) -> bool:  # fmt: skip
    """Only statements that finish: bindings, assignments, branches, counted loops and calls that stand alone,
    and in a callee's body its returns."""
    allowed = {"let", "reg", "assign", "if", "for", "block", "expr", *(("return",) if returns else ())}
    for s in ss:
        if s.tag not in allowed or not all(quiet(e, rows, open_) for e in s.exprs):
            return False
        if not quiet_block([*s.body, *s.other], rows, open_, returns):
            return False
    return True


def alike(a: Expr, b: Expr) -> bool:
    """Two extents spelled the same way from names, literals, fields, `len` and arithmetic: the same value, since
    nothing runs between two adjacent regions and no lane writes a scalar outside itself."""
    if a.tag != b.tag or a.val != b.val or len(a.args) != len(b.args) or a.tag not in ALIKE:
        return False
    if a.tag == "call" and a.val != "len":
        return False
    return all(alike(x, y) for x, y in zip(a.args, b.args, strict=True))


def compatible(regions: list[Stmt]) -> bool:
    """What any region writes, the others touch only at their own index, and so does it."""
    written = {name for r in regions for name, _, write in r.touched if write}
    for name in written:
        users = [r for r in regions if any(n == name for n, _, _ in r.touched)]
        if len(users) > 1 and any(stride != 1 for r in users for n, stride, _ in r.touched if n == name):
            return False
    return True


def fusible(a: Stmt, b: Stmt, rows: dict[str, set[str]]) -> bool:
    return (
        a.tag == b.tag == "parallel"
        and a.ref == b.ref
        and a.block == b.block == 1
        and alike(a.exprs[0], b.exprs[0])
        and quiet_block(a.body, rows)
        and quiet_block(b.body, rows)
    )


def lists(ss: list[Stmt]):
    """Every statement list of a body, itself first: fusion joins neighbours within one of them."""
    yield ss
    for s in ss:
        for inner in (s.body, s.other, *(arm.body for arm in s.arms)):
            if inner:
                yield from lists(inner)


def chains(ss: list[Stmt], rows: dict[str, set[str]], f: Function | None = None) -> list[Chain]:
    """The longest runs of adjacent regions in `ss` whose plan asks for fusion, each of at most `fuse` regions."""
    found, i = [], 0
    while i < len(ss):
        head, run = ss[i], [ss[i]]
        limit = head.fuse if head.tag == "parallel" else 0
        while limit and len(run) < limit and i + len(run) < len(ss):
            nxt = ss[i + len(run)]
            if not (fusible(run[-1], nxt, rows) and fusible(head, nxt, rows) and compatible([*run, nxt])):
                break
            run.append(nxt)
        if len(run) > 1:
            found.append(Chain(run, scratch(ss[:i], run, f) if f is not None else []))
        i += len(run)
    return found


def scratch(before: list[Stmt], run: list[Stmt], f: Function) -> list[str]:
    """Local arrays declared ahead of the chain in its block that nothing but the chain touches, each only as
    `name[binder]` read as a value or assigned, never lent, measured or passed on."""
    kept = []
    for s in before:
        if s.tag not in {"buffer", "stack"} or s.ty is None or s.ty.name not in INT | FLOAT | {"bool"}:
            continue
        if all(use_ok(s.name, *use) for use in uses(f.body, run)):
            kept.append(s.name)
    return kept


def use_ok(name: str, e: Expr, parent: Expr | None, region: Stmt | None, lent: bool) -> bool:
    """One mention of `name`: acceptable only as the array of an index at its region's own binder, not lent."""
    if e.tag != "name" or e.val != name:
        return True
    if region is None or lent or parent is None or parent.tag != "index" or parent.args[0] is not e:
        return False
    at = parent.args[1]
    return at.tag == "name" and at.val == (region.binder or region.name)


def uses(body: list[Stmt], run: list[Stmt]):
    """Every expression of the function: its parent, the fused region it sits in, and whether the index holding
    it is an argument of a function, which may borrow the element rather than read it."""

    def walk(e: Any, parent: Expr | None, region: Stmt | None, lent: bool):
        if not isinstance(e, Expr):
            return
        yield e, parent, region, lent
        borrows = e.tag == "call" and not (isinstance(e.ref, tuple) and e.ref and e.ref[0] == "builtin")
        for a in e.args:
            yield from walk(a, e, region, (e.tag == "index" and lent) or (borrows and a.tag == "index"))
        if e.tag == "lambda" and isinstance(e.ref, Function):
            yield from stmts(e.ref.body, None)

    def stmts(ss: list[Stmt], region: Stmt | None):
        for s in ss:
            inside = s if any(s is r for r in run) else region
            for e in s.exprs:
                yield from walk(e, None, inside, False)
            yield from stmts([*s.body, *s.other, *(x for arm in s.arms for x in arm.body)], inside)

    yield from stmts(body, None)

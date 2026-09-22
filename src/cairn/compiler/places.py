"""Places, ownership and leases: what a name denotes, what may be written, moved, lent or reached."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import facts
from .concurrency import PINNED
from .tree import USIZE, Expr, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker


def path(e: Expr, stable=lambda name: False) -> str:
    """A syntactic place identity for alias checks: a.b, a[], a[lo..hi].

    A part bound counts only when it cannot change: a literal, or a name `stable` vouches for.
    """
    if e.tag == "field":
        return path(e.args[0], stable) + "." + e.val
    if e.tag == "index":
        return path(e.args[0], stable) + "[]"
    if e.tag == "slice":
        base = path(e.args[0], stable)
        if e.args[0].tag == "slice":  # A part of a part is somewhere inside the outer part: no visible bounds.
            return base.partition("[")[0] + "[?..?]"
        bounds = (a.val if a.tag == "int" or (a.tag == "name" and stable(a.val)) else "?" for a in e.args[1:])
        return base + "[" + "..".join(bounds) + "]"
    return e.val


def vague(place: str) -> str:
    """The same place without its bounds: `d[a..b]` becomes `d[?..?]`, which orders nothing."""
    return place.partition("[")[0] + "[?..?]" if ".." in place else place


def settle(paths: list[dict[str, list[tuple[str, str]]]]) -> dict[str, list[tuple[str, str]]]:
    """The leases after alternatives join. What any path lent stays lent, but a part keeps its bounds only if
    every path lent it: the `lo <= hi` guard that makes them a fact ran only where the slice was formed."""
    names = dict.fromkeys(t for held in paths for t in held)
    return {t: list(dict.fromkeys((p if all((p, m) in held.get(t, ()) for held in paths) else vague(p), m)
                                  for held in paths for p, m in held.get(t, ()))) for t in names}  # fmt: skip


def field_path(e: Expr) -> bool:
    """Is this a chain of fields on a local: `box.a`, `box.a.b`, and nothing computed on the way?

    Reaching such a place reads that field's header, not the record it sits in.
    """
    while e.tag == "field":
        e = e.args[0]
    return e.tag == "name"


def overlaps(a: str, b: str, others: Any = ()) -> bool:
    """Two parts of one array are disjoint only when one visibly ends at or before the other begins.

    Every part in play was guarded `lo <= hi`, so the bounds of `others` (parts of the same array that
    are lent alongside) chain: d[0..a], d[a..b] and d[b..n] are pairwise disjoint."""
    (base_a, _, part_a), (base_b, _, part_b) = a.partition("["), b.partition("[")
    if base_a == base_b and ".." in part_a and ".." in part_b:
        known = [
            p.partition("[")[2][:-1].split("..") for p in (a, b, *others) if p.startswith(base_a + "[") and ".." in p
        ]
        edges = [(lo, hi) for lo, hi in known if "?" not in (lo, hi)]

        def reaches(x: str, goal: str, seen: set[str]) -> bool:
            before = x == goal or (x.isdigit() and goal.isdigit() and int(x) <= int(goal))
            return before or any(
                reaches(hi, goal, seen | {x}) for lo, hi in edges
                if hi not in seen and (lo == x or (lo.isdigit() and x.isdigit() and int(x) <= int(lo)))
            )  # fmt: skip

        (lo_a, hi_a), (lo_b, hi_b) = part_a[:-1].split(".."), part_b[:-1].split("..")
        return "?" in (lo_a, hi_a, lo_b, hi_b) or not (reaches(hi_a, lo_b, set()) or reaches(hi_b, lo_a, set()))
    return base_a == base_b or base_a.startswith(base_b + ".") or base_b.startswith(base_a + ".")


def extent_of(c: Checker, e: Expr) -> str | None:
    """The name/literal identity of an extent expression, or None when it has none."""
    if e.tag == "name" and e.val not in c.env:  # A named constant is its literal.
        const = c.qualify(e.val, c.p.consts)
        return c.p.consts[const][1].val if const else e.val
    if e.tag in {"name", "int"}:
        return e.val
    if e.tag == "field" and field_path(e) and root(e).val in c.env:
        ty = e.ty or c.peek(e)  # An extern may name its length after the view it measures.
        return c.identity(e) if ty == USIZE else None
    if e.tag == "call" and e.val == "len" and len(e.args) == 1 and root(e.args[0]).tag in {"name", "str"}:
        ty = e.args[0].ty or c.expr(e.args[0], consume=False)
        if is_view(ty):
            return ty.extent
        if ty.name == "Buf":  # A declared field extent answers for the field, so both spellings agree.
            return c.declared_extent(e.args[0]) or f"len({c.identity(e.args[0])})"
        if ty.name == "Array":  # An inline array's length is its literal.
            return str(ty.args[1])
    return None


def declared_extent(c: Checker, e: Expr) -> str:
    """The extent identity a record gives one of its own Buf fields: `c.price` is `c.rows` elements long."""
    if e.tag != "field" or not field_path(e) or e.args[0].ty is None:
        return ""
    named = c.p.field_extents.get(e.args[0].ty.name, {}) if e.args[0].ty.mode == "value" else {}
    return c.identity(e.args[0]) + "." + named[e.val] if e.val in named else ""


def writable(c: Checker, e: Expr) -> bool:
    b = c.env.get(root(e).val) if root(e).tag == "name" else None
    return b is not None and (b.ty.mode == "rw" or (b.ty.mode == "value" and b.mutable))


def place(c: Checker, e: Expr, write: bool = False) -> Type:
    """Type a storage location without consuming it; `write` demands mutability."""
    if e.tag == "name":
        b = c.env.get(e.val)
        if write and (b is None or is_view(b.ty) or not c.writable(e)):
            fail("E-IMMUTABLE", f"{e.val} is not a mutable local.", e)
        if write and b.ty.mode == "rw":
            c.effect("write:" + e.val)
        whole = write or (b is not None and not is_view(b.ty) and b.ty.name not in {"Buf", "Array"})
        if whole and not c.reaching:  # Inside a field path the field itself is leased, not the record.
            c.leased(e.val, "rw" if write else "ro", e)
        if write and c.lanes and e.val in c.lanes.outer:
            fail("E-PARALLEL-WRITE", f"Every lane would write {e.val}; write element [{c.lanes.binder}] or reduce.", e)
        return c.expr(e, consume=False)
    if e.tag == "index":
        if write and not c.writable(e):
            fail("E-WRITE-LEASE", "Indexed assignment requires a direct rw slice parameter.", e)
        if write:
            c.effect("write:" + root(e).val)
        e.ty = c.e_index(e, None, read=not write)
        return e.ty
    if e.tag == "field":
        outer, reached = not c.reaching, field_path(e) and root(e).val in c.env
        c.reaching += reached
        at = c.place(e.args[0], write)
        carrier = c.participates(at, e.val) if write else ""
        if carrier:  # len(c.price) == c.rows holds of every value, so neither half moves on its own.
            fail("E-EXTENT-FIELD", f"{e.val} takes part in the declared extent of {at.name}.{carrier}; "
                 "assign, take or swap the whole record.", e)  # fmt: skip
        ty = c.expr(e, consume=False)
        c.reaching -= reached
        if reached and outer:  # A new value in this cell replaces what a lease of its elements holds.
            c.leased(c.where(e), "rw" if write else "ro", e, elements=write)
        return ty
    fail("E-LVALUE", "Assignment requires a mutable variable, field, or rw element.", e)


def stable(c: Checker, name: str) -> bool:
    """A name whose value cannot change while it is in scope: a parameter, a let, a loop binder."""
    return name in c.env and not c.env[name].mutable and c.env[name].ty.mode == "value"


def where(c: Checker, e: Expr) -> str:
    return path(e, c.stable)


def identity(c: Checker, e: Expr) -> str:
    """Which storage an extent belongs to: exact for a literal or stable index, unique otherwise."""
    if e.tag == "index":
        i, c.unique = e.args[1], c.unique + 1
        key = i.val if i.tag == "int" or (i.tag == "name" and c.stable(i.val)) else f"?{c.unique}"
        return f"{c.identity(e.args[0])}[{key}]"
    return c.identity(e.args[0]) + "." + e.val if e.tag == "field" else e.val


def leased(c: Checker, place: str, mode: str, node: Any, elements: bool = True):
    """Every access to a named place: a task's lease may forbid it, and a closure records what it captures.
    `elements=False` is a read of an owner's length, which a lease on its elements cannot change."""
    lent = [p for held in c.leases.values() for p, _ in held]
    for ticket, held in c.leases.items():
        if any(overlaps(place, p, lent) and "rw" in (mode, m) and (elements or "[" not in p) for p, m in held):
            fail("E-LEASED", f"{place.removesuffix('[]')} is lent to {ticket} until wait({ticket}).", node)
    if c.touched is not None:
        c.touched.append((place, mode, elements, node))
    c.capture(place, mode)


def carried(c: Checker, held: dict[str, list[tuple[str, str]]], touched: list[tuple[str, str, bool, Any]]):
    """A group declared outside a loop keeps what every iteration lent it, so each later iteration runs while
    those leases are held: nothing the body touches may conflict with them. They leave the loop without their
    bounds, since the loop may not have run, and a bound the body names may differ between iterations."""
    for group, places in c.leases.items():
        kept = held.get(group, [])
        new = [(vague(p), m) for p, m in places if (p, m) not in kept]
        for place, mode, elements, node in touched:
            if any(overlaps(place, p) and "rw" in (mode, m) and (elements or "[" not in p) for p, m in new):
                fail("E-LEASED", f"{place.removesuffix('[]')} is lent to {group} by an earlier iteration, "
                     f"until wait({group}).", node)  # fmt: skip
        c.leases[group] = [*kept, *new]


def capture(c: Checker, place: str, mode: str):
    if c.closure and re.split(r"[.\[]", place)[0] in c.closure[1]:
        c.closure[0].captures.append((place, mode))


def consume(c: Checker, e: Expr):
    """An owner used as a value moves; its name is dead afterwards."""
    waited = e.ty.name in {"Ticket", "Group", "IoRing"} and c.spawning == "<wait>"
    if e.tag in {"name", "field", "index"} and e.ty.name in PINNED and not waited:
        fail("E-PINNED", f"{e.ty.display()} cannot move; wait() a ticket or a group, borrow an atomic or mutex.", e)
    if e.tag == "name":
        c.leased(e.val, "rw", e)
        if e.val in c.moved | c.deferred:
            fail("E-MOVED", f"{e.val} was already moved or scheduled for cleanup.", e)
        if c.env[e.val].ty.mode != "value":
            fail("E-MOVE-BORROW", f"{e.val} is borrowed; take() or swap() its contents instead.", e)
        c.moved.add(e.val)
        e.ref = "move"
    elif e.tag == "index" or (e.tag == "field" and not isinstance(e.ref, tuple)):  # Enum.None is a value.
        fail("E-PARTIAL-MOVE", "An owner cannot be moved out of a place; use take() or swap().", e)


def intact(c: Checker, a: Expr, mode: str, elements: bool):
    """A field in a declared extent is never lent as a whole mutable place: a callee assigns an `rw<T>`
    borrow by name, and the record's invariant would leave with it. An `rw<T>[n]` view writes elements."""
    if mode != "rw" or elements or a.tag != "field" or a.args[0].ty is None:
        return
    carrier = c.participates(a.args[0].ty, a.val)
    if carrier:
        fail("E-EXTENT-FIELD", f"{a.val} takes part in the declared extent of {a.args[0].ty.name}.{carrier}; "
             "it is not lent as a whole rw place.", a)  # fmt: skip


def lend(c: Checker, a: Expr, mode: str, borrows: list[tuple[str, str]], elements: bool = False) -> str:
    """A named place lent to a call: leased places and lanes object here; returns its root name.
    An array view lends the elements (`x[]`), which leaves the owner's length readable."""
    c.intact(a, mode, elements)
    if root(a).tag != "name" or root(a).val not in c.env:
        return ""
    place = c.where(a) + "[]" * (elements and a.tag != "slice")
    c.leased(place, mode, a)
    borrows.append((place, mode))
    if c.lanes and root(a).val in c.lanes.outer:  # A part, or an element, inside the lane's own block is that block.
        shown = {"slice": a, "index": a.args[-1]}.get(a.tag) if a.args and a.args[0].tag == "name" else None
        block = facts.window(c, shown, c.lanes.binder) if shown is not None else None
        c.lanes.accesses.append((root(a).val, block, mode == "rw", a))
    return root(a).val


def disjoint(c: Checker, borrows: list[tuple[str, str]], node: Any, *closures: list[tuple[str, str]]):
    """No argument of one call may write what another can reach; a closure reaches what it captured."""
    groups = [[b] for b in borrows] + list(closures)
    lent = [place for place, _ in borrows]
    for i, group in enumerate(groups):
        rest = [b for later in groups[i + 1 :] for b in later]
        if any(overlaps(place, other, lent) and "rw" in (m, k) for place, m in group for other, k in rest):
            fail("E-ALIAS", "A mutable view cannot be passed to overlapping call arguments.", node)

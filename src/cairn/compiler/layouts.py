"""Layouts: where each logical element of a tile is stored, and which participant holds it.

`layout NAME = expression;` declares a compile-time object that the checker evaluates here and nowhere else. A
storage layout maps a logical coordinate to an element offset: `rows(R, C)`, `cols(R, C)`, `strided(R, C, SR, SC)`,
and what `pad`, `swizzle`, `transpose` and `tile` make of one. A spread maps a participant and one of its values to
a coordinate of a tile: `spread(L, TR, TC, VR, VC)` gives TR x TC participants, numbered row by row, a VR x VC block
of L each, and repeats that block of blocks down and across L. The values, and everything the checker answers from
them (owners, coverage, runs, bank conflicts, conversions, what a consumer can read), are in `layout_algebra.py`.
This file evaluates a declaration, holds every declared layout to its rules, reports them in the receipt and
`cairn explain`, and checks and lowers `L.at(...)` and the other methods in code.

Every offset a storage layout places is below 2^63 - 1, the most elements an array holds, and a swizzle reads and
flips bits below bit 63 (B + M + S at most 63), so the lowered arithmetic in 64-bit unsigned integers never wraps or
shifts past its width, and gives what the enumeration gives (E-LAYOUT).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .layout_algebra import (
    Layout,
    Spread,
    Value,
    cols,
    conversion,
    distinct,
    exactly_once,
    facts,
    inverse,
    owners,
    rows,
    shares,
    spread,
    strided,
    tiled,
    transposed,
)
from .tree import USIZE, Expr, Type, fail

if TYPE_CHECKING:
    from .checking import Checker

MAX_ELEMENTS = 1 << 18  # the most elements, or participant-value pairs, one layout may enumerate
LARGEST = 2**63 - 1  # the most elements an array holds (compiler/checking.py), so every offset a layout places is below


# Evaluating a declaration --------------------------------------------------------------------------------------

ARITY = {"rows": 2, "cols": 2, "strided": 4, "pad": 2, "swizzle": 4, "transpose": 1, "tile": 3, "spread": 5}
ARITY["inverse"] = 1
LAYOUT_FIRST = {"pad", "swizzle", "transpose", "tile", "spread", "inverse"}  # constructors that take a layout first
LEAST = {"strided": (1, 1, 0, 0), "pad": (0,), "swizzle": (1, 0, 0)}  # the least each natural may be; else 1


def value(c: Checker, name: str, pending: tuple[str, ...] = ()) -> Value:
    """The layout a declaration names, evaluated once and kept among the checker's folded constants."""
    if name not in c.folded:
        if name in pending:
            fail("E-LAYOUT", f"{name} is defined in terms of itself.", c.p.layouts[name])
        written = c.p.layouts[name]
        with c.within(c.p.modules.get(name, "")):
            made = evaluate(c, written, (*pending, name))
        if isinstance(made, Spread):  # A declared spread gives every element of its tile one holder,
            distinct(made.tile, written, "A spread's tile")  # and every declared layout gives each element
            exactly_once(made, written, "a spread")  # its own offset: writes through either never collide.
        else:
            distinct(made, written, "A storage layout")
        c.folded[name] = made
    return c.folded[name]


def named(c: Checker, e: Expr) -> str | None:
    """The declared layout an expression names, when it names one and no local hides it."""
    if e.tag == "name" and e.val in c.env:
        return None
    path = e.val if e.tag == "name" else None
    if e.tag == "field" and e.args and e.args[0].tag == "name":
        path = f"{e.args[0].val}.{e.val}"
    return c.qualify(path, c.p.layouts, node=e) if path else None


def natural(c: Checker, e: Expr, least: int = 1) -> int:
    from .constants import fold  # constants.py folds layout queries through this module, so it is imported here

    n = fold(c, e, USIZE, [])
    if isinstance(n, bool) or not isinstance(n, int) or n < least:
        fail("E-LAYOUT", f"A layout's extents, strides and counts are naturals of at least {least}.", e)
    return n


def evaluate(c: Checker, e: Expr, pending: tuple[str, ...]) -> Value:
    name = named(c, e)
    if name:
        return value(c, name, pending)
    if e.tag != "call" or e.val not in ARITY:
        fail("E-LAYOUT", f"A layout is one of {', '.join(ARITY)}, applied to naturals and layouts, or the name of "
             "another layout.", e)  # fmt: skip
    if len(e.args) != ARITY[e.val]:
        fail("E-ARITY", f"{e.val} takes {ARITY[e.val]} arguments.", e)
    inner = evaluate(c, e.args[0], pending) if e.val in LAYOUT_FIRST else None
    given = e.args[int(inner is not None) :]
    numbers = [natural(c, a, least) for a, least in zip(given, LEAST.get(e.val, (1,) * len(given)), strict=True)]
    made = build(e.val, inner, numbers, e)
    bounded(made, e)
    enumerated = made.size if isinstance(made, Layout) else max(made.count * made.each, made.tile.size)
    if enumerated > MAX_ELEMENTS:
        fail("E-LAYOUT", f"The checker evaluates a layout of at most {MAX_ELEMENTS} elements; this one has "
             f"{enumerated}.", e)  # fmt: skip
    return made


def bounded(v: Value, node: Any):
    """Refuse a layout whose arithmetic would not fit in 64 bits: an offset no array can reach, or a swizzle that
    reads or flips bit 63 or past it. A spread's own sums stay below MAX_ELEMENTS squared."""
    tile = v if isinstance(v, Layout) else v.tile
    bits, base, shift = tile.swizzle
    if bits and bits + base + shift > 63:
        fail("E-LAYOUT", f"swizzle(L, {bits}, {base}, {shift}) reads bits {base + shift} to {base + shift + bits - 1} "
             "of an offset, and an offset below 2^63 has bits 0 to 62: B + M + S is at most 63.", node)  # fmt: skip
    reach = sum((extent - 1) * stride for modes in tile.dims for extent, stride in modes)
    if reach >= LARGEST:
        fail("E-LAYOUT", f"This layout places an element at offset {reach}, and an array holds at most {LARGEST} "
             "elements.", node)  # fmt: skip


def storage(inner: Value | None, what: str, node: Any) -> Layout:
    if not isinstance(inner, Layout):
        fail("E-LAYOUT", f"{what} takes a storage layout, and this is a spread.", node)
    return inner


def build(kind: str, inner: Value | None, n: list[int], node: Any) -> Value:
    if kind in {"rows", "cols", "strided"}:
        return rows(*n) if kind == "rows" else cols(*n) if kind == "cols" else strided(*n)
    if kind == "inverse":
        assert inner is not None
        return inverse(inner, node)
    if kind == "transpose":
        assert inner is not None
        if isinstance(inner, Layout) and len(inner.dims) != 2:
            fail("E-LAYOUT", "transpose swaps the two dimensions of a 2-D layout.", node)
        return transposed(inner)
    base = storage(inner, kind, node)
    if kind == "spread":
        if len(base.dims) != 2:
            fail("E-LAYOUT", "spread shares a 2-D tile among participants.", node)
        return spread(base, *n)
    if kind == "tile":
        if len(base.dims) != 2:
            fail("E-LAYOUT", "tile cuts a 2-D layout into a grid of tiles.", node)
        return tiled(base, n[0], n[1], node)
    if base.swizzle[0] or any(len(modes) != 1 for modes in base.dims) or len(base.dims) != 2:
        fail("E-LAYOUT", f"{kind} applies to a 2-D layout of one stride a dimension, before any swizzle.", node)
    if kind == "pad":  # the dimension of the larger stride steps over the padding
        (((r, sr),), ((cc, sc),)) = base.dims
        return strided(r, cc, sr + n[0], sc) if sr >= sc else strided(r, cc, sr, sc + n[0])
    return Layout(base.dims, (n[0], n[1], n[2]))  # a shift below the bits is no bijection: `distinct` says so


def settle(c: Checker):
    """Evaluate every declared layout, so one that nothing uses is still held to its rules."""
    for name in c.p.layouts:
        value(c, name)


def receipt(c: Checker) -> dict[str, Any]:
    """What the build receipt says of each declared layout."""
    return {name: facts(value(c, name)) for name in sorted(c.p.layouts)}


SHOWN = 4096  # the largest tile whose owner of every element `cairn explain` lists


def explained(c: Checker) -> dict[str, Any]:
    """What `cairn explain` says of each declared layout: its facts; for a spread over a tile of at most SHOWN
    elements, the participant that holds each element, row by row; and what moving values held by one spread into
    another over the same storage needs (`conversion`)."""
    out = {}
    spreads = {name: v for name in sorted(c.p.layouts) if isinstance(v := value(c, name), Spread)}
    for name, facts_ in receipt(c).items():
        v = value(c, name)
        if isinstance(v, Spread):
            if len(v.tile.shape) == 2 and v.tile.size <= SHOWN:
                facts_["owners"] = owners(v)
            into = {other: conversion(v, w) for other, w in spreads.items() if other != name and shares(v, w)}
            if into:
                facts_["conversions"] = into
        out[name] = facts_
    return out


# In code: `L.at(r, c)`, `D.row(t, v)`, `D.col(t, v)`, `D.at(t, v)`, and the counts ------------------------------

QUERIES = {"size", "cosize", "participants", "values", "extent"}  # constants: each folds to its literal
COORDINATES = {"at", "row", "col"}  # usize values computed at run time from checked coordinates


def declared(c: Checker, n: str) -> tuple[str, str] | None:
    """(the layout, the method) when a call's name is `L.method` for a declared layout L no local hides."""
    owner, _, name = n.rpartition(".")
    if not owner or owner.split(".")[0] in c.env:
        return None
    layout = c.qualify(owner, c.p.layouts)
    return (layout, name) if layout else None


def query(c: Checker, e: Expr) -> int | None:
    """The value of `L.size()`, `L.cosize()`, `D.participants()`, `D.values()` or `L.extent(k)`, or None when e is
    not one: a constant, which constant folding takes too."""
    found = declared(c, e.val) if e.tag == "call" else None
    if found is None or found[1] not in QUERIES:
        return None
    v, name = value(c, found[0]), found[1]
    tile = v.tile if isinstance(v, Spread) else v
    if name == "extent":
        if len(e.args) != 1:
            fail("E-ARITY", "extent takes the dimension it measures.", e)
        k = natural(c, e.args[0], 0)
        if k >= len(tile.shape):
            fail("E-LAYOUT", f"{found[0]} has {len(tile.shape)} dimensions; there is no dimension {k}.", e)
        return tile.shape[k]
    if e.args:
        fail("E-ARITY", f"{name} takes no arguments.", e)
    if name in {"size", "cosize"}:
        return tile.size if name == "size" else tile.cosize
    if not isinstance(v, Spread):
        fail("E-LAYOUT", f"{found[0]} is a storage layout; {name} counts a spread's.", e)
    return v.count if name == "participants" else v.each


def method(c: Checker, e: Expr, n: str, args: list[Expr]) -> Type | None:
    """`L.at(...)` and the other questions a layout answers in code; None when `n` names no layout's method.
    A coordinate or participant outside its layout traps, as an index outside a view does."""
    found = declared(c, n)
    if found is None:
        return None
    layout, name = found
    if name in QUERIES:
        known = query(c, e)
        e.tag, e.val, e.args, e.ref = "int", str(known), [], None
        return USIZE
    v = value(c, layout)
    if name not in COORDINATES:
        fail("E-CALLEE", f"A layout answers {', '.join(sorted(COORDINATES | QUERIES))}; {name} is none of them.", e)
    if name in {"row", "col"} and not (isinstance(v, Spread) and len(v.tile.shape) == 2):
        fail("E-LAYOUT", f"{name} is a coordinate a spread over a 2-D tile gives a participant's value.", e)
    rank = 2 if isinstance(v, Spread) else len(v.dims)
    if len(args) != rank:
        takes = "a participant and one of its values" if isinstance(v, Spread) else f"{rank} coordinates"
        fail("E-ARITY", f"{layout}.{name} takes {takes}.", e)
    for a in args:
        c.expect(c.expr(a, USIZE), USIZE, a)
    if isinstance(v, Layout):
        consumer(c, v, layout, args, e)
    c.guard("layout")
    e.ref = ("layout", layout, name)
    return USIZE


def consumer(c: Checker, v: Layout, layout: str, args: list[Expr], node: Any):
    """`L.at(D.row(t, v), D.col(t, v))` reads D's tile through L: the two must lay out one shape."""
    spreads = {a.ref[1] for a in args if isinstance(a.ref, tuple) and a.ref[:1] == ("layout",) and a.ref[2] != "at"}
    for name in sorted(spreads):
        d = value(c, name)
        if isinstance(d, Spread) and d.tile.shape != v.shape:
            fail("E-LAYOUT-CONSUMER", f"{name} spreads a {' x '.join(map(str, d.tile.shape))} tile, and {layout} "
                 f"lays out {' x '.join(map(str, v.shape))}: its coordinates name no element of {layout}.",
                 node)  # fmt: skip


def apply(c: Checker, e: Expr, given: tuple[Any, ...]) -> int | None:
    """What a checked `L.at(...)`, `D.row(t, v)`, `D.col(t, v)` or `D.at(t, v)` gives for these argument values,
    for a rule that runs a body with numbers (compiler/phases.py): None when an argument is not a number, and
    IndexError when one is outside its extent, where the program traps."""
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in given):
        return None
    _, layout, name = e.ref
    v = value(c, layout)
    limits = v.shape if isinstance(v, Layout) else (v.count, v.each)
    if any(not 0 <= x < n for x, n in zip(given, limits, strict=True)):
        raise IndexError(f"{layout}.{name}{tuple(given)} is outside {limits}")
    if isinstance(v, Layout):
        return v.offset(tuple(given))
    at = v.coords(*given)
    return at[0] if name == "row" else at[1] if name == "col" else v.tile.offset(at)


# Lowering --------------------------------------------------------------------------------------------------------


def places(x: str, modes: Any, k: int | None = None) -> list[str]:
    """The terms one coordinate adds: each of its places in the modes, by a constant division and remainder (the
    last place needs no remainder, since the coordinate is inside the shape), times that mode's stride."""
    terms, below = [], 1
    for j, (extent, stride) in enumerate(modes):
        step = stride if k is None else stride[k]
        place = f"({x} / {below})" if below > 1 else x
        place = place if j == len(modes) - 1 else f"({place} % {extent})"
        if step and extent > 1:
            terms.append(place if step == 1 else f"{place} * {step}")
        below *= extent
    return terms


def lower_offset(v: Layout, coords: list[str]) -> str:
    """The C++ that computes the offset of the coordinate `coords` under v."""
    o = " + ".join(t for x, modes in zip(coords, v.dims, strict=True) for t in places(x, modes)) or "0"
    return f"cr::layout::swizzle<{', '.join(map(str, v.swizzle))}>({o})" if v.swizzle[0] else o


def lower_coords(d: Spread, t: str, v: str) -> list[str]:
    """Each coordinate of participant t's value v, reduced modulo the tile only where some pair would pass it."""
    reach = [0] * len(d.tile.shape)
    for extent, stride in (*d.participants, *d.values):
        reach = [r + (extent - 1) * s for r, s in zip(reach, stride, strict=True)]
    out = []
    for k, n in enumerate(d.tile.shape):
        total = " + ".join([*places(t, d.participants, k), *places(v, d.values, k)]) or "0"
        out.append(f"(({total}) % {n})" if reach[k] >= n else f"({total})")
    return out


def lower(g: Any, e: Expr) -> str:
    """`L.at(...)`, `D.row(t, v)`, `D.col(t, v)`, `D.at(t, v)`: each argument checked against its extent once,
    then the arithmetic the layout's modes and swizzle spell."""
    g.need("cairn_layout.hpp")
    _, layout, name = e.ref
    v = g.c.folded[layout]
    limits = v.shape if isinstance(v, Layout) else (v.count, v.each)
    names = [f"x{k}" for k in range(len(limits))]
    if isinstance(v, Layout):
        body = lower_offset(v, names)
    else:
        coords = lower_coords(v, *names)
        body = coords[0] if name == "row" else coords[1] if name == "col" else lower_offset(v.tile, coords)
    given = ", ".join(f"cr::layout::within({g.expr(a)}, {n})" for a, n in zip(e.args, limits, strict=True))
    return f"[](std::size_t {', std::size_t '.join(names)}) noexcept {{ return std::size_t({body}); }}({given})"

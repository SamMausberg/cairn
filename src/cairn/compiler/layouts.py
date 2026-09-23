"""Layouts: where each logical element of a tile is stored, and which participant holds it.

`layout NAME = expression;` declares a compile-time object that the checker evaluates here and nowhere else. A
storage layout maps a logical coordinate to an element offset: `rows(R, C)`, `cols(R, C)`, `strided(R, C, SR, SC)`,
and what `pad`, `swizzle`, `transpose` and `tile` make of one. A spread maps a participant and one of its values to
a coordinate of a tile: `spread(L, TR, TC, VR, VC)` gives TR x TC participants, numbered row by row, a VR x VC block
of L each, and repeats that block of blocks down and across L. From the data alone the checker answers what a
kernel's author would otherwise compute by hand:

- which participant holds an element, and which of its values it is (`owner`);
- whether a spread covers its tile exactly once, else the first element it leaves out or holds twice (`cover`);
- which of a participant's values sit side by side in memory, and so move in one access (`runs`);
- how many ways the lanes of a warp split over shared memory's 32 banks (`conflicts`);
- what moving values held one way into a spread of the same tile needs: nothing, a permutation of each
  participant's registers, a warp shuffle, or a round trip through shared memory (`conversion`);
- whether a consumer that needs an affine layout (`affine`), or rows that move 16 bytes at a time (`rows16`), can
  read it.

Every answer comes from enumerating the layout, so it is exact at every size MAX_ELEMENTS admits.
`proofs/Cairn/Layout.lean` states the coverage rule and proves what a spread that passes it promises.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .tree import USIZE, Expr, Type, fail

if TYPE_CHECKING:
    from .checking import Checker

MAX_ELEMENTS = 1 << 18  # the most elements, or participant-value pairs, one layout may enumerate
WIDEST = 16  # bytes one thread moves in a single access
BANKS, WORD = 32, 4  # shared memory: 32 banks of 4-byte words, served 128 bytes a phase
Mode = tuple[int, int]  # (extent, stride): one digit of a logical dimension, fastest first


@dataclass(frozen=True)
class Layout:
    """A storage layout: each logical dimension is a list of modes, fastest first, and a coordinate's digits in
    those modes, times their strides, sum to its offset; then the swizzle, if any, permutes the offset."""

    dims: tuple[tuple[Mode, ...], ...]
    swizzle: tuple[int, int, int] = (0, 0, 0)  # CuTe's Swizzle<B, M, S>; B == 0 is none

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(math.prod(e for e, _ in modes) for modes in self.dims)

    @property
    def size(self) -> int:
        return math.prod(self.shape)

    def offset(self, coords: tuple[int, ...]) -> int:
        o = 0
        for x, modes in zip(coords, self.dims, strict=True):
            for extent, stride in modes:
                o += (x % extent) * stride
                x //= extent
        return swizzled(o, self.swizzle)

    def table(self) -> list[int]:
        """Every element's offset, in logical order: row by row, the last dimension fastest."""
        return [self.offset(unravel(e, self.shape)) for e in range(self.size)]

    @property
    def cosize(self) -> int:
        """The elements an array needs to hold every offset: one past the largest."""
        return max(self.table(), default=-1) + 1


@dataclass(frozen=True)
class Spread:
    """A spread over `tile`: participant `t` and value `v` name the coordinate that the digits of `t` in
    `participants` and of `v` in `values`, times their stride vectors, sum to; with `wrap`, reduced modulo the
    tile's shape, so participants past the tile hold its elements again."""

    tile: Layout
    participants: tuple[tuple[int, tuple[int, ...]], ...]
    values: tuple[tuple[int, tuple[int, ...]], ...]
    wrap: bool = True

    @property
    def count(self) -> int:
        return math.prod(e for e, _ in self.participants)

    @property
    def each(self) -> int:
        return math.prod(e for e, _ in self.values)

    def coords(self, t: int, v: int) -> tuple[int, ...]:
        at = [0] * len(self.tile.shape)
        for digits, modes in ((t, self.participants), (v, self.values)):
            for extent, stride in modes:
                d = digits % extent
                digits //= extent
                at = [a + d * s for a, s in zip(at, stride, strict=True)]
        return tuple(a % n for a, n in zip(at, self.tile.shape, strict=True)) if self.wrap else tuple(at)


Value = Layout | Spread


@dataclass
class Cover:
    """What a spread holds of its tile: `holders[e]` lists the (participant, value) pairs at element e."""

    spread: Spread
    holders: list[list[tuple[int, int]]] = field(default_factory=list)

    @property
    def gap(self) -> int | None:
        return next((e for e, h in enumerate(self.holders) if not h), None)

    @property
    def overlap(self) -> int | None:
        return next((e for e, h in enumerate(self.holders) if len(h) > 1), None)


def unravel(e: int, shape: tuple[int, ...]) -> tuple[int, ...]:
    """The coordinate of element number e, counting row by row with the last dimension fastest."""
    out = []
    for n in reversed(shape):
        out.append(e % n)
        e //= n
    return tuple(reversed(out))


def swizzled(o: int, swizzle: tuple[int, int, int]) -> int:
    """CuTe's Swizzle<B, M, S>: bits M + S to M + S + B of the offset flip bits M to M + B."""
    bits, base, shift = swizzle
    return o ^ ((o >> shift) & (((1 << bits) - 1) << base)) if bits else o


def rows(r: int, c: int) -> Layout:
    return Layout((((r, c),), ((c, 1),)))


def cols(r: int, c: int) -> Layout:
    return Layout((((r, 1),), ((c, r),)))


def strided(r: int, c: int, sr: int, sc: int) -> Layout:
    return Layout((((r, sr),), ((c, sc),)))


def spread(tile: Layout, tr: int, tc: int, vr: int, vc: int) -> Spread:
    """TR x TC participants, numbered row by row, each holding a VR x VC block; the TR*VR x TC*VC block of blocks
    repeats down and across the tile as many whole times as fit, and at least once."""
    r, c = tile.shape
    reps_r, reps_c = max(1, r // (tr * vr)), max(1, c // (tc * vc))
    participants = ((tc, (0, vc)), (tr, (vr, 0)))
    values = ((vc, (0, 1)), (vr, (1, 0)), (reps_c, (0, tc * vc)), (reps_r, (tr * vr, 0)))
    return Spread(tile, participants, values)


def transposed(v: Value) -> Value:
    if isinstance(v, Spread):
        flip = tuple((e, (s[1], s[0])) for e, s in v.participants), tuple((e, (s[1], s[0])) for e, s in v.values)
        return Spread(transposed(v.tile), *flip, v.wrap)  # type: ignore[arg-type]
    return Layout(v.dims[::-1], v.swizzle)


def split(modes: tuple[Mode, ...], inner: int) -> tuple[tuple[Mode, ...], tuple[Mode, ...]] | None:
    """The modes of one dimension cut into its first `inner` values and the rest, or None where no mode boundary
    or divisor falls there."""
    head: list[Mode] = []
    rest = list(modes)
    while inner > 1:
        if not rest:
            return None
        extent, stride = rest.pop(0)
        if extent <= inner and inner % extent == 0:
            head.append((extent, stride))
            inner //= extent
        elif extent % inner == 0:
            head.append((inner, stride))
            rest.insert(0, (extent // inner, stride * inner))
            inner = 1
        else:
            return None
    return tuple(head) or ((1, 0),), tuple(rest) or ((1, 0),)


def tiled(v: Layout, tr: int, tc: int, node: Any) -> Layout:
    """L seen as a grid of TR x TC tiles: coordinate (i, j, r, c) is element (i * TR + r, j * TC + c) of L."""
    r, c = v.shape
    if r % tr or c % tc:
        fail("E-LAYOUT", f"tile({tr}, {tc}) cuts a {r} x {c} layout into whole tiles only; {tr} x {tc} does not "
             "divide it.", node)  # fmt: skip
    rows_, cols_ = split(v.dims[0], tr), split(v.dims[1], tc)
    if rows_ is None or cols_ is None:
        fail("E-LAYOUT", f"tile({tr}, {tc}) cuts each dimension where one of its modes ends or divides; this "
             "layout's modes do not fall there.", node)  # fmt: skip
    return Layout((rows_[1], cols_[1], rows_[0], cols_[0]), v.swizzle)


# Evaluating a declaration --------------------------------------------------------------------------------------

ARITY = {"rows": 2, "cols": 2, "strided": 4, "pad": 2, "swizzle": 4, "transpose": 1, "tile": 3, "spread": 5}
LAYOUT_FIRST = {"pad", "swizzle", "transpose", "tile", "spread"}  # constructors that take a layout first
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
    enumerated = made.size if isinstance(made, Layout) else max(made.count * made.each, made.tile.size)
    if enumerated > MAX_ELEMENTS:
        fail("E-LAYOUT", f"The checker evaluates a layout of at most {MAX_ELEMENTS} elements; this one has "
             f"{enumerated}.", e)  # fmt: skip
    return made


def storage(inner: Value | None, what: str, node: Any) -> Layout:
    if not isinstance(inner, Layout):
        fail("E-LAYOUT", f"{what} takes a storage layout, and this is a spread.", node)
    return inner


def build(kind: str, inner: Value | None, n: list[int], node: Any) -> Value:
    if kind in {"rows", "cols", "strided"}:
        return rows(*n) if kind == "rows" else cols(*n) if kind == "cols" else strided(*n)
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


# The questions -------------------------------------------------------------------------------------------------


def cover(d: Spread) -> Cover:
    """Every element of the tile with the (participant, value) pairs that hold it; a coordinate past the tile,
    which only a spread without wrap can name, is refused."""
    shape = d.tile.shape
    found = Cover(d, [[] for _ in range(d.tile.size)])
    for t in range(d.count):
        for v in range(d.each):
            at = d.coords(t, v)
            if any(not 0 <= a < n for a, n in zip(at, shape, strict=True)):
                fail("E-LAYOUT", f"participant {t}'s value {v} falls at {at}, outside the {shape} tile.")
            e = 0
            for a, n in zip(at, shape, strict=True):
                e = e * n + a
            found.holders[e].append((t, v))
    return found


def exactly_once(d: Spread, node: Any, what: str = "a spread that writes"):
    """Refuse a spread that leaves an element of its tile to nobody (E-LAYOUT-GAP) or gives one to two holders
    (E-LAYOUT-OVERLAP): writes through it would miss that element, or race on it."""
    found, shape = cover(d), d.tile.shape
    if (e := found.overlap) is not None:
        (t1, v1), (t2, v2) = found.holders[e][:2]
        fail("E-LAYOUT-OVERLAP", f"Element {unravel(e, shape)} of the {' x '.join(map(str, shape))} tile is held "
             f"by participant {t1} (value {v1}) and participant {t2} (value {v2}); {what} must give each element "
             "one holder.", node)  # fmt: skip
    if (e := found.gap) is not None:
        fail("E-LAYOUT-GAP", f"No participant holds element {unravel(e, shape)} of the "
             f"{' x '.join(map(str, shape))} tile; {what} must cover it: {d.count} participants with {d.each} "
             f"values each hold {d.count * d.each} of its {d.tile.size} elements.", node)  # fmt: skip


def injective(v: Layout) -> tuple[int, int] | None:
    """The first two elements, by number, that share an offset, or None when every element has its own."""
    seen: dict[int, int] = {}
    for e, o in enumerate(v.table()):
        if o in seen:
            return seen[o], e
        seen[o] = e
    return None


def distinct(v: Layout, node: Any, what: str = "A layout that is written"):
    """Refuse a storage layout under which two elements share memory (E-LAYOUT-OVERLAP)."""
    if clash := injective(v):
        a, b = (unravel(e, v.shape) for e in clash)
        fail("E-LAYOUT-OVERLAP", f"{what} gives each element its own offset; elements {a} and {b} share offset "
             f"{v.offset(a)}.", node)  # fmt: skip


def owner(d: Spread, coords: tuple[int, ...]) -> list[tuple[int, int]]:
    """Which (participant, value) pairs hold the element at `coords`."""
    e = 0
    for a, n in zip(coords, d.tile.shape, strict=True):
        e = e * n + a
    return cover(d).holders[e]


def offsets(d: Spread) -> list[list[int]]:
    """Each participant's values, as the offsets its tile's storage gives them, in value order."""
    return [[d.tile.offset(d.coords(t, v)) for v in range(d.each)] for t in range(d.count)]


def runs(d: Spread, size: int) -> int:
    """The widest group of consecutive values, a power of two of at most 16 bytes, that every participant's values
    fall into: each group at consecutive offsets from one that is a multiple of its width. One such group is one
    access when the array starts on 16 bytes."""
    table = offsets(d)
    width = 1
    while width * 2 * size <= WIDEST and d.each % (width * 2) == 0:
        w = width * 2
        if not all(
            o[g] % w == 0 and all(o[g + k] == o[g] + k for k in range(w)) for o in table for g in range(0, d.each, w)
        ):
            break
        width = w
    return width


def conflicts(d: Spread, size: int, width: int = 1) -> int:
    """The most distinct 4-byte words one shared-memory bank serves at once, over every warp of participants and
    every access: 1 is conflict free. An access moves `width` values of `size` bytes, and 128 bytes go a phase."""
    table = offsets(d)
    moved = size * width
    phase = max(1, min(32, 128 // moved))
    worst = 1
    for warp in range(0, d.count, 32):
        for g in range(0, d.each, width):
            for first in range(warp, min(warp + 32, d.count), phase):
                banks: dict[int, set[int]] = {}
                for t in range(first, min(first + phase, warp + 32, d.count)):
                    start = table[t][g] * size
                    for word in range(start // WORD, (start + moved - 1) // WORD + 1):
                        banks.setdefault(word % BANKS, set()).add(word)
                worst = max(worst, *(len(words) for words in banks.values()))
    return worst


def conversion(held: Spread, wanted: Spread, warp: int = 32) -> str:
    """What values spread as `held` need to be spread as `wanted` instead: `none` when every participant holds the
    same element at the same value, `registers` when only the order of each participant's values changes,
    `shuffle` when every element stays inside its warp, `shared` when it crosses warps: a store, a barrier, a
    load."""
    if held.tile.shape != wanted.tile.shape:
        fail("E-LAYOUT-CONSUMER", f"A {held.tile.shape} spread cannot feed one over a {wanted.tile.shape} tile.")
    before, after = cover(held).holders, cover(wanted).holders
    if all(sorted(a) == sorted(b) for a, b in zip(before, after, strict=True)):
        return "none"
    if all({t for t, _ in a} == {t for t, _ in b} for a, b in zip(before, after, strict=True)):
        return "registers"
    if all({t // warp for t, _ in a} == {t // warp for t, _ in b} for a, b in zip(before, after, strict=True)):
        return "shuffle"
    return "shared"


def affine(v: Layout) -> tuple[str, int] | None:
    """How a consumer that takes a pointer and a leading dimension reads this layout: ("row", ld) when elements
    of a row are adjacent and rows ld apart, ("col", ld) the other way round, None when it is neither."""
    if v.swizzle[0] or len(v.dims) != 2 or any(len(m) != 1 for m in v.dims):
        return None
    ((_, sr),), ((_, sc),) = v.dims
    return ("row", sr) if sc == 1 else ("col", sc) if sr == 1 else None


def rows16(v: Layout, size: int) -> bool:
    """Every 16-byte run of a row, starting at a multiple of 16 bytes along it, lies at consecutive offsets from
    one on 16 bytes: what a load that takes each row's address (ldmatrix) needs."""
    run = WIDEST // size
    r, c = v.shape
    return c % run == 0 and all(
        v.offset((i, j)) % run == 0 and all(v.offset((i, j + k)) == v.offset((i, j)) + k for k in range(run))
        for i in range(r)
        for j in range(0, c, run)
    )


def facts(v: Value) -> dict[str, Any]:
    """What the checker knows of a layout, as the receipt and `cairn explain` report it."""
    if isinstance(v, Layout):
        clash = injective(v)
        form = affine(v)
        return {
            "kind": "storage",
            "shape": list(v.shape),
            "cosize": v.cosize,
            "modes": [[list(m) for m in modes] for modes in v.dims],
            "swizzle": list(v.swizzle) if v.swizzle[0] else None,
            "injective": clash is None,
            "affine": f"{form[0]}-major, leading dimension {form[1]}" if form else None,
        }
    found = cover(v)
    gap, overlap = found.gap, found.overlap
    covers = "exactly-once" if gap is None and overlap is None else "overlap" if overlap is not None else "gap"
    return {
        "kind": "spread",
        "participants": v.count,
        "values": v.each,
        "covers": covers,
        **({"first_gap": list(unravel(gap, v.tile.shape))} if gap is not None else {}),
        **({"first_overlap": list(unravel(overlap, v.tile.shape))} if overlap is not None else {}),
        "tile": facts(v.tile),
        "runs": {str(size): runs(v, size) for size in (1, 2, 4, 8)},
        "bank_ways": {str(size): conflicts(v, size) for size in (2, 4, 8)},
    }


def settle(c: Checker):
    """Evaluate every declared layout, so one that nothing uses is still held to its rules."""
    for name in c.p.layouts:
        value(c, name)


def receipt(c: Checker) -> dict[str, Any]:
    """What the build receipt says of each declared layout."""
    return {name: facts(value(c, name)) for name in sorted(c.p.layouts)}


def owners(d: Spread) -> list[list[int]]:
    """The participant holding each element of a 2-D tile, row by row; -1 where none does."""
    r, c = d.tile.shape
    held = cover(d).holders
    return [[held[i * c + j][0][0] if held[i * c + j] else -1 for j in range(c)] for i in range(r)]


SHOWN = 4096  # the largest tile whose owner of every element `cairn explain` lists


def explained(c: Checker) -> dict[str, Any]:
    """What `cairn explain` says of each declared layout: its facts, and for a spread over a tile of at most SHOWN
    elements, the participant that holds each element, row by row."""
    out = {}
    for name, facts_ in receipt(c).items():
        v = value(c, name)
        if isinstance(v, Spread) and len(v.tile.shape) == 2 and v.tile.size <= SHOWN:
            facts_["owners"] = owners(v)
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

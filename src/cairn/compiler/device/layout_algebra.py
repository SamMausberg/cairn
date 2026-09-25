"""Storage layouts and spreads as values, and what the checker answers from them.

A `Layout` maps a logical coordinate to an element offset: modes of (extent, stride) per dimension, then CuTe's
swizzle. A `Spread` maps a participant and one of its values to a coordinate of a tile. `layouts.py` builds these from
a `layout` declaration; everything here is a function of the value alone:

- which participant holds an element, and which of its values it is (`owner`);
- whether a spread covers its tile exactly once, else the first element it leaves out or holds twice (`cover`,
  `exactly_once`), and whether a storage layout gives each element its own offset (`injective`, `distinct`);
- which of a participant's values sit side by side in memory, and so move in one access (`runs`);
- how many ways the lanes of a warp split over shared memory's 32 banks (`conflicts`);
- what moving values held one way into a spread of the same tile needs: nothing, a permutation of each
  participant's registers, a warp shuffle, or a round trip through shared memory (`conversion`);
- whether a consumer that needs an affine layout (`affine`), or rows that move 16 bytes at a time (`rows16`), can
  read it;
- what a plan's `vector` and `stage` ask of a region's lanes (`moved`, `halo`).

Every answer comes from enumerating the layout, so it is exact at every size `layouts.MAX_ELEMENTS` admits.
`proofs/Cairn/Layout.lean` states the coverage rule and proves what a spread that passes it promises.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from ..syntax.tree import fail

WIDEST = 16  # bytes one thread moves in a single access
BANKS, WORD = 32, 4  # shared memory: 32 banks of 4-byte words, served 128 bytes a phase
Mode = tuple[int, int]  # (extent, stride): one digit of a logical dimension, fastest first


@dataclass(frozen=True)
class Layout:
    """A storage layout: each logical dimension is a list of modes, fastest first, and a coordinate's digits in
    those modes, times their strides, sum to its offset; then the swizzle, if any, permutes the offset. A value never
    changes, so what is worked out from it (its shape, size and cosize) is worked out once."""

    dims: tuple[tuple[Mode, ...], ...]
    swizzle: tuple[int, int, int] = (0, 0, 0)  # CuTe's Swizzle<B, M, S>; B == 0 is none

    @cached_property
    def shape(self) -> tuple[int, ...]:
        return tuple(math.prod(e for e, _ in modes) for modes in self.dims)

    @cached_property
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

    @cached_property
    def cosize(self) -> int:
        """The elements an array needs to hold every offset: one past the largest."""
        return max(self.table(), default=-1) + 1


@dataclass(frozen=True)
class Spread:
    """A spread over `tile`: participant `t` and value `v` name the coordinate that `origin` and the digits of `t`
    in `participants` and of `v` in `values`, times their stride vectors, sum to; with `wrap`, reduced modulo the
    tile's shape, so participants past the tile hold its elements again."""

    tile: Layout
    participants: tuple[tuple[int, tuple[int, ...]], ...]
    values: tuple[tuple[int, tuple[int, ...]], ...]
    wrap: bool = True
    origin: tuple[int, ...] = ()  # where participant 0's value 0 sits; the tile's first element when empty

    @cached_property
    def count(self) -> int:
        return math.prod(e for e, _ in self.participants)

    @cached_property
    def each(self) -> int:
        return math.prod(e for e, _ in self.values)

    def coords(self, t: int, v: int) -> tuple[int, ...]:
        at = list(self.origin or (0,) * len(self.tile.shape))
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


def inverse(v: Value, node: Any) -> Spread:
    """Who holds each element, as a spread of its own. `inverse(D)` of a spread over an R x C tile is a spread over
    the grid of D's (participant, value) pairs whose participant r and value c name the pair holding element (r, c):
    `INV.row(r, c)` is its participant, `INV.col(r, c)` its value. `inverse(L)` of a storage layout takes an offset,
    as its participant, to the element stored there: `INV.row(o, 0)` and `INV.col(o, 0)`. It exists where each mode
    moves one coordinate and the modes of each coordinate, taken by stride, count it in mixed radix."""
    if isinstance(v, Layout):
        if v.swizzle[0] or len(v.dims) != 2:
            fail("E-LAYOUT", "inverse takes a spread, or a 2-D storage layout with no swizzle.", node)
        digits = [(s_, e, (k, place)) for k, dim in enumerate(v.dims) for e, s_, place in placed(dim)]
        return Spread(v, counted(digits, v.size, "the offsets", node), ((1, (0, 0)),), wrap=False)
    if len(v.tile.shape) != 2:
        fail("E-LAYOUT", "inverse takes a spread over a 2-D tile.", node)
    per: list[list[tuple[int, int, tuple[int, int]]]] = [[], []]
    for side, group in enumerate((v.participants, v.values)):
        for extent, place, stride in ((e, pl, st) for (e, st), pl in zip(group, weights(group), strict=True)):
            moved = [k for k, x in enumerate(stride) if x]
            if extent > 1 and len(moved) != 1:
                fail("E-LAYOUT", "inverse needs every mode of a spread to move one coordinate.", node)
            if extent > 1:
                per[moved[0]].append((stride[moved[0]], extent, (side, place)))
    grid = rows(v.count, v.each)
    down, across = (counted(per[k], v.tile.shape[k], f"coordinate {k}", node) for k in (0, 1))
    return Spread(grid, down, across, wrap=False)


def weights(modes: Any) -> list[int]:
    """What one step of each mode is worth in the number its modes count, fastest first."""
    out, below = [], 1
    for extent, _ in modes:
        out.append(below)
        below *= extent
    return out


def placed(modes: Any) -> list[tuple[int, int, int]]:
    """(extent, stride, place) of each mode of one dimension of a storage layout."""
    return [(e, s_, pl) for (e, s_), pl in zip(modes, weights(modes), strict=True)]


def counted(digits: list, total: int, what: str, node: Any) -> tuple:
    """The modes that count `what` back: its digits, taken by stride, each worth its place in the pair (side 0 the
    participant or row, side 1 the value or column), in mixed radix up to `total`."""
    out, below = [], 1
    for stride, extent, (side, place) in sorted(digits, key=lambda d: d[0]):
        if stride != below:
            fail("E-LAYOUT", f"inverse needs {what} to be counted in mixed radix by the modes that move them; they "
                 f"step by {stride} where {below} is next.", node)  # fmt: skip
        out.append((extent, (place, 0) if side == 0 else (0, place)))
        below *= extent
    if below != total:
        fail("E-LAYOUT", f"inverse needs the modes to count all {total} of {what}; they count {below}.", node)
    return tuple(out) or ((1, (0, 0)),)


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


# The questions -------------------------------------------------------------------------------------------------


def outside(d: Spread) -> tuple[int, int, tuple[int, ...]] | None:
    """The first (participant, value) pair whose coordinate falls outside the tile, with that coordinate: only a
    spread without wrap can name one."""
    for t in range(d.count):
        for v in range(d.each):
            at = d.coords(t, v)
            if any(not 0 <= a < n for a, n in zip(at, d.tile.shape, strict=True)):
                return t, v, at
    return None


def cover(d: Spread) -> Cover:
    """Every element of the tile with the (participant, value) pairs that hold it; a coordinate past the tile is
    refused."""
    shape = d.tile.shape
    if (past := outside(d)) is not None:
        t, v, at = past
        fail("E-LAYOUT", f"participant {t}'s value {v} falls at {at}, outside the {shape} tile.")
    found = Cover(d, [[] for _ in range(d.tile.size)])
    for t in range(d.count):
        for v in range(d.each):
            at = d.coords(t, v)
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


def shares(held: Spread, wanted: Spread) -> bool:
    """Whether two spreads are over one tile, or over a tile and its transpose: then an element is its offset,
    whichever coordinates each spread names it by."""
    return held.tile == wanted.tile or transposed(held.tile) == wanted.tile


def conversion(held: Spread, wanted: Spread, warp: int = 32) -> str:
    """What values spread as `held` need to be spread as `wanted` instead: `none` when every participant holds the
    same element at the same value, `registers` when only the order of each participant's values changes,
    `shuffle` when every element stays inside its warp, `shared` when it crosses warps: a store, a barrier, a
    load. Over one piece of storage an element is its offset; otherwise the tiles must have one shape and an element
    is its coordinate."""
    if shares(held, wanted):
        pairs = [{o: sorted(h) for o, h in zip(d.tile.table(), cover(d).holders, strict=True)} for d in (held, wanted)]
        before, after = [pairs[0][o] for o in sorted(pairs[0])], [pairs[1][o] for o in sorted(pairs[0])]
    elif held.tile.shape != wanted.tile.shape:
        fail("E-LAYOUT-CONSUMER", f"A {held.tile.shape} spread cannot feed one over a {wanted.tile.shape} tile.")
    else:
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


def owners(d: Spread) -> list[list[int]]:
    """The participant holding each element of a 2-D tile, row by row; -1 where none does."""
    r, c = d.tile.shape
    held = cover(d).holders
    return [[held[i * c + j][0][0] if held[i * c + j] else -1 for j in range(c)] for i in range(r)]


# What a plan's `vector` and `stage` ask of a region's lanes ------------------------------------------------------


def moved(width: int, size: int) -> int:
    """How many adjacent elements of `size` bytes one access moves for a lane that runs `width` adjacent indices
    (compiler/plans/chunks.py): the runs of a spread of 32 lanes over a line, each holding `width` in a row."""
    return runs(spread(rows(1, 32 * width), 1, 32, 1, width), size)


def halo(offsets: set[int], radius: int, block: int = 32) -> bool:
    """Whether a block's tile, from `radius` before its first index to `radius` after its last, holds every element
    its lanes read at `[i + d]` for the offsets d (compiler/plans/staging.py): the reads, as a spread of the block's lanes
    over the offsets from the least to the greatest, stay inside the tile. It holds at every block width alike."""
    low, high = min(offsets), max(offsets)
    tile = rows(1, block + 2 * radius)
    reads = Spread(tile, ((block, (0, 1)),), ((high - low + 1, (0, 1)),), wrap=False, origin=(0, low + radius))
    return low + radius >= 0 and outside(reads) is None

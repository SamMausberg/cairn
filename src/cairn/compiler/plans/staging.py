"""Shared-memory staging: which arrays a device region's lanes may read from a tile its block loads once.

`plan f { stage R; }` asks each block of f's device regions to load, into shared memory, the elements of each array
its lanes read near their own index, from R before the block's first index to R after its last, and to read them
there. The plan asks; this module decides, from the checked tree, which arrays that is:

- no lane of the region writes the array, and nothing else can while the region runs (a device region finishes
  before the next statement, and a queued one holds its lease), so a tile loaded before the lanes read is what
  they would have read;
- every use of the array is an element `x[i]`, `x[i + d]` or `x[i - d]` for the lane's index `i` and a literal
  `d` of at most R, and the checker discharged its bounds guard, so the element lies inside the array and inside
  the tile; the tile loads only elements inside the array.

A region with no such array read at an offset is refused (E-PLAN): with every read at `[i]` the tile would be read
once and staging would change nothing. On the device the region runs tile by tile, the same for every thread of a
block: a barrier, the block's threads loading its tile, a barrier, each thread running the body at its index. Every
index runs exactly once and reads the value the array holds, so the result is the unplanned one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..device import layout_algebra
from ..lower import execution
from ..syntax.tree import Expr, Stmt, fail, is_view
from .chunks import ELEMENTS, assignments, exprs

if TYPE_CHECKING:
    from ..check.checking import Checker
    from ..lower.codegen import Emitter


def offset(e: Expr, binder: str) -> int | None:
    """d when e is `binder`, `binder + d`, `d + binder` or `binder - d` with d a literal; None otherwise."""
    if e.tag == "name" and e.val == binder:
        return 0
    if e.tag != "binary" or e.val not in {"+", "-"} or len(e.args) != 2:
        return None
    a, b = e.args
    if a.tag == "name" and a.val == binder and b.tag == "int":
        return int(b.val) if e.val == "+" else -int(b.val)
    if e.val == "+" and b.tag == "name" and b.val == binder and a.tag == "int":
        return int(a.val)
    return None


def stageable(s: Stmt, radius: int) -> dict[str, tuple[Any, Expr, set[int]]]:
    """The arrays region s may read from a tile of `radius` either side: each with its element, its view as the
    body names it, and the offsets its reads use."""
    binder = s.binder or s.name
    written = {name for name, _, write in s.touched if write}
    written |= {a.exprs[0].args[0].val for a in assignments(s.body) if a.exprs[0].tag == "index" and a.exprs[0].args}
    uses: dict[str, list[Expr]] = {}
    seen: dict[str, int] = {}
    for e in exprs(s.body):
        if e.tag == "name":
            seen[e.val] = seen.get(e.val, 0) + 1
        if e.tag == "index" and e.args[0].tag == "name":
            uses.setdefault(e.args[0].val, []).append(e)
    chosen: dict[str, tuple[Any, Expr, set[int]]] = {}
    for name, found in uses.items():
        view, element = found[0].args[0], found[0].ty
        offsets = {offset(u.args[1], binder) for u in found}
        if name in written or seen.get(name) != len(found) or not is_view(view.ty) or view.ty.place != "device":
            continue
        if None in offsets or element is None or element.name not in ELEMENTS or not all(u.established for u in found):
            continue
        if offsets == {0} or not layout_algebra.halo({d for d in offsets if d is not None}, radius):
            continue  # every read at [i]: nothing to share; or a read the block's tile would not hold
        chosen[name] = (element, view, {d for d in offsets if d is not None})
    return chosen


def staged(c: Checker, s: Stmt, radius: int, token: Any):
    """Choose what `stage R` loads for the device region s, or refuse the plan."""
    if s.fuse or s.vector:
        fail("E-PLAN", "stage loads one region's tiles; fuse and vector reshape the region, and a plan takes one.",
             token)  # fmt: skip
    chosen = stageable(s, radius)
    if not chosen:
        fail("E-PLAN", f"stage {radius} loads a tile of an array the region only reads, each read at [i + d] with |d| at "
             f"most {radius} and its guard discharged, and at least one at d other than 0; this region reads none "
             "that way, so the plan would change nothing.", token)  # fmt: skip
    s.stage = radius
    s.staged = tuple((name, element, view) for name, (element, view, _) in sorted(chosen.items()))


def lower(g: Emitter, s: Stmt, extent: str, body, schedule: list[int], unroll: int):
    """The launch of a staged region: one lambda that loads a block's tiles, one that runs the body at an index
    reading staged arrays from them, the bytes a block's tiles take, and the kernel that runs them tile by tile."""
    radius, binder = s.stage, s.binder or s.name
    arrays = [(name, g.type(element), g.pointer(view)) for name, element, view in s.staged]

    def carve(const: str):
        at = "0"
        for name, ty, _ in arrays:
            g.put(f"{const}{ty}* const cr_s_{name} = reinterpret_cast<{const}{ty}*>(cr_shared + {at});")
            at = f"{at} + cr::gpu::tile_bytes<{ty}>(cr_w)"

    def load():
        carve("")
        for name, _, (data, count) in arrays:
            g.nest("for (std::size_t cr_e = cr_t; cr_e < cr_w; cr_e += cr_b) {", lambda d=data, c=count, n=name: g.puts(
                f"const std::size_t cr_g = cr_base + cr_e;  // element cr_g - {radius}, loaded only inside the array",
                f"if (cr_g >= {radius} && cr_g - {radius} < {c}) cr_s_{n}[cr_e] = {d}[cr_g - {radius}];"))  # fmt: skip

    def run():
        carve("const ")
        saved = dict(g.staged)
        g.staged |= {name: (f"cr_s_{name}", radius) for name, _, _ in arrays}
        body()
        g.staged = saved

    head = "[=] CR_DEVICE(std::size_t cr_base, std::size_t cr_w, std::size_t cr_t, std::size_t cr_b, unsigned char* cr_shared)"
    loads = g.inner(lambda: head, load)
    lanes = g.inner(lambda: f"[=] CR_DEVICE(std::size_t v_{binder}, std::size_t cr_base, std::size_t cr_w, "
                    "unsigned char* cr_shared)", run)  # fmt: skip
    bytes_ = " + ".join(f"cr::gpu::tile_bytes<{ty}>(cr_w)" for _, ty, _ in arrays)
    size = g.inner(lambda: "[](std::size_t cr_w)", lambda: g.put(f"return {bytes_};"))
    arguments = [extent, loads, lanes, size, *map(str, schedule)]
    g.put(execution.call("run_staged", arguments, [radius, *execution.unrolled(unroll)]) + ";")

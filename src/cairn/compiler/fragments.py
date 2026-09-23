"""Tensor-core fragments: a warp's operand A, operand B or accumulator, and what a warp does with them.

    let mut acc = WmmaAcc[f32, 16, 16, 16](0.0);       // every element 0
    let a = mma_load[WmmaA[f16, 16, 16, 16]](as, SA, i, q);   // fragment (i, q) of the shared tile `as`, laid out by SA
    let b = mma_load[WmmaB[f16, 16, 16, 16]](bs, SB, q, j);
    acc = mma_unordered(acc, a, b);                      // acc + a * b, its sums in the hardware's order
    mma_store(cs, SC, i, j, acc);

A fragment type names its family, its role, its element and its shape `M, N, K`: `WmmaA`, `WmmaB` and `WmmaAcc`
(nvcuda::wmma: 16 x 16 x 16, 32 x 8 x 16 or 8 x 32 x 16), `MmaA`, `MmaB` and `MmaAcc` (PTX mma.sync: 16 x 8 x 16),
and `TmemAcc`, an accumulator in tcgen05 tensor memory. Operands hold f16 or bf16, and an accumulator f32. A is
M x K, B is K x N, the accumulator M x N, and the coordinates a load or a store takes count whole fragments of the
tile, so fragment (i, j) of an A tile starts at element (i * M, j * K).

A fragment is a warp's value: every thread of the warp names the same matrix, so every fragment operation is a warp
operation, legal only where each warp of a cooperative block reaches it whole (compiler/cooperative.py, E-COOP-WARP),
and nowhere outside one (E-FRAGMENT). The tile is a shared array of the block, or a device view, and the layout the
operation names says where each element of it lives (compiler/layouts.py). Each family reads what it can:

- WMMA takes a pointer and a leading dimension: an operand's layout must be row-major with its rows a multiple of 16
  bytes apart, an accumulator's row- or column-major with the same, and a swizzled tile is refused
  (E-LAYOUT-CONSUMER);
- mma.sync reads a shared tile with ldmatrix, one row address a lane: every 16 bytes of a row that starts on 16
  bytes must stay together, which a swizzle of 16-byte runs keeps and a pad that is not a multiple of 16 bytes
  breaks (E-LAYOUT-CONSUMER). A device view it reads element by element, from any layout.

The layout's shape must hold whole fragments. The coordinates are checked against it at run time, as a layout's
coordinates are, and so are the array's length against every offset the layout places, where the checker cannot
know it, and the pointer's alignment where the family needs one: a guard, never undefined behaviour. Every lane of a
warp names one fragment together, so the coordinates, a fill's value, A and B, and a WMMA accumulator are the same
in every thread of a warp (E-COOP-WARP, from compiler/cooperative.py's `Reach`); an mma.sync accumulator may differ,
since each output element uses only the accumulator element its own lane holds.

`mma_unordered(acc, a, b)` is `acc + a * b` under the contract of the whole-matrix multiply (tensor.py): every
product exact in f32, every output its old value plus its K products, each partial sum rounded to f32 in an order
the hardware picks. The receipt lists it under `numerics`. On the host the sums run in increasing k, so a kernel
built of fragments gives there, bit for bit, what the reference loop gives.

Each family is a device capability the build's target must provide (projects/target.py): `wmma`, and `bf16` for a
bf16 operand, or `mma_sync`. `TmemAcc` needs `tcgen05`, which sm_120 does not have and for which nothing here
lowers, so it is refused (E-TARGET-FEATURE) rather than emulated. On the host every thread holds each fragment whole
and stores only the elements its lane holds on the device, so the threads of a warp write each element once.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from . import layouts
from .tree import USIZE, VOID, Expr, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

TYPES = {  # each fragment type: the device capability its family needs, and its role
    "WmmaA": ("wmma", "a"), "WmmaB": ("wmma", "b"), "WmmaAcc": ("wmma", "acc"),
    "MmaA": ("mma_sync", "a"), "MmaB": ("mma_sync", "b"), "MmaAcc": ("mma_sync", "acc"),
    "TmemAcc": ("tcgen05", "acc"),
}  # fmt: skip
SHAPES = {"wmma": {(16, 16, 16), (32, 8, 16), (8, 32, 16)}, "mma_sync": {(16, 8, 16)}, "tcgen05": {(128, 256, 16)}}
OPERANDS = {"f16", "bf16"}
BOUND = "(k + 1) * 2^-22 * (|c| + sum |a * b|)"  # the whole-matrix multiply's bound, for each output's k products
OPERATIONS = {"mma_load", "mma_store"}  # what moves a fragment through a tile, which the phase rule records


def fragment(ty: Type | None) -> tuple[str, str, str, tuple[int, int, int]] | None:
    """(family, role, element, (M, N, K)) of a fragment type, or None."""
    if ty is None or ty.mode != "value" or ty.name not in TYPES or len(ty.args) != 4:
        return None
    family, role = TYPES[ty.name]
    element, *shape = ty.args
    return family, role, element.name, (int(shape[0]), int(shape[1]), int(shape[2]))


def extent(role: str, shape: tuple[int, int, int]) -> tuple[int, int]:
    """The rows and columns of a fragment: A is M x K, B is K x N, the accumulator M x N."""
    m, n, k = shape
    return (m, k) if role == "a" else (k, n) if role == "b" else (m, n)


def valid(ty: Type, node: Any) -> tuple[str, str, str, tuple[int, int, int]]:
    """Refuse a fragment type the families do not have."""
    found = fragment(ty)
    assert found is not None
    family, role, element, shape = found
    if family == "tcgen05":
        from ..projects.target import FEATURES, needs

        fail("E-TARGET-FEATURE", f"{ty.name} keeps its accumulator in {FEATURES['tcgen05'].what}; {needs('tcgen05')}, "
             "and this compiler lowers it for no target, so it is refused rather than emulated.", node,
             feature="tcgen05")  # fmt: skip
    if shape not in SHAPES[family]:
        shapes = ", ".join(" x ".join(map(str, s)) for s in sorted(SHAPES[family]))
        fail("E-FRAGMENT", f"{ty.name} is one of the shapes {family} has: M, N, K of {shapes}.", node)
    wanted = {"f32"} if role == "acc" else OPERANDS
    if element not in wanted:
        fail("E-FRAGMENT", f"{ty.name} holds {' or '.join(sorted(wanted))}: operands are f16 or bf16, and they "
             "accumulate in f32.", node)  # fmt: skip
    return found


def warp(c: Checker, node: Any, what: str):
    """A fragment operation is a warp operation: legal only where each warp of a cooperative block reaches it."""
    if getattr(c, "coop", None) is None:
        fail("E-FRAGMENT", f"{what} is a warp operation on a tensor-core fragment: write it inside a cooperative "
             "region, `blocks ... threads ... { }`, where each warp reaches it whole.", node)  # fmt: skip
    cooperative = import_module(".cooperative", __package__)  # the cooperative layer, which sets c.coop
    cooperative.collective(c, node, cooperative.WARP, what, "E-COOP-WARP")


def uniform(c: Checker, args: list[Expr], what: str):
    """Refuse an argument of a warp operation that may differ between the threads of one warp: every lane passes
    it, and the hardware takes the warp's one fragment from all of them together."""
    cooperative = import_module(".cooperative", __package__)
    for a in args:
        level, why = c.coop.levels.get(id(a), (cooperative.THREAD, None))
        if level > cooperative.WARP:
            line = getattr(why, "line", 0) or a.line
            fail("E-COOP-WARP", f"{what} must be the same in every thread of a warp, and the value at line {line} "
                 "differs from thread to thread: the lanes of a warp name one fragment together. Compute it from the "
                 "warp's number (t / 32), the block's names and constants.", a)  # fmt: skip


def check_fill(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`WmmaAcc[f32, 16, 16, 16](0.0)`: an accumulator with every element one f32 value."""
    ty = c.resolve(Type(e.val, args=targs), e) if targs else expected
    if ty is None or ty.name != e.val:
        fail("E-INFER", f"Write {e.val}[T, M, N, K](value).", e)
    _, role, _, _ = valid(ty, e)
    if role != "acc":
        fail("E-FRAGMENT", f"An operand is loaded from a tile: write mma_load[{ty.display()}](tile, LAYOUT, i, j).", e)
    warp(c, e, f"{e.val}(...)")
    if len(args) != 1:
        fail("E-ARITY", f"{e.val} takes the f32 value every element starts at.", e)
    c.expect(c.expr(args[0], Type("f32")), Type("f32"), args[0])
    uniform(c, args, f"The value {e.val}(...) fills its accumulator with")
    e.ref = ("builtin", ty)
    return ty


def tile(c: Checker, e: Expr, array: Expr, written: Expr, coords: list[Expr], ty: Type, write: bool) -> Any:
    """The array, its layout and the fragment coordinates a load or store names, checked against each other."""
    family, role, element, shape = valid(ty, e)
    name = layouts.named(c, written)
    v = layouts.value(c, name) if name else None
    if not isinstance(v, layouts.Layout) or len(v.shape) != 2:
        fail("E-LAYOUT-CONSUMER", "A fragment moves through a 2-D storage layout, named by its declaration; "
             "the second argument names none.", written)  # fmt: skip
    rows, cols = extent(role, shape)
    if v.shape[0] % rows or v.shape[1] % cols:
        fail("E-LAYOUT-CONSUMER", f"{name} lays out {v.shape[0]} x {v.shape[1]}, which does not hold whole "
             f"{rows} x {cols} fragments.", written)  # fmt: skip
    kind = c.expr(array, consume=False)
    want = "f32" if role == "acc" else element
    if not is_view(kind) or kind.name != want or (write and kind.mode != "rw"):
        fail("E-TYPE-MISMATCH", f"{ty.display()} moves through an {'rw ' if write else ''}array of {want}.", array)
    shared = root(array).tag == "name" and root(array).val in getattr(getattr(c, "coop", None), "shared", {})
    if not shared and kind.place not in {"device", "unified"}:
        fail("E-FRAGMENT", f"A fragment moves through a block's shared array or a device view; {root(array).val} "
             f"is {kind.place} memory.", array)  # fmt: skip
    if not shared and (write or kind.mode != "ro"):  # nothing in the region can then write what a fragment reads
        fail("E-FRAGMENT", "A fragment is stored into a block's shared array, and read from a shared array or a "
             f"read-only device view; {root(array).val} is {'written' if write else 'an rw view'}.", array)  # fmt: skip
    if kind.extent.isdigit() and v.cosize > int(kind.extent):
        fail("E-LAYOUT-CONSUMER", f"{name} places elements up to offset {v.cosize - 1}, past the {kind.extent} "
             f"elements of {root(array).val}.", array)  # fmt: skip
    size = 4 if role == "acc" else c.sizeof(Type(element))
    consumer(family, role, shared, v, name, size, (rows, cols), written)
    for a in coords:
        c.expect(c.expr(a, USIZE), USIZE, a)
    if root(array).tag == "name":
        c.effect(("write:" if write else "read:") + root(array).val)
    c.guard("fragment")  # the coordinates against the layout's grid of fragments, the pointer's alignment
    return family, role, element, shape, name, shared


def consumer(
    family: str, role: str, shared: bool, v: layouts.Layout, name: str, size: int, grid: tuple[int, int], node: Any
):
    """Whether this family's loads and stores can read `v`: WMMA through a pointer and a leading dimension, mma.sync
    from shared memory through ldmatrix's row addresses, anything else element by element."""
    if family == "wmma":
        form = layouts.affine(v)
        if form is None or (role != "acc" and form[0] != "row"):
            how = "row-major" if role != "acc" else "row- or column-major"
            fail("E-LAYOUT-CONSUMER", f"WMMA reads a fragment through a pointer and a leading dimension, so {name} "
                 f"must be {how} with no swizzle; use the mma.sync fragments, whose loads take a row address a "
                 "lane, or an unswizzled layout.", node)  # fmt: skip
        if form[1] * size % 16:
            fail("E-LAYOUT-CONSUMER", f"WMMA needs rows a multiple of 16 bytes apart; {name}'s are "
                 f"{form[1] * size} bytes apart.", node)  # fmt: skip
        rows, cols = grid
        for i in range(v.shape[0] // rows):
            for j in range(v.shape[1] // cols):
                if v.offset((i * rows, j * cols)) * size % 32:
                    fail("E-LAYOUT-CONSUMER", f"WMMA reads a fragment from 32 bytes on, and fragment ({i}, {j}) "
                         f"of {name} starts {v.offset((i * rows, j * cols)) * size} bytes in.", node)  # fmt: skip
    elif family == "mma_sync" and shared and role != "acc" and not layouts.rows16(v, size):
        fail("E-LAYOUT-CONSUMER", f"ldmatrix reads each 16 bytes of a row from one address; {name} splits a run of "
             "16 bytes, or starts one off 16 bytes: keep a swizzle's base at 16 bytes and a pad a multiple of 16 "
             "bytes.", node)  # fmt: skip


def footprint(c: Checker, e: Expr, i: int, j: int) -> list[tuple[int, int | None]]:
    """The elements a fragment load or store at fragment coordinates (i, j) touches, each with the one lane that
    writes it (a store: its holder under mma.sync, which the phase rule uses only for that family) or None (a load:
    every lane of the warp reads every element on the host). The phase rule records them per thread
    (compiler/phases.py)."""
    _, _, (_, role, _, shape, name, _) = e.ref
    v = layouts.value(c, name)
    assert isinstance(v, layouts.Layout)  # a fragment moves only through a storage layout (`tile`)
    rows, cols = extent(role, shape)
    if not (0 <= i < v.shape[0] // rows and 0 <= j < v.shape[1] // cols):
        raise IndexError(f"fragment ({i}, {j}) is outside {name}'s grid of {rows} x {cols} fragments")
    store = e.val == "mma_store"
    return [(v.offset((i * rows + r, j * cols + q)), (r % 8) * 4 + (q % 8) // 2 if store else None)
            for r in range(rows) for q in range(cols)]  # fmt: skip


def check_load(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`mma_load[WmmaA[f16, 16, 16, 16]](tile, LAYOUT, i, j)`: fragment (i, j) of the tile."""
    ty = c.resolve(targs[0], e) if len(targs) == 1 else expected
    if fragment(ty) is None:
        fail(
            "E-INFER",
            "Write mma_load[F](tile, LAYOUT, i, j) with F a fragment type, as mma_load[WmmaA[f16, 16, 16, 16]].",
            e,
        )
    assert ty is not None
    if len(args) != 4:
        fail(
            "E-ARITY",
            "mma_load takes the tile, its layout and the fragment's coordinates: mma_load[F](tile, L, i, j).",
            e,
        )
    e.ref = ("builtin", ty, tile(c, e, args[0], args[1], args[2:], ty, False))
    warp(c, e, "mma_load")
    uniform(c, args[2:], "mma_load's fragment coordinate")
    return ty


def check_store(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`mma_store(tile, LAYOUT, i, j, acc)`: the accumulator into fragment (i, j) of the tile."""
    if len(args) != 5:
        fail("E-ARITY", "mma_store takes the tile, its layout, the fragment's coordinates and the accumulator.", e)
    ty = c.expr(args[4])
    found = fragment(ty)
    if found is None or found[1] != "acc":
        fail("E-TYPE-MISMATCH", f"mma_store writes an accumulator; {ty.display()} is not one.", args[4])
    e.ref = ("builtin", ty, tile(c, e, args[0], args[1], args[2:4], ty, True))
    warp(c, e, "mma_store")
    uniform(c, args[2:4], "mma_store's fragment coordinate")
    if found[0] == "wmma":  # which lane holds which element is unspecified, so every lane holds the same matrix
        uniform(c, args[4:], "A WMMA accumulator")
    return VOID


def owned(c: Checker, e: Expr, acc: Expr, value: Expr) -> Type:
    """The accumulator and the value index of `mma_get` and `mma_set`: an mma.sync accumulator, whose lanes hold
    the elements the PTX ISA says, the value below how many each lane holds."""
    ty = c.expr(acc)
    found = fragment(ty)
    if found is None or found[1] != "acc":
        fail("E-TYPE-MISMATCH", f"{e.val} reaches one element of an accumulator; {ty.display()} is not one.", acc)
    if found[0] != "mma_sync":
        fail("E-FRAGMENT", f"{e.val} reaches the element a lane holds, and {ty.name} leaves which lane holds which "
             "element unspecified; use an mma.sync accumulator (MmaAcc), whose share the PTX ISA states: lane l's "
             "value v is element (l / 4 + 8 * (v / 2), 2 * (l % 4) + v % 2).", e)  # fmt: skip
    if getattr(c, "coop", None) is None:
        fail("E-FRAGMENT", f"{e.val} reaches the element this thread's lane holds, and only a thread of a "
             "cooperative region has a lane: write it inside `blocks ... threads ... { }`.", e)  # fmt: skip
    c.expect(c.expr(value, USIZE), USIZE, value)
    c.guard("fragment")  # the value index against the four a lane holds
    return ty


def check_get(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`mma_get(acc, v)`: this thread's lane's value v of an mma.sync accumulator."""
    if len(args) != 2:
        fail("E-ARITY", "mma_get takes an accumulator and one of the values the thread's lane holds.", e)
    e.ref = ("builtin", owned(c, e, args[0], args[1]))
    return Type("f32")


def check_set(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`acc = mma_set(acc, v, x)`: the accumulator with this thread's lane's value v replaced by x."""
    if len(args) != 3:
        fail("E-ARITY", "mma_set takes an accumulator, one of the values the thread's lane holds, and an f32.", e)
    ty = owned(c, e, args[0], args[1])
    c.expect(c.expr(args[2], Type("f32")), Type("f32"), args[2])
    e.ref = ("builtin", ty)
    return ty


def check_mma(c: Checker, e: Expr, args: list[Expr]) -> Type:
    """`mma_unordered(acc, a, b)`: acc + a * b, one warp's tensor-core step."""
    from .builtins import contract

    types = [c.expr(a) for a in args]
    found = [fragment(t) for t in types]
    if any(f is None for f in found):
        fail("E-MMA", "mma_unordered of three arguments multiplies fragments: an accumulator, then A, then B.", e)
    acc, a, b = found
    if [acc[1], a[1], b[1]] != ["acc", "a", "b"] or len({acc[0], a[0], b[0]}) != 1 or acc[3] != a[3] or a[3] != b[3]:
        fail("E-MMA", "mma_unordered(acc, a, b) takes an accumulator, an A and a B of one family and one shape, in "
             "that order.", e)  # fmt: skip
    if a[2] != b[2]:
        fail("E-MMA", f"A and B hold one format; this A holds {a[2]} and this B {b[2]}.", e)
    warp(c, e, "mma_unordered")
    uniform(c, args[1:], "An A or B fragment")
    if acc[0] == "wmma":
        uniform(c, args[:1], "A WMMA accumulator")
    contract(c, e, "mma", a[2], "f32", rounding="unordered-f32", products="exact-in-f32", bound=BOUND,
             k=str(a[3][2]))  # fmt: skip
    e.ref = ("builtin", types[0], "fragment")
    return types[0]


# Lowering -------------------------------------------------------------------------------------------------------------


def spelled(g: Emitter, ty: Type) -> str:
    family, role, element, (m, n, k) = fragment(ty)
    kind = "Wmma" if family == "wmma" else "Mma"
    held = "float" if role == "acc" else g.type(Type(element))
    return f"cr::frag::{kind}Fragment<cr::frag::Role::{role}, {held}, {m}, {n}, {k}>"


def need(g: Emitter, ty: Type):
    g.need("cairn_fragment.hpp")
    family, _, element, _ = fragment(ty)
    g.feature(family)
    if element == "bf16":
        g.feature("bf16")


def held(g: Emitter, array: Expr, name: str) -> str:
    """The tile's pointer, after a guard that its array holds every offset the layout places: a length the checker
    does not know is checked where the operation runs, on the host and in a device lane alike."""
    data, count = g.pointer(array)
    return f"cr::layout::holding({data}, {count}, {g.c.folded[name].cosize})"


def lower_fill(g: Emitter, e: Expr) -> str:
    need(g, e.ty)
    return f"cr::frag::filled<{spelled(g, e.ty)}>({g.expr(e.args[0])})"


def offset(g: Emitter, e: Expr, name: str, role: str, shape: tuple[int, int, int], coords: list[Expr]) -> str:
    """A lambda from an element (r, c) of the fragment to its offset in the tile, the fragment's coordinates
    checked against the layout's grid of fragments once, where the lambda is made."""
    v = g.c.folded[name]
    rows, cols = extent(role, shape)
    grid = [v.shape[0] // rows, v.shape[1] // cols]
    i, j = (f"cr::layout::within({g.expr(x)}, {n})" for x, n in zip(coords, grid, strict=True))
    g.need("cairn_layout.hpp")
    at = layouts.lower_offset(v, [f"(cr_i * {rows} + cr_r)", f"(cr_j * {cols} + cr_c)"])
    return (f"[cr_i = {i}, cr_j = {j}](std::size_t cr_r, std::size_t cr_c) noexcept "
            f"{{ return std::size_t({at}); }}")  # fmt: skip


def lower_load(g: Emitter, e: Expr) -> str:
    _, ty, (_, role, _, shape, name, shared) = e.ref
    need(g, ty)
    data = held(g, e.args[0], name)
    at = offset(g, e, name, role, shape, e.args[2:])
    form = layouts.affine(g.c.folded[name]) or ("row", 0)
    return (f"cr::frag::loaded<{spelled(g, ty)}>({data}, {at}, {form[1]}, {'true' if form[0] == 'row' else 'false'}, "
            f"{'true' if shared else 'false'})")  # fmt: skip


def lower_store(g: Emitter, e: Expr) -> str:
    _, ty, (_, role, _, shape, name, _) = e.ref
    need(g, ty)
    data = held(g, e.args[0], name)
    at = offset(g, e, name, role, shape, e.args[2:4])
    form = layouts.affine(g.c.folded[name]) or ("row", 0)
    return (f"cr::frag::stored({g.expr(e.args[4])}, {data}, {at}, {form[1]}, {'true' if form[0] == 'row' else 'false'}, "
            f"unsigned(cr_blk.lane()))")  # fmt: skip


def lower_get(g: Emitter, e: Expr) -> str:
    need(g, e.ref[1])
    return f"cr::frag::get({g.expr(e.args[0])}, {g.expr(e.args[1])}, unsigned(cr_blk.lane()))"


def lower_set(g: Emitter, e: Expr) -> str:
    need(g, e.ref[1])
    at = ", ".join(g.expr(a) for a in e.args)
    return f"cr::frag::set({at}, unsigned(cr_blk.lane()))"


def lower_mma(g: Emitter, e: Expr) -> str:
    need(g, e.ty)
    acc, a, b = (g.expr(x) for x in e.args)
    return f"cr::frag::multiplied({acc}, {a}, {b})"

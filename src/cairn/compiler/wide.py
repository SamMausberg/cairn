"""Wide loads and stores: `load_wide[K](x, i)` and `store_wide(x, i, v)`, their rule, their cache hints and their
lowering.

    let v = load_wide[4](x, i, Cache.streaming);   // x[i .. i + 4] as one Array[f32, 4]: one 16-byte access
    store_wide(out, i, v);                         // v into out[i .. i + 4]: one 16-byte access

A thread, a lane or host code moves K adjacent elements at once. K is a constant power of two, and K elements of the
array's scalar or storage-float type hold at most 16 bytes, the widest one access moves (E-WIDE): 4 f32, 8 f16, 2 f64,
16 u8. A load gives an `Array[T, K]` value, and a store takes one of the array's element type.

Each access has two guards, and a failed one traps like any other: the K elements lie inside the array, i + K at most
its length, and the first sits on the access's width, so the address of x[i] is a multiple of K * sizeof(T). A view
the caller passes may start anywhere its element's alignment allows, so the second guard stays even where the index
is a multiple of K; on the device it is one test beside the load.

A hint says how the device's caches treat the access, from a closed set written by name: `Cache.all` (the default,
.ca and .wb), `Cache.l2` (.cg), `Cache.streaming` (.cs), `Cache.last_use` (.lu, loads only) and `Cache.read_only`
(.nc, the path __ldg takes, loads only and only from an ro view, which nothing writes while it is lent). A block's
shared memory has no caches to hint, so a hint other than `Cache.all` on a shared array is E-WIDE. On the host the
access is K ordinary loads or stores and the hint says nothing.

Every other rule sees the access as the part x[i .. i + K] it reaches: a lane may write it only inside its own block
(`store_wide(out, 4 * i, v)` in `parallel i in m` owns out[4i .. 4i + 4]), and the phase rule and the rule that each
outside element has one writer count all K elements. The row gains read:x or write:x and trap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .tree import FLOAT, INT, STORAGE, USIZE, VOID, Expr, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

NAMES = ("load_wide", "store_wide")
WIDEST = 16  # bytes one thread moves in a single access (LDG.E.128, STG.E.128, LDS.128)
ELEMENTS = {"bool", *INT, *FLOAT, *STORAGE}
CACHES = ["all", "l2", "streaming", "last_use", "read_only"]  # the variants of the builtin enum Cache
STORED = ("all", "l2", "streaming")  # PTX has no last-use or read-only store


@dataclass
class Wide:
    """One wide access as the checker settled it: for the lowering, the lane, phase and global rules and the census."""

    count: int  # K, the elements it moves
    element: Type
    hint: str
    shared: bool  # an array of a cooperative block's shared memory, which no hint reaches
    part: Expr  # x[i .. i + K], what every other rule sees it reach
    size: int  # the bytes of one element

    @property
    def bytes(self) -> int:
        return self.count * self.size


def check_load(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`load_wide[K](x, i)` and `load_wide[K](x, i, hint)`: x[i .. i + K] as one Array[T, K]."""
    if len(targs) != 1 or not isinstance(targs[0], int) or isinstance(targs[0], bool):
        fail("E-WIDE", "Write load_wide[K](x, i) with K a constant: how many adjacent elements one access moves.", e)
    if len(args) not in (2, 3):
        fail("E-ARITY", "load_wide[K] takes an array, an index and, optionally, a cache hint: load_wide[4](x, i).", e)
    wide = reached(c, e, args[0], args[1], targs[0], write=False)
    wide.hint = hint(c, e, args[2:], args[0], store=False)
    wide.shared = shared(c, wide, args[0])
    e.ref = ("builtin", wide)
    return Type("Array", args=(wide.element, wide.count))


def check_store(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`store_wide(x, i, v)` and `store_wide(x, i, v, hint)`: v, an Array[T, K], into x[i .. i + K]."""
    if targs:
        fail("E-WIDE", "store_wide takes its width from the Array it stores: store_wide(x, i, v).", e)
    if len(args) not in (3, 4):
        fail("E-ARITY", "store_wide takes an array, an index, the Array[T, K] it stores and, optionally, a cache "
             "hint: store_wide(out, i, v).", e)  # fmt: skip
    value = c.expr(args[2])
    if value.mode != "value" or value.name != "Array" or not isinstance(value.args[1], int):
        fail("E-WIDE", f"store_wide stores an Array[T, K] of the array's element type, not {value.display()}.", args[2])
    wide = reached(c, e, args[0], args[1], value.args[1], write=True)
    if value.args[0] != wide.element:
        fail("E-TYPE-MISMATCH", f"store_wide into an array of {wide.element.display()} takes an Array of "
             f"{wide.element.display()}, not {value.display()}.", args[2])  # fmt: skip
    wide.hint = hint(c, e, args[3:], args[0], store=True)
    wide.shared = shared(c, wide, args[0])
    e.ref = ("builtin", wide)
    return VOID


def reached(c: Checker, e: Expr, base: Expr, index: Expr, count: int, write: bool) -> Wide:
    """The array a wide access reaches and the part it reaches: typed, placed, lent and guarded as an index is."""
    what = "store_wide" if write else "load_wide"
    ty = c.expr(base, consume=False)
    if root(base).tag != "name" or (not is_view(ty) and ty.name not in {"Buf", "Array"}):
        fail("E-WIDE", f"{what} reaches an array: a view, a buffer, a Buf, an Array or a shared array.", base)
    element = ty.value if is_view(ty) else ty.args[0]
    if element.mode != "value" or element.name not in ELEMENTS:
        fail("E-WIDE", f"{what} moves scalars or storage floats; {element.display()} is not one.", base)
    size = c.sizeof(element)
    if count < 1 or count & (count - 1) or count * size > WIDEST:
        most = WIDEST // size
        fail("E-WIDE", f"{what} moves a power of two of elements in one access of at most {WIDEST} bytes: "
             f"{element.display()} takes 1 to {most}, and {count} is not one of them.", e)  # fmt: skip
    outer = c.lanes.outer if c.lanes else {n for n, _ in c.f.params}
    private = c.device_depth and root(base).val not in outer
    if (ty.place == "device") != bool(c.device_depth) and ty.place != "unified" and not private:
        fail("E-PLACEMENT", f"{ty.place} memory is not addressable from {'device' if c.device_depth else 'host'} "
             "code.", base)  # fmt: skip
    c.expr(index, USIZE)
    if write and not c.writable(base):
        fail("E-WRITE-LEASE", f"store_wide writes {root(base).val}, which is read-only here.", base)
    last = Expr("binary", "+", [index, Expr("int", str(count), line=e.line, col=e.col, ty=USIZE)], e.line, e.col)
    last.ty = USIZE
    part = Expr("slice", "", [base, index, last], e.line, e.col)
    part.ty = Type(element.name, "rw" if write else "ro", "part", element.args, ty.place)
    c.lend(part, "rw" if write else "ro", [], elements=True)
    c.effect(("write:" if write else "read:") + root(base).val)
    c.guard("wide")  # the K elements inside the array, and the first on the access's width
    return Wide(count, element, "all", False, part, size)


def hint(c: Checker, e: Expr, given: list[Expr], base: Expr, store: bool) -> str:
    """The cache hint, written by name as `Cache.X`; `Cache.all` when there is none."""
    if not given:
        return "all"
    a = given[0]
    named = c.named_type(a.args[0]) if a.tag == "field" and a.args else None
    if named is None or named.name != "Cache" or c.p.enums.get("Cache") != CACHES:
        fail("E-WIDE", f"A cache hint is written by name: Cache.{', Cache.'.join(STORED if store else CACHES)}.", a)
    c.expr(a, named)
    if a.val not in (STORED if store else CACHES):
        fail("E-WIDE", f"store_wide takes Cache.{', Cache.'.join(STORED)}; PTX has no {a.val} store.", a)
    if a.val == "read_only" and (not is_view(base.ty) or base.ty.mode != "ro" or base.ty.place not in {"device",
                                                                                                        "unified"}):  # fmt: skip
        fail("E-WIDE", "Cache.read_only reads through the path __ldg takes, which only memory nobody writes while the "
             "access can see it may use: an ro view of device memory.", a)  # fmt: skip
    return a.val


def shared(c: Checker, wide: Wide, base: Expr) -> bool:
    """Whether the access reaches a cooperative block's shared memory, where no hint but Cache.all applies."""
    inside = c.coop is not None and root(base).val in c.coop.shared
    if inside and wide.hint != "all":
        fail("E-WIDE", f"{root(base).val} is the block's shared memory, which has no caches to hint; drop "
             f"Cache.{wide.hint}.", base)  # fmt: skip
    return inside


def lower(g: Emitter, e: Expr) -> str:
    """`cr::wide::load<K, hint, shared>(x, i, n)` or `cr::wide::store<hint, shared>(x, i, n, v)`: both guards, then one
    access on the device and one memcpy on the host (runtime/cairn_access.hpp)."""
    g.need("cairn_access.hpp")
    wide: Wide = e.ref[1]
    data, count = g.pointer(e.args[0])
    where = f"cr::wide::Cache::{wide.hint}, {'true' if wide.shared else 'false'}"
    if e.val == "load_wide":
        return f"cr::wide::load<{wide.count}, {where}>({data}, {g.expr(e.args[1])}, {count})"
    return f"cr::wide::store<{where}>({data}, {g.expr(e.args[1])}, {count}, {g.expr(e.args[2])})"


OPERATORS = {"all": "ca", "l2": "cg", "streaming": "cs", "last_use": "lu", "read_only": "nc"}  # PTX's, for a load
WRITTEN = {"all": "wb", "l2": "cg", "streaming": "cs"}  # and for a store


def records(e: Expr) -> dict:
    """What `cairn explain` says of one wide access: the elements it moves, the one access they are on the device
    with its cache operator, and what the host does instead."""
    wide: Wide = e.ref[1]
    store = e.val == "store_wide"
    space = "shared-memory access" if wide.shared else \
        f"access, .{(WRITTEN if store else OPERATORS)[wide.hint]} ({wide.hint})"  # fmt: skip
    return {"operation": e.val, "array": root(e.args[0]).val, "elements": wide.count, "bytes": wide.bytes,
            "device": f"one {wide.bytes}-byte {space}",
            "host": f"{wide.count} ordinary {'stores' if store else 'loads'}, no hint"}  # fmt: skip

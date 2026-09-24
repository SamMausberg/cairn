"""Atomic updates of one element: `atomic_add_wrap(x[i], v)` and its kin, their rule, the class they form beside
plain accesses, and their lowering.

    atomic_add_wrap(bins[usize(v & 255)], 1);          // one bin, from any lane or thread, at once
    let before = atomic_max(best[0], score);           // the element's old value comes back

Each updates one element of an array a lane, a cooperative thread or host code may write, reads it and writes it in
one indivisible step, and returns what it held before:

- `atomic_add_wrap(x[i], v)` on u32, u64 and usize adds modulo 2^N, as `add_wrap` does: an atomic add cannot trap
  on overflow, since no thread sees the whole sum, so the only atomic add on integers is the one that wraps by name;
- `atomic_min`, `atomic_max` on u32, i32, u64, i64 and usize, and `atomic_and`, `atomic_or`, `atomic_xor` on u32,
  u64 and usize, as `min`, `max`, `&`, `|` and `^` compute;
- `atomic_cas(x[i], expected, desired)` on the same five integers: desired where the element holds expected, and the
  old value either way, so a thread that gets expected back made the change;
- `atomic_add_unordered(x[i], v)` on f32 and f64: each add is rounded once, in the order the threads arrive, which
  the hardware and the host's scheduler pick. The name says so, as `mma_unordered`'s does, and the receipt lists its
  contract under `numerics`: after k adds the element is its old value plus every added value, each partial sum
  rounded to nearest once, in some order. On the device an f32 add also flushes a subnormal operand or result to
  zero, as PTX's atom.add.f32 does. So an f32 element ends within k * 2^-23 * (|old| + sum |v|) + k * 2^-125 of the
  exact sum while k is below 2^22, and an f64 one within k * 2^-52 * (|old| + sum |v|) while k is below 2^51.

Every update is relaxed: the element's updates happen one at a time in some order, and they order nothing else. What
makes their results visible is what makes any write visible: the end of a region, a barrier, or a cooperative
region's finish.

Atomic updates are a class of their own beside plain reads and writes. Two atomic updates never race, so a lane may
update any element of an array other lanes update, and the rules that give each element one writer do not count
them. An array a region updates atomically is not also read or written plainly in it (E-ATOMIC-MIXED): in a
`parallel` region anywhere, for an outside array anywhere in a cooperative region (no barrier orders two blocks), and
for a block's shared array between two barriers, by another thread. The row gains `atomic`, `read:x`, `write:x` and
`trap` for the index's guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .tree import FLOAT, USIZE, Expr, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

UNSIGNED_WORDS = ("u32", "u64", "usize")
WORDS = ("u32", "i32", "u64", "i64", "usize")
TYPES = {
    "atomic_add_wrap": UNSIGNED_WORDS,
    "atomic_min": WORDS,
    "atomic_max": WORDS,
    "atomic_and": UNSIGNED_WORDS,
    "atomic_or": UNSIGNED_WORDS,
    "atomic_xor": UNSIGNED_WORDS,
    "atomic_cas": WORDS,
    "atomic_add_unordered": tuple(sorted(FLOAT)),
}
NAMES = tuple(TYPES)
BOUND = (
    "over k adds, k * 2^-23 * (|old| + sum |v|) + k * 2^-125 for f32 while k < 2^22, and k * 2^-52 * (|old| + sum |v|)"
    " for f64 while k < 2^51"
)


@dataclass
class Atomic:
    """One atomic update as the checker settled it: its element type and the element it reaches."""

    element: Type
    place: Expr  # x[i]


def check(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """An atomic update of one element: typed, placed, leased and guarded as an index is, its values of the element's
    type, and recorded as an atomic access for the rules that keep atomics apart from plain ones."""
    from .builtins import contract  # builtins registers this rule, so it is imported here, not above

    count = 3 if e.val == "atomic_cas" else 2
    shape = f"{e.val}(x[i], expected, desired)" if e.val == "atomic_cas" else f"{e.val}(x[i], v)"
    if targs or len(args) != count:
        fail("E-ARITY", f"{e.val} takes the element it updates and {'two values' if count == 3 else 'a value'}: "
             f"{shape}.", e)  # fmt: skip
    place = args[0]
    if place.tag != "index" or len(place.args) != 2 or root(place).tag != "name" or place.args[0].tag != "name":
        fail("E-ATOMIC", f"{e.val} updates one element of an array, written in place: {shape}.", place)
    base, index = place.args
    ty = c.expr(base, consume=False)
    if not is_view(ty) and ty.name not in {"Buf", "Array"}:
        fail("E-ATOMIC", f"{e.val} updates an element of an array; {base.val} is not one.", base)
    element = ty.value if is_view(ty) else ty.args[0]
    if element.mode != "value" or element.name not in TYPES[e.val]:
        fail("E-ATOMIC", f"{e.val} updates {', '.join(TYPES[e.val])}; {base.val} holds {element.display()}."
             + (" An atomic add on integers wraps: atomic_add_wrap on an unsigned one." if e.val == "atomic_add_wrap"
                or element.name in FLOAT else ""), place)  # fmt: skip
    if not c.writable(base):
        fail("E-WRITE-LEASE", f"{e.val} writes {base.val}, which is read-only here.", base)
    outer = c.lanes.outer if c.lanes else {n for n, _ in c.f.params}
    private = c.device_depth and base.val not in outer
    if (ty.place == "device") != bool(c.device_depth) and ty.place != "unified" and not private:
        fail("E-PLACEMENT", f"{ty.place} memory is not addressable from {'device' if c.device_depth else 'host'} "
             "code.", base)  # fmt: skip
    c.expr(index, USIZE)
    place.ty = element
    c.leased(c.where(place), "rw", place)
    for value in args[1:]:
        c.expect(c.expr(value, element), element, value)
    c.effects |= {"atomic", "read:" + base.val, "write:" + base.val}
    c.guard("bounds")  # the element's index, below the array's length
    if c.lanes is not None and base.val in c.lanes.outer:
        c.lanes.atomics.append((base.val, place))
    if e.val == "atomic_add_unordered":
        subnormals = "flushed-on-device" if element.name == "f32" else "kept"
        contract(c, e, "atomic_add", element.name, element.name, rounding=f"unordered-{element.name}", bound=BOUND,
                 subnormals=subnormals)  # fmt: skip
    e.ref = ("builtin", Atomic(element, place))
    return element


def mixed(name: str, atomic: Expr, plain: Expr, where: str):
    fail("E-ATOMIC-MIXED", f"{name} is updated atomically at line {atomic.line} and {where} at line {plain.line}: an "
         "atomic update and a plain access of one element may run at once, and the plain one sees half an update "
         f"or undoes one. Keep {name} atomic throughout the region, and read it after the region ends, after a "
         "barrier, or in the region's finish.", plain, array=name)  # fmt: skip


def lower(g: Emitter, e: Expr) -> str:
    """`cr::atomic::OP(x, i, n, values...)`: the index's guard, then one atomic instruction on the device and one
    std::atomic_ref operation on the host, relaxed on both (runtime/cairn_access.hpp)."""
    g.need("cairn_access.hpp")
    data, count = g.pointer(e.args[0].args[0])
    values = ", ".join(g.expr(a) for a in e.args[1:])
    op = e.val.removeprefix("atomic_")
    op += "_" * (op in {"and", "or", "xor"})  # C++ reserves and, or and xor
    return f"cr::atomic::{op}({data}, {g.expr(e.args[0].args[1])}, {count}, {values})"

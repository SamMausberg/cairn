"""I/O rings: kernel operations in flight without a thread each, and the owners they hold while they run.

`let q = IoRing(n);` declares, in place, a ring of at most `n` operations in flight. `q.read(fd, data, count,
offset, tag)` and its siblings move the `Buf[u8]` they are given into the ring, so nothing the kernel reads or
writes is reachable from the program until `q.next(tag, result)` hands that owner back with the kernel's
result. No borrow outlives its call and no lease is recorded, which is why a ring, unlike a task group, may be
lent to a callee. `wait(q)` is the ring's one consumer: it lets every operation finish before it releases
anything. Lowered onto `cr::io::Ring` (runtime/cairn_io.hpp), which is io_uring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .tree import USIZE, Expr, Type, fail, root

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

RING = Type("IoRing")
BYTES = Type("Buf", args=(Type("u8"),))
I32, I64, U64 = Type("i32"), Type("i64"), Type("u64")
# What each operation takes after the ring, and the runtime operation it lowers to.
OPERATIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "read": (("fd", "data", "count", "offset", "tag"), "read"),
    "write": (("fd", "data", "count", "offset", "tag"), "write"),
    "recv": (("fd", "data", "count", "tag"), "recv"),
    "send": (("fd", "data", "count", "tag"), "send"),
    "accept": (("fd", "tag"), "accept"),
    "timeout": (("ns", "tag"), "timeout"),  # finishes with -ETIME after ns nanoseconds
}
KINDS = {"fd": I32, "data": BYTES, "count": USIZE, "offset": U64, "ns": U64, "tag": U64}


def check_ring(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """IoRing(n): the kernel ring and all n berths are taken here, so no operation allocates."""
    c.host_only(e, "An I/O ring is a host object")
    if targs or len(args) != 1:
        fail("E-ARITY", "Write IoRing(capacity): the most operations it holds in flight at once.", e)
    c.expr(args[0], USIZE)
    c.effects |= {"alloc", "free", "io"}
    c.guard("allocation")
    return RING


def method(c: Checker, e: Expr, name: str, args: list[Expr]) -> Type:
    """`q.read(...)`, `q.write(...)`, `q.recv(...)`, `q.send(...)`, `q.accept(...)`, `q.timeout(ns, tag)`, `q.next(tag,
    result)` and `q.cancel(tag)`, which asks the kernel to stop what is in flight under a tag: each still comes back
    through `next`, with -ECANCELED or its own result, so cancelling releases nothing early."""
    c.host_only(e, "An I/O ring is a host object")
    b = c.env.get(root(e.args[0]).val) if root(e.args[0]).tag == "name" else None
    if b is None or b.ty.mode == "ro":
        fail("E-WRITE-LEASE", "Submitting to a ring or collecting from it changes it; name a ring or one lent rw.", e)
    c.leased(c.where(e.args[0]), "rw", e)  # A task the ring is lent to is the only one that may use it.
    e.ref = ("ring", name)
    if name == "next":
        if len(args) != 2:
            fail("E-ARITY", "q.next(tag, result) writes the finished operation's tag and result.", e)
        for a, want in zip(args, (U64, I64), strict=True):
            c.expect(c.place(a, write=True), want, a)
        c.effect("io")
        c.guard("collect")
        return BYTES
    if name == "cancel":
        if len(args) != 1:
            fail("E-ARITY", "q.cancel(tag) names the operations to stop by their tag.", e)
        c.expr(args[0], U64)
        c.effect("io")
        return Type("void")
    if name not in OPERATIONS:
        fail("E-CALLEE", f"A ring offers {', '.join(OPERATIONS)}, next and cancel.", e)
    names = OPERATIONS[name][0]
    if len(args) != len(names):
        fail("E-ARITY", f"q.{name} takes {', '.join(names)}.", e)
    for a, part in zip(args, names, strict=True):
        c.expr(a, KINDS[part])  # `data` is an owner by value: submitting it is a move.
    c.effect("io")
    c.guard("ring")
    return Type("void")


def waited(c: Checker, e: Expr) -> None:
    """wait(q) on a ring: only the function that declared it may consume it, never a callee it was lent to."""
    b = c.env.get(root(e).val) if root(e).tag == "name" else None
    if b is None or b.ty.mode != "value":
        fail("E-TYPE-MISMATCH", "wait(q) consumes a ring declared in this function, not one it was lent.", e)
    c.effects |= {"io", "free"}


def lower(g: Emitter, e: Expr) -> str:
    ring, rest = g.expr(e.args[0]), e.args[1:]
    if e.ref[1] == "next":
        return f"{ring}.collect({g.expr(rest[0])}, {g.expr(rest[1])})"
    if e.ref[1] == "cancel":
        return f"{ring}.cancel({g.expr(rest[0])})"
    named: dict[str, Any] = dict(zip(OPERATIONS[e.ref[1]][0], rest, strict=True))
    data = g.expr(named["data"]) if "data" in named else "cr::Buf<std::uint8_t>()"
    count = g.expr(named["count"]) if "count" in named else "0"
    offset = g.expr(named.get("offset") or named["ns"]) if {"offset", "ns"} & set(named) else "0"
    fd, tag = g.expr(named["fd"]) if "fd" in named else "-1", g.expr(named["tag"])
    return f"{ring}.submit(cr::io::Op::{OPERATIONS[e.ref[1]][1]}, {fd}, {data}, {count}, {offset}, {tag})"

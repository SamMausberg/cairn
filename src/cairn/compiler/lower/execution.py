"""Which runtime operation each piece of device work lowers to, all of them on the calling thread's execution
context (`runtime/cairn_exec.hpp`).

A region runs on the context's stream and returns once that stream has finished it; a reduction's, scan's or
compaction's temporaries come from the context's arena; queued work borrows a lane until its ticket's wait; a
transfer crosses on the context's stream. The context is the thread's own, made by its first device operation, so the
lowering names it at every call and passes nothing through signatures: a C caller's entry sees the same context its
thread's other device work does. What each operation computes is unchanged, and so is when the host sees it.

A function whose row shows nothing that lets the host observe device memory (`unwaited`) needs no wait until it
returns. Its body runs held when it queues device work more than once: one wait at its end instead of one per
operation. Its library's C header also gives it an enqueued entry, `cq_NAME(stream, ...)`, which queues the work on
the caller's stream and returns without any wait (compiler/lower/header.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..primitives.atomics import NAMES as ATOMIC
from ..syntax.tree import Expr, Function, Stmt, is_view, nested

if TYPE_CHECKING:
    from ..check.checking import Checker

CONTEXT = "cr::gpu::here()"  # the calling thread's execution context


def call(name: str, arguments: Iterable[str], templates: Iterable[object] = ()) -> str:
    """`cr::gpu::name<templates>(context, arguments...)`, one operation of cairn_exec.hpp."""
    listed = ", ".join(str(t) for t in templates)
    return f"cr::gpu::{name}{f'<{listed}>' if listed else ''}({', '.join([CONTEXT, *arguments])})"


def unrolled(unroll: int) -> list[int]:
    """The template argument a plan's `unroll` adds to a launch, when it asks for more than one pass."""
    return [unroll] if unroll > 1 else []


# What in a function's row lets the host see device memory, or makes it wait, before the function returns. Without
# any of these a function's device work may run held, waiting once at its end, or enqueued on a caller's stream
# with no wait at all; with one of them, each operation waits as it always did.
OBSERVES = {
    "transfer:h2d": "it transfers from host memory, which the host may change once the copy is queued",
    "transfer:d2h": "it transfers to host memory, which the host reads when it returns",
    "gpu_alloc": "it allocates device memory: a device buffer, or the scratch of a device reduce, scan or compact, "
    "whose result returns to the host (no form of the language writes such a result to a device view yet)",
    "gpu_free": "it releases device memory, which work still queued may reach",
    "spawn": "it queues device work whose wait is the host's",
    "join": "it waits for queued work",
    "io": "it does I/O",
    "mmio": "it reaches a machine register",
    "asm": "it holds untyped assembly",
    "asm:x86_64": "it holds host assembly",
    "asm:aarch64": "it holds host assembly",
    "indirect_call": "it calls a function value, whose work its row does not show",
    "atomic": "it updates host memory atomically, which another host thread observes",
    "lock": "it takes a lock another host thread observes",
}
ENQUEUE = "E-ENQUEUE"  # what a library header says of a function with device work and no enqueued entry
DEVICE_WORK = {"par:device", "transfer:d2d", "transfer:h2d", "transfer:d2h"}  # a row that reaches the device
HELD = "const cr::gpu::Held cr_held;"  # the run a function's body is held in (runtime/cairn_exec.hpp)


def unwaited(c: Checker, f: Function) -> str:
    """Why `f`'s device work must wait on the host before `f` returns, or "" when nothing in it lets the host observe
    device memory: then it may be held to one wait, or enqueued on a caller's stream with none. An atomic update in
    a device lane is device work; one in host code or a host lane is something another host thread can see."""
    for effect in sorted(c.rows.get(f.name, set())):
        if effect == "atomic" and not host_atomics(c, ran(c, f), set()):
            continue
        if effect in OBSERVES:
            return OBSERVES[effect]
        if effect.startswith("ffi:"):
            return "it makes a foreign call, which may observe device memory"
    unified = next((n for n, t in f.params if is_view(t) and t.place == "unified"), "")
    return f"{unified} is a @unified view, which the host may read while device work is queued" if unified else ""


def held(c: Checker, f: Function) -> bool:
    """Whether the lowering holds `f`'s body in one run: nothing in it observes device memory on the host, and it
    queues device work more than once (two operations, or one in a loop), so one wait replaces several."""
    row = c.rows.get(f.name, set())
    if f.kernel or f.name in c.device_functions or not row & DEVICE_WORK or unwaited(c, f):
        return False
    return operations(c, f.body) > 1


def ran(c: Checker, f: Function) -> list[Stmt]:
    """What a call of `f` may run: its body, and the body of each implementation a plan may run in its place."""
    return [*f.body, *(s for g in c.alternatives.get(f.name, ()) if g in c.fs for s in c.fs[g].body)]


def host_atomics(c: Checker, ss: list[Stmt], seen: set[str]) -> bool:
    """Whether the statements, or a function they call from host code, update memory atomically outside a device
    region: in host code, a host lane or a host cooperative thread, or through a host `Atomic`."""

    def found(e: Expr) -> bool:
        if e.tag == "lambda" and isinstance(e.ref, Function) and host_atomics(c, e.ref.body, seen):
            return True
        if e.tag == "call" and (e.val in ATOMIC or (isinstance(e.ref, tuple) and e.ref[:1] == ("shared",))):
            return True
        if e.tag == "call" and isinstance(e.ref, Function) and e.ref.name not in seen:
            seen.add(e.ref.name)
            if host_atomics(c, ran(c, e.ref), seen):
                return True
        return any(found(a) for a in e.args)

    for s in ss:
        if (s.tag in {"parallel", "reduce", "scan", "compact"} and s.ref == "device") or (
            s.tag == "blocks" and getattr(s.ref, "device", False)
        ):
            continue  # a device region's atomics update device memory
        if any(found(e) for e in s.exprs) or host_atomics(c, nested(s), seen):
            return True
    return False


def operations(c: Checker, ss: list[Stmt], times: int = 1) -> int:
    """How many device operations the statements queue, counting any inside a loop twice: regions, cooperative
    regions, device transfers and multiplies, and calls to functions that queue device work."""
    count = 0
    for s in ss:
        if s.tag in {"parallel", "reduce", "scan", "compact"} and s.ref == "device":
            count += times
            continue
        if s.tag == "blocks" and getattr(s.ref, "device", False):
            count += times
            continue
        for e in s.exprs:
            count += calls(c, e) * times
        count += operations(c, nested(s), 2 if s.tag in {"for", "while"} else times)
    return count


def calls(c: Checker, e: Expr) -> int:
    """The device operations an expression queues: calls whose rows hold device work, device transfers, multiplies."""
    count = sum(calls(c, a) for a in e.args)
    if e.tag != "call":
        return count
    if isinstance(e.ref, Function):
        return count + bool(c.rows.get(e.ref.name, set()) & DEVICE_WORK)
    return count + (e.val in {"transfer", "mma_unordered"})

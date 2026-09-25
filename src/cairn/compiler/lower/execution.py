"""Which runtime operation each piece of device work lowers to, all of them on the calling thread's execution
context (`runtime/cairn_exec.hpp`).

A region runs on the context's stream and returns once that stream has finished it; a reduction's, scan's or
compaction's temporaries come from the context's arena; queued work borrows a lane until its ticket's wait; a
transfer crosses on the context's stream. The context is the thread's own, made by its first device operation, so the
lowering names it at every call and passes nothing through signatures: a C caller's entry sees the same context its
thread's other device work does. What each operation computes is unchanged, and so is when the host sees it.

A function's body runs held when a run of its device work, two operations or one in a loop, meets nothing but
operations that wait for the run where they stand (`ROUTED`: a copy to or from host memory, an owner's allocation or
release, a result the host reads, queued work), and nothing in it lets the host observe device memory another way
(`unheld`): the run waits once, before the first thing the host observes, instead of after each operation. A function
whose row shows nothing that lets the host observe device memory at all (`unwaited`) needs no wait until it returns,
and its library's C header also gives it an enqueued entry, `cq_NAME(stream, ...)`, which queues the work on the
caller's stream and returns without any wait (compiler/lower/header.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..plans import fusion
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
    "which grows when a call needs more than it holds, and a caller's graph capture cannot allocate",
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


# What the runtime waits for itself before the host can see anything through it: a copy to or from host memory, an
# owner's allocation and release, a device reduction's, scan's or compaction's result, and queued device work each
# call observed() first (runtime/cairn_exec.hpp), which waits for everything a held run has queued. A held body may
# hold these, and each only ends a run of device work; the rest of OBSERVES keeps a body from being held at all.
ROUTED = {"transfer:h2d", "transfer:d2h", "gpu_alloc", "gpu_free", "spawn", "join"}
REGIONS = {"parallel", "reduce", "scan", "compact"}


def unwaited(c: Checker, f: Function, routed: set[str] | frozenset[str] = frozenset()) -> str:
    """Why `f`'s device work must wait on the host before `f` returns, or "" when nothing in it, beyond what `routed`
    names, lets the host observe device memory: then it may be enqueued on a caller's stream with no wait at all. An
    atomic update in a device lane is device work; one in host code or a host lane is something another host thread
    can see."""
    for effect in sorted(c.rows.get(f.name, set()) - routed):
        if effect == "atomic" and not host_atomics(c, ran(c, f), set()):
            continue
        if effect in OBSERVES:
            return OBSERVES[effect]
        if effect.startswith("ffi:"):
            return "it makes a foreign call, which may observe device memory"
    unified = next((n for n, t in f.params if is_view(t) and t.place == "unified"), "")
    return f"{unified} is a @unified view, which the host may read while device work is queued" if unified else ""


def unheld(c: Checker, f: Function) -> str:
    """Why `f`'s body may not be held even with each ROUTED operation waiting where it stands, or "": something in its
    row lets the host observe device memory without such a wait, it starts a host task, which may observe what the
    run has queued, or it declares a @unified buffer, which host code reads without one."""
    if why := unwaited(c, f, ROUTED):
        return why
    if reaches(c, ran(c, f), set(), lambda e: e.tag == "spawn" and e.val != "queue"):
        return "it starts a host task, which may observe device memory while the run's work is queued"
    if reaches(c, ran(c, f), set(), stmt=lambda s: s.tag == "buffer" and s.ref == "unified"):
        return "it declares a @unified buffer, which the host may read while device work is queued"
    return ""


def held(c: Checker, f: Function, fused: bool = True) -> bool:
    """Whether the lowering holds `f`'s body in one run: nothing in it lets the host observe device memory without a
    wait (unheld), and some stretch of it queues device work more than once with nothing between that waits (runs),
    so one wait replaces several. `fused` counts the regions a plan fuses as the one launch the lean emission makes."""
    row = c.rows.get(f.name, set())
    if f.kernel or f.name in c.device_functions or not row & DEVICE_WORK or unheld(c, f):
        return False
    return runs(c, f.body, 0, f if fused else None)[0] > 1


def ran(c: Checker, f: Function) -> list[Stmt]:
    """What a call of `f` may run: its body, and the body of each implementation a plan may run in its place."""
    return [*f.body, *(s for g in c.alternatives.get(f.name, ()) if g in c.fs for s in c.fs[g].body)]


def on_device(s: Stmt) -> bool:
    """Whether `s` is a region or a cooperative region whose lanes run on the device."""
    return (s.tag in REGIONS and s.ref == "device") or (s.tag == "blocks" and getattr(s.ref, "device", False))


def resident(s: Stmt) -> bool:
    """Whether a device statement leaves its result on the device, where the host does not wait for it: a region, a
    reduction into an element, a scan whose total nobody reads."""
    return (
        s.tag in {"parallel", "blocks"} or (s.tag == "reduce" and len(s.exprs) > 2) or (s.tag == "scan" and not s.name)
    )


def reaches(c: Checker, ss: list[Stmt], seen: set[str], expr=lambda e: False, stmt=lambda s: False) -> bool:
    """Whether a statement `stmt` accepts or an expression `expr` accepts is in the statements outside their device
    regions, in a closure they make, or in what a function they call may run, each function walked once."""

    def found(e: Expr) -> bool:
        if expr(e) or (e.tag == "lambda" and isinstance(e.ref, Function) and reaches(c, e.ref.body, seen, expr, stmt)):
            return True
        if e.tag == "call" and isinstance(e.ref, Function) and e.ref.name not in seen:
            seen.add(e.ref.name)
            if reaches(c, ran(c, e.ref), seen, expr, stmt):
                return True
        return any(found(a) for a in e.args)

    return any(
        not on_device(s) and (stmt(s) or any(found(e) for e in s.exprs) or reaches(c, nested(s), seen, expr, stmt))
        for s in ss
    )


def host_atomics(c: Checker, ss: list[Stmt], seen: set[str]) -> bool:
    """Whether the statements, or a function they call from host code, update memory atomically outside a device
    region: in host code, a host lane or a host cooperative thread, or through a host `Atomic`."""

    def atomic(e: Expr) -> bool:
        return e.tag == "call" and (e.val in ATOMIC or (isinstance(e.ref, tuple) and e.ref[:1] == ("shared",)))

    return reaches(c, ss, seen, atomic)


def runs(c: Checker, ss: list[Stmt], open_: int, fusing: Function | None = None) -> tuple[int, int]:
    """The most device operations the statements queue in one stretch with nothing between that waits for a held run,
    and how many are still queued unwaited at their end, after `open_` were before them. An operation is a region,
    a cooperative region, a device copy, a multiply, a reduction or scan whose result stays on the device, or a call to
    a function that queues device work (`queued`); a device reduction, scan or compaction whose result the host reads
    waits, as does a ROUTED operation, a device,
    pinned or unified owner's allocation and its release where its block ends. A loop's body counts twice, since its
    end meets its start, and the arms of a branch count one after another. With `fusing`, the function they are in,
    regions a plan fuses are one operation and the arrays their lanes hold are never allocated (plans/fusion.py)."""
    chains = fusion.chains(ss, c.rows, fusing) if fusing else []
    inside = {id(r) for chain in chains for r in chain.regions[1:]}
    in_lanes = {name for chain in chains for name in chain.scratch}
    best, owners = open_, False
    for s in ss:
        if id(s) in inside or (s.tag in {"buffer", "stack"} and s.name in in_lanes):
            continue
        if on_device(s):
            open_ = open_ + 1 if resident(s) else 0
        elif s.tag == "buffer" and s.ref != "host":
            open_, owners = 0, True
        else:
            for e in s.exprs:
                waits, count = queued(c, e)
                open_ = 0 if waits else open_ + count
                best = max(best, open_)
            for _ in range(2 if s.tag in {"for", "while"} else 1):
                inner, open_ = runs(c, nested(s), open_, fusing)
                best = max(best, inner)
        best = max(best, open_)
    return best, 0 if owners else open_


def queued(c: Checker, e: Expr) -> tuple[bool, int]:
    """Whether evaluating `e` waits for a held run, and the device operations it queues: calls whose rows hold device
    work, device copies and multiplies. A call whose row holds a ROUTED effect waits, and so does queued work."""
    parts = [queued(c, a) for a in e.args]
    waits, count = any(w for w, _ in parts), sum(n for _, n in parts)
    if e.tag == "spawn":
        return True, count
    if e.tag != "call":
        return waits, count
    if isinstance(e.ref, Function):
        row = c.rows.get(e.ref.name, set())
        return waits or bool(row & ROUTED), count + bool(row & DEVICE_WORK)
    if e.val == "transfer":
        from ..primitives.builtins import crossing  # builtins lowers through this module

        way = crossing(e.args[1].ty, e.args[0].ty)
        return waits or way in {"h2d", "d2h"}, count + (way == "d2d")
    return waits, count + (e.val == "mma_unordered")

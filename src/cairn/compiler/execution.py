"""Which runtime operation each piece of device work lowers to, all of them on the calling thread's execution
context (`runtime/cairn_exec.hpp`).

A region runs on the context's stream and returns once that stream has finished it; a reduction's, scan's or
compaction's temporaries come from the context's arena; queued work borrows a lane until its ticket's wait; a
transfer crosses on the context's stream. The context is the thread's own, made by its first device operation, so the
lowering names it at every call and passes nothing through signatures: a C caller's entry sees the same context its
thread's other device work does. What each operation computes is unchanged, and so is when the host sees it.
"""

from __future__ import annotations

from collections.abc import Iterable

CONTEXT = "cr::gpu::here()"  # the calling thread's execution context


def call(name: str, arguments: Iterable[str], templates: Iterable[object] = ()) -> str:
    """`cr::gpu::name<templates>(context, arguments...)`, one operation of cairn_exec.hpp."""
    listed = ", ".join(str(t) for t in templates)
    return f"cr::gpu::{name}{f'<{listed}>' if listed else ''}({', '.join([CONTEXT, *arguments])})"


def unrolled(unroll: int) -> list[int]:
    """The template argument a plan's `unroll` adds to a launch, when it asks for more than one pass."""
    return [unroll] if unroll > 1 else []

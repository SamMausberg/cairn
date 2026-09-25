"""The lowering of lane regions and collectors: the lambda every lane runs, which host threads and device lanes
share; `parallel`, `reduce`, `scan` and `compact` on the host lane pool (runtime/cairn_parallel.hpp), in order, or
on the calling thread's execution context (runtime/cairn_exec.hpp); and a chain of regions a plan fuses, run as one
region. Their rules are in concurrency.py and fusion.py; the Emitter binds these functions as its methods.

A device `reduce`, `scan` or `compact` runs on CUB, which only such a program includes (runtime/cairn_cub.hpp): CUB's
headers are about half of an nvcc build."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ..plans import chunks, fusion, staging
from ..primitives.builtins import WRAPPING
from ..syntax.tree import UNSIGNED, Stmt
from . import execution

if TYPE_CHECKING:
    from .codegen import Emitter

IDENTITY = {"*": "1", "mul_wrap": "1", "&": "max()", "min": "max()", "max": "lowest()"}  # Of a reduction; else 0.


def lane(g: Emitter, s: Stmt, body) -> str:
    """The lambda every lane runs; its body is identical for host threads and device lanes."""
    device = s.ref == "device"  # CUDA wants by-value capture and the annotation before the parameters.
    g.need("cairn_gpu.hpp" if device else "cairn_parallel.hpp")
    binder = f"(std::size_t v_{s.binder or s.name})"
    return g.inner(lambda: f"[=] CR_DEVICE{binder}" if device else f"[&]{binder} noexcept", body)


def collected(g: Emitter, name: str, arguments: list[str], templates: Iterable[object] = ()) -> str:
    """A device collector's operation on the execution context, in a program that now includes CUB."""
    g.need("cairn_cub.hpp")
    return execution.call(name, arguments, templates)


def s_parallel(g: Emitter, s: Stmt, es: list[str], body=None):
    unroll = 0
    if s.ref == "device":  # A plan fixes the block, the indices per thread and the unrolled passes of each thread.
        block, per_lane, unroll = s.launch
        schedule = [block or 256, per_lane] if per_lane else [block] if block else []
    else:  # Each index is a block of that many elements, and a plan fixes the claim and the lanes.
        schedule = [s.block, *s.plan]
        while schedule and schedule[-1] == (1 if len(schedule) == 1 else 0):  # defaults say nothing
            schedule.pop()
    lanes = g.lane(s, body or (lambda: g.block(s.body)))
    if s.vector and body is None:  # Each lane runs W indices over chunks when every pointer sits on a chunk.
        return chunks.lower(g, s, es[0], lanes, schedule, s.launch[2])
    if s.stage and body is None:  # Each block loads its tiles, then its lanes read them (staging.py).
        return staging.lower(g, s, es[0], lambda: g.block(s.body), schedule, s.launch[2])
    if s.ref == "device":  # On the thread's execution context: its stream, waited for alone.
        return g.put(execution.call("run", [es[0], lanes, *map(str, schedule)], execution.unrolled(unroll)) + ";")
    g.put(f"cr::par::run({es[0]}, {lanes}{''.join(f', {x}' for x in schedule)});")


def folding(g: Emitter, s: Stmt) -> tuple[str, str, str, str, str]:
    """A reduction's or a scan's element type, its combine over `a` and `b`, its identity, the type it carries
    and that type's start: a device sum carries its overflow flag, which the host checks at the end."""
    ty, op, device = g.type(s.ty), s.op, s.ref == "device"
    combine = (f"cr::{op}<{ty}>(a, b)" if op in WRAPPING else f"(a {'<' if op == 'min' else '>'} b ? a : b)"
               if op in {"min", "max"} else f"static_cast<{ty}>(a {op} b)")  # fmt: skip
    identity, carried = IDENTITY.get(op, "0"), ty
    identity = identity if identity.isdigit() else f"std::numeric_limits<{ty}>::{identity}"
    if op == "+" and s.ty.name in UNSIGNED:  # Checked: the total traps if it overflows, whatever the order.
        combine, carried = ("a + b", f"cr::Sum<{ty}>") if device else (f"cr::add<{ty}>(a, b)", ty)
    return ty, combine, identity, carried, f"static_cast<{ty}>({identity})" if carried == ty else carried + "{}"


def combiner(g: Emitter, s: Stmt, carried: str, combine: str) -> str:
    marked, promise = (" CR_DEVICE", "") if s.ref == "device" else ("", " noexcept")
    return f"[]{marked}({carried} a, {carried} b){promise} {{ return {combine}; }}"


def s_reduce(g: Emitter, s: Stmt, es: list[str], before=None):
    ty, combine, identity, carried, start = g.folding(s)
    i = "v_" + s.binder
    if s.ref == "device" or s.pooled:  # Pooled: blocks the count fixes, each folded in order, then their totals.
        value = g.lane(s, lambda: [before and before(), g.put(f"return {g.expr(s.exprs[1])};")])
        fold, parts = g.combiner(s, carried, combine), [es[0], start]
        total = (collected(g, "reduce_on", [*parts, fold, value], [carried]) if s.ref == "device"
                 else f"cr::par::reduce<{carried}>({es[0]}, {start}, {fold}, {value})")  # fmt: skip
        return g.put(f"const {ty} v_{s.name} = {total}{'' if carried == ty else '.checked()'};")
    count = g.fresh("n")[0]  # Without `parallel`, a host reduction is an in-order fold; its extent is read once.
    g.puts(f"{ty} v_{s.name} = static_cast<{ty}>({identity});", f"const std::size_t {count} = {es[0]};")
    step = [f"const {ty} a = v_{s.name}, b = {es[1]};", f"v_{s.name} = {combine};"]
    g.nest(f"for (std::size_t {i} = 0; {i} < {count}; ++{i}) {{",
              lambda: [before and before(), g.puts(*step)])  # fmt: skip


def s_scan(g: Emitter, s: Stmt, _: list[str]):
    ty, combine, identity, carried, start = g.folding(s)
    out, hi, value, store = s.exprs
    total, exclusive = ("v_" + s.name if s.name else g.fresh("cr_scan_")[0]), str(s.exclusive).lower()
    if s.ref == "device" or s.pooled:  # The runtime writes each element; the yield is a lane's.
        each = g.lane(s, lambda: g.put(f"return {g.expr(value)};"))
        fold, parts = g.combiner(s, carried, combine), [g.expr(out), g.expr(hi), start]
        called = (collected(g, "scan_on", [*parts, fold, each], [exclusive, carried]) if s.ref == "device"
                  else f"cr::par::scan<{exclusive}, {carried}>({', '.join([*parts, fold, each])})")  # fmt: skip
        return g.put(f"const {ty} {total} = {called}{'' if carried == ty else '.checked()'};")
    count, i = g.fresh("n")[0], "v_" + s.binder  # In order: the yield, then the store it feeds, per index.
    stored = f"{g.expr(store)} = {'a' if s.exclusive else total};"
    g.puts(f"{ty} {total} = static_cast<{ty}>({identity});", f"const std::size_t {count} = {g.expr(hi)};",
              f"for (std::size_t {i} = 0; {i} < {count}; ++{i}) {{", f"  const {ty} a = {total}, b = {g.expr(value)};",
              f"  {total} = {combine};", f"  {stored}", "}")  # fmt: skip
    if not s.name:
        g.put(f"static_cast<void>({total});")


def s_compact(g: Emitter, s: Stmt, _: list[str]):
    out, hi, pred, value = (g.expr(e) for e in s.exprs)
    used, i = "v_" + s.name, "v_" + s.binder
    if s.ref == "device":
        keep = g.lane(s, lambda: g.put(f"return {pred};"))
        project = g.lane(s, lambda: g.put(f"return {value};"))
        return g.put(f"const std::size_t {used} = {collected(g, 'compact_on', [out, hi, keep, project])};")

    def selected():  # The only unchecked store: induction gives used <= i < n (Lean: store_index_lt_capacity).
        g.puts(f"{out}[{used}] = {value};", f"++{used};")

    g.put(f"std::size_t {used} = 0;")
    g.nest(f"for (std::size_t {i}=0; {i}<{hi}; ++{i}) {{", lambda: g.nest(f"if ({pred}) {{", selected))


def chain(g: Emitter, chain: fusion.Chain, es: list[str], held: dict[str, Any]):
    """One region whose lanes run each fused body at their own index, in order, each in a block of its own; or,
    when a host reduce ends the chain, one fold whose value for each index runs the bodies first."""
    tail = chain.regions[-1] if chain.regions[-1].tag == "reduce" else None
    lead = tail or chain.regions[0]  # whose loop or lanes carry the index
    index = lead.binder or lead.name
    for name in chain.scratch:
        g.scalar[name] = f"s_{name}"

    def bodies():
        for name in chain.scratch:
            g.put(f"{g.type(held[name])} s_{name}{{}};")
        for r in chain.regions[: -1 if tail else None]:

            def one(r: Stmt = r):
                if (r.binder or r.name) != index:
                    g.put(f"const std::size_t v_{r.binder or r.name} = v_{index};")
                g.block(r.body)

            g.nest("{", one)

    record = {"line": chain.regions[0].line, "regions": len(chain.regions), "scratch_in_lanes": chain.scratch}
    g.fused.setdefault(g.f.name, []).append({**record, **({"into": "reduce"} if tail else {})})
    if tail:
        g.s_reduce(tail, [g.expr(e) for e in tail.exprs], bodies)
    else:
        g.s_parallel(chain.regions[0], es, bodies)
    for name in chain.scratch:
        del g.scalar[name]

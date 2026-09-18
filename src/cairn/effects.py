"""The effect vocabulary, the interprocedural effect fixed point and the operand-order audit.

A row says what a function may do, never what it computes. Rows are joined over the call graph
with borrowed footprints renamed to the caller's arguments; nothing here is mechanically proved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .syntax import Expr, Function, Stmt, fail, root

if TYPE_CHECKING:
    from .checking import Checker

PURE = {"trap", "diverge", "local_read", "local_write", "stack_storage", "zero_init", "ffi_precondition"}
LANE_SAFE = PURE | {"alloc", "free"}  # What a function called from a parallel lane may do.
EFFECTS = LANE_SAFE | {"gpu_alloc", "gpu_free", "indirect_call", "dispatch", "spawn", "join", "atomic", "lock",
                       "io", "mmio", "asm"}  # fmt: skip
EFFECT_FAMILIES = ("ffi:", "transfer:", "par:")  # With read:/write: of a borrow, the whole effect vocabulary.


def exposed(effect: str, borrowed: set[str]) -> str:
    """Private storage keeps its cost visible without exporting a local name."""
    kind, _, name = effect.partition(":")
    return "local_" + kind if kind in {"read", "write"} and name not in borrowed else effect


def fixed_point(c: Checker) -> dict[str, set[str]]:
    """Least fixed point of E_f = L_f + divergence + renamed callee footprints."""
    effects = {n: set(es) for n, es in c.local_effects.items()}
    for n in effects:
        todo, visited = list(c.calls[n]), set()
        while todo:
            q = todo.pop()
            if q == n:
                effects[n].add("diverge")
                break
            if q not in visited:
                visited.add(q)
                todo.extend(c.calls[q])
    universe = {x for es in effects.values() for x in es if not x.startswith(("read:", "write:"))}
    borrowed = {f.name: {n for n, t in f.params if t.mode != "value"} for f in c.p.functions}
    # A recursive argument permutation can need more sweeps than there are functions.
    for _ in range(1 + sum(2 * len(f.params) + len(universe) for f in c.p.functions)):
        changed = False
        for n in effects:
            before = len(effects[n])
            if "indirect_call" in effects[n]:  # A function value may be any function whose address was taken.
                effects[n] |= {
                    x for g in c.address_taken for x in effects[g] if x.partition(":")[0] not in {"read", "write"}
                }
            for callee, mapping in c.call_edges[n]:
                for effect in tuple(effects[callee]):
                    kind, _, formal = effect.partition(":")
                    if kind in {"read", "write"}:
                        if formal not in mapping:
                            fail("E-INTERNAL", "Unmapped callee memory footprint.")
                        if not mapping[formal]:
                            continue  # A temporary or static argument has no caller footprint.
                        effect = exposed(kind + ":" + mapping[formal], borrowed[n])
                    effects[n].add(effect)
            changed |= len(effects[n]) != before
        if not changed:
            break
    else:
        fail("E-EFFECT-LIMIT", "Effect fixed point exceeded its finite universe.")
    for f in c.p.functions:
        if f.effects is not None and not f.extern:
            allowed = set(f.effects) | (PURE if "pure" in f.effects else set())
            reads = "pure" in f.effects
            excess = {e for e in effects[f.name] if e not in allowed and not (reads and e.startswith("read:"))}
            if excess:
                fail("E-EFFECT-CEILING", f"{f.name} exceeds its declared effects.", f, added_effects=sorted(excess))
    return effects


def audit(c: Checker, effects: dict[str, set[str]]):
    """C++ leaves operand order open, so one expression may not contain two operands that could
    observe each other: a nested writing call, a nested take, or a nested opaque call (closure,
    function value, dynamic dispatch) next to anything mutable. `&&` and `||` are sequenced."""

    def names(e: Expr, out: list[Expr]) -> list[Expr]:
        out += [e] if e.tag == "name" else []
        for child in e.args if e.tag != "lambda" else []:
            names(child, out)
        return out

    def nested(e: Expr, at_root: bool, out: list[Expr]) -> list[Expr]:
        if e.tag == "lambda":
            block(e.ref.body)
            return out
        if e.tag == "call" and not at_root:
            direct = isinstance(e.ref, Function) and effects.get(e.ref.name, set())
            if direct and any(x.startswith("write:") or x in {"alloc", "free"} for x in direct):
                fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", e)
            opaque = isinstance(e.ref, tuple) and e.ref[0] in {"indirect", "dispatch"}
            if opaque or any(a.tag == "lambda" for a in e.args) or (e.val == "take" and isinstance(e.ref, tuple)):
                out.append(e)
        for child in e.args:  # `try f()` and `spawn f()` add no operand order: f stays a root.
            nested(child, at_root and e.tag in {"try", "spawn"}, out)
        return out

    def unsequenced(e: Expr) -> list[Expr]:
        return [g for a in e.args for g in unsequenced(a)] if e.tag == "binary" and e.val in {"&&", "||"} else [e]

    def group(e: Expr, at_root: bool):
        found = nested(e, at_root, [])
        for call in found:
            inside = {id(n) for n in names(call, [])}
            others = [n for n in names(e, []) if id(n) not in inside]
            if call.val == "take" and isinstance(call.ref, tuple):
                clash = any(n.val == root(call.args[0]).val for n in others)
            else:  # Whatever the callee closes over or dispatches to might write any mutable name.
                clash = len(found) > 1 or any(n.ref == "mut" for n in others)
            if clash:
                fail("E-EFFECT-ORDER", "Bind this call first: another operand here could observe its writes.", call)

    def block(ss: list[Stmt]):
        for s in ss:
            for i, e in enumerate(s.exprs):
                at_root = s.tag != "compact" and not (s.tag == "assign" and i == 0)
                for part in unsequenced(e):
                    group(part, at_root and part is e)
            block(s.body)
            block(s.other)
            for arm in s.arms:
                block(arm.body)

    for f in c.p.functions:
        block(f.body)

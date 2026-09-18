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
LANE_SAFE = PURE | {"alloc", "free", "atomic", "lock"}  # What a host lane, and whatever it calls, may do.
EFFECTS = LANE_SAFE | {"gpu_alloc", "gpu_free", "indirect_call", "dispatch", "spawn", "join", "io", "mmio", "asm"}
EFFECT_FAMILIES = ("ffi:", "transfer:", "par:")  # With read:/write:/lane: of a parameter, the whole vocabulary.


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
                    if kind in {"read", "write", "lane"}:  # Footprints of a parameter become the caller's argument.
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
    observe each other: a nested writing call, a nested take, a nested closure call next to what
    it writes, or a nested `try` next to an operand that already owns something. `&&` and `||`
    are sequenced."""

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
            kind = e.ref[0] if isinstance(e.ref, tuple) else ""
            known = [e.ref.name] if isinstance(e.ref, Function) else e.ref[4] if kind == "dispatch" else []
            rows = set().union(*(effects.get(name, set()) for name in known))  # Dispatch: every implementor.
            if any(x.startswith("write:") or x in {"alloc", "free"} for x in rows):
                fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", e)
            if kind == "indirect" or any(a.tag == "lambda" for a in e.args) or (e.val == "take" and kind == "builtin"):
                out.append(e)
        for child in e.args:  # `try f()` and `spawn f()` add no operand order: f stays a root.
            nested(child, at_root and e.tag in {"try", "spawn"}, out)
        return out

    def tries(e: Expr, at_root: bool, out: list[Expr]) -> list[Expr]:
        out += [e] if e.tag == "try" and not at_root else []
        for child in e.args if e.tag != "lambda" else []:
            tries(child, at_root and e.tag in {"try", "spawn"}, out)
        return out

    def within(e: Expr, node: Expr) -> bool:
        return e is node or any(within(a, node) for a in e.args)

    def abandons(e: Expr, leaving: Expr) -> bool:
        """Would a return from `leaving` drop an owner that an operand beside it already holds?"""
        if e is leaving or e.tag == "lambda":
            return False
        holds = e.ty is not None and c.kind(e.ty) != "copy" and (e.tag in {"call", "try"} or e.ref == "move")
        return (holds and not within(e, leaving)) or any(abandons(a, leaving) for a in e.args)

    def unsequenced(e: Expr) -> list[Expr]:
        return [g for a in e.args for g in unsequenced(a)] if e.tag == "binary" and e.val in {"&&", "||"} else [e]

    def group(e: Expr, at_root: bool):
        for leaving in tries(e, at_root, []):
            if abandons(e, leaving):
                fail("E-EFFECT-ORDER", "Bind this try first: leaving from here would abandon an owner that another "
                     "operand already holds.", leaving)  # fmt: skip
        found = nested(e, at_root, [])
        for call in found:
            inside = {id(n) for n in names(call, [])}
            others = [n for n in names(e, []) if id(n) not in inside]
            written = {p.split(".")[0].split("[")[0] for a in call.args if a.tag == "lambda"
                       for p, mode in a.ref.captures if mode == "rw"}  # fmt: skip
            if call.val == "take" and call.ref[0] == "builtin":
                clash = any(n.val == root(call.args[0]).val for n in others)
            else:  # A closure writes what it captured rw; one received as a parameter reaches nothing named here.
                clash = len(found) > 1 or any(n.val in written for n in others)
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

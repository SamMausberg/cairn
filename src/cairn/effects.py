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


OBSERVABLE = {"io", "mmio", "asm", "atomic", "lock", "spawn", "join", "indirect_call", "gpu_alloc", "gpu_free"}


def audit(c: Checker, effects: dict[str, set[str]]):
    """Costs stay visible and operands cannot tell which ran first (C++ leaves their order open): a call that
    writes through a borrow or allocates is never a nested operand; a nested call may not move, take or (as a
    closure) write a place that another operand names; a call the outside world can observe (I/O, the machine,
    shared state, a function value) may not sit beside another call; and a nested `try` may not sit beside an
    operand that already owns something. `&&` and `||` are sequenced, and so are a call and its arguments."""

    def mentioned(e: Expr, out: list[tuple[int, str]]) -> list[tuple[int, str]]:
        """Every place an operand names, a closure's captures included, with the node that names it."""
        out += [(id(e), e.val)] if e.tag == "name" else []
        out += [(id(e), p.split(".")[0].split("[")[0]) for p, _ in e.ref.captures] if e.tag == "lambda" else []
        for child in e.args:
            mentioned(child, out)
        return out

    def footprint(call: Expr) -> tuple[set[str], set[str]]:
        """(the caller's places this call may change besides what its row says, the row it is judged by)."""
        kind = call.ref[0] if isinstance(call.ref, tuple) else ""
        named = [root(a).val if root(a).tag == "name" else "" for a in call.args]
        captured = [x for a in call.args if a.tag == "lambda" for x in a.ref.captures]
        changed = {place.split(".")[0].split("[")[0] for place, mode in captured if mode == "rw"}
        changed |= {a.val for a in call.args if a.tag == "name" and a.ref == "move"}  # A moved owner is gone.
        if isinstance(call.ref, Function):
            return changed, effects.get(call.ref.name, set())
        if kind == "dispatch":  # Judged by every implementation.
            return changed, set().union(*(effects.get(t, set()) for t in call.ref[4]))
        if kind == "indirect":
            return changed, {"indirect_call"}
        if kind == "builtin" and call.val in {"take", "swap"}:
            return changed | set(named), set()
        machine = kind == "builtin" and call.val in {"transfer", "mmio_read", "mmio_write", "asm"}
        return changed, {"atomic"} if kind == "shared" else {"io"} if machine else set()

    def nested(e: Expr, at_root: bool, out: list[Expr]) -> list[Expr]:
        if e.tag == "lambda":
            block(e.ref.body)
            return out
        out += [e] if e.tag == "call" and not at_root else []
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
        calls, everything = nested(e, at_root, []), mentioned(e, [])
        for call in calls:
            changed, row = footprint(call)
            if any(x.startswith("write:") or x in {"alloc", "free"} for x in row):  # Syntactic on purpose.
                fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", call)
            mine = {node for node, _ in mentioned(call, [])}
            if any(place in changed for node, place in everything if node not in mine):
                fail("E-EFFECT-ORDER", "Bind this call first: another operand here could observe its writes.", call)
            beside = [d for d in calls if d is not call and not within(call, d) and not within(d, call)]
            beside = [d for d in beside if not (isinstance(d.ref, tuple) and d.ref[0] in {"record", "variant"})]
            if beside and any(x in OBSERVABLE or x.startswith(("ffi:", "transfer:", "par:")) for x in row):
                fail("E-EFFECT-ORDER", "Bind this call first: it can be observed from outside, and the call beside "
                     "it could run before or after it.", call)  # fmt: skip

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

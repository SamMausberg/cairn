"""The phase rule of a cooperative region: between two barriers, no two threads of one block touch one element of a
shared array where either of them writes it.

The checker runs the region's body once for every thread of one block together, each thread with its own index and
the block's names and everything outside the region as symbols (footprints.Poly). A usize value is a number, a
polynomial over those symbols, or unknown; a condition is true, false or unknown for each thread. A thread whose
condition is unknown runs both arms, which only adds accesses. A loop whose bounds are numbers runs its iterations; a
loop whose bounds are symbols runs a first iteration from what came before and two generic ones, so every place a
barrier can split it is seen, with what the loop changes unknown. An `if` every thread of the block takes the same
way, on a condition the checker cannot evaluate, is followed both ways as two alternatives that never meet.

Every access to a shared array is recorded with the phase it falls in: the stretch of the run between two barriers.
When a phase ends, every pair of accesses to one element by two different threads is looked at, and a pair where
either writes is refused:

- both write: E-COOP-CONFLICT;
- the write runs first: the read wants the new value, and a barrier between them would give it (E-COOP-UNORDERED);
- the read runs first: the write replaces what the reader may not have read yet (E-COOP-REUSE).

Two indexes are one element when they are the same number, or the same polynomial; they are apart when they differ by
a nonzero constant. Anything else, an unknown index or two indexes whose difference depends on a symbol, beside an
access by another thread that writes, is refused with E-COOP-UNDECIDED: the rule never accepts what it cannot decide.
An index outside the array is no access, since its bounds guard aborts the thread first.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any

from . import fragments, layouts
from .footprints import Poly, assigned, lent, opaque
from .tree import BOOL, USIZE, Expr, Stmt, fail, nested, root

if TYPE_CHECKING:
    from .checking import Checker
    from .scope import Block

MAX = 2**64 - 1
UNROLL = 4096  # iterations of one loop run one at a time; more are followed as generic ones
WORK = 6_000_000  # thread steps the rule takes for one region before it answers E-COOP-UNDECIDED
ALTERNATIVES = 16  # ways a phase may have begun that the rule keeps apart before it merges them
RANGE = 4096  # elements of a part lent to a call that are recorded one by one


class Trap:
    """A value whose computation traps: the thread aborts the process before it uses it."""

    def __repr__(self) -> str:
        return "trap"


TRAP = Trap()


def same(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def join(a: Any, b: Any) -> Any:
    return a if same(a, b) else None


def as_poly(v: Any) -> Poly:
    return v if isinstance(v, Poly) else Poly.of(v)


def norm(p: Poly) -> Any:
    k = p.constant
    return p if k is None else k if 0 <= k <= MAX else TRAP


def arith(op: str, a: Any, b: Any) -> Any:
    """One usize operation, as the machine does it: the checked ones trap where the program's guard would."""
    if a is TRAP or b is TRAP:
        return TRAP
    if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
        return None
    if isinstance(a, int) and isinstance(b, int):
        if (op in {"/", "%"} and b == 0) or (op in {"shr", "shl_wrap"} and b > 63):
            return TRAP
        r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a // b, "%": lambda: a % b,
             "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b, "min": lambda: min(a, b),
             "max": lambda: max(a, b), "add_wrap": lambda: (a + b) & MAX, "sub_wrap": lambda: (a - b) & MAX,
             "mul_wrap": lambda: (a * b) & MAX, "shr": lambda: a >> b,
             "shl_wrap": lambda: (a << b) & MAX}.get(op)  # fmt: skip
        if r is None:
            return None
        value = r()
        return value if 0 <= value <= MAX else TRAP
    if op in {"+", "-", "*"}:
        pa, pb = as_poly(a), as_poly(b)
        return norm(pa + pb if op == "+" else pa - pb if op == "-" else pa * pb)
    return opaque(op, as_poly(a), as_poly(b))


def compare(op: str, a: Any, b: Any) -> Any:
    if a is TRAP or b is TRAP:
        return TRAP
    if a is None or b is None:
        return None
    if isinstance(a, bool) or isinstance(b, bool):
        return (a == b) if op == "==" else (a != b) if op == "!=" else None
    d = as_poly(a) - as_poly(b)
    k = d.constant
    if k is None:
        return None
    return {"<": k < 0, "<=": k <= 0, ">": k > 0, ">=": k >= 0, "==": k == 0, "!=": k != 0}[op]


def logic(op: str, a: Any, b: Any) -> Any:
    """`a && b` or `a || b` for one thread. Where `a` is unknown and `b` traps, the thread either stops at `b` or
    goes on with what `a` alone decides, false for `&&` and true for `||`, so that arm still runs."""
    if a is TRAP:
        return TRAP
    if op == "&&":
        return False if a is False else b if a is True else (False if b is False or b is TRAP else None)
    return True if a is True else b if a is False else (True if b is True or b is TRAP else None)


@dataclass
class Event:
    array: str
    index: Any  # a value per thread (a list) or one for all; a part is ("part", lo, hi)
    write: bool
    node: Any
    time: float
    mask: list[int] | None  # which threads make it: 0 no, 1 yes, 2 perhaps; None all


@dataclass
class Loop:
    broken: list[int]
    skipped: list[int]


@dataclass
class Phases:
    c: Any
    block: Any
    counts: dict[str, int]  # shared array -> elements
    T: int = 0
    open: list[list[Event]] = field(default_factory=lambda: [[]])
    time: int = 0
    loops: list[Loop] = field(default_factory=list)
    work: int = 0
    fresh: Any = field(default_factory=count)
    node: Any = None

    def __post_init__(self):
        self.T = self.block.count

    # Values, one per thread or one for all -------------------------------------------------------------------

    def each(self, f, *vs: Any) -> Any:
        if not any(isinstance(v, list) for v in vs):
            self.work += 1
            return f(*vs)
        self.work += self.T
        if self.work > WORK:
            fail("E-COOP-UNDECIDED", f"The phase rule would take more than {WORK} thread steps for this region; "
                 "split it, or give its loops fewer iterations.", self.node)  # fmt: skip
        return [f(*(v[t] if isinstance(v, list) else v for v in vs)) for t in range(self.T)]

    def at(self, v: Any, t: int) -> Any:
        return v[t] if isinstance(v, list) else v

    def restrict(self, mask: list[int] | None, cond: Any, want: bool) -> list[int] | None:
        """The threads of `mask` for which `cond` is `want`, perhaps where it is unknown."""
        if not isinstance(cond, list) and mask is None:
            return None if cond is want else [0] * self.T if cond is not None else [2] * self.T
        out = []
        for t in range(self.T):
            m = 1 if mask is None else mask[t]
            c = self.at(cond, t)
            out.append(0 if not m else m if c is want else 2 if c is None else 0)
        return out

    def live(self, mask: list[int] | None) -> list[int] | None:
        """Leave out the threads that broke out of, or continued, the innermost loop."""
        if not self.loops:
            return mask
        loop = self.loops[-1]
        dead = [max(a, b) if 1 not in (a, b) else 1 for a, b in zip(loop.broken, loop.skipped, strict=True)]
        if not any(dead):
            return mask
        out = []
        for t in range(self.T):
            m = 1 if mask is None else mask[t]
            out.append(0 if dead[t] == 1 or not m else 2 if dead[t] == 2 else m)
        return out

    def assign(self, env: dict[str, Any], name: str, value: Any, mask: list[int] | None):
        if mask is None or name not in env:
            env[name] = value
            return
        old = env[name]
        env[name] = [self.at(value, t) if m == 1 else join(self.at(old, t), self.at(value, t)) if m == 2
                     else self.at(old, t) for t, m in enumerate(mask)]  # fmt: skip

    # Expressions ---------------------------------------------------------------------------------------------

    def expr(self, e: Expr, env: dict[str, Any], mask: list[int] | None, now: float) -> Any:
        """The value of `e` for each thread, recording every shared array element it reads."""
        tag = e.tag
        if tag == "lambda":
            return self.closure(e, env, mask)
        if tag == "int":
            return int(e.val) if e.ty == USIZE else None
        if tag == "bool":
            return e.val == "true"
        if tag == "name":
            if isinstance(e.ref, Expr):
                return self.expr(e.ref, env, mask, now)
            if e.val in env:
                return env[e.val]
            if e.ty == USIZE and e.val in self.c.env:
                return Poly.of(e.val)
            return None
        if tag == "index":
            base = e.args[0]
            i = self.expr(e.args[1], env, mask, now)
            if base.tag == "name" and base.val in self.counts:
                self.record(base.val, i, False, e, now, mask)
            elif base.tag != "name":
                self.expr(base, env, mask, now)
            return None
        if tag == "call":
            return self.call(e, env, mask, now)
        if tag == "binary" and e.val in {"&&", "||"}:
            a = self.expr(e.args[0], env, mask, now)
            b = self.expr(e.args[1], env, self.restrict(mask, a, e.val == "&&") if a is not False else mask, now)
            return self.each(lambda x, y: logic(e.val, x, y), a, b)
        values = [self.expr(a, env, mask, now) for a in e.args]
        if tag == "unary" and e.val == "!":
            return self.each(lambda x: (not x) if isinstance(x, bool) else x if x is TRAP else None, values[0])
        if tag == "binary" and e.val in {"<", "<=", ">", ">=", "==", "!="}:
            if e.args[0].ty not in (USIZE, BOOL):
                return None
            return self.each(lambda x, y: compare(e.val, x, y), *values)
        if tag == "binary" and e.ty == USIZE:
            return self.each(lambda x, y: arith(e.val, x, y), *values)
        return None

    def call(self, e: Expr, env: dict[str, Any], mask: list[int] | None, now: float) -> Any:
        given = {id(a): mode for a, mode in lent(e)}
        values = []
        for a in e.args:
            mode = given.get(id(a))
            if mode is None or a.tag == "lambda":
                values.append(self.expr(a, env, mask, now))
                continue
            values.append(None)
            name = root(a).val if root(a).tag == "name" else ""
            if name not in self.counts:
                for x in a.args[1:]:
                    self.expr(x, env, mask, now)
                if mode == "rw" and name in env:  # a local lent to be written holds what the callee left
                    self.assign(env, name, None, mask)
                continue
            if a.tag == "slice":
                lo, hi = (self.expr(x, env, mask, now) for x in a.args[1:3])
                self.record(name, ("part", lo, hi), mode == "rw", a, now, mask)
            elif a.tag == "index":
                i = self.expr(a.args[1], env, mask, now)
                if mode == "rw":
                    self.record(name, i, False, a, now, mask)
                self.record(name, i, mode == "rw", a, now + 0.5, mask)
            else:
                self.record(name, ("part", 0, self.counts[name]), mode == "rw", a, now, mask)
        if isinstance(e.ref, tuple) and e.ref[:1] == ("layout",):  # `L.at(r, c)`, `D.row(t, v)`: compiler/layouts.py
            return self.each(lambda *xs: laid(self.c, e, xs), *values)
        if e.val in fragments.OPERATIONS and isinstance(e.ref, tuple) and len(e.ref) == 3:
            self.fragment(e, values, mask, now)
            return None
        if not isinstance(e.ref, tuple) or e.ty != USIZE:
            return None
        if e.val == "len" and e.args and e.args[0].ty is not None:
            extent = e.args[0].ty.extent
            return int(extent) if extent.isdigit() else Poly.of(extent) if extent else None
        if e.val == "usize" and len(values) == 1 and e.args[0].ty == USIZE:
            return values[0]
        if len(values) == 2 and e.val in {"min", "max", "shr", "shl_wrap", "add_wrap", "sub_wrap", "mul_wrap"}:
            return self.each(lambda x, y: arith(e.val, x, y), *values)
        return None

    def closure(self, e: Expr, env: dict[str, Any], mask: list[int] | None) -> None:
        """A closure runs when, and as often as, the callee calls it: a local it writes holds an unknown value from
        here on. One that reaches a shared array is refused by cooperative.py before this rule runs."""
        for place, mode in getattr(e.ref, "captures", ()):
            name = place.split(".", 1)[0].split("[", 1)[0]
            if mode == "rw" and name in env:
                self.assign(env, name, None, mask)
        return None

    def fragment(self, e: Expr, values: list[Any], mask: list[int] | None, now: float):
        """A fragment load reads every element of its fragment in every thread of the warp; a store writes each
        element in the one thread whose lane holds it (compiler/fragments.py, `footprint`)."""
        array = root(e.args[0]).val if root(e.args[0]).tag == "name" else ""
        if array not in self.counts:
            return  # a read-only device view: nothing in the region writes it
        found: list[Any] = []
        for t in range(self.T):
            i, j = self.at(values[2], t), self.at(values[3], t)
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (i, j)):
                found.append(None)  # an unknown fragment: every element it may touch is unknown
                continue
            try:
                found.append(fragments.footprint(self.c, e, i, j))
            except IndexError:
                found.append(TRAP)  # the coordinates' guard aborts the thread first
        size = next((len(f) for f in found if isinstance(f, list)), 1)
        for k in range(size):
            index = [f[k][0] if isinstance(f, list) else f for f in found]
            holder = next((f[k][1] for f in found if isinstance(f, list)), None)
            only = (
                None
                if holder is None
                else [(1 if mask is None else mask[t]) * (t % 32 == holder) for t in range(self.T)]
            )
            self.record(array, index, holder is not None, e, now, mask if only is None else only)

    def record(self, array: str, index: Any, write: bool, node: Any, time: float, mask: list[int] | None):
        for alternative in self.open:
            alternative.append(Event(array, index, write, node, time, mask))

    # Statements ----------------------------------------------------------------------------------------------

    def stmts(self, ss: list[Stmt], env: dict[str, Any], mask: list[int] | None):
        for s in ss:
            here = self.live(mask)
            if here is not None and not any(here):
                return
            self.stmt(s, env, here)

    def stmt(self, s: Stmt, env: dict[str, Any], mask: list[int] | None):
        self.time += 1
        now, tag = float(self.time), s.tag
        if synchronizes(s, self.block.pipelines):  # a barrier, or a pipeline's wait, which is one
            return self.barrier(s)
        if tag == "assign":
            target, value = s.exprs
            v = self.expr(value, env, mask, now)
            if target.tag == "index" and target.args[0].tag == "name" and target.args[0].val in self.counts:
                self.record(target.args[0].val, self.expr(target.args[1], env, mask, now), True, target, now + 0.5,
                            mask)  # fmt: skip
            elif target.tag == "index":
                self.expr(target.args[1], env, mask, now)
            elif target.tag == "name" and target.val in env:
                self.assign(env, target.val, v, mask)
            return None
        values = [self.expr(e, env, mask, now) for e in s.exprs if tag not in {"for", "while"}]
        if tag in {"let", "reg"}:
            env[s.name] = values[0]
        elif tag in {"warp_reduce", "unpack", "stack", "shared", "pipeline"}:
            for name in [s.name, *(n.val for n in s.other_names)]:
                env[name] = None
        elif tag == "if":
            self.branch(s, values[0], env, mask)
        elif tag == "for" or tag == "while":
            self.loop(s, env, mask, now)
        elif tag == "match":
            for arm in s.arms:
                inner = {**env, arm.binder: None}
                self.stmts(arm.body, inner, [2 if m else 0 for m in mask] if mask else [2] * self.T)
                for name in env:
                    env[name] = self.each(join, env[name], inner.get(name))
        elif tag in {"block", "unsafe"}:
            self.stmts(s.body, env, mask)
        elif tag == "asm" and s.assembly is not None:  # an address reaches the whole array (compiler/machine.py)
            for effect in s.assembly.effects:
                kind, _, name = effect.partition(":")
                if kind in {"read", "write"} and name in self.counts:
                    whole = ("part", 0, self.counts[name])
                    self.record(name, whole, kind == "write", s, now + 0.5 * (kind == "write"), mask)
            for name, *_ in s.assembly.outputs:
                env[name] = None
        elif tag in {"break", "continue"} and self.loops:
            loop = self.loops[-1]
            held = loop.broken if tag == "break" else loop.skipped
            for t in range(self.T):
                m = 1 if mask is None else mask[t]
                held[t] = 1 if m == 1 or held[t] == 1 else max(held[t], m)
        return None

    def barrier(self, node: Any):
        for alternative in self.open:
            self.check(alternative)
        self.open = [[]]

    def branch(self, s: Stmt, cond: Any, env: dict[str, Any], mask: list[int] | None):
        if not isinstance(cond, list) and mask is None and cond is not TRAP:
            if isinstance(cond, bool):
                return self.stmts(s.body if cond else s.other, env, None)
            before, results = self.open, []  # one way for the whole block, unknown which: two alternatives
            snapshot = dict(env)
            arms = []
            for body in (s.body, s.other):
                self.open = [list(x) for x in before]
                inner = dict(snapshot)
                self.stmts(body, inner, None)
                results += self.open
                arms.append(inner)
            self.open = self.merged(results)
            for name in env:
                env[name] = self.each(join, arms[0].get(name), arms[1].get(name))
            return None
        yes, no = self.restrict(mask, cond, True), self.restrict(mask, cond, False)
        then, other = dict(env), dict(env)
        if yes is None or any(yes):
            self.stmts(s.body, then, yes)
        if no is None or any(no):
            self.stmts(s.other, other, no)
        for name in env:
            a, b = then.get(name), other.get(name)
            env[name] = self.each(lambda c, x, y, old: old if c is TRAP else x if c is True else y if c is False
                                  else join(x, y), cond, a, b, env[name])  # fmt: skip
        return None

    def merged(self, alternatives: list[list[Event]]) -> list[list[Event]]:
        if len(alternatives) <= ALTERNATIVES:
            return alternatives
        return [[e for a in alternatives for e in a]]  # every access together: more pairs, never fewer

    def loop(self, s: Stmt, env: dict[str, Any], mask: list[int] | None, now: float):
        whole = s.tag == "for" and s.name != "_"
        lo = hi = None
        if s.tag == "for":
            lo, hi = (self.expr(e, env, mask, now) for e in s.exprs[:2])
        synchronizing = holds_barrier(s.body, self.block.pipelines)
        numbers = all(isinstance(v, int) and not isinstance(v, bool) for v in (lo, hi))
        if s.tag == "for" and numbers and hi - lo <= UNROLL:
            return self.iterate(s, env, mask, range(lo, hi))
        if s.tag == "for" and (isinstance(lo, list) or isinstance(hi, list)) and not synchronizing:
            los, his = [self.at(lo, t) for t in range(self.T)], [self.at(hi, t) for t in range(self.T)]
            if all(v is TRAP or (isinstance(v, int) and not isinstance(v, bool)) for v in los + his):
                start = min((v for v in los if v is not TRAP), default=0)
                end = max((v for v in his if v is not TRAP), default=0)
                if end - start <= UNROLL:
                    return self.iterate(s, env, mask, range(start, end), los, his)
        if s.tag == "while" and mask is None and self.counted(s, env, now):
            return None
        for name in assigned(s.body) & set(env):
            env[name] = None
        if synchronizing:  # generic iterations, where each phase may begin in the one before
            return self.generic(s, env, mask, lo)
        for _ in range(2):  # two generic iterations meet in one phase: their accesses are all there
            inner = dict(env)
            if s.tag == "while":
                self.expr(s.exprs[0], inner, mask, now)
            if whole:
                inner[s.name] = Poly.of(f"{s.name}#{next(self.fresh)}") if not isinstance(lo, list) else None
            self.loops.append(Loop([0] * self.T, [0] * self.T))
            self.stmts(s.body, inner, [2 if m else 0 for m in mask] if mask else [2] * self.T)
            self.loops.pop()
        for name in assigned(s.body) & set(env):
            env[name] = None
        return None

    def iterate(self, s: Stmt, env: dict[str, Any], mask: list[int] | None, steps: range, los=None, his=None):
        loop = Loop([0] * self.T, [0] * self.T)
        self.loops.append(loop)
        for k in steps:
            loop.skipped = [0] * self.T
            here = mask
            if los is not None:
                here = [0 if (mask is not None and not mask[t]) or not (isinstance(los[t], int) and los[t] <= k)
                        or not (isinstance(his[t], int) and k < his[t]) else (1 if mask is None else mask[t])
                        for t in range(self.T)]  # fmt: skip
            if s.name != "_":
                env[s.name] = k
            self.stmts(s.body, env, here)
            if all(b == 1 for b in loop.broken):
                break
        self.loops.pop()
        env.pop(s.name, None)

    def counted(self, s: Stmt, env: dict[str, Any], now: float) -> bool:
        """Run a while loop whose condition the block evaluates alike, one iteration at a time, when it ends within
        UNROLL iterations; otherwise leave everything as it was and say so."""
        saved = (dict(env), [list(x) for x in self.open], self.time)
        loop = Loop([0] * self.T, [0] * self.T)
        self.loops.append(loop)
        for _ in range(UNROLL):
            cond = self.expr(s.exprs[0], env, None, now)
            if not isinstance(cond, bool):
                break
            if not cond or any(loop.broken):
                self.loops.pop()
                return True
            loop.skipped = [0] * self.T
            self.stmts(s.body, env, None)
        self.loops.pop()
        env.clear()
        env.update(saved[0])
        self.open, self.time = saved[1], saved[2]
        return False

    def generic(self, s: Stmt, env: dict[str, Any], mask: list[int] | None, lo: Any):
        """A loop with a barrier inside and a trip count the rule does not know, all threads in step: no iteration
        (the phase before goes on after it), a first one begun in the phase before, and a generic iteration s + 1
        begun in the tail of iteration s, whose own tail goes on after the loop."""
        before = [list(x) for x in self.open]
        name = s.name if s.tag == "for" and s.name != "_" else ""

        def once(scope: dict[str, Any], value: Any):
            if name:
                scope[name] = value
            if s.tag == "while":
                self.expr(s.exprs[0], scope, mask, float(self.time))
            self.stmts(s.body, scope, mask)

        self.open = [list(x) for x in before]
        once(dict(env), lo)
        after_first = self.open
        self.open = [[]]
        atom = Poly.of(f"{name or 'loop'}#{next(self.fresh)}")
        generic = dict(env)
        once(generic, atom)
        for widened in assigned(s.body) & set(generic):
            generic[widened] = None
        once(generic, atom + Poly.of(1))
        self.open = self.merged([*before, *after_first, *self.open])
        for widened in assigned(s.body) & set(env):
            env[widened] = None

    # The rule ------------------------------------------------------------------------------------------------

    def who(self, t: int) -> str:
        names, extents, parts = self.block.threads, self.block.extents, []
        for n, k in zip(names, extents, strict=True):
            parts.append(f"{n} = {t % k}")
            t //= k
        return "thread " + ", ".join(parts)

    def check(self, events: list[Event]):
        """Refuse a phase in which two threads touch one element of a shared array and either writes."""
        arrays: dict[str, list[Event]] = {}
        for ev in events:
            arrays.setdefault(ev.array, []).append(ev)
        for array, found in arrays.items():
            if any(ev.write for ev in found):
                self.check_array(array, found)

    def check_array(self, array: str, found: list[Event]):
        size = self.counts[array]
        cells: dict[tuple, list[tuple[int, Event]]] = {}
        vague: list[tuple[int, Event]] = []
        for ev in found:
            for t in range(self.T):
                m = 1 if ev.mask is None else ev.mask[t]
                if not m:
                    continue
                for idx in self.elements(ev.index, t):
                    if idx is TRAP:
                        continue
                    if idx is None or isinstance(idx, bool):
                        vague.append((t, ev))
                        continue
                    if isinstance(idx, int):
                        if idx < size:
                            cells.setdefault((frozenset(), idx), []).append((t, ev))
                    else:
                        cells.setdefault((idx.key, idx.offset), []).append((t, ev))
        for (key, offset), touches in cells.items():
            for writer in (x for x in touches if x[1].write):
                others = [x for x in touches if x[0] != writer[0]]
                if others:
                    element = repr(Poly({**dict(key), (): offset})) if key else str(offset)
                    self.refuse(array, element, writer, next((x for x in others if x[1].write), others[0]))
        keyed: dict[frozenset, list[tuple[int, Event]]] = {}
        for (key, _), touches in cells.items():
            keyed.setdefault(key, []).extend(touches)
        groups = list(keyed.values())
        for i, a in enumerate(groups):
            for b in groups[i + 1 :]:
                self.apart(array, a, b)
        everything = [*vague, *(y for g in groups for y in g)]
        for t, ev in vague:
            self.apart(array, [(t, ev)], everything)

    def elements(self, index: Any, t: int) -> list[Any]:
        if isinstance(index, tuple):
            lo, hi = self.at(index[1], t), self.at(index[2], t)
            if lo is TRAP or hi is TRAP:
                return []
            if isinstance(lo, int) and isinstance(hi, int) and not isinstance(lo, bool) and hi - lo <= RANGE:
                return list(range(lo, hi))
            return [None]
        return [self.at(index, t)]

    def apart(self, array: str, a: list[tuple[int, Event]], b: list[tuple[int, Event]]):
        """Accesses whose indexes differ by a symbol, or are unknown: they may meet, so a write among them by one
        thread beside any access by another is undecided."""
        for writes, others in ((a, b), (b, a)):
            for x in (x for x in writes if x[1].write):
                y = next((y for y in others if y[0] != x[0]), None)
                if y is not None:
                    self.undecided(array, x, y)

    def undecided(self, array: str, x: tuple[int, Event], y: tuple[int, Event]):
        first, second = sorted((x, y), key=lambda z: z[1].time)
        fail("E-COOP-UNDECIDED", f"The checker cannot tell whether {array}[...] at line {first[1].node.line} "
             f"({self.who(first[0])}) and at line {second[1].node.line} ({self.who(second[0])}) are one element, "
             "and one of them writes it in the same phase. Index shared arrays by the thread and loop names and "
             "constants, or put a barrier between the two.", second[1].node, array=array)  # fmt: skip

    def refuse(self, array: str, element: str, x: tuple[int, Event], y: tuple[int, Event]):
        a, b = (x, y) if x[1].time <= y[1].time else (y, x)
        where = f"line {a[1].node.line}" if a[1].node.line == b[1].node.line else \
            f"lines {a[1].node.line} and {b[1].node.line}"  # fmt: skip
        if x[1].write and y[1].write:
            fail("E-COOP-CONFLICT", f"{array}[{element}] is written by {self.who(a[0])} and by {self.who(b[0])} in "
                 f"the same phase, at {where}: two threads write one element with no barrier between them, and "
                 "the last to write wins. Give each thread its own element.", b[1].node, array=array,
                 threads=[a[0], b[0]])  # fmt: skip
        if a[1].write:
            fail("E-COOP-UNORDERED", f"{self.who(b[0])} reads {array}[{element}] at line {b[1].node.line}, which "
                 f"{self.who(a[0])} writes at line {a[1].node.line} in the same phase: nothing makes the write "
                 f"happen first. Put a barrier {between(a[1].node.line, b[1].node.line)}.", b[1].node, array=array,
                 write=a[1].node.line, read=b[1].node.line)  # fmt: skip
        fail("E-COOP-REUSE", f"{self.who(b[0])} rewrites {array}[{element}] at line {b[1].node.line} while "
             f"{self.who(a[0])} may still be reading what it held, at line {a[1].node.line}, in the same phase. "
             f"Put a barrier {between(a[1].node.line, b[1].node.line)}, so every thread has read the old value "
             "first.", b[1].node, array=array, read=a[1].node.line, write=b[1].node.line)  # fmt: skip


def laid(c: Any, e: Expr, given: tuple[Any, ...]) -> Any:
    """A layout's answer for one thread's arguments: a number, TRAP where its guard aborts, None for a symbol."""
    try:
        return layouts.apply(c, e, given)
    except IndexError:
        return TRAP


def between(first: int, second: int) -> str:
    """Where a barrier orders an access at line `first` before one at line `second` that runs after it."""
    if first == second:
        return f"after line {first}, before it runs again"
    return f"after line {first} and before line {second} runs"


def synchronizes(s: Stmt, pipelines: Collection[str] = ()) -> bool:
    """A statement that ends a phase for the whole block: a barrier, or a pipeline's wait, typed or not yet."""
    if s.tag == "barrier":
        return True
    e = s.exprs[0] if s.tag == "expr" and s.exprs else None
    if e is None or e.tag != "call":
        return False
    if isinstance(e.ref, tuple) and e.ref[:1] == ("stage",):
        return e.ref[2] == "wait"
    head, _, op = e.val.rpartition(".")
    return op == "wait" and head in pipelines


def holds_barrier(ss: list[Stmt], pipelines: Collection[str] = ()) -> bool:
    return any(synchronizes(s, pipelines) or holds_barrier(nested(s), pipelines) for s in ss)


def check(c: Checker, s: Stmt, block: Block, grid: list[Any]):
    """Run the region's body for every thread of one block and refuse a phase with two threads at one element."""
    if not block.shared:
        return
    run = Phases(c, block, {name: n for name, (_, n, _) in block.shared.items()}, node=s)
    env: dict[str, Any] = {}
    t = list(range(block.count))
    for name, extent in zip(block.threads, block.extents, strict=True):
        env[name] = [x % extent for x in t]
        t = [x // extent for x in t]
    for name in block.grid:
        env[name] = Poly.of(name)
    run.stmts(s.body, env, None)
    run.barrier(s)  # the block's end: every thread is done before the next block uses its arrays

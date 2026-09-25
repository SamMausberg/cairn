"""Where the threads of a cooperative region write memory it did not declare: every element of such an array is
written by at most one thread of one block.

An index is a polynomial over atoms: the region's block and thread names, the counter of every loop it runs inside,
and the values the region cannot change (parameters, lets outside it, the grid's extents), each a usize. The rule
asks one question of every array the region writes: may two different (block, thread) pairs write one element? It
answers no only when it shows the index is a mixed-radix number: the terms order so that each weight is larger than
the most every lighter term adds up to. Then the index names every digit, and the block and thread digits say whose
element it is.

    out[(bx * 32 + ty + 8 * k) * (32 * gy) + by * 32 + tx]    digits tx < 32, by < gy, ty < 8, k < 4, bx < gx
                                                               weights 1, 32, 32 gy, 256 gy, 1024 gy

A weight is compared with a sum by sign: the difference must have no negative coefficient on anything but its
constant, and be positive where every atom is at its least (0, or 1 for a grid extent, since a block runs only when
every extent is at least 1). A condition `a < b` the write sits under bounds `a` when `a` is exactly a sum of the
digits placed so far, all counting the same way, which is how a guarded transpose `if col < h { out[row * h + col] =
v; }` is shown. A digit's range may not move with another digit (`for c in l..l + 2`), since then two threads' values
of it lie in two different ranges. A loop counter is a digit the thread may repeat, so a thread may rewrite its own
element; a block or thread name the index does not use is refused unless conditions pin it to one value the same in
every thread (`if t == 255 { out[b] = total; }`).

A read of an array the region writes must name the element its own thread writes: a write's polynomial, its loop
counters renamed to the read's over the same ranges, standing under every condition the write does (`own`). Another
block may be writing any other element, and no barrier orders two blocks. Anything else, an index the
checker cannot put in this form among them, is refused with E-COOP-GLOBAL: the rule never guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count, permutations
from typing import TYPE_CHECKING, Any

from ..primitives.atomics import Atomic, mixed
from ..primitives.wide import Wide
from ..syntax.tree import USIZE, Expr, Function, Stmt, fail, is_view, nested, root

if TYPE_CHECKING:
    from ..check.checking import Checker
    from ..check.scope import Block

Monomial = tuple[str, ...]


class Poly:
    """A polynomial with integer coefficients over named usize atoms; the empty monomial is the constant."""

    __slots__ = ("terms",)

    def __init__(self, terms: dict[Monomial, int] | None = None):
        self.terms = {m: k for m, k in (terms or {}).items() if k}

    @staticmethod
    def of(value: int | str) -> Poly:
        return Poly({(): value}) if isinstance(value, int) else Poly({(value,): 1})

    def __add__(self, other: Poly) -> Poly:
        out = dict(self.terms)
        for m, k in other.terms.items():
            out[m] = out.get(m, 0) + k
        return Poly(out)

    def __neg__(self) -> Poly:
        return Poly({m: -k for m, k in self.terms.items()})

    def __sub__(self, other: Poly) -> Poly:
        return self + -other

    def __mul__(self, other: Poly) -> Poly:
        out: dict[Monomial, int] = {}
        for m, k in self.terms.items():
            for n, j in other.terms.items():
                key = tuple(sorted(m + n))
                out[key] = out.get(key, 0) + k * j
        return Poly(out)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Poly) and self.terms == other.terms

    def __hash__(self) -> int:
        return hash(frozenset(self.terms.items()))

    @property
    def constant(self) -> int | None:
        """The value, when no atom is left."""
        return self.terms.get((), 0) if all(m == () for m in self.terms) else None

    @property
    def offset(self) -> int:
        return self.terms.get((), 0)

    @property
    def key(self) -> frozenset:
        """Everything but the constant: two values with one key differ exactly by their offsets."""
        return frozenset((m, k) for m, k in self.terms.items() if m)

    def atoms(self) -> set[str]:
        return {a for m in self.terms for a in m}

    def weight(self, atom: str) -> Poly:
        """The coefficient of `atom` in terms that hold it once."""
        out: dict[Monomial, int] = {}
        for m, k in self.terms.items():
            if atom in m:
                rest = list(m)
                rest.remove(atom)
                out[tuple(rest)] = out.get(tuple(rest), 0) + k
        return Poly(out)

    def __repr__(self) -> str:
        parts = [(("*".join(m) if k == 1 else f"{k}*{'*'.join(m)}") if m else str(k))
                 for m, k in sorted(self.terms.items())]  # fmt: skip
        return " + ".join(parts) or "0"


def positive(p: Poly, least: dict[str, int]) -> bool:
    """p > 0 wherever every atom is at least its least value: no negative coefficient but the constant's, and
    positive at the least values, so it only grows from there."""
    if any(k < 0 for m, k in p.terms.items() if m):
        return False
    total = 0
    for m, k in p.terms.items():
        term = k
        for a in m:
            term *= least.get(a, 0)
        total += term
    return total > 0


def heavier(a: Poly, b: Poly, least: dict[str, int]) -> bool:
    """a is at least b everywhere and is not b: 64 n is heavier than n, though at n = 0 they are equal. Which of two
    weights goes first only orders the search; `positive` still decides that each fits."""
    return positive(a - b, least) or (a != b and all(k >= 0 for k in (a - b).terms.values()))


def opaque(op: str, a: Poly, b: Poly) -> Poly:
    """A value computed from others in a way the domain does not follow: one atom, the same for the same inputs."""
    return Poly.of(f"({a!r} {op} {b!r})")


def assigned(ss: list[Stmt]) -> set[str]:
    """Every local name a statement list assigns whole."""
    found: set[str] = set()
    for s in ss:
        if s.tag == "assign" and s.exprs[0].tag == "name":
            found.add(s.exprs[0].val)
        found |= assigned(nested(s))
    return found


def arguments(e: Expr) -> list[Expr]:
    """A call's arguments as the rules see them: a wide access reaches the part x[i .. i + K] of its array
    (compiler/primitives/wide.py), and that part stands in its array's place."""
    if isinstance(e.ref, tuple) and len(e.ref) == 2 and isinstance(e.ref[1], Wide) and e.args:
        return [e.ref[1].part, *e.args[1:]]
    return e.args


def lent(e: Expr) -> list[tuple[Expr, str]]:
    """The arguments a call lends, with the mode each is lent in: rw for what it may write."""
    if isinstance(e.ref, tuple) and len(e.ref) == 2 and isinstance(e.ref[1], Wide):  # a wide load or store
        return [(e.ref[1].part, "store" if e.val == "store_wide" else "ro")]  # a store writes every element
    if isinstance(e.ref, tuple) and len(e.ref) == 2 and isinstance(e.ref[1], Atomic):  # an atomic update
        return [(e.args[0], "atomic")]
    if isinstance(e.ref, Function):
        return [(a, t.mode) for a, (_, t) in zip(e.args, e.ref.params, strict=False) if t.mode != "value"]
    if isinstance(e.ref, tuple) and e.ref[:1] == ("stage",) and e.ref[2] == "fill":  # a pipeline reads its source
        return [(e.args[1], "ro")]
    if isinstance(e.ref, tuple) and e.val in {"swap", "take"}:
        return [(a, "rw") for a in e.args]
    return []


def natural(e: Expr) -> int | None:
    """The value of a usize expression built from literals, named constants and static naturals, when it is one."""
    if e.tag == "int":
        return int(e.val)
    if e.tag == "name":
        if isinstance(e.ref, Expr):
            return natural(e.ref)
        return e.ref if isinstance(e.ref, int) and not isinstance(e.ref, bool) else None
    if e.tag == "binary" and e.val in {"+", "-", "*", "/", "%"} and len(e.args) == 2:
        a, b = (natural(x) for x in e.args)
        if a is None or b is None or (e.val in {"/", "%"} and b == 0):
            return None
        value = {"+": a + b, "-": a - b, "*": a * b, "/": a // max(b, 1), "%": a % max(b, 1)}[e.val]
        return value if value >= 0 else None
    return None


@dataclass
class Digit:
    lo: Poly | None
    hi: Poly | None  # None: a bound the checker cannot state
    agent: bool  # a block or thread name: two of them are two writers


@dataclass
class Site:
    node: Expr
    index: Poly | None  # None: not a polynomial the rule can read
    write: bool
    facts: tuple  # (lhs, bound) pairs, lhs <= bound, of the conditions it sits under
    atomic: bool = False  # an atomic update (atomics.py), which never races another and counts toward no writer


@dataclass
class Globals:
    """One walk over a cooperative body with every thread and block at once, its names as atoms."""

    c: Any
    outer: set[str]
    digits: dict[str, Digit] = field(default_factory=dict)
    least: dict[str, int] = field(default_factory=dict)
    sites: dict[str, list[Site]] = field(default_factory=dict)
    facts: list[tuple[Poly, Poly]] = field(default_factory=list)
    fresh: Any = field(default_factory=count)

    def poly(self, e: Expr, env: dict[str, Poly | None], depth: int = 4) -> Poly | None:
        """The value of a usize expression as a polynomial, when it is one."""
        if e.ty != USIZE:
            return None
        if e.tag == "int":
            return Poly.of(int(e.val))
        if e.tag == "name":
            if isinstance(e.ref, Expr):  # a named constant: its literal
                return self.poly(e.ref, env, depth)
            if isinstance(e.ref, int) and not isinstance(e.ref, bool):  # a static natural, K of scale_blocks[64]
                return Poly.of(e.ref)
            if e.val in env:
                return env[e.val]
            return self.outside(e.val, depth)
        if (
            e.tag == "call"
            and e.val == "len"
            and len(e.args) == 1
            and e.args[0].ty is not None
            and is_view(e.args[0].ty)
        ):
            extent = e.args[0].ty.extent
            return Poly.of(int(extent)) if extent.isdigit() else Poly.of(extent)
        if e.tag == "call" and e.val == "usize" and len(e.args) == 1:
            return self.poly(e.args[0], env, depth)
        if e.tag not in {"binary", "call"} or len(e.args) != 2:
            return None
        a, b = (self.poly(x, env, depth) for x in e.args)
        if a is None or b is None:
            return None
        if e.tag == "binary" and e.val in {"+", "-", "*"}:
            return a + b if e.val == "+" else a - b if e.val == "-" else a * b
        if not (a.atoms() | b.atoms()) & set(self.digits):
            return opaque(e.val, a, b)  # the same for every thread of every block: n / 32, min(n, m)
        return None

    def outside(self, name: str, depth: int = 4) -> Poly | None:
        """A usize from outside the region: what an immutable let there was bound to, else an atom of its own."""
        binding = self.c.env.get(name)
        if binding is None:
            return None
        known = self.c.values.get(name)
        if depth and known and known[0] is binding and not binding.mutable:
            found = self.poly(known[1], {}, depth - 1)
            if found is not None:
                return found
        return Poly.of(name)

    def reads(self, e: Expr, env: dict[str, Poly | None]):
        """Record every access to an outer array an expression makes, and what its calls lend."""
        if e.tag == "lambda":
            return
        if e.tag == "call":
            given = {id(a): mode for a, mode in lent(e)}
            for a in arguments(e):
                mode = given.get(id(a))
                if mode is None:
                    self.reads(a, env)
                elif a.tag == "slice":
                    for x in a.args[1:]:
                        self.reads(x, env)
                    self.record(a, env, mode in {"rw", "store"})
                elif a.tag == "index":
                    self.reads(a.args[1], env)
                    self.record(a, env, mode in {"rw", "atomic"}, atomic=mode == "atomic")
                elif a.tag == "name":
                    self.record(a, env, mode == "rw")
            return
        for a in e.args[1:] if e.tag == "index" else e.args:
            self.reads(a, env)
        if e.tag == "index":
            self.record(e, env, write=False)

    def record(self, e: Expr, env: dict[str, Poly | None], write: bool, atomic: bool = False):
        """One access: an element `x[i]`, a part `x[lo..hi]` (its first element plus a digit below its length), or
        a whole array lent to a call (a digit below its extent)."""
        base = e if e.tag == "name" else e.args[0]
        if root(base).tag != "name" or root(base).val not in self.outer:
            return
        name = root(base).val
        index: Poly | None = None
        if e.tag == "index":
            index = self.poly(e.args[1], env) if base.tag == "name" else None
        else:
            if e.tag == "slice":
                lo, hi = (self.poly(x, env) for x in e.args[1:3])
            else:
                extent = e.ty.extent if e.ty is not None and is_view(e.ty) else ""
                lo = Poly()
                hi = Poly.of(int(extent)) if extent.isdigit() else Poly.of(extent) if extent else None
            if lo is not None and hi is not None and base.tag == "name":
                digit = f"{name}[..]#{next(self.fresh)}"
                self.digits[digit] = Digit(Poly(), hi - lo, agent=False)
                index = lo + Poly.of(digit)
        self.sites.setdefault(name, []).append(Site(e, index, write, tuple(self.facts), atomic))

    def condition(self, e: Expr, env: dict[str, Poly | None], truth: bool) -> list[tuple[Poly, Poly]]:
        """What `e` being `truth` says, as lhs <= bound pairs over polynomials."""
        if e.tag == "binary" and e.val == ("&&" if truth else "||"):
            return [*self.condition(e.args[0], env, truth), *self.condition(e.args[1], env, truth)]
        if e.tag == "unary" and e.val == "!":
            return self.condition(e.args[0], env, not truth)
        if e.tag != "binary" or e.val not in {"<", "<=", ">", ">=", "=="} or e.args[0].ty != USIZE:
            return []
        a, b = (self.poly(x, env) for x in e.args)
        if a is None or b is None:
            return []
        op = e.val if truth else {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "==": "!="}[e.val]
        one = Poly.of(1)
        pairs = {"<": [(a, b - one)], "<=": [(a, b)], ">": [(b, a - one)], ">=": [(b, a)], "==": [(a, b), (b, a)]}
        return pairs.get(op, [])

    def stmts(self, ss: list[Stmt], env: dict[str, Poly | None]):
        for s in ss:
            self.stmt(s, env)

    def stmt(self, s: Stmt, env: dict[str, Poly | None]):
        tag = s.tag
        if tag == "assign":
            target, value = s.exprs
            self.reads(value, env)
            if target.tag == "index":
                self.reads(target.args[1], env)
                self.record(target, env, write=True)
            elif target.tag == "name" and target.val in env:
                env[target.val] = None  # a mutable local: the rule does not follow what it holds
            return
        for e in s.exprs:
            self.reads(e, env)
        if tag in {"let", "reg"}:
            env[s.name] = self.poly(s.exprs[0], env) if tag == "let" else None
        elif tag == "if":
            branches = []
            for body, truth in ((s.body, True), (s.other, False)):
                inner, known = dict(env), len(self.facts)
                self.facts += self.condition(s.exprs[0], env, truth)
                self.stmts(body, inner)
                del self.facts[known:]
                branches.append(inner)
            self.join(env, branches)
        elif tag in {"for", "while"}:
            for name in assigned(s.body) & set(env):
                env[name] = None
            inner = dict(env)
            if tag == "for" and s.name != "_":
                digit = f"{s.name}#{next(self.fresh)}"
                lo, hi = (self.poly(x, env) for x in s.exprs[:2])
                self.digits[digit] = Digit(lo, hi, agent=False)
                inner[s.name] = Poly.of(digit)
            self.stmts(s.body, inner)
        elif tag == "match":
            branches = []
            for arm in s.arms:
                inner = {**env, arm.binder: None}
                self.stmts(arm.body, inner)
                branches.append(inner)
            self.join(env, branches)
        elif tag in {"block", "unsafe"}:
            inner = dict(env)
            self.stmts(s.body, inner)
            self.join(env, [inner])
        elif tag in {"warp_reduce", "unpack", "stack", "shared", "pipeline"}:
            for name in [s.name, *(n.val for n in s.other_names)]:
                env[name] = None

    @staticmethod
    def join(env: dict[str, Poly | None], branches: list[dict[str, Poly | None]]):
        for name in list(env):
            if any(b.get(name) != env[name] for b in branches):
                env[name] = None


def check(c: Checker, s: Stmt, block: Block, outer: set[str], grid: list[Poly | None]):
    """Refuse a region two of whose threads, in one block or two, may write one element of an array it did not
    declare, or where a thread may read an element another thread writes."""
    walk = Globals(c, outer)
    env: dict[str, Poly | None] = {}
    for name, extent in zip(block.grid, grid, strict=True):
        walk.digits[name] = Digit(Poly(), extent, agent=True)
        env[name] = Poly.of(name)
        if extent is not None and extent.offset == 0 and len(extent.terms) == 1:
            ((monomial, k),) = extent.terms.items()
            if len(monomial) == 1 and k > 0:
                walk.least[monomial[0]] = 1  # a block runs only where every extent is at least 1
    for name, size in zip(block.threads, block.extents, strict=True):
        walk.digits[name] = Digit(Poly(), Poly.of(size), agent=True)
        env[name] = Poly.of(name)
    walk.stmts(s.body, env)
    for name, sites in walk.sites.items():
        updates = [x for x in sites if x.atomic]
        if updates:  # no barrier orders two blocks, so an array updated atomically is touched no other way
            plain = next((x for x in sites if not x.atomic), None)
            if plain is not None:
                mixed(name, updates[0].node, plain.node, "read or written plainly by the region's threads")
            continue
        writes = [x for x in sites if x.write]
        if not writes:
            continue
        why = disjoint(writes, walk.digits, walk.least)
        if why:
            fail("E-COOP-GLOBAL", f"{name} is written at line {writes[0].node.line} by the threads of every block, "
                 f"and the checker cannot show that no two of them write one element: {why}", writes[0].node,
                 array=name)  # fmt: skip
        for read in (x for x in sites if not x.write):
            if not any(own(read, w, walk.digits) for w in writes):
                fail("E-COOP-GLOBAL", f"{name} is written by the threads of this region, so a thread reads it only "
                     f"at the element it writes itself; the read at line {read.node.line} may reach an element "
                     "another thread writes, and no barrier orders two blocks.", read.node, array=name)  # fmt: skip


def own(read: Site, write: Site, digits: dict[str, Digit]) -> bool:
    """Whether a read names an element only its own thread may write: the write's index, its loop counters renamed to
    the read's over the same ranges, under every condition the write sits under. The write's index holds every block
    and thread name with more than one value, so one index is one thread; a name a condition pins (`if t == 0 {
    out[b] = v; }`) says nothing of which thread reads. The conditions are what showed no two threads write one
    element, so the read must stand where they hold."""
    agents = {n for n, d in digits.items() if d.agent and not (d.hi is not None and (d.hi - d.lo).constant == 1)}
    if read.index is None or write.index is None or not agents <= write.index.atoms():
        return False

    def counters(site: Site) -> list[str]:
        found = site.index.atoms() if site.index is not None else set()
        found |= {a for lhs, bound in site.facts for a in lhs.atoms() | bound.atoms()}
        return sorted(a for a in found if a in digits and not digits[a].agent)

    mine, theirs = counters(read), counters(write)
    if len(mine) != len(theirs) or len(mine) > 6:
        return False
    for order in permutations(theirs):
        mapping = dict(zip(mine, order, strict=True))
        if any(digits[a].lo != digits[b].lo or digits[a].hi != digits[b].hi for a, b in mapping.items()):
            continue
        facts = {(renamed(lhs, mapping), renamed(bound, mapping)) for lhs, bound in read.facts}
        if renamed(read.index, mapping) == write.index and all(f in facts for f in write.facts):
            return True
    return False


def renamed(p: Poly, mapping: dict[str, str]) -> Poly:
    out: dict[Monomial, int] = {}
    for m, k in p.terms.items():
        key = tuple(sorted(mapping.get(a, a) for a in m))
        out[key] = out.get(key, 0) + k
    return Poly(out)


def disjoint(writes: list[Site], digits: dict[str, Digit], least: dict[str, int]) -> str:
    """Why the writes may meet, or the empty string when no two (block, thread) pairs write one element."""
    for w in writes:
        if w.index is None:
            return f"the index at line {w.node.line} is not a sum of the region's block, thread and loop names " \
                "times values the region cannot change."  # fmt: skip
    base: Poly = writes[0].index
    offsets = set()
    for w in writes:
        shift = (w.index - base).constant
        if shift is None:
            return f"the writes at lines {writes[0].node.line} and {w.node.line} differ by more than a constant."
        offsets.add(shift)
    base = base + Poly.of(min(offsets))
    common = [f for f in writes[0].facts if all(f in w.facts for w in writes)]
    for m in base.terms:
        inside = [a for a in m if a in digits]
        if len(inside) > 1:
            return f"{' * '.join(inside)} multiplies two of the region's names."
    items: list[tuple[str, Poly, Poly, bool]] = []  # (digit, weight, the most it rises above its least, counts down)
    for name, digit in digits.items():
        weight = base.weight(name)
        if not weight.terms:
            if digit.agent and not pinned(name, digit, common, digits):
                return f"the index does not depend on {name}, so every {name} writes the same element."
            continue
        span = narrowed(name, digit, common, least, digits)
        down = all(k <= 0 for k in weight.terms.values())
        if down:
            weight = -weight  # counts down: the same digit, read from the other end
        elif any(k < 0 for k in weight.terms.values()):
            return f"{name} is both added and taken away in one index."
        if span is None or digit.lo is None:
            return f"the checker cannot bound {name} where it is written."
        moving = sorted(a.partition("#")[0] for a in (digit.lo.atoms() | span.atoms()) & set(digits))
        if moving:  # two threads' ranges for it differ, so its span says nothing of how far apart their values are
            return f"the range of {name.partition('#')[0]} moves with {', '.join(moving)}."
        items.append((name, weight, span, down))
    if max(offsets) > min(offsets):
        items.append(("#site", Poly.of(1), Poly.of(max(offsets) - min(offsets)), False))
    return radix(items, common, digits, least)


def bounds(name: str, digit: Digit, facts: list[tuple[Poly, Poly]],
           digits: dict[str, Digit]) -> tuple[list[Poly], list[Poly]]:  # fmt: skip
    """The values a digit stays at or below, and at or above: its range's ends, and each bound a condition puts on it
    by values that are the same in every thread."""
    highs = [digit.hi - Poly.of(1)] if digit.hi is not None else []
    lows = [digit.lo] if digit.lo is not None else []
    for lhs, bound in facts:  # lhs <= bound
        above, below = (lhs - Poly.of(name)).constant, (bound - Poly.of(name)).constant
        if above is not None and not bound.atoms() & set(digits):
            highs.append(bound - Poly.of(above))
        if below is not None and not lhs.atoms() & set(digits):
            lows.append(lhs - Poly.of(below))
    return highs, lows


def narrowed(name: str, digit: Digit, facts: list[tuple[Poly, Poly]], least: dict[str, int],
             digits: dict[str, Digit]) -> Poly | None:  # fmt: skip
    """The most a digit rises above its least value: its range, or less where a condition bounds it by values that
    are the same in every thread."""
    if digit.lo is None:
        return None
    span = None
    for high in bounds(name, digit, facts, digits)[0]:
        if span is None or positive(span - (high - digit.lo) + Poly.of(1), least):
            span = high - digit.lo
    return span


def pinned(name: str, digit: Digit, facts: list[tuple[Poly, Poly]], digits: dict[str, Digit]) -> bool:
    """Whether a digit has at most one value where the conditions hold: some bound above it is no more than some
    bound below. `if t == 255` and `if t == n` pin t as `if t == 0` does."""
    highs, lows = bounds(name, digit, facts, digits)
    return any((high - low).constant is not None and (high - low).constant <= 0 for high in highs for low in lows)


def radix(items: list[tuple[str, Poly, Poly, bool]], facts: list[tuple[Poly, Poly]], digits: dict[str, Digit],
          least: dict[str, int]) -> str:  # fmt: skip
    """Order the digits lightest first, each weight above the most the lighter ones add up to: then the index names
    every digit. A condition on exactly the digits placed so far may bound their sum more tightly than their ranges,
    while they all count the same way: `t + 32 k < 40` says nothing of how far apart two values of `32 k - t` are."""
    placed: list[tuple[str, Poly, bool]] = []
    bounds = [Poly()]  # the most the placed digits rise above their least, each way the checker knows it
    prefix = Poly()  # the placed digits' own sum, which a condition may bound
    remaining = list(items)
    while remaining:
        fits = [x for x in remaining if any(positive(x[1] - b, least) for b in bounds)]
        if not fits:
            name, weight, *_ = remaining[0]
            return f"the weight of {name}, {weight!r}, is not shown to exceed what the lighter terms add up to."
        chosen = min(fits, key=lambda x: sum(heavier(x[1], y[1], least) for y in fits))
        name, weight, span, down = chosen
        remaining.remove(chosen)
        placed.append((name, weight, down))
        prefix = prefix + weight * Poly.of(name)
        grown = [b + weight * span for b in bounds]
        for lhs, bound in facts if len({d for *_, d in placed}) == 1 else []:
            rest = (lhs - prefix).constant
            if rest is not None:
                lows = Poly()
                for other, w, _ in placed:
                    lo = digits[other].lo if other in digits else Poly()
                    lows = lows + w * (lo if lo is not None else Poly())
                grown.append(bound - Poly.of(rest) - lows)
        bounds = grown[-4:]
    return ""

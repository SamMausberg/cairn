"""What a function does each time it runs, counted from the typed tree in the sizes it is written in.

A count is a polynomial in the function's extents: a region over `n` runs its body `n` times, a loop over `m` inside
it runs `n*m` times. An access is a stream when its index moves with a loop or lane binder, invariant when it does
not move, and irregular when it depends on data. What the tree cannot bound, such as a `while` loop's trip count, a
foreign call or a function value, is named in `unknown`, and the model's confidence falls with each entry. Nothing
here is timed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..compiler import fusion
from ..compiler.builtins import WRAPPING
from ..compiler.tree import FLOAT, INT, NUMERIC, Expr, Function, Stmt, Type, is_view


class Poly:
    """A polynomial with float coefficients over extent names; a name starting with `?` is one nothing bounds."""

    __slots__ = ("terms",)

    def __init__(self, terms: dict[tuple[str, ...], float] | None = None):
        self.terms = {k: v for k, v in (terms or {}).items() if v}

    @staticmethod
    def of(value: float) -> Poly:
        return Poly({(): float(value)})

    @staticmethod
    def var(name: str) -> Poly:
        return Poly({(name,): 1.0})

    def __add__(self, other: Poly) -> Poly:
        terms = dict(self.terms)
        for k, v in other.terms.items():
            terms[k] = terms.get(k, 0.0) + v
        return Poly(terms)

    def __sub__(self, other: Poly) -> Poly:
        return self + other * -1.0

    def __mul__(self, other: Poly | float) -> Poly:
        if not isinstance(other, Poly):
            return Poly({k: v * other for k, v in self.terms.items()})
        terms: dict[tuple[str, ...], float] = {}
        for a, x in self.terms.items():
            for b, y in other.terms.items():
                k = tuple(sorted(a + b))
                terms[k] = terms.get(k, 0.0) + x * y
        return Poly(terms)

    __rmul__ = __mul__

    def join(self, other: Poly) -> Poly:
        """The larger coefficient of each term: what the dearer of two branches costs, term by term."""
        return Poly({k: max(self.terms.get(k, 0.0), other.terms.get(k, 0.0)) for k in {*self.terms, *other.terms}})

    def symbols(self) -> set[str]:
        return {s for k in self.terms for s in k}

    def subst(self, given: dict[str, Poly]) -> Poly:
        out = Poly()
        for k, v in self.terms.items():
            term = Poly.of(v)
            for s in k:
                term = term * given.get(s, Poly.var(s))
            out = out + term
        return out

    def value(self, sizes: dict[str, float]) -> float | None:
        total = 0.0
        for k, v in self.terms.items():
            if any(s not in sizes for s in k):
                return None
            product = v
            for s in k:
                product *= sizes[s]
            total += product
        return total

    def render(self) -> str:
        def term(k: tuple[str, ...], v: float) -> str:
            shown = f"{v:.3g}" if v != int(v) or abs(v) >= 1e6 else str(int(v))
            return shown if not k else "*".join(k) if v == 1 else f"{shown}*{'*'.join(k)}"

        ordered = sorted(self.terms.items(), key=lambda kv: (-len(kv[0]), kv[0]))
        return " + ".join(term(k, v) for k, v in ordered).replace("+ -", "- ") or "0"

    def __repr__(self) -> str:
        return f"Poly({self.render()})"


ONE = Poly.of(1)


@dataclass
class Work:
    """Operations and bytes, each a count per run of the piece of code that holds them."""

    ops: dict[str, Poly] = field(default_factory=dict)
    reads: dict[str, Poly] = field(default_factory=dict)  # stream -> bytes moved
    writes: dict[str, Poly] = field(default_factory=dict)
    footprint: dict[str, Poly] = field(default_factory=dict)  # stream -> the most distinct bytes it can touch
    irregular: dict[str, Poly] = field(default_factory=dict)  # view -> accesses whose address depends on data

    def op(self, kind: str, times: Poly) -> None:
        self.ops[kind] = self.ops.get(kind, Poly()) + times

    def tables(self) -> tuple[dict[str, Poly], ...]:
        return self.ops, self.reads, self.writes, self.irregular

    def merge(self, other: Work, times: Poly = ONE, rename: dict[str, str] | None = None) -> None:
        rename = rename or {}
        for mine, theirs in zip(self.tables(), other.tables(), strict=True):
            for k, n in theirs.items():
                key = rename.get(k, k) if mine is not self.ops else k
                mine[key] = mine.get(key, Poly()) + n * times
        for k, n in other.footprint.items():
            key = rename.get(k, k)
            self.footprint[key] = self.footprint.get(key, Poly()).join(n)

    def join(self, other: Work) -> Work:
        both = Work()
        for mine, a, b in zip((*both.tables(), both.footprint), (*self.tables(), self.footprint),
                              (*other.tables(), other.footprint), strict=True):  # fmt: skip
            for k in {*a, *b}:
                mine[k] = a.get(k, Poly()).join(b.get(k, Poly()))
        return both

    def subst(self, given: dict[str, Poly]) -> Work:
        out = Work()
        for mine, theirs in zip((*out.tables(), out.footprint), (*self.tables(), self.footprint), strict=True):
            for k, n in theirs.items():
                mine[k] = n.subst(given)
        return out

    def irregular_count(self) -> Poly:
        total = Poly()
        for n in self.irregular.values():
            total = total + n
        return total


@dataclass
class Region:
    """One `parallel`, pooled `reduce` or device statement: `count` indices each doing `body`, run `runs` times."""

    kind: str  # host, pooled, device
    line: int
    count: Poly
    runs: Poly
    body: Work
    weight: int = 1  # elements one index stands for, when a lane owns a block
    plan: tuple[int, int] = (0, 0)  # a host region's (grain, lanes)
    launch: tuple[int, int, int] = (0, 0, 0)  # a device region's (block, per_lane, unroll)
    registers: int = 0  # what ptxas said its kernel uses, when something asked; 0 is not known
    fuse: int = 0  # the fuse its plan sets, whether or not a chain formed


@dataclass
class Cost:
    """Everything one call of a function does, in the names of its own parameters."""

    name: str
    line: int
    extents: list[str]
    placement: str = "host"
    seq: Work = field(default_factory=Work)
    regions: list[Region] = field(default_factory=list)
    tasks: list[tuple[Poly, Cost]] = field(default_factory=list)  # (spawns, what each task runs) until its wait
    transfers: dict[str, Poly] = field(default_factory=dict)  # direction -> bytes
    allocated: Poly = field(default_factory=Poly)  # bytes taken from the heap and zeroed
    allocations: Poly = field(default_factory=Poly)
    waits: Poly = field(default_factory=Poly)
    unknown: list[str] = field(default_factory=list)  # what makes a number a guess: its confidence is low
    approximate: list[str] = field(default_factory=list)  # what makes a number an approximation: medium

    def subst(self, given: dict[str, Poly], rename: dict[str, str], times: Poly) -> Cost:
        """This cost as a caller sees it: its extents replaced by the caller's sizes, run `times` times."""
        out = Cost(
            self.name, self.line, [], self.placement, unknown=list(self.unknown), approximate=list(self.approximate)
        )
        out.seq.merge(self.seq.subst(given), times, rename)
        for r in self.regions:
            body = Work()
            body.merge(r.body.subst(given), ONE, rename)
            out.regions.append(Region(r.kind, r.line, r.count.subst(given), r.runs.subst(given) * times, body,
                                      r.weight, r.plan, r.launch))  # fmt: skip
        out.tasks = [(n.subst(given) * times, t.subst(given, rename, ONE)) for n, t in self.tasks]
        out.transfers = {d: b.subst(given) * times for d, b in self.transfers.items()}
        out.allocated, out.allocations = self.allocated.subst(given) * times, self.allocations.subst(given) * times
        out.waits = self.waits.subst(given) * times
        return out

    def symbols(self) -> set[str]:
        """Every size any count of this call depends on."""
        found: set[str] = set()
        for w in (self.seq, *(r.body for r in self.regions), *(t.seq for _, t in self.tasks)):
            for table in (*w.tables(), w.footprint):
                for n in table.values():
                    found |= n.symbols()
        for p in (*(r.count for r in self.regions), *(r.runs for r in self.regions), *(n for n, _ in self.tasks),
                  *self.transfers.values(), self.allocated, self.allocations, self.waits):  # fmt: skip
            found |= p.symbols()
        for _, t in self.tasks:
            found |= t.symbols()
        return found

    def absorb(self, other: Cost) -> None:
        self.seq.merge(other.seq)
        self.regions += other.regions
        self.tasks += other.tasks
        for d, b in other.transfers.items():
            self.transfers[d] = self.transfers.get(d, Poly()) + b
        self.allocated, self.allocations = self.allocated + other.allocated, self.allocations + other.allocations
        self.waits = self.waits + other.waits
        self.unknown += [u for u in other.unknown if u not in self.unknown]
        self.approximate += [u for u in other.approximate if u not in self.approximate]


@dataclass
class Frame:
    """Where counting stands: the work it adds to, how many times this point runs, the binders around it."""

    work: Work
    times: Poly
    binders: tuple[str, ...]
    lane: bool = False
    seen: set[tuple[str, Any]] = field(default_factory=set)  # streams and elements already paid for this pass

    def inner(self, times: Poly, binder: str = "", lane: bool | None = None) -> Frame:
        """One level deeper: a loop or a lane, whose every pass pays for its streams again."""
        return Frame(self.work, self.times * times, (*self.binders, binder) if binder else self.binders,
                     self.lane if lane is None else lane)  # fmt: skip


CHECKED = {"+": "int_checked", "-": "int_checked", "*": "mul_checked", "/": "div", "%": "div"}


class Counter:
    """Counts every function of a checked program once; a call is the callee's cost in the caller's sizes."""

    def __init__(self, program: Any, checker: Any):
        self.p, self.c = program, checker
        self.costs: dict[str, Cost] = {}
        self.open: set[str] = set()
        self.values: dict[str, Poly] = {}  # immutable usize locals and Buf lengths of the function being counted
        self.minimum: dict[str, list[Expr]] = {}  # `let hi = min(a, b)`: a bound counts at whichever side is fixed
        self.bound: list[str] = []  # the loop and lane binders in scope, which sizes may mention
        self.data: set[str] = set()  # locals whose value was read from memory or computed from what was
        self.cost = Cost("", 0, [])
        self.f: Function | None = None  # the function being counted, whose blocks fusion reads
        self.kept: set[str] = set()  # scratch a fused chain holds in its lanes: never allocated, never streamed

    def function(self, f: Function) -> Cost:
        if f.name in self.costs:
            return self.costs[f.name]
        if f.name in self.open:
            return Cost(f.name, f.line, [], unknown=[f"{f.name} is recursive: its depth is not a size"])
        self.open.add(f.name)
        saved = self.values, self.cost, self.bound, self.data, self.f
        self.bound, self.data, self.f = [], set(), f
        extents = [n for n, t in f.params if t == Type("usize")]
        self.values = {n: Poly.var(n) for n in extents}
        for n, t in f.params:
            if is_view(t):
                self.values[f"len({n})"] = self.extent(t, n)
        self.cost = Cost(f.name, f.line, extents)
        if f.extern:
            self.cost.unknown.append(f"{f.name} is foreign: its body is not visible")
        self.block(f.body, Frame(self.cost.seq, ONE, ()))
        # An extent is a usize parameter some count depends on; `interior(i, n)` does the same work for every i and n.
        self.cost.extents = [n for n in extents if n in self.cost.symbols()]
        cost, (self.values, self.cost, self.bound, self.data, self.f) = self.cost, saved
        self.open.discard(f.name)
        self.costs[f.name] = cost
        return cost

    # Sizes -------------------------------------------------------------------------------------------------------

    def extent(self, ty: Type, base: str) -> Poly:
        if ty.extent.isdigit():
            return Poly.of(int(ty.extent))
        if ty.extent.startswith("len("):
            return self.values.get(ty.extent, Poly.var(ty.extent))
        if ty.extent:
            return self.values.get(ty.extent, Poly.var(ty.extent))
        if ty.name == "Array" and len(ty.args) > 1 and isinstance(ty.args[1], int):
            return Poly.of(ty.args[1])
        return self.values.get(f"len({base})", Poly.var(f"len({base})"))

    def size(self, e: Expr) -> Poly | None:
        """The value of a `usize` expression as a polynomial in the extents, or None when it is not one."""
        if isinstance(e.ref, Expr):
            return self.size(e.ref)
        if e.tag == "int":
            return Poly.of(int(e.val))
        if e.tag == "name":
            if isinstance(e.ref, int):
                return Poly.of(e.ref)
            return Poly.var(e.val) if e.val in self.bound else self.values.get(e.val)
        if e.tag == "call" and e.val == "len" and e.args:
            viewed = e.args[0]
            return self.extent(viewed.ty, path(viewed)) if viewed.ty else None
        if e.tag == "binary" and e.val in {"+", "-", "*", "/"}:
            left, right = self.size(e.args[0]), self.size(e.args[1])
            if left is None or right is None:
                return None
            if e.val == "/":  # only by a constant: a quotient of two sizes is not a polynomial
                divisor = right.value({})
                return left * (1 / divisor) if divisor else None
            return left + right if e.val == "+" else left - right if e.val == "-" else left * right
        return None

    def trips(self, lo: Expr, hi: Expr, line: int) -> Poly:
        a = self.size(lo)
        if a is not None and hi.tag == "name" and isinstance(self.minimum.get(hi.val), list):
            hi = min(self.minimum[hi.val], key=lambda side: len(((self.size(side) or Poly.var("?")) - a).symbols()))
            self.note(
                f"line {line}: a loop bounded by min() is counted at its fixed side, which the last pass may not reach"
            )
        b = self.size(hi)
        if a is not None and b is not None:
            return b - a
        self.cost.unknown.append(f"line {line}: a trip count that is not a function of the extents")
        return Poly.var(f"?trips@{line}")

    def note(self, why: str) -> None:
        if why not in self.cost.approximate:
            self.cost.approximate.append(why)

    # Statements --------------------------------------------------------------------------------------------------

    def block(self, ss: list[Stmt], at: Frame) -> None:
        chains = {id(chain.regions[0]): chain for chain in fusion.chains(ss, self.c.rows, self.f)}
        inside = {id(r) for chain in chains.values() for r in chain.regions[1:]}
        self.kept |= {name for chain in chains.values() for name in chain.scratch}
        for s in ss:
            if id(s) in chains:
                self.fused(chains[id(s)], at)
            elif id(s) not in inside:
                getattr(self, "s_" + s.tag, self.s_plain)(s, at)

    def fused(self, chain: fusion.Chain, at: Frame) -> None:
        """A chain a plan fused is one region: one pass over the extent doing every body, sharing what it reads,
        with its lane-held scratch neither streamed nor allocated. A host reduce that ends it is one fold whose
        every step runs the bodies first: in order on this thread, or pooled in blocks."""
        head, tail, body = chain.regions[0], chain.regions[-1], Work()
        folds = tail.tag == "reduce"
        binders = tuple(r.binder or r.name for r in chain.regions)
        self.bound += binders
        count = self.size(head.exprs[0]) or Poly.var(f"?count@{head.line}")
        lane = Frame(body, ONE, (*at.binders, *binders), not folds or tail.pooled)
        for r in chain.regions[: -1 if folds else None]:
            self.block(r.body, lane)
            # What this body wrote, a later body reads back only at the lane's own index: from a register or L1.
            lane.seen |= {(k, False) for k in body.writes}
        if folds:
            self.expr(tail.exprs[1], lane)
            body.op(f"{tail.ty.name}_fold" if tail.ty.name in FLOAT else "int_fold" if tail.op == "+" else "int", ONE)
        del self.bound[-len(binders) :]
        for name in chain.scratch:
            for table in (body.reads, body.writes, body.footprint):
                table.pop(name, None)
        if folds and not tail.pooled:  # the fold's own thread runs every step, the bodies with it
            at.work.merge(body, at.times * count)
            return
        kind = "pooled" if folds else "device" if head.ref == "device" else "host"
        self.cost.regions.append(
            Region(kind, head.line, count, at.times, body, head.block, head.plan, head.launch, fuse=head.fuse)
        )

    def s_plain(self, s: Stmt, at: Frame) -> None:
        for e in s.exprs:
            self.expr(e, at)
        self.block(s.body, at)

    def s_let(self, s: Stmt, at: Frame) -> None:
        e = s.exprs[0]
        self.expr(e, at)
        if s.tag == "let" and s.ty == Type("usize") and (value := self.size(e)) is not None:
            self.values[s.name] = value
        elif s.tag == "let" and e.tag == "call" and e.val == "min" and len(e.args) == 2:
            self.minimum[s.name] = list(e.args)
        elif s.tag == "reg" or data_dependent(e, self.data, tuple(self.bound)) or e.tag == "index":
            self.data.add(s.name)
        if e.tag == "call" and e.val == "Buf" and e.args and (n := self.size(e.args[0])) is not None:
            self.values[f"len({s.name})"] = n

    s_reg = s_let

    def s_assign(self, s: Stmt, at: Frame) -> None:
        target, value = s.exprs
        if kind := fold(target, value, at.binders):
            self.expr(value.args[1], at)
            at.work.op(kind, at.times)
        else:
            self.expr(value, at)
        if target.tag == "index":
            self.access(target, at, write=True)
        else:
            self.expr(target, at, place=True)

    def s_buffer(self, s: Stmt, at: Frame) -> None:
        n = self.size(s.exprs[0]) or Poly.var(f"?capacity@{s.line}")
        self.values[f"len({s.name})"] = n
        if s.tag == "buffer" and s.name not in self.kept:
            self.cost.allocations = self.cost.allocations + at.times
            self.cost.allocated = self.cost.allocated + n * self.c.sizeof(s.ty) * at.times

    s_stack = s_buffer

    def branches(self, bodies: list[list[Stmt]], at: Frame) -> None:
        """The dearer of the branches, term by term: the model prices a branch as the one that costs more."""
        joined: Work | None = None
        for body in bodies:
            w = Work()
            self.block(body, Frame(w, at.times, at.binders, at.lane, at.seen))
            joined = w if joined is None else joined.join(w)
        if joined is not None:
            at.work.merge(joined)

    def s_if(self, s: Stmt, at: Frame) -> None:
        self.expr(s.exprs[0], at)
        at.work.op("branch", at.times)
        self.branches([s.body, s.other], at)

    def s_match(self, s: Stmt, at: Frame) -> None:
        self.expr(s.exprs[0], at)
        at.work.op("branch", at.times)
        self.branches([arm.body for arm in s.arms], at)

    def s_for(self, s: Stmt, at: Frame) -> None:
        lo, hi = s.exprs
        self.expr(lo, at)
        self.expr(hi, at)
        n = self.trips(lo, hi, s.line)
        if any(b in n.symbols() for b in at.binders):  # A triangular loop is counted at its bound.
            n = n.subst({b: Poly.var(f"?bound({b})") for b in at.binders})
            self.note(f"line {s.line}: a loop bounded by an outer index is counted at its largest")
        self.bound.append(s.name)
        self.block(s.body, at.inner(n, s.name))
        self.bound.pop()

    def s_while(self, s: Stmt, at: Frame) -> None:
        n = Poly.var(f"?trips@{s.line}")
        self.cost.unknown.append(f"line {s.line}: a while loop's trip count is not a size")
        inner = at.inner(n)
        self.expr(s.exprs[0], inner)
        self.block(s.body, inner)

    def s_parallel(self, s: Stmt, at: Frame) -> None:
        count = self.size(s.exprs[0]) or Poly.var(f"?count@{s.line}")
        body = Work()
        self.bound.append(s.name)
        self.block(s.body, Frame(body, ONE, (*at.binders, s.name), True))
        self.bound.pop()
        kind = "device" if s.ref == "device" else "host"
        self.cost.regions.append(Region(kind, s.line, count, at.times, body, s.block, s.plan, s.launch, fuse=s.fuse))

    def s_reduce(self, s: Stmt, at: Frame) -> None:
        count = self.size(s.exprs[0]) or Poly.var(f"?count@{s.line}")
        # An in-order float fold and a checked sum wait on the previous step; wrapping, min and max reassociate.
        combine = f"{s.ty.name}_fold" if s.ty.name in FLOAT else "int_fold" if s.op == "+" else "int"
        if s.ref == "device" or s.pooled:
            body = Work()
            self.expr(s.exprs[1], Frame(body, ONE, (s.binder,), True))
            body.op(combine, ONE)
            self.cost.regions.append(Region("device" if s.ref == "device" else "pooled", s.line, count, at.times,
                                            body))  # fmt: skip
            return
        inner = at.inner(count, s.binder)
        self.expr(s.exprs[1], inner)
        inner.work.op(combine, inner.times)

    def s_scan(self, s: Stmt, at: Frame) -> None:
        out, hi, value, store = s.exprs
        count = self.size(hi) or Poly.var(f"?count@{s.line}")
        combine = f"{s.ty.name}_fold" if s.ty.name in FLOAT else "int_fold"  # each step waits on the one before
        if s.ref == "device" or s.pooled:
            body = Work()
            lane = Frame(body, ONE, (s.binder,), True)
            self.expr(value, lane)
            self.access(store, lane, write=True)
            body.op(combine, ONE)
            key, size = path(out), Poly.of(self.c.sizeof(out.ty.value) if out.ty else 8)
            body.reads[key] = body.reads.get(key, Poly()) + size  # the second pass: each element read and written
            body.writes[key] = body.writes.get(key, Poly()) + size  # once more, with its block's offset combined in
            body.op("int", ONE)
            self.cost.regions.append(Region("device" if s.ref == "device" else "pooled", s.line, count, at.times,
                                            body))  # fmt: skip
            return
        inner = at.inner(count, s.binder)
        self.expr(value, inner)
        self.access(store, inner, write=True)
        inner.work.op(combine, inner.times)

    def s_compact(self, s: Stmt, at: Frame) -> None:
        out, hi, predicate, value = s.exprs
        count = self.size(hi) or Poly.var(f"?count@{s.line}")
        inner = Frame(at.work if s.ref != "device" else Work(), at.times * count, (*at.binders, s.binder), at.lane)
        inner.seen.add((path(out), True))
        self.expr(predicate, inner)
        self.expr(value, inner)
        size = self.c.sizeof(out.ty.value) if out.ty else 8
        inner.work.writes[path(out)] = inner.work.writes.get(path(out), Poly()) + inner.times * size
        inner.work.op("collect", inner.times)  # a store at a count that moves with the data, one element at a time
        if s.ref == "device":
            self.cost.regions.append(Region("device", s.line, count, at.times, inner.work))

    def s_defer(self, s: Stmt, at: Frame) -> None:
        self.block(s.body, at)

    def s_submit(self, s: Stmt, at: Frame) -> None:
        self.s_plain(s, at)
        self.io(at, f"line {s.line}: a submission's completion time is the kernel's")

    # Expressions -------------------------------------------------------------------------------------------------

    def io(self, at: Frame, why: str) -> None:
        at.work.op("io", at.times)
        if why not in self.cost.unknown:
            self.cost.unknown.append(why)

    def expr(self, e: Expr, at: Frame, place: bool = False) -> None:
        tag = e.tag
        if tag == "index":
            self.access(e, at, write=False)
        elif tag == "slice":
            for a in e.args:
                self.expr(a, at)
            at.work.op("part_guard", at.times)
        elif tag == "field":
            if not isinstance(e.ref, tuple):
                self.expr(e.args[0], at, place)
        elif tag == "binary":
            for a in e.args:
                self.expr(a, at)
            kind = self.arithmetic(e)
            if kind == "int_checked" and self.size(e) is not None:
                kind = "index_checked"  # arithmetic on sizes and binders: the vectorizer checks it once per block
            at.work.op(kind, at.times)
        elif tag == "unary":
            self.expr(e.args[0], at)
            checked = e.val == "-" and e.ty is not None and e.ty.name in INT
            at.work.op("int_checked" if checked else e.ty.name if e.ty and e.ty.name in FLOAT else "int", at.times)
        elif tag == "try":
            self.expr(e.args[0], at)
            at.work.op("branch", at.times)
        elif tag == "call":
            self.call(e, at)
        elif tag == "spawn":
            self.spawn(e, at)
        else:
            for a in e.args:
                if isinstance(a, Expr):
                    self.expr(a, at)

    def arithmetic(self, e: Expr) -> str:
        ty, op = e.ty.name if e.ty else "", e.val
        if op in {"==", "!=", "<", "<=", ">", ">=", "&&", "||"}:
            return "compare"
        if ty in FLOAT:
            return f"{ty}_div" if op == "/" else ty
        if op in {"&", "|", "^"} or e.established:
            return "mul" if op == "*" else "div" if op in {"/", "%"} else "int"
        return CHECKED.get(op, "int")

    def access(self, e: Expr, at: Frame, write: bool) -> None:
        """An element read or written: a stream if its index moves with a binder, irregular if it rests on data."""
        base, index = e.args
        self.expr(base, at, place=True)
        self.expr(index, at)
        if not e.established:
            at.work.op("bounds_guard", at.times)
        at.work.op("store" if write else "load", at.times)
        key, element = path(base), self.c.sizeof(e.ty) if e.ty and e.ty.name != "void" else 8
        reach = self.extent(base.ty, key) * element if base.ty is not None else Poly()
        if data_dependent(index, self.data, at.binders):  # priced by latency at the level its own view lives in
            place = (key, spelled(index))  # reading a bin and writing it back is one trip to its line
            if place not in at.seen:
                at.seen.add(place)
                at.work.irregular[key] = at.work.irregular.get(key, Poly()) + at.times
            at.work.footprint[key] = at.work.footprint.get(key, Poly()).join(reach)
            return
        if not mentioned(index) & set(at.binders):
            return  # One element, the same on every pass: a register or the first cache line, not a stream.
        if (key, write) in at.seen:
            return  # A second access on the same pass is a neighbour of the first, already in cache.
        at.seen.add((key, write))
        streams = at.work.writes if write else at.work.reads
        streams[key] = streams.get(key, Poly()) + at.times * element
        at.work.footprint[key] = at.work.footprint.get(key, Poly()).join(reach)

    def call(self, e: Expr, at: Frame) -> None:
        for a in e.args:
            self.expr(a, at)
        ref = e.ref
        if isinstance(ref, Function):
            self.inline(ref, e.args, at)
            return
        kind = ref[0] if isinstance(ref, tuple) and ref else ""
        name = e.val
        if kind == "builtin":
            if name in NUMERIC:
                at.work.op("convert" if e.established or name in FLOAT else "convert_guard", at.times)
            elif name in WRAPPING or name in {"min", "max"}:
                at.work.op("mul" if name == "mul_wrap" else "int", at.times)
                if name in {"shr", "shl_wrap"} and not e.established:
                    at.work.op("shift_guard", at.times)
            elif name == "Buf" and e.args:
                n = self.size(e.args[0]) or Poly.var(f"?capacity@{e.line}")
                element = self.c.sizeof(e.ty.args[0]) if e.ty and e.ty.args else 8
                self.cost.allocations = self.cost.allocations + at.times
                self.cost.allocated = self.cost.allocated + n * element * at.times
            elif name == "transfer":
                self.transfer(e, at)
            elif name in {"wait", "collect"}:
                self.cost.waits = self.cost.waits + at.times
            elif name in {"take", "swap"}:
                at.work.op("move", at.times)
            elif name in {"mmio_read", "mmio_write", "asm"}:
                self.io(at, f"line {e.line}: {name} reaches the machine")
        elif kind in {"indirect", "dispatch"}:
            at.work.op("indirect", at.times)
            why = f"line {e.line}: a call through a function value or a dyn table has no body to count"
            if why not in self.cost.unknown:
                self.cost.unknown.append(why)
        elif kind == "shared":
            at.work.op("atomic", at.times)
            self.note(f"line {e.line}: an atomic or a lock costs what its contention costs")
        elif kind == "ring":
            self.io(at, f"line {e.line}: an I/O ring operation costs what the kernel takes")

    def given(self, f: Function, args: list[Expr]) -> tuple[dict[str, Poly], dict[str, str]]:
        sizes, rename = {}, {}
        for (n, t), a in zip(f.params, args, strict=False):
            if t == Type("usize"):
                sizes[n] = self.size(a) or Poly.var(f"?{n}@{a.line}")
            if t.mode != "value" or t.name == "Buf":
                rename[n] = path(a)
        return sizes, rename

    def inline(self, f: Function, args: list[Expr], at: Frame) -> None:
        callee = self.function(f)
        sizes, rename = self.given(f, args)
        seen = callee.subst(sizes, rename, at.times)
        if f.extern:
            self.io(at, f"line {args[0].line if args else f.line}: {f.name} is foreign, so its time is not counted")
        at.work.op("call", at.times)
        at.work.merge(seen.seq)
        if at.lane:  # A lane's callee runs inside the lane: its own regions cannot exist there.
            for r in seen.regions:
                at.work.merge(r.body, r.count * r.runs)
            seen.regions = []
        seen.seq = Work()
        self.cost.absorb(seen)

    def spawn(self, e: Expr, at: Frame) -> None:
        call = e.args[0] if e.args else None
        if call is None or not isinstance(call.ref, Function):
            at.work.op("spawn", at.times)
            return
        for a in call.args:
            self.expr(a, at)
        sizes, rename = self.given(call.ref, call.args)
        self.cost.tasks.append((at.times, self.function(call.ref).subst(sizes, rename, ONE)))

    def transfer(self, e: Expr, at: Frame) -> None:
        src = e.args[1] if len(e.args) > 1 else e.args[0]
        dst = e.args[0]
        places = (getattr(src.ty, "place", "host"), getattr(dst.ty, "place", "host"))
        way = {("host", "device"): "h2d", ("device", "host"): "d2h", ("device", "device"): "d2d"}.get(places, "h2h")
        element = self.c.sizeof(src.ty.value) if src.ty else 8
        count = (self.extent(src.ty, path(src)) if src.ty and src.tag != "slice" else self.span(src)) or Poly.var(
            f"?extent@{e.line}"
        )
        self.cost.transfers[way] = self.cost.transfers.get(way, Poly()) + count * element * at.times

    def span(self, e: Expr) -> Poly | None:
        if e.tag != "slice":
            return None
        lo, hi = self.size(e.args[1]), self.size(e.args[2])
        return None if lo is None or hi is None else hi - lo


def path(e: Expr) -> str:
    """The place an expression names, as the source writes it: `x`, `c.price`, `rows[k]`."""
    if e.tag == "name":
        return e.val
    if e.tag == "field" and e.args:
        return f"{path(e.args[0])}.{e.val}"
    if e.tag in {"index", "slice"} and e.args:
        return path(e.args[0])
    return f"<{e.tag}@{e.line}>"


def fold(target: Expr, value: Expr, binders: tuple[str, ...]) -> str | None:
    """`acc = acc op x` inside a loop: each pass waits for the last one's result, so it costs the latency of `op`."""
    if not binders or target.tag != "name" or value.tag != "binary" or value.val not in {"+", "-", "*"}:
        return None
    if value.args[0].tag != "name" or value.args[0].val != target.val or value.ty is None:
        return None
    if value.ty.name in FLOAT:
        return f"{value.ty.name}_fold"
    return "int_fold" if value.ty.name in INT and not value.established else None


def spelled(e: Expr) -> str:
    """An expression as a key: two accesses at the same spelling on one pass reach the same element."""
    inner = ",".join(spelled(a) for a in e.args if isinstance(a, Expr))
    return f"{e.tag}:{e.val}({inner})"


def mentioned(e: Expr) -> set[str]:
    found = {e.val} if e.tag == "name" else set()
    for a in e.args:
        if isinstance(a, Expr):
            found |= mentioned(a)
    return found


def data_dependent(e: Expr, data: set[str], binders: tuple[str, ...]) -> bool:
    """An index that reads an element, calls a function or names a local holding data is an address from data."""
    if e.tag == "index" or (e.tag == "call" and e.val not in {"len", *NUMERIC, *WRAPPING, "min", "max"}):
        return True
    if e.tag == "name" and (e.val in data or (e.ref == "mut" and e.val not in binders)):
        return True
    return any(data_dependent(a, data, binders) for a in e.args if isinstance(a, Expr))


def count(program: Any, checker: Any, names: set[str] | None = None) -> dict[str, Cost]:
    """The cost of every written function of a checked program, or of `names` alone."""
    counter = Counter(program, checker)
    chosen = [f for f in program.functions if not f.extern and (names is None or f.name in names)]
    return {f.name: counter.function(f) for f in chosen}

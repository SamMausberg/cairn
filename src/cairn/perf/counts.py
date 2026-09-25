"""What a count is: a polynomial in a function's extents, the work and cost records the walk in `work.py` fills, and
how an access is keyed.

`Poly` is a polynomial with float coefficients over extent names. `Work` holds operations and bytes, each a count per
run of the code that holds them; a `Region` is one parallel statement's indices and the work each does; a `Cost` is
everything one call does, in the names of its own parameters, and becomes a caller's by substitution. Nothing here
reads a checker or times anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..compiler.primitives.builtins import WRAPPING
from ..compiler.syntax.tree import NUMERIC, Expr


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


def add(table: dict[str, Poly], key: str, n: Poly) -> None:
    table[key] = table.get(key, Poly()) + n


def widen(table: dict[str, Poly], key: str, n: Poly) -> None:
    """The larger of what `key` could touch and `n`: a footprint is the most distinct bytes, never a sum."""
    table[key] = table.get(key, Poly()).join(n)


@dataclass
class Work:
    """Operations and bytes, each a count per run of the piece of code that holds them."""

    ops: dict[str, Poly] = field(default_factory=dict)
    reads: dict[str, Poly] = field(default_factory=dict)  # stream -> bytes moved
    writes: dict[str, Poly] = field(default_factory=dict)
    footprint: dict[str, Poly] = field(default_factory=dict)  # stream -> the most distinct bytes it can touch
    irregular: dict[str, Poly] = field(default_factory=dict)  # view -> accesses whose address depends on data

    def op(self, kind: str, times: Poly) -> None:
        add(self.ops, kind, times)

    def tables(self) -> tuple[dict[str, Poly], ...]:
        return self.ops, self.reads, self.writes, self.irregular

    def merge(self, other: Work, times: Poly = ONE, rename: dict[str, str] | None = None) -> None:
        rename = rename or {}
        for mine, theirs in zip(self.tables(), other.tables(), strict=True):
            for k, n in theirs.items():
                add(mine, rename.get(k, k) if mine is not self.ops else k, n * times)
        for k, n in other.footprint.items():
            widen(self.footprint, rename.get(k, k), n)

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
    shared: int = 0  # the shared memory one block uses, static and a staged tile's, when something read it
    spilled: int = 0  # the bytes of spill stores and loads ptxas reported in its cooperative kernel's code, if read
    fuse: int = 0  # the fuse its plan sets, whether or not a chain formed
    vector: int = 0  # the vector its plan sets: adjacent indices a lane runs over chunks
    tensor: str = ""  # a tensor-core multiply's input format; its body holds the whole call's work, count one
    chunks: tuple = ()  # a device region's chunkable arrays: (element accesses per index, loaded, stored, bytes each)
    coop: Any = None  # a cooperative region's shape (perf/cooperative_work.py): count is its blocks, body one thread's


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
            out.regions.append(replace(r, count=r.count.subst(given), runs=r.runs.subst(given) * times, body=body))
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
            add(self.transfers, d, b)
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


def path(e: Expr) -> str:
    """The place an expression names, as the source writes it: `x`, `c.price`, `rows[k]`."""
    if e.tag == "name":
        return e.val
    if e.tag == "field" and e.args:
        return f"{path(e.args[0])}.{e.val}"
    if e.tag in {"index", "slice"} and e.args:
        return path(e.args[0])
    return f"<{e.tag}@{e.line}>"


def data_dependent(e: Expr, data: set[str], binders: tuple[str, ...]) -> bool:
    """An index that reads an element, calls a function or names a local holding data is an address from data."""
    if e.tag == "index" or (e.tag == "call" and e.val not in {"len", *NUMERIC, *WRAPPING, "min", "max"}):
        return True
    if e.tag == "name" and (e.val in data or (e.ref == "mut" and e.val not in binders)):
        return True
    return any(data_dependent(a, data, binders) for a in e.args if isinstance(a, Expr))

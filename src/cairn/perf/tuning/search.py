"""The bounded search behind `cairn tune`: which plans the checker accepts, what each compiles to, what it costs.

1. The space is every value of every item the function's regions take (`SPACE`), `stage` at the radii the staging
   rule reads for the region, every combination of them, `vector` beside `fuse` and `stage` included, and each of
   the function's implementations. It is never listed whole. `Order` generates it with the model's likely winners
   first: the plan with no items, each implementation, then each item alone, each checked and priced as it comes;
   then every combination, in the order of the product of what each of its items did alone to the time of the plan
   with none. The estimate only orders: every candidate generated is priced for itself. When the time is spent,
   generation stops, and what it never generated is counted. An implementation is tried with the reference's plan
   as it is now and with no other: a plan's items schedule only the reference's own regions, which the model does
   not price where the implementation runs, so each other plan beside it is the same candidate to the model. This
   file does not decide which combinations are legal: each complete candidate is written into the source and
   checked, and a refused one is kept with the checker's code and message.
2. Each legal candidate is counted from its own checked program, priced by the model at every size and ranked by
   the objective over them (`perf/tuning/objective.py`). It is lowered as `cairn build` lowers it, and one whose function
   lowers to the same canonical code (`verify/emission.py`) as an earlier candidate is that candidate: it takes its
   price, its compile and its measurement, and the answer lists them together. Its check cannot be shared, since
   the code is known only once it is checked.
3. In ranked order, ties broken toward kernel items no earlier compile covered, each device candidate is compiled
   for the target and read by ptxas and cuobjdump (`perf/tuning/resources.py`) until the compile budget is spent. For
   `blur` in evidence/v1_0/search, the model priced all 160 plans alike, and 32 compiles wrote 8 distinct SASS: a
   plan's `block` and `per_lane` are arguments the host passes to the launch, and `grain` and `lanes` schedule host
   regions, so a candidate whose other items (`KERNEL`) and implementation match one already read takes that
   reading, with its own staged tiles. An inspection is kept by the key of what it compiled, so a candidate that
   emits a program already compiled, in this search or an earlier one with the same history, costs no compile.
   Registers and shared memory then enter its price through occupancy.

A budget bounds the whole search: `compiles` device compiles started, `seconds` of wall time, and `runs` timed runs
(`perf/tuning/tune.py` times; device plans only under the owner's make target). The clock is read before every step. A
compile or a timed run gets the time left as its limit and is stopped there with every process it started, and one
is not started when less time is left than the shortest this search has seen. When compiles or runs follow, the
candidates may take half of the time. What a spent budget left undone is counted in the answer, and a candidate
nothing compiled says so rather than borrow another's figures.

Each candidate is checked whole, though most of a check cannot depend on its plan. The parser puts a plan and a
selection into `Program.plans` and `Program.selections`, and only three steps read them, after every body and every
implementation instance is checked: the selection, `concurrency.plans` and `concurrency.fusions`, with the effect
fixed point and the rules over every row between them. Checking less would run that tail on a copy of a checker
whose bodies are done, and neither half is this file's to write: the checker's state does not copy
(`copy.deepcopy` fails on its facts), and the order of the tail is `Checker.check`'s. On the tuned example in
examples/cooperative the part a plan cannot reach is more than nine tenths of a check (evidence/v1_1/search), so
the checker is where that saving belongs.
"""

from __future__ import annotations

import copy
import heapq
import itertools
import math
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from ...compiler.cairnc import Diagnostic, compile_program, joined
from ...compiler.check.concurrency import PLAN_ITEMS
from ...compiler.lower.codegen import Emitter
from ...verify.emission import canonical, definitions
from .. import model
from ..counts import Cost
from ..profile import Profile
from ..work import count
from .objective import Objective
from .plan_source import KEEP, Placement, Plan, written

SPACE = {  # the values tried for each item; 0 is the runtime's own choice
    "grain": (0, 1, 64, 1024, 8192, 65536),
    "lanes": (0, 1, 2, 4, 8, 16, 32, 64),
    "block": (0, 64, 128, 512, 1024),
    "per_lane": (0, 4, 16, 64),
    "unroll": (0, 4),
    "vector": (0, 2, 4),
}
LAUNCH = {"grain", "lanes", "block", "per_lane"}  # items that say how a region is claimed or launched
KERNEL = set(PLAN_ITEMS) - LAUNCH  # items that change the device code a region compiles to
REFUSED = 1e9  # the estimate a probe the checker refused adds: combinations with it come last


@dataclass
class Budget:
    """How much a search may spend: device compiles started, wall seconds, timed runs (None: what --measure asks)."""

    compiles: int = 4
    seconds: float = 300.0
    runs: int | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.compiles <= 4096 or not 0 < self.seconds <= 86_400 or (self.runs or 0) < 0:
            raise ValueError("A budget is 0 to 4096 compiles, up to a day of seconds and no negative number of runs.")


@dataclass
class Spent:
    """What a search has used of its budget, and what it left undone for want of more."""

    budget: Budget
    started: float = field(default_factory=time.monotonic)
    compiles: int = 0
    kept: int = 0  # inspections an earlier compile answered
    runs: int = 0
    reused_runs: int = 0  # measurements an earlier run answered
    undone: dict[str, int] = field(default_factory=dict)
    shortest: dict[str, float] = field(default_factory=dict)  # the shortest compile and run this search has seen

    def seconds(self) -> float:
        return time.monotonic() - self.started

    def left(self) -> float:
        return max(self.budget.seconds - self.seconds(), 0.0)

    def out_of_time(self, share: float = 1.0) -> bool:
        """Whether the search has spent `share` of its seconds."""
        return self.seconds() >= self.budget.seconds * share

    def fits(self, step: str) -> bool:
        """Whether the time left holds one more `step` (compile, run) as long as the shortest this search has seen."""
        return self.left() > 0 and self.left() >= self.shortest.get(step, 0.0)

    def took(self, step: str, seconds: float) -> None:
        self.shortest[step] = min(self.shortest.get(step, math.inf), seconds)

    def skip(self, why: str, n: int = 1) -> None:
        if n:
            self.undone[why] = self.undone.get(why, 0) + n

    def report(self) -> dict[str, Any]:
        return {"compiles": {"allowed": self.budget.compiles, "started": self.compiles, "kept": self.kept},
                "seconds": {"allowed": self.budget.seconds, "spent": round(self.seconds(), 2)},
                "runs": {"allowed": self.budget.runs, "started": self.runs, "kept": self.reused_runs},
                "undone": dict(sorted(self.undone.items()))}  # fmt: skip


def radii(program: Any, name: str) -> tuple[int, ...]:
    """The stage radii worth trying for `name`: the least that tiles one array its device regions read at an
    offset, and the least that tiles every such array. A wider radius only loads more of the same halo."""
    from ...compiler.plans.staging import stageable
    from ..regions import walked

    f = next(f for f in program.functions if f.name == name and not f.bindings)
    reach: set[int] = set()
    for s in walked(f.body):
        if s.tag == "parallel" and s.ref == "device":
            spans = [max(abs(d) for d in offsets) for _, _, offsets in stageable(s, PLAN_ITEMS["stage"][2]).values()]
            reach |= {min(spans), max(spans)} if spans else set()
    return tuple(sorted(reach))


def axes(kinds: set[str], host_lanes: int, regions: int = 1, stages: tuple[int, ...] = ()) -> dict[str, tuple]:
    """The values tried for each item the function's regions take, 0 (off) first: lane caps no wider than the host,
    fuse off or joining as many regions as the function has where there are two to join, stage off or at `stages`."""
    tried = {**SPACE, "fuse": (0, min(regions, PLAN_ITEMS["fuse"][2])), "stage": (0, *stages)}
    items = [k for k in PLAN_ITEMS if PLAN_ITEMS[k][0] in kinds or (PLAN_ITEMS[k][0] == "either" and regions > 1)]
    return {k: tuple(v for v in tried[k] if k != "lanes" or v <= host_lanes) for k in items}


def space(kinds: set[str], host_lanes: int, regions: int = 1, stages: tuple[int, ...] = ()) -> list[Plan]:
    """Every combination of `axes`, listed whole. Whether a combination is legal is the checker's to say."""
    given = axes(kinds, host_lanes, regions, stages)
    return list(dict.fromkeys(written(dict(zip(given, chosen, strict=True))) for chosen in itertools.product(
        *given.values())))  # fmt: skip


Key = tuple[Plan, Any]  # a plan, and the implementation it selects: a qualified name, None for the reference, or KEEP


class Order:
    """The space of one search, generated as it is asked for, in the order the model ranks it (the module
    docstring, 1). `priced` holds the objective of each plan the search has priced with the reference, which orders
    the combinations; `generated` counts what has been handed out."""

    def __init__(self, given: dict[str, tuple[int, ...]], uses: tuple[str | None, ...] = (), current: Plan = ()):
        self.axes, self.uses, self.current = given, uses, current
        self.plans = math.prod(len(v) for v in given.values())
        self.configurations = self.plans * max(len(uses), 1)  # every plan beside every implementation
        self.excluded = (self.plans - 1) * max(len(uses) - 1, 0)  # the other plans beside an implementation
        self.generated = 0
        self.priced: dict[Plan, float | None] = {}  # None: the checker refused it

    def left(self) -> int:
        return self.configurations - self.excluded - self.generated

    def __iter__(self) -> Iterator[Key]:
        reference = None if self.uses else KEEP
        handed: set[Plan] = set()
        alone = [written({k: v}) for k, values in self.axes.items() for v in values[1:]]
        first = [((), reference), *((self.current, g) for g in self.uses[1:]), *((p, reference) for p in alone)]
        for plan, use in itertools.chain(first, ((p, reference) for p in self.combined())):
            if use is reference and plan in handed:
                continue
            if use is reference:
                handed.add(plan)
            self.generated += 1
            yield plan, use

    def estimate(self, item: str, value: int) -> float:
        """What `item` at `value` did alone to the plan with no items, as a log ratio of their objectives."""
        base, alone = self.priced.get(()), self.priced.get(written({item: value}), None)
        if value == 0 or not base:
            return 0.0
        return REFUSED if alone is None or alone <= 0 else math.log(alone / base)

    def combined(self) -> Iterator[Plan]:
        """Every combination of the items' values, lowest estimate first, fewer items first among equals: a
        best-first walk of the product, each step one item moved to its next best value."""
        items = list(self.axes)
        guess = {(k, v): self.estimate(k, v) for k in items for v in self.axes[k]}

        def best(k: str) -> list[int]:  # an item's values, best first, off first among equals
            return sorted(self.axes[k], key=lambda v: (guess[k, v], v != 0))

        ranked = {k: best(k) for k in items}

        def entry(at: tuple[int, ...]) -> tuple[float, int, tuple[int, ...]]:
            chosen = [(k, ranked[k][i]) for k, i in zip(items, at, strict=True)]
            return sum(guess[kv] for kv in chosen), sum(bool(v) for _, v in chosen), at

        start = (0,) * len(items)
        heap, seen = [entry(start)], {start}
        while heap:
            _, _, at = heapq.heappop(heap)
            yield written({k: ranked[k][i] for k, i in zip(items, at, strict=True)})
            for j, k in enumerate(items):
                step = (*at[:j], at[j] + 1, *at[j + 1 :])
                if at[j] + 1 < len(ranked[k]) and step not in seen:
                    seen.add(step)
                    heapq.heappush(heap, entry(step))


@dataclass
class Candidate:
    """One complete configuration: its plan, and what the checker, the model and the device compiler said of it."""

    plan: Plan
    source: str = ""
    refused: tuple[str, str] | None = None  # the checker's code and message
    cost: Cost | None = None
    program: Any = None
    checker: Any = None
    predicted_ns: float = 0.0  # the objective of `at`
    at: list[float] = field(default_factory=list)  # the predicted time at each size
    resources: dict[str, Any] | None = None
    use: str | None = None  # the implementation a `plan f use g;` selects, by its qualified name; None: the reference
    cpp: str = ""  # the program `cairn build` compiles for it
    code: str = ""  # the canonical code of the searched function in it
    same: list[Candidate] = field(default_factory=list)  # later candidates that lowered to the same code
    alike: Candidate | None = None  # the earlier candidate this one lowered the same as

    def runs(self, name: str) -> str:
        """The function whose code this candidate runs where it applies: the selected implementation, or `name`."""
        return self.use or name

    def key(self) -> Key:
        return self.plan, self.use


def lowered(program: Any, checker: Any, name: str) -> tuple[str, str]:
    """The C++ `cairn build` compiles for a checked program, and the canonical code of `name` and of every instance
    of it in that program (a plan schedules those, and changes no other function)."""
    interface, bodies = Emitter(program, checker).units()
    emitted = [f for f in program.functions if not f.test and not f.extern]
    types = definitions(interface)
    own = [canonical(f.name, "\n".join(lines), types) for f, (_, lines) in zip(emitted, bodies, strict=True)
           if name in (f.name, f.source_name)]  # fmt: skip
    return joined(interface, bodies), "\n".join(own)


def searched(placement: Placement, name: str, order: Order, spent: Spent, goal: Objective, profile: Profile,
             arch: str | None, share: float = 1.0) -> list[Candidate]:  # fmt: skip
    """Each candidate `order` generates, written into the source, checked as a whole program, counted, priced at
    every size and lowered, until `share` of the time is spent. A candidate that selects an implementation is
    counted as that implementation, the code that runs where its condition holds."""
    out: list[Candidate] = []
    lowerings: dict[str, Candidate] = {}
    spent.skip("not generated: an implementation is tried with the reference's current plan alone", order.excluded)
    generated = iter(order)
    while True:
        if spent.out_of_time(share):
            why = "out of time" if share == 1.0 else "half of the time is kept for compiles and runs"
            spent.skip(f"not generated: {why}", order.left())
            break
        if (key := next(generated, None)) is None:
            break
        plan, use = key
        chosen = use if isinstance(use, str) else None
        written_as = chosen.rsplit(".", 1)[-1] if chosen else None
        c = Candidate(plan, placement.apply(plan, written_as if use is not KEEP else KEEP), use=chosen)
        out.append(c)
        try:
            c.program, c.checker, _ = compile_program(c.source)
            c.cost = count(c.program, c.checker, {c.runs(name)})[c.runs(name)]
        except Diagnostic as error:
            c.refused = (error.data["code"], error.data["message"])
            if chosen is None:
                order.priced.setdefault(plan, None)
            continue
        c.cpp, c.code = lowered(c.program, c.checker, name)
        first = lowerings.setdefault(c.code, c)
        if first is not c:  # the same code: nothing of its own is read again, so nothing of it is kept
            c.alike, c.at, c.predicted_ns = first, first.at, first.predicted_ns
            c.program = c.checker = None
            c.cpp = ""
            first.same.append(c)
        else:
            price(c, profile, goal, arch)
        if chosen is None:
            order.priced.setdefault(plan, c.predicted_ns)
    return out


def priced(c: Cost, profile: Profile, sizes: list[dict[str, float]] | tuple[dict[str, float], ...],
           arch: str | None, resources: dict[str, Any] | None = None) -> list[float]:  # fmt: skip
    """The predicted time of `c` at each of `sizes`, with the registers and shared memory an inspection read."""
    trial = copy.copy(c)
    trial.regions = [copy.copy(r) for r in c.regions]
    for r in trial.regions:
        if (r.kind == "device" or (r.coop is not None and r.coop.device)) and resources and resources.get(
                "status") == "read":  # fmt: skip
            r.registers = resources["registers"]
            r.shared = resources["shared_bytes"] + resources["dynamic_shared_bytes"]
    return [model.predict(trial, profile, s, arch)["ns"] for s in sizes]


def price(c: Candidate, profile: Profile, goal: Objective, arch: str | None) -> None:
    """`c` priced at every size, with what an inspection read of it, and ranked by the objective; and so is every
    candidate that lowered to the same code."""
    assert c.cost is not None
    c.at = priced(c.cost, profile, goal.sizes, arch, c.resources)
    c.predicted_ns = goal.value(c.at)
    for other in c.same:
        other.at, other.predicted_ns = c.at, c.predicted_ns


def refusals(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """The checker's refusals, one entry per distinct reason: how many configurations it refused and one of them."""
    grouped: dict[tuple[str, str], list[Plan]] = {}
    for c in candidates:
        if c.refused:
            grouped.setdefault(c.refused, []).append(c.plan)
    return [{"code": code, "message": message, "configurations": len(plans), "example": dict(plans[0])}
            for (code, message), plans in sorted(grouped.items(), key=lambda kv: -len(kv[1]))]  # fmt: skip


def shaped(ranked: list[Candidate]) -> list[Candidate]:
    """`ranked` with each run of equal predicted times reordered: a candidate whose items apart from `LAUNCH` no
    earlier candidate had comes before one whose items some earlier candidate had. Unequal times keep their order."""
    out: list[Candidate] = []
    seen: set[tuple] = set()
    for _, tier in itertools.groupby(ranked, key=lambda c: model.significant(c.predicted_ns)):
        fresh: list[Candidate] = []
        again: list[Candidate] = []
        for c in tier:
            shape = kernel(c)
            (again if shape in seen else fresh).append(c)
            seen.add(shape)
        out += fresh + again
    return out


def kernel(c: Candidate) -> tuple:
    """What decides the device code `c`'s function compiles to: the implementation it selects, whose kernels no plan
    of the reference touches, or else the reference's items apart from `LAUNCH`."""
    return (c.use,) if c.use else (None, *((k, v) for k, v in c.plan if k in KERNEL))


def inspected(candidates: list[Candidate], name: str, inspector: Any, profile: Profile, goal: Objective,
              arch: str | None, spent: Spent) -> None:  # fmt: skip
    """Compile the legal device candidates in predicted order for resource inspection, within the budget, and price
    each inspected one again with what it uses. Among candidates the model prices alike, those whose kernel items
    the budget has not yet reached come first, so a small budget reads more distinct kernels before it reads the
    launch variants of one; and a candidate whose kernel (`kernel`) a compile has read takes that reading, with the
    staged tiles of its own plan, and names the candidate it came from."""
    from .resources import tiles

    read: dict[tuple, dict[str, Any]] = {}  # each kernel's reading, with the candidate it was read for
    for c in shaped(sorted((c for c in candidates if not c.refused and not c.alike), key=lambda c: c.predicted_ns)):
        if (first := read.get(kernel(c))) is not None:
            c.resources = {**first, "kept": True}
            if c.use is None and first.get("status") == "read":
                c.resources["dynamic_shared_bytes"] = tiles(c.program, c.checker, name)  # its own plan's tiles
            if c.resources.get("status") == "read":
                price(c, profile, goal, arch)
            continue
        if spent.out_of_time():
            spent.skip("not inspected: out of time")
            continue
        kept = inspector.kept(c.source, c.runs(name), c.program, c.cpp)
        if kept is None and spent.compiles >= spent.budget.compiles:
            spent.skip("not inspected: compile budget spent")
            continue
        if kept is None and not spent.fits("compile"):
            spent.skip("not inspected: less time left than the shortest compile took")
            continue
        if kept is None:
            spent.compiles += 1
            began = time.monotonic()
            try:
                c.resources = inspector.inspect(c.source, c.runs(name), c.program, c.checker, c.cpp, spent.left())
            except subprocess.TimeoutExpired:
                spent.skip("not inspected: stopped at the time budget")
                continue
            spent.took("compile", time.monotonic() - began)
        else:
            spent.kept += 1
            c.resources = kept
        read[kernel(c)] = {**c.resources, "same_kernels_as": c.key()}  # a failed compile fails for them too
        if c.resources.get("status") == "read" and c.cost is not None:
            price(c, profile, goal, arch)

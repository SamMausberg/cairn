"""The bounded search behind `cairn tune`: which plans the checker accepts, what each compiles to, what it costs.

1. The space is every value of every item the function's regions take (`SPACE`), `stage` at the radii the staging
   rule reads for the region, and every combination of them, `vector` beside `fuse` and `stage` included. This file
   does not decide which combinations are legal: each complete candidate is written into the source and checked, and
   a refused one is kept with the checker's code and message.
2. Each legal candidate is counted from its own checked program and priced by the model.
3. In predicted order, ties broken toward kernel items no earlier compile covered, each device candidate is compiled
   for the target and read by ptxas and cuobjdump (`perf/resources.py`) until the compile budget is spent. For
   `blur` in evidence/v0_9/search, the model priced all 160 plans alike, and 32 compiles wrote 8 distinct SASS. An
   inspection is kept by the key of what it compiled, so a candidate that emits a program already compiled, in this
   search or an earlier one with the same history, costs no compile. Registers and shared memory then enter its
   price through occupancy.

A budget bounds the whole search: `compiles` device compiles started, `seconds` of wall time checked between steps,
and `runs` timed runs (`perf/tune.py` times; device plans only under the owner's make target). What a spent budget
left undone is counted in the answer, and a candidate nothing compiled says so rather than borrow another's figures.
"""

from __future__ import annotations

import copy
import itertools
import time
from dataclasses import dataclass, field
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_program
from ..compiler.concurrency import PLAN_ITEMS
from . import model
from .plan_source import KEEP, Placement, Plan, written
from .profile import Profile
from .work import Cost, count

SPACE = {  # the values tried for each item; 0 is the runtime's own choice
    "grain": (0, 1, 64, 1024, 8192, 65536),
    "lanes": (0, 1, 2, 4, 8, 16, 32, 64),
    "block": (0, 64, 128, 512, 1024),
    "per_lane": (0, 4, 16, 64),
    "unroll": (0, 4),
    "vector": (0, 2, 4),
}


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

    def seconds(self) -> float:
        return time.monotonic() - self.started

    def out_of_time(self) -> bool:
        return self.seconds() >= self.budget.seconds

    def skip(self, why: str) -> None:
        self.undone[why] = self.undone.get(why, 0) + 1

    def report(self) -> dict[str, Any]:
        return {"compiles": {"allowed": self.budget.compiles, "started": self.compiles, "kept": self.kept},
                "seconds": {"allowed": self.budget.seconds, "spent": round(self.seconds(), 2)},
                "runs": {"allowed": self.budget.runs, "started": self.runs, "kept": self.reused_runs},
                "undone": dict(sorted(self.undone.items()))}  # fmt: skip


def radii(program: Any, name: str) -> tuple[int, ...]:
    """The stage radii worth trying for `name`: the least that tiles one array its device regions read at an
    offset, and the least that tiles every such array. A wider radius only loads more of the same halo."""
    from ..compiler.staging import stageable
    from .regions import walked

    f = next(f for f in program.functions if f.name == name and not f.bindings)
    reach: set[int] = set()
    for s in walked(f.body):
        if s.tag == "parallel" and s.ref == "device":
            spans = [max(abs(d) for d in offsets) for _, _, offsets in stageable(s, PLAN_ITEMS["stage"][2]).values()]
            reach |= {min(spans), max(spans)} if spans else set()
    return tuple(sorted(reach))


def space(kinds: set[str], host_lanes: int, regions: int = 1, stages: tuple[int, ...] = ()) -> list[Plan]:
    """Every combination of the items the function's regions take: lane caps no wider than the host, fuse off or
    joining as many regions as the function has where there are two to join, stage off or at `stages`. Whether a
    combination is legal is the checker's to say."""
    tried = {**SPACE, "fuse": (0, min(regions, PLAN_ITEMS["fuse"][2])), "stage": (0, *stages)}
    items = [k for k in PLAN_ITEMS if PLAN_ITEMS[k][0] in kinds or (PLAN_ITEMS[k][0] == "either" and regions > 1)]
    values = [[v for v in tried[k] if k != "lanes" or v <= host_lanes] for k in items]
    return list(dict.fromkeys(written(dict(zip(items, chosen, strict=True))) for chosen in itertools.product(*values)))


@dataclass
class Candidate:
    """One complete configuration: its plan, and what the checker, the model and the device compiler said of it."""

    plan: Plan
    source: str = ""
    refused: tuple[str, str] | None = None  # the checker's code and message
    cost: Cost | None = None
    program: Any = None
    checker: Any = None
    predicted_ns: float = 0.0
    resources: dict[str, Any] | None = None
    use: str | None = None  # the implementation a `plan f use g;` selects, by its qualified name; None: the reference

    def runs(self, name: str) -> str:
        """The function whose code this candidate runs where it applies: the selected implementation, or `name`."""
        return self.use or name


def checked(placement: Placement, name: str, plans: list[Plan], spent: Spent,
            uses: tuple[str | None, ...] = ()) -> list[Candidate]:  # fmt: skip
    """Each plan, beside each implementation of `uses` when there are any (None is the reference), written into the
    source and checked as a whole program, until the time runs out. A candidate that selects an implementation is
    counted as that implementation, the code that runs where its condition holds."""
    out = []
    for plan, use in itertools.product(plans, uses or (KEEP,)):
        if spent.out_of_time():
            spent.skip("not checked: out of time")
            continue
        chosen = use if isinstance(use, str) else None
        written_as = chosen.rsplit(".", 1)[-1] if chosen else None
        c = Candidate(plan, placement.apply(plan, written_as if use is not KEEP else KEEP), use=chosen)
        try:
            c.program, c.checker, _ = compile_program(c.source)
            c.cost = count(c.program, c.checker, {c.runs(name)})[c.runs(name)]
        except Diagnostic as error:
            c.refused = (error.data["code"], error.data["message"])
        out.append(c)
    return out


def priced(c: Cost, profile: Profile, sizes: list[dict[str, float]], arch: str | None,
           resources: dict[str, Any] | None = None) -> float:  # fmt: skip
    """The predicted time of `c` summed over `sizes`, with the registers and shared memory an inspection read."""
    trial = copy.copy(c)
    trial.regions = [copy.copy(r) for r in c.regions]
    for r in trial.regions:
        if (r.kind == "device" or (r.coop is not None and r.coop.device)) and resources and resources.get(
                "status") == "read":  # fmt: skip
            r.registers = resources["registers"]
            r.shared = resources["shared_bytes"] + resources["dynamic_shared_bytes"]
    return sum(model.predict(trial, profile, s, arch)["ns"] for s in sizes)


def refusals(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """The checker's refusals, one entry per distinct reason: how many configurations it refused and one of them."""
    grouped: dict[tuple[str, str], list[Plan]] = {}
    for c in candidates:
        if c.refused:
            grouped.setdefault(c.refused, []).append(c.plan)
    return [{"code": code, "message": message, "configurations": len(plans), "example": dict(plans[0])}
            for (code, message), plans in sorted(grouped.items(), key=lambda kv: -len(kv[1]))]  # fmt: skip


LAUNCH = {"grain", "lanes", "block", "per_lane"}  # items that say how a region is claimed or launched


def shaped(ranked: list[Candidate]) -> list[Candidate]:
    """`ranked` with each run of equal predicted times reordered: a candidate whose items apart from `LAUNCH` no
    earlier candidate had comes before one whose items some earlier candidate had. Unequal times keep their order."""
    out: list[Candidate] = []
    seen: set[tuple] = set()
    for _, tier in itertools.groupby(ranked, key=lambda c: model.significant(c.predicted_ns)):
        fresh: list[Candidate] = []
        again: list[Candidate] = []
        for c in tier:
            shape = (c.use, *((k, v) for k, v in c.plan if k not in LAUNCH))
            (again if shape in seen else fresh).append(c)
            seen.add(shape)
        out += fresh + again
    return out


def inspected(candidates: list[Candidate], name: str, inspector: Any, profile: Profile, sizes: list[dict[str, float]],
              arch: str | None, spent: Spent) -> None:  # fmt: skip
    """Compile the legal device candidates in predicted order for resource inspection, within the budget, and price
    each inspected one again with what it uses. Among candidates the model prices alike, those whose kernel items
    (unroll, vector, stage, fuse) the budget has not yet reached come first, so a small budget reads more distinct
    kernels before it reads the launch variants of one; every candidate is still compiled on its own."""
    for c in shaped(sorted((c for c in candidates if not c.refused), key=lambda c: c.predicted_ns)):
        if spent.out_of_time():
            spent.skip("not inspected: out of time")
            continue
        kept = inspector.kept(c.source, c.runs(name), c.program)
        if kept is None and spent.compiles >= spent.budget.compiles:
            spent.skip("not inspected: compile budget spent")
            continue
        if kept is None:
            spent.compiles += 1
            c.resources = inspector.inspect(c.source, c.runs(name), c.program, c.checker)
        else:
            spent.kept += 1
            c.resources = kept
        if c.resources.get("status") == "read" and c.cost is not None:
            c.predicted_ns = priced(c.cost, profile, sizes, arch, c.resources)

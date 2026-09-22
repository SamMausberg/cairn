"""`cairn tune f`: choose a plan by prediction, and measure only the few the prediction ranks best.

A plan changes how a function's host regions are scheduled and nothing they compute (docs/concurrency.md), so every
candidate here is correct by construction: the search never has to ask whether a variant is equivalent, only which
is fastest. Every legal plan is priced by the model in microseconds. `--measure` then times the best-ranked few and
the plan the function has now on this host, halving the field each round with more blocks for the survivors, and
reports how the measured order agreed with the predicted one. Device plans are priced but never timed here.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from ..compiler.cairnc import compile_program
from . import model
from .profile import Profile, default
from .work import Cost, count

GRAINS = (0, 1, 64, 1024, 8192, 65536)  # indices per claim; 0 is the pool's own choice
LANES = (0, 1, 2, 4, 8, 16, 32, 64)  # lanes at most; 0 is every lane


def text(name: str, grain: int, lanes: int) -> str:
    items = [f"grain {grain};" if grain else "", f"lanes {lanes};" if lanes else ""]
    return f"plan {name} {{ {' '.join(i for i in items if i)} }}" if grain or lanes else ""


def replanned(source: str, name: str, plan: str) -> str:
    """`source` with `name`'s plan replaced by `plan`, or its plan removed when `plan` is empty."""
    written = re.compile(rf"^\s*plan\s+{re.escape(name)}\s*\{{[^}}]*\}}[ \t]*\n?", re.M)
    stripped = written.sub("", source)
    return stripped if not plan else stripped.rstrip("\n") + "\n\n" + plan + "\n"


def space(host_lanes: int) -> list[tuple[int, int]]:
    return [(g, lanes) for g in GRAINS for lanes in LANES if lanes <= host_lanes]


def priced(c: Cost, plan: tuple[int, int], profile: Profile, sizes: list[dict[str, float]], arch: str | None) -> float:
    trial = copy.copy(c)
    trial.regions = [copy.copy(r) for r in c.regions]
    for r in trial.regions:
        if r.kind == "host":
            r.plan = plan
    return sum(model.predict(trial, profile, s, arch)["ns"] for s in sizes)


def tune(source: str, name: str, sizes: list[dict[str, float]], profile: Profile | None = None,
         arch: str | None = None, measure: int = 0, cxx: str = "clang++") -> dict[str, Any]:  # fmt: skip
    """Every legal plan of `name` ranked by prediction at `sizes`; with `measure`, that many of the best timed."""
    chosen = profile or default()
    p, checker, _ = compile_program(source)
    costs = count(p, checker, {name})
    if name not in costs:
        raise ValueError(f"No function {name} to tune.")
    c = costs[name]
    if not any(r.kind == "host" for r in c.regions):
        raise ValueError(f"{name} has no host parallel region, so no plan applies to it (E-PLAN).")
    if not sizes:
        raise ValueError("Give the sizes to tune for with --at, such as --at n=1e7.")
    current = next(r.plan for r in c.regions if r.kind == "host")
    lanes = chosen.host.lanes if chosen.host else 16
    ranked = sorted(space(lanes), key=lambda plan: priced(c, plan, chosen, sizes, arch))
    rows = [{"plan": text(name, *plan) or f"(no plan for {name})", "grain": plan[0], "lanes": plan[1],
             "predicted_ns": round(priced(c, plan, chosen, sizes, arch), 1)} for plan in ranked]  # fmt: skip
    result: dict[str, Any] = {
        "schema": "cairn.tune/1",
        "function": name,
        "sizes": sizes,
        "predicted": "Every plan is priced by cairn predict; a plan changes no result, so none needed checking.",
        "profile": chosen.describe(),
        "current": text(name, *current) or f"(no plan for {name})",
        "candidates": rows,
        "chosen": rows[0],
    }
    if measure:
        result.update(timed(source, name, ranked, current, sizes, measure, cxx, arch))
    return result


def timed(source: str, name: str, ranked: list[tuple[int, int]], current: tuple[int, int],
          sizes: list[dict[str, float]], keep: int, cxx: str, arch: str | None) -> dict[str, Any]:  # fmt: skip
    """Successive halving over the best-ranked `keep` plans and the current one, timed on this host."""
    from ..projects.toolchain import resolve_arch
    from . import measure

    field = list(dict.fromkeys([*distinct(ranked, keep), current]))
    blocks, rounds = 3, []
    times: dict[tuple[int, int], float] = {}
    while True:
        for plan in field:
            variant = replanned(source, name, text(name, *plan))
            total = 0.0
            for s in sizes:
                got = measure.time(variant, name, s, cxx=cxx, arch=resolve_arch(arch), blocks=blocks)
                if got["status"] != "measured":
                    raise ValueError(f"{text(name, *plan) or 'the current plan'} did not run: {got}")
                total += float(got["median_ns"])
            times[plan] = total
        rounds.append(
            {"blocks": blocks, "measured_ns": {text(name, *p) or "(none)": round(times[p], 1) for p in field}}
        )
        if len(field) <= 2:
            break
        field = sorted(field, key=lambda plan: times[plan])[: max(2, len(field) // 2)]
        blocks *= 2
    best = min(field, key=lambda plan: times[plan])
    order = [p for p in ranked if p in times]  # predicted order, fastest first
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :]]
    agree = sum(times[a] <= times[b] for a, b in pairs)
    chosen = {"plan": text(name, *best) or f"(no plan for {name})", "grain": best[0], "lanes": best[1]}
    return {
        "measured": "Timed on this host by cairn.perf.measure, the median of each round's blocks; a busy machine "
        "adds noise, so a close measured order says little.",
        "rounds": rounds,
        "measured_best": chosen["plan"],
        "pairs_ordered_as_predicted": f"{agree} of {len(pairs)}",
        "chosen": {**chosen, "measured_ns": round(times[best], 1)},
    }


def distinct(ranked: list[tuple[int, int]], keep: int) -> list[tuple[int, int]]:
    """The best-ranked `keep` plans that differ in their lane cap: plans the model prices alike are one candidate,
    so timing more than one of them spends the budget on noise."""
    seen: set[int] = set()
    picked = []
    for plan in ranked:
        if plan[1] not in seen:
            seen.add(plan[1])
            picked.append(plan)
        if len(picked) == keep:
            break
    return picked

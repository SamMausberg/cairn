"""`cairn tune f`: choose a plan by prediction, and measure only the few the prediction ranks best.

A plan changes how a function's regions are scheduled and nothing they compute (docs/concurrency.md), so every
candidate here is correct by construction: the search never has to ask whether a variant is equivalent, only which
is fastest. Every legal plan is priced by the model in microseconds: `grain` and `lanes` for host regions, `block`,
`per_lane` and `unroll` for device regions, whose kernels ptxas reads once per unroll for their registers, without
running anything. `--measure` then times the best-ranked few and the plan the function has now, halving the field
each round with more blocks for the survivors, and says how the measured order agreed with the predicted one. Host
plans are timed on this host. Device plans are timed only with `--device`, which runs device code and so runs only
where `make tune-device` allows it: the owner's target, holding the device lock.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_program
from ..compiler.concurrency import PLAN_ITEMS
from ..projects.target import DeviceTarget, resolve
from . import model
from .plan_source import Placement, Plan, shown, written
from .profile import Profile, default
from .work import Cost, count

SPACE = {  # the values tried for each item; 0 is the runtime's own choice
    "grain": (0, 1, 64, 1024, 8192, 65536),
    "lanes": (0, 1, 2, 4, 8, 16, 32, 64),
    "block": (0, 64, 128, 512, 1024),
    "per_lane": (0, 4, 16, 64),
    "unroll": (0, 4),
    "vector": (0, 2, 4),
    "stage": (0,),  # the model prices a staged region as the unplanned one, so ranking cannot choose it
}


def space(kinds: set[str], host_lanes: int, regions: int = 1) -> list[Plan]:
    """Every plan of the items the function's regions take, lane caps no wider than the host; fuse, off or joining
    as many regions as the function has, only where there are two to join."""
    tried = {**SPACE, "fuse": (0, min(regions, PLAN_ITEMS["fuse"][2]))}
    items = [k for k in PLAN_ITEMS if PLAN_ITEMS[k][0] in kinds or (PLAN_ITEMS[k][0] == "either" and regions > 1)]
    values = [[v for v in tried[k] if k != "lanes" or v <= host_lanes] for k in items]
    plans = [dict(zip(items, chosen, strict=True)) for chosen in itertools.product(*values)]
    return [written(p) for p in plans if not (p.get("vector") and p.get("fuse"))]  # a plan takes one or the other


def registers(source: str, name: str, unrolls: set[int], target: DeviceTarget | None = None) -> dict[int, int]:
    """ptxas's register count for `name`'s kernels at each unroll, compiled for `target` and never run."""
    from .device import available, kernels

    if not available():
        return {}
    found = {}
    for u in sorted(unrolls):
        read = kernels(Placement(source, name).apply((("unroll", u),) if u > 1 else ()), target)
        used = [k["registers"] for k in read.get("kernels", {}).get(name, [])]
        if used:
            found[u] = max(used)
    return found


def priced(c: Cost, plan: Plan, profile: Profile, sizes: list[dict[str, float]], arch: str | None,
           held: dict[int, int]) -> float:  # fmt: skip
    items = dict(plan)
    trial = copy.copy(c)
    trial.regions = [copy.copy(r) for r in c.regions]
    for r in trial.regions:
        if r.kind == "host":
            r.plan = (items.get("grain", 0), items.get("lanes", 0))
        elif r.kind == "device":
            r.launch = (items.get("block", 0), items.get("per_lane", 0), items.get("unroll", 0))
            r.registers = held.get(max(items.get("unroll", 1), 1), 0)
            r.vector = items.get("vector", 0)
    return sum(model.predict(trial, profile, s, arch)["ns"] for s in sizes)


def tune(source: str, name: str, sizes: list[dict[str, float]], profile: Profile | None = None,
         arch: str | None = None, measure: int = 0, cxx: str = "clang++", device: bool = False,
         device_target: DeviceTarget | None = None) -> dict[str, Any]:  # fmt: skip
    """Every legal plan of `name` ranked by prediction at `sizes`; with `measure`, that many of the best timed. Device
    plans are read, priced and timed for one device target: `device_target`, or the one resolved here."""
    from .report import targeted

    chosen = profile or default()
    p, checker, _ = compile_program(source)
    costs = count(p, checker, {name})
    if name not in costs:
        raise ValueError(f"No function {name} to tune.")
    c = costs[name]
    kinds = {r.kind for r in c.regions} & {"host", "device"}
    if not kinds:
        raise ValueError(f"{name} has no parallel region, so no plan applies to it (E-PLAN).")
    if not sizes:
        raise ValueError("Give the sizes to tune for with --at, such as --at n=1e7.")
    current = written(now(c))
    placement = Placement(source, name)
    target = (device_target or resolve(required=False)) if "device" in kinds else None
    on = targeted({name: c}, chosen, target)  # the card that prices device plans runs the target's code
    held = registers(source, name, {max(u, 1) for u in SPACE["unroll"]}, target) if "device" in kinds else {}
    candidates = space(kinds, chosen.host.lanes if chosen.host else 16, regions(p, name))
    counted: dict[int, Cost] = {}  # a fused chain is one region, not two, so each fuse is counted as written
    for joined in {dict(plan).get("fuse", 0) for plan in candidates}:
        again = placement.apply((("fuse", joined),) if joined else ())
        try:
            p2, checker2, _ = compile_program(again)
            counted[joined] = count(p2, checker2, {name})[name]
        except Diagnostic:  # no chain these rules allow: the plan is refused, so it is no candidate
            candidates = [plan for plan in candidates if dict(plan).get("fuse", 0) != joined]
    for width in {dict(plan).get("vector", 0) for plan in candidates} - {0}:  # the widths the checker allows here
        try:
            compile_program(placement.apply((("vector", width),)))
        except Diagnostic:  # nothing to chunk, or a chunk wider than one access: the plan is refused
            candidates = [plan for plan in candidates if dict(plan).get("vector", 0) != width]
    cost = {plan: priced(counted[dict(plan).get("fuse", 0)], plan, chosen, sizes, arch, held) for plan in candidates}
    ranked = sorted(candidates, key=lambda plan: cost[plan])
    rows = [{"plan": shown(name, plan), **dict(plan), "predicted_ns": model.significant(cost[plan])} for plan in ranked]
    result: dict[str, Any] = {
        "schema": "cairn.tune/1",
        "function": name,
        "sizes": sizes,
        "predicted": "Every plan is priced by cairn predict; a plan changes no result, so none needed checking.",
        "profile": chosen.describe(),
        **on,
        **({"registers_by_unroll": held} if held else {}),
        "current": shown(name, current),
        "candidates": rows,
        "chosen": rows[0],
    }
    if measure and "device" in kinds and not device:
        result["measured"] = "Not measured: device plans are timed only by `make tune-device`, which the owner runs."
    elif measure:
        result.update(timed(source, name, ranked, current, sizes, measure, cxx, arch, device, target))
    return result


def timed(source: str, name: str, ranked: list[Plan], current: Plan, sizes: list[dict[str, float]], keep: int,
          cxx: str, arch: str | None, device: bool, target: DeviceTarget | None = None) -> dict[str, Any]:  # fmt: skip
    """Successive halving over the best-ranked `keep` distinct plans and the current one."""
    from ..projects.toolchain import resolve_arch
    from . import measure, on_device

    placement = Placement(source, name)
    field = list(dict.fromkeys([*distinct(ranked, keep), current]))
    blocks, rounds = 3, []
    times: dict[Plan, float] = {}
    while True:
        for plan in field:
            variant = placement.apply(plan)
            total = 0.0
            for s in sizes:
                if device:
                    got = on_device.time_device(variant, name, s, blocks=blocks, target=target)
                else:
                    got = measure.time(variant, name, s, cxx=cxx, arch=resolve_arch(arch), blocks=blocks)
                if got["status"] != "measured":
                    raise ValueError(f"{shown(name, plan)} did not run: {got}")
                total += float(got["median_ns"])
            times[plan] = total
        rounds.append({"blocks": blocks, "measured_ns": {shown(name, p): round(times[p], 1) for p in field}})
        if len(field) <= 2:
            break
        field = sorted(field, key=lambda plan: times[plan])[: max(2, len(field) // 2)]
        blocks *= 2
    best = min(field, key=lambda plan: times[plan])
    order = [p for p in ranked if p in times]  # predicted order, fastest first
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :]]
    agree = sum(times[a] <= times[b] for a, b in pairs)
    where = "on the device, under the owner's make target" if device else "on this host"
    return {
        "measured": f"Timed {where} by cairn.perf.measure, the median of each round's blocks; a busy machine adds "
        "noise, so a close measured order says little.",
        "rounds": rounds,
        "measured_best": shown(name, best),
        "pairs_ordered_as_predicted": f"{agree} of {len(pairs)}",
        "chosen": {"plan": shown(name, best), **dict(best), "measured_ns": round(times[best], 1)},
    }


def distinct(ranked: list[Plan], keep: int) -> list[Plan]:
    """The best-ranked `keep` plans the model tells apart: plans it prices alike would spend the budget on noise,
    so one of each predicted time is timed."""
    picked: list[Plan] = []
    seen: set[str] = set()
    for plan in ranked:
        items = dict(plan)
        key = f"{items.get('lanes', 0)}/{items.get('block', 0)}/{items.get('per_lane', 0)}"
        if key not in seen:
            seen.add(key)
            picked.append(plan)
        if len(picked) == keep:
            break
    return picked


def regions(p: Any, name: str) -> int:
    """How many parallel statements `name` has, fused or not: what a fuse could join."""
    from ..compiler.concurrency import walk

    return sum(s.tag == "parallel" for f in p.functions if name in (f.name, f.source_name) for s in walk(f.body))


def now(c: Cost) -> dict[str, int]:
    """The plan the function has now, read back from its regions."""
    items: dict[str, int] = {}
    for r in c.regions:
        if r.kind == "host":
            items |= {"grain": r.plan[0], "lanes": r.plan[1]}
        elif r.kind == "device":
            items |= dict(zip(("block", "per_lane", "unroll"), r.launch, strict=True))
        if r.fuse:
            items["fuse"] = r.fuse
    return items

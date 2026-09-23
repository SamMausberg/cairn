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
import re
from typing import Any

from ..compiler.cairnc import compile_program
from ..compiler.concurrency import PLAN_ITEMS
from . import model
from .profile import Profile, default
from .work import Cost, count

SPACE = {  # the values tried for each item; 0 is the runtime's own choice
    "grain": (0, 1, 64, 1024, 8192, 65536),
    "lanes": (0, 1, 2, 4, 8, 16, 32, 64),
    "block": (0, 64, 128, 512, 1024),
    "per_lane": (0, 4, 16, 64),
    "unroll": (0, 4),
}
Plan = tuple[tuple[str, int], ...]  # the items a plan sets, in the checker's order, zeros left out


def written(items: dict[str, int]) -> Plan:
    return tuple((k, items[k]) for k in PLAN_ITEMS if items.get(k))


def text(name: str, plan: Plan) -> str:
    return f"plan {name} {{ {' '.join(f'{k} {v};' for k, v in plan)} }}" if plan else ""


def shown(name: str, plan: Plan) -> str:
    return text(name, plan) or f"(no plan for {name})"


def replanned(source: str, name: str, plan: str) -> str:
    """`source` with `name`'s plan replaced by `plan`, or its plan removed when `plan` is empty."""
    found = re.compile(rf"^\s*plan\s+{re.escape(name)}\s*\{{[^}}]*\}}[ \t]*\n?", re.M)
    stripped = found.sub("", source)
    return stripped if not plan else stripped.rstrip("\n") + "\n\n" + plan + "\n"


def write_plan(manifest: Any, symbol: str, chosen: dict[str, Any]) -> str:
    """Write `chosen` as `symbol`'s plan into the one file that declares it, and return that file's path.

    The file is where the checked program places the function, never a text match: a comment or another module's
    function of the same short name would claim it. The plan it has, written by its short or its qualified name, is
    replaced by one under its short name, and the rewrite is written only when the whole project still checks."""
    from ..compiler.cairnc import Diagnostic, compile_source
    from ..projects.project import ProjectError, contained_file, load_project

    project = load_project(manifest)
    f = next((f for f in compile_program(project.source)[0].functions if f.name == symbol and not f.bindings), None)
    unit = project.unit_at(f.line) if f else None
    if unit is None or unit.path in project.vendored_units:
        raise ProjectError(f"{symbol} is not declared in a file of this project, so its plan has nowhere to go.")
    path = contained_file(project.root, unit.path, ".cairn")
    local = symbol.rsplit(".", 1)[-1]
    after = replanned(replanned(path.read_text(encoding="utf-8"), symbol, ""), local, text(local, written(chosen)))
    try:
        compile_source(load_project(manifest, given={path.resolve(): after}).source)
    except Diagnostic as error:
        raise ProjectError(f"The plan chosen for {symbol} would leave the project refused ({error.data['code']}: "
                           f"{error.data['message']}); nothing was written.") from error  # fmt: skip
    path.write_text(after, encoding="utf-8")
    return unit.path


def space(kinds: set[str], host_lanes: int) -> list[Plan]:
    """Every plan of the items the function's regions take, lane caps no wider than the host."""
    items = [k for k in PLAN_ITEMS if PLAN_ITEMS[k][0] in kinds]
    values = [[v for v in SPACE[k] if k != "lanes" or v <= host_lanes] for k in items]
    return [written(dict(zip(items, chosen, strict=True))) for chosen in itertools.product(*values)]


def registers(source: str, name: str, unrolls: set[int]) -> dict[int, int]:
    """ptxas's register count for `name`'s kernels at each unroll, compiled for the device and never run."""
    from .device import available, kernels

    if not available():
        return {}
    found = {}
    for u in sorted(unrolls):
        read = kernels(replanned(source, name, text(name, (("unroll", u),)) if u > 1 else ""))
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
    return sum(model.predict(trial, profile, s, arch)["ns"] for s in sizes)


def tune(source: str, name: str, sizes: list[dict[str, float]], profile: Profile | None = None,
         arch: str | None = None, measure: int = 0, cxx: str = "clang++", device: bool = False) -> dict[str, Any]:  # fmt: skip
    """Every legal plan of `name` ranked by prediction at `sizes`; with `measure`, that many of the best timed."""
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
    held = registers(source, name, {max(u, 1) for u in SPACE["unroll"]}) if "device" in kinds else {}
    candidates = space(kinds, chosen.host.lanes if chosen.host else 16)
    cost = {plan: priced(c, plan, chosen, sizes, arch, held) for plan in candidates}
    ranked = sorted(candidates, key=lambda plan: cost[plan])
    rows = [{"plan": shown(name, plan), **dict(plan), "predicted_ns": model.significant(cost[plan])} for plan in ranked]
    result: dict[str, Any] = {
        "schema": "cairn.tune/1",
        "function": name,
        "sizes": sizes,
        "predicted": "Every plan is priced by cairn predict; a plan changes no result, so none needed checking.",
        "profile": chosen.describe(),
        **({"registers_by_unroll": held} if held else {}),
        "current": shown(name, current),
        "candidates": rows,
        "chosen": rows[0],
    }
    if measure and "device" in kinds and not device:
        result["measured"] = "Not measured: device plans are timed only by `make tune-device`, which the owner runs."
    elif measure:
        result.update(timed(source, name, ranked, current, sizes, measure, cxx, arch, device))
    return result


def timed(source: str, name: str, ranked: list[Plan], current: Plan, sizes: list[dict[str, float]], keep: int,
          cxx: str, arch: str | None, device: bool) -> dict[str, Any]:  # fmt: skip
    """Successive halving over the best-ranked `keep` distinct plans and the current one."""
    from ..projects.toolchain import resolve_arch
    from . import measure, on_device

    field = list(dict.fromkeys([*distinct(ranked, keep), current]))
    blocks, rounds = 3, []
    times: dict[Plan, float] = {}
    while True:
        for plan in field:
            variant = replanned(source, name, text(name, plan))
            total = 0.0
            for s in sizes:
                if device:
                    got = on_device.time_device(variant, name, s, blocks=blocks)
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


def now(c: Cost) -> dict[str, int]:
    """The plan the function has now, read back from its regions."""
    items: dict[str, int] = {}
    for r in c.regions:
        if r.kind == "host":
            items |= {"grain": r.plan[0], "lanes": r.plan[1]}
        elif r.kind == "device":
            items |= dict(zip(("block", "per_lane", "unroll"), r.launch, strict=True))
    return items

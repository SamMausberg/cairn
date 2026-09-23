"""`cairn tune f`: search a function's plans within a budget, and measure only the few the search ranks best.

A plan changes how a function's regions are scheduled and nothing they compute (docs/concurrency.md), so every
candidate the checker accepts is correct by construction: the search never has to ask whether a variant is
equivalent, only which is fastest. `perf/search.py` builds the space, checks each complete candidate, prices it, and
compiles device candidates for their registers and shared memory within the compile budget. `--measure` then times
the best-ranked few and the plan the function has now, halving the field each round with more blocks for the
survivors, within the run budget, and says how the measured order agreed with the predicted one. Host plans are
timed on this host. Device plans are timed only with `--device`, which runs device code and so runs only where
`make tune-device` allows it: the owner's target, holding the device lock.

With a history (`agent/history.py`), the search records what it tried, what the checker or a compiler refused, what
each compile read and what each run measured, and it answers from the history what an earlier search already
established for the same identity: a kept inspection is not compiled again, and a kept measurement is not run again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..agent import history as kept
from ..compiler.cairnc import compile_program
from ..projects.target import DeviceTarget, resolve
from . import model
from .plan_source import Placement, Plan, contract, shown, written
from .profile import Profile, default
from .regions import applied, identified
from .resources import Inspector, device_identity, host_target
from .search import SPACE, Budget, Candidate, Spent, checked, inspected, priced, radii, refusals, space
from .work import Cost, count

__all__ = ["SPACE", "Budget", "distinct", "now", "regions", "space", "tune"]
PROCEDURE = {  # how a measurement was made, word for word: a kept one answers only for the same procedure
    False: "cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, "
    "timed in blocks of at least 2 ms, the median of {blocks} blocks, on this host",
    True: "cairn.perf.on_device: the candidate built for the device beside a driver that fills each view on the host "
    "and copies it across once, timed in blocks of at least 2 ms, the median of {blocks} blocks, under make "
    "tune-device holding the device lock",
}


class Recorder:
    """What a search writes into a history, under the identity of each candidate it is about."""

    def __init__(self, where: str | Path, source: str, name: str, contract: Any, host: dict,
                 device: DeviceTarget | None):  # fmt: skip
        self.where, self.name, self.contract = where, name, contract
        self.base = kept.as_written(source, name)
        self.host, self.device = kept.digest(host), device_identity(device)
        self.history = kept.History(where)

    def put(self, kind: str, plan: Plan, target: str, detail: dict, artifact: str | None = None) -> None:
        variant = {"plan": dict(plan)}
        identity = kept.identity(self.base, variant, self.contract, target, artifact)
        kept.record(self.where, kind, self.name, shown(local(self.name), plan), identity, detail, variant)

    def earlier(self, plan: Plan, sizes: dict[str, float], procedure: str, target: str) -> dict[str, Any] | None:
        """A current measurement of `plan` at `sizes` by `procedure` on `target`, if one is kept."""
        split = self.history.judged(self.name, self.base, {kept.digest(self.contract)}, {target})
        return next((r["detail"] for r in split["current"] if r["kind"] == "measurement" and r["variant"] ==
                     {"plan": dict(plan)} and r["detail"].get("sizes") == sizes and r["detail"]["procedure"] ==
                     procedure), None)  # fmt: skip


def local(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def row(name: str, c: Candidate, regions: list[str]) -> dict[str, Any]:
    """One candidate as the answer shows it: its plan, its price, what a compile read, what it did to which region."""
    out: dict[str, Any] = {"plan": shown(local(name), c.plan), **dict(c.plan),
                           "predicted_ns": model.significant(c.predicted_ns)}  # fmt: skip
    if c.resources is not None:
        r = c.resources
        out["resources"] = ({k: r[k] for k in ("registers", "spill_bytes", "stack_bytes", "shared_bytes",
                                               "dynamic_shared_bytes", "instructions") if k in r}
                            if r["status"] == "read" else {"status": r["status"]})  # fmt: skip
        out["resources"]["key"] = r["key"][:16]
        out["resources"]["kept"] = bool(r.get("kept"))
    if any(k in dict(c.plan) for k in ("vector", "stage", "fuse")) and regions:
        done = applied(c.source, name, (c.program, c.checker))
        targets = {item: {rid: v[item] for rid, v in done.items() if item in v} for item in ("vector", "stage")}
        chains = {tuple(v["fuse"]) for v in done.values() if "fuse" in v}
        out["applies_to"] = {**{k: v for k, v in targets.items() if v}, **({"fuse": [list(c) for c in chains]}
                                                                          if chains else {})}  # fmt: skip
    return out


def tune(source: str, name: str, sizes: list[dict[str, float]], profile: Profile | None = None,
         arch: str | None = None, measure: int = 0, cxx: str = "clang++", device: bool = False,
         device_target: DeviceTarget | None = None, budget: Budget | None = None,
         history: str | Path | None = None) -> dict[str, Any]:  # fmt: skip
    """Every legal plan of `name` ranked by prediction at `sizes`, device candidates compiled within `budget` for one
    device target, `device_target` or the one resolved here; with `measure`, that many of the best timed. `history`
    is a directory to record into and answer from."""
    from .report import targeted

    chosen, spent = profile or default(), Spent(budget or Budget())
    p, checker, receipts = compile_program(source)
    costs = count(p, checker, {name})
    if name not in costs:
        raise ValueError(f"No function {name} to tune.")
    c = costs[name]
    kinds = {r.kind for r in c.regions} & {"host", "device"}
    if not kinds:
        raise ValueError(f"{name} has no parallel region, so no plan applies to it (E-PLAN).")
    if not sizes:
        raise ValueError("Give the sizes to tune for with --at, such as --at n=1e7.")
    placement = Placement(source, name)
    target = (device_target or resolve(required=False)) if "device" in kinds else None
    on = targeted({name: c}, chosen, target)  # the card that prices device plans runs the target's code
    current = written(receipts[name].get("plan", {}))
    stages = radii(p, name) if "device" in kinds else ()
    plans = space(kinds, chosen.host.lanes if chosen.host else 16, regions(p, name), stages)
    candidates = checked(placement, name, plans, spent)
    for cand in candidates:
        if cand.cost is not None:
            cand.predicted_ns = priced(cand.cost, chosen, sizes, arch)
    recorder = None
    if history is not None:
        recorder = Recorder(history, source, name, contract(source, name), host_target(arch, cxx), target)
    if "device" in kinds:
        from .device import available

        if target is None:
            spent.undone["not inspected: no device target; name one with --device-target"] = sum(
                not x.refused for x in candidates
            )
        elif available():
            inspector = Inspector(target, recorder.history if recorder else None)
            inspected(candidates, name, inspector, chosen, sizes, arch, spent)
        else:
            spent.undone["not inspected: nvcc and cuobjdump are needed"] = sum(not x.refused for x in candidates)
    legal = sorted((x for x in candidates if not x.refused), key=lambda x: x.predicted_ns)
    if not legal:
        raise ValueError(f"The checker refused every plan of {name} the search tried.")
    ids = [r["id"] for r in identified(source, name)]
    rows = [row(name, x, ids) for x in legal]
    read = [r for r in rows if r.get("resources", {}).get("registers") is not None]
    result: dict[str, Any] = {
        "schema": "cairn.tune/2",
        "function": name,
        "sizes": sizes,
        "predicted": "Every plan the checker accepted is priced by cairn predict; a plan changes no result, so none "
        "needed checking. Registers and shared memory come from compiling the candidate, never from a run.",
        "profile": chosen.describe(),
        "target": {"host": {"arch": arch or "baseline"}},
        **on,
        "regions": identified(source, name),
        "current": shown(local(name), current),
        "space": {
            "configurations": len(plans),
            "checked": len(candidates),
            "legal": len(legal),
            "refused": refusals(candidates),
        },
        "candidates": rows,
        "chosen": read[0] if read else rows[0],
    }
    predicted = result["chosen"]
    if measure and "device" in kinds and not device:
        result["measured"] = "Not measured: device plans are timed only by `make tune-device`, which the owner runs."
    elif measure:
        ranked = [x.plan for x in legal]
        result.update(timed(source, name, ranked, current, sizes, measure, cxx, arch, device, spent, recorder, target))
    if recorder is not None:
        record_search(recorder, candidates, {**result, "chosen": predicted})
    result["budget"] = spent.report()
    return result


def record_search(recorder: Recorder, candidates: list[Candidate], result: dict) -> None:
    """The search itself, the checker's refusals and each compile's reading, into the history."""
    summary = {k: result["space"][k] for k in ("configurations", "checked", "legal")}
    chosen = written({k: v for k, v in result["chosen"].items() if isinstance(v, int)})
    ranked = [[row["plan"], row["predicted_ns"]] for row in result["candidates"][:8]]  # what to time next
    recorder.put("attempt", chosen, recorder.host, {"by": "cairn tune", "sizes": result["sizes"], **summary,
                                                    "chosen": result["chosen"]["plan"], "ranked": ranked,
                                                    **({"measured_best": result["measured_best"]}
                                                       if result.get("measured_best") else {})})  # fmt: skip
    for group in refusals(candidates):
        plan = written(group["example"])
        recorder.put("failure", plan, recorder.host, {"stage": "check", "why": f"{group['code']}: {group['message']}",
                                                      "configurations": group["configurations"]})  # fmt: skip
    for x in candidates:
        r = x.resources
        if r is None or r.get("kept"):
            continue
        if r["status"] == "read":
            detail = {"by": "ptxas and cuobjdump", "analysis": r["key"], **{k: r[k] for k in (
                "registers", "spill_bytes", "stack_bytes", "shared_bytes", "dynamic_shared_bytes", "instructions",
                "memory", "kernels")}}  # fmt: skip
            recorder.put("observation", x.plan, recorder.device, detail, r.get("cubin_sha256"))
        elif r["status"] == "compile-failed":
            recorder.put("failure", x.plan, recorder.device, {"stage": "build", "why": r.get("why", "nvcc failed"),
                                                              "analysis": r["key"]})  # fmt: skip


def timed(source: str, name: str, ranked: list[Plan], current: Plan, sizes: list[dict[str, float]], keep: int,
          cxx: str, arch: str | None, device: bool, spent: Spent | None = None,
          recorder: Recorder | None = None, target: DeviceTarget | None = None) -> dict[str, Any]:  # fmt: skip
    """Successive halving over the best-ranked `keep` distinct plans and the current one, within the run budget; a
    measurement the history already holds for the same identity and procedure is used rather than run again."""
    from ..projects.toolchain import resolve_arch
    from . import measure, on_device

    spent = spent or Spent(Budget())
    allowed = spent.budget.runs
    placement = Placement(source, name)
    field = list(dict.fromkeys([*distinct(ranked, keep), current]))
    where = (recorder.device if device else recorder.host) if recorder else ""
    blocks, rounds = 3, []
    times: dict[Plan, float] = {}
    while True:
        done: list[Plan] = []
        procedure = PROCEDURE[device].format(blocks=blocks)
        for plan in field:
            total = 0.0
            for s in sizes:
                got = recorder.earlier(plan, s, procedure, where) if recorder else None
                if got is not None:
                    spent.reused_runs += 1
                elif allowed is not None and spent.runs >= allowed:
                    spent.skip("not measured: run budget spent")
                    break
                else:
                    spent.runs += 1
                    variant = placement.apply(plan)
                    if device:
                        got = on_device.time_device(variant, name, s, blocks=blocks, target=target)
                    else:
                        got = measure.time(variant, name, s, cxx=cxx, arch=resolve_arch(arch), blocks=blocks)
                    if got["status"] != "measured":
                        raise ValueError(f"{shown(name, plan)} did not run: {got}")
                    if recorder:
                        numbers = {k: got[k] for k in ("median_ns", "min_ns", "max_ns", "inner") if k in got}
                        recorder.put("measurement", plan, where, {"procedure": procedure, "sizes": s, **numbers})
                total += float(got["median_ns"])
            else:
                times[plan] = total
                done.append(plan)
        rounds.append({"blocks": blocks, "measured_ns": {shown(name, p): round(times[p], 1) for p in done}})
        if len(done) < len(field) or len(field) <= 2:
            field = done or field
            break
        field = sorted(field, key=lambda plan: times[plan])[: max(2, len(field) // 2)]
        blocks *= 2
    ran = "on the device, under the owner's make target" if device else "on this host"
    note = (f"Timed {ran} by cairn.perf.measure, the median of each round's blocks; a busy machine adds noise, so "
            "a close measured order says little.")  # fmt: skip
    if not times:
        return {"measured": note, "rounds": rounds, "measured_best": None}
    best = min((p for p in field if p in times), key=lambda plan: times[plan])
    order = [p for p in ranked if p in times]  # predicted order, fastest first
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :]]
    agree = sum(times[a] <= times[b] for a, b in pairs)
    return {
        "measured": note,
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

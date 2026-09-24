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
from ..compiler.tree import local
from ..projects.target import DeviceTarget, resolve
from . import model
from .counts import Cost
from .plan_source import KEEP, Placement, Plan, contract, selecting, shown, text, written
from .profile import Profile, default
from .regions import applied, identified
from .resources import Inspector, device_identity, host_target
from .search import SPACE, Budget, Candidate, Spent, checked, inspected, priced, radii, refusals, space
from .work import count

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
                 device: DeviceTarget | None, implementations: dict[str, Any] | None = None):  # fmt: skip
        self.where, self.name, self.contract = where, name, contract
        self.base = kept.as_written(source, name)
        self.host, self.device = kept.digest(host), device_identity(device)
        self.history = kept.History(where)
        self.implementations = implementations or {}  # the receipt's table: each implementation's identity

    def variant(self, key: Key) -> dict[str, Any]:
        return variant(key, self.implementations)

    def put(self, kind: str, key: Key, target: str, detail: dict, artifact: str | None = None) -> None:
        identity = kept.identity(self.base, self.variant(key), self.contract, target, artifact)
        kept.record(self.where, kind, self.name, label(self.name, key), identity, detail, self.variant(key))

    def earlier(self, key: Key, sizes: dict[str, float], procedure: str, target: str) -> dict[str, Any] | None:
        """A current measurement of the candidate `key` at `sizes` by `procedure` on `target`, if one is kept."""
        split = self.history.judged(self.name, self.base, {kept.digest(self.contract)}, {target})
        return next((r["detail"] for r in split["current"] if r["kind"] == "measurement" and r["variant"] ==
                     self.variant(key) and r["detail"].get("sizes") == sizes and r["detail"]["procedure"] ==
                     procedure), None)  # fmt: skip


Key = tuple[Plan, str | None]  # a plan, and the implementation it selects by qualified name, or None


def variant(key: Key, implementations: dict[str, Any]) -> dict[str, Any]:
    """What makes the candidate `key`: its plan, and the implementation it selects with that implementation's
    identity from the receipt's table, so an edit to the implementation leaves the records of the candidate stale."""
    plan, use = key
    chosen = {"use": use, "implementation": implementations.get(use, {}).get("identity")} if use else {}
    return {"plan": dict(plan), **chosen}


def label(name: str, key: Key) -> str:
    plan, use = key
    selected = selecting(name, use) if use else ""
    return " ".join(x for x in (text(local(name), plan), selected) if x) or shown(local(name), plan)


def validations(source: str, name: str, receipts: dict[str, Any], alternatives: list[str],
                recorder: Recorder | None) -> dict[str, Any]:  # fmt: skip
    """For each implementation of `name`, the validation the history holds for it as it is now (its identity with
    everything it calls, `history.selectable`, the reference as written, this compiler); none without a history. An
    implementation without one is searched and priced but never chosen or timed: selecting it could change a result."""
    if recorder is None:
        return {}
    table = recorder.implementations
    out: dict[str, Any] = {}
    for g in alternatives:
        found = recorder.history.holding(name, recorder.base, "validation", table.get(g, {}).get("identity"))
        if found:
            out[g] = {"evidence": found[-1]["detail"].get("evidence"), "record": found[-1]["id"]}
    return out


def placed(r: Any) -> str:
    """Where a region runs, for what a search compiles: a cooperative region takes no plan item, and its kernel is
    inspected as a device region's is when it runs on the device."""
    return ("device" if r.coop.device else "host") if r.coop is not None else r.kind


def row(name: str, c: Candidate, regions: list[str], validated: dict[str, Any] | None = None) -> dict[str, Any]:
    """One candidate as the answer shows it: its plan, its price, what a compile read, what it did to which region,
    and for an implementation, the validation the history holds for it as it is now."""
    out: dict[str, Any] = {"plan": label(name, (c.plan, c.use)), **dict(c.plan),
                           "predicted_ns": model.significant(c.predicted_ns)}  # fmt: skip
    if c.use:
        out["use"] = local(c.use)
        out["validated"] = (validated or {}).get(c.use) or "no validation holds: run cairn validate before using it"
    if c.resources is not None:
        r = c.resources
        out["resources"] = ({k: r[k] for k in ("registers", "spill_bytes", "stack_bytes", "shared_bytes",
                                               "dynamic_shared_bytes", "instructions") if k in r}
                            if r["status"] == "read" else {"status": r["status"]})  # fmt: skip
        out["resources"]["key"] = r["key"][:16]
        if "sass_sha256" in r:  # candidates whose code is the same share this
            out["resources"]["sass"] = r["sass_sha256"][:16]
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
         history: str | Path | None = None, vendored: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Every legal plan of `name` ranked by prediction at `sizes`, device candidates compiled within `budget` for one
    device target, `device_target` or the one resolved here; with `measure`, that many of the best timed. `history`
    is a directory to record into and answer from; `vendored` (history.vendored) pins the project's foreign sources."""
    from .report import targeted

    chosen, spent = profile or default(), Spent(budget or Budget())
    p, checker, receipts = compile_program(source)
    costs = count(p, checker, {name})
    if name not in costs:
        raise ValueError(f"No function {name} to tune.")
    c = costs[name]
    kinds = {r.kind for r in c.regions} & {"host", "device"}
    alternatives = list(getattr(checker, "alternatives", {}).get(name, []))  # its implementations (E-IMPL-*)
    if not kinds and not alternatives:
        raise ValueError(f"{name} has no parallel region and no implementation, so no plan applies to it (E-PLAN).")
    kinds |= {placed(r) for r in c.regions} & {"host", "device"}
    kinds |= {placed(r) for g in alternatives for r in count(p, checker, {g})[g].regions} & {"host", "device"}
    if not sizes:
        raise ValueError("Give the sizes to tune for with --at, such as --at n=1e7.")
    placement = Placement(source, name)
    target = (device_target or resolve(required=False)) if "device" in kinds else None
    on = targeted({name: c}, chosen, target)  # the card that prices device plans runs the target's code
    current: Key = (written(receipts[name].get("plan", {})), receipts[name].get("runs"))
    stages = radii(p, name) if "device" in kinds else ()
    own = {r.kind for r in c.regions} & {"host", "device"}  # plan items schedule the reference's own regions
    nothing: list[Plan] = [()]
    plans = space(own, chosen.host.lanes if chosen.host else 16, regions(p, name), stages) if own else nothing
    uses = (None, *alternatives) if alternatives else ()
    candidates = checked(placement, name, plans, spent, uses)
    for cand in candidates:
        if cand.cost is not None:
            cand.predicted_ns = priced(cand.cost, chosen, sizes, arch)
    recorder = None
    if history is not None:
        recorder = Recorder(
            history,
            source,
            name,
            contract(source, name),
            host_target(arch, cxx),
            target,
            kept.selectable(source, receipts[name].get("implementations"), vendored),
        )
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
    named = identified(source, name)
    ids = [r["id"] for r in named]
    validated = validations(source, name, receipts, alternatives, recorder)
    rows = [row(name, x, ids, validated) for x in legal]
    table = receipts[name].get("implementations", {})
    for r, x in zip(rows, legal, strict=True):  # an instance of a parameterized implementation: its values
        if x.use and "parameters" in table.get(x.use, {}):
            r["parameters"] = table[x.use]["parameters"]
    usable = [r for r in rows if not isinstance(r.get("validated"), str)]  # the reference, or a validated one
    read = [r for r in usable if r.get("resources", {}).get("registers") is not None]
    result: dict[str, Any] = {
        "schema": "cairn.tune/2",
        "function": name,
        "sizes": sizes,
        "predicted": "Every plan the checker accepted is priced by cairn predict; a plan changes no result, so none "
        "needed checking. Registers and shared memory come from compiling the candidate, never from a run.",
        "profile": chosen.describe(),
        "target": {"host": {"arch": arch or "baseline"}},
        **on,
        "regions": named,
        "current": label(name, current),
        "space": {
            "configurations": len(plans) * max(len(uses), 1),
            "checked": len(candidates),
            "legal": len(legal),
            "refused": refusals(candidates),
        },
        "candidates": rows,
        "chosen": read[0] if read else usable[0] if usable else rows[0],
        **({"implementations": [local(g) for g in alternatives]} if alternatives else {}),
    }
    predicted = result["chosen"]
    best = legal[rows.index(predicted)]
    if measure and "device" in kinds and not device:
        result["measured"] = "Not measured: device plans are timed only by `make tune-device`, which the owner runs."
    elif measure:
        eligible = [
            (x.plan, x.use) for x, r in zip(legal, rows, strict=True) if not isinstance(r.get("validated"), str)
        ]
        result.update(
            timed(
                source,
                name,
                eligible,
                current,
                sizes,
                measure,
                cxx,
                arch,
                device,
                spent,
                recorder,
                target,
                bool(alternatives),
            )
        )
    if recorder is not None:
        record_search(recorder, candidates, {**result, "chosen": predicted}, (best.plan, best.use))
    result["budget"] = spent.report()
    return result


def record_search(recorder: Recorder, candidates: list[Candidate], result: dict, chosen: Key) -> None:
    """The search itself, the checker's refusals and each compile's reading, into the history."""
    summary = {k: result["space"][k] for k in ("configurations", "checked", "legal")}
    ranked = [[row["plan"], row["predicted_ns"]] for row in result["candidates"][:8]]  # what to time next
    recorder.put("attempt", chosen, recorder.host, {"by": "cairn tune", "sizes": result["sizes"], **summary,
                                                    "chosen": result["chosen"]["plan"], "ranked": ranked,
                                                    **({"measured_best": result["measured_best"]}
                                                       if result.get("measured_best") else {})})  # fmt: skip
    for group in refusals(candidates):
        first = next(x for x in candidates if x.refused == (group["code"], group["message"]))
        recorder.put("failure", (first.plan, first.use), recorder.host, {"stage": "check", "why": f"{group['code']}: "
                     f"{group['message']}", "configurations": group["configurations"]})  # fmt: skip
    for x in candidates:
        r = x.resources
        if r is None or r.get("kept"):
            continue
        if r["status"] == "read":
            read = ("registers", "spill_bytes", "stack_bytes", "shared_bytes", "dynamic_shared_bytes", "instructions",
                    "memory", "kernels", "sass_sha256")  # fmt: skip
            detail: dict[str, Any] = {
                "by": "ptxas and cuobjdump",
                "analysis": r["key"],
                **{k: r[k] for k in read if k in r},
            }
            recorder.put("observation", (x.plan, x.use), recorder.device, detail, r.get("cubin_sha256"))
        elif r["status"] in {"compile-failed", "target-refused"}:
            recorder.put("failure", (x.plan, x.use), recorder.device, {"stage": "build", "why": r.get("why", "nvcc failed"),
                                                              "analysis": r["key"]})  # fmt: skip


def timed(source: str, name: str, ranked: list[Any], current: Any, sizes: list[dict[str, float]], keep: int,
          cxx: str, arch: str | None, device: bool, spent: Spent | None = None, recorder: Recorder | None = None,
          target: DeviceTarget | None = None, selecting: bool = False) -> dict[str, Any]:  # fmt: skip
    """Successive halving over the best-ranked `keep` distinct candidates and the current one, within the run budget;
    a measurement the history already holds for the same identity and procedure is used rather than run again. A
    candidate is a plan, or a `Key` of a plan and the implementation it selects when `selecting`."""
    from ..projects.toolchain import resolve_arch
    from . import measure, on_device

    spent = spent or Spent(Budget())
    allowed = spent.budget.runs
    placement = Placement(source, name)
    field: list[Key] = list(dict.fromkeys(keyed(x) for x in [*distinct(ranked, keep), current]))
    where = (recorder.device if device else recorder.host) if recorder else ""
    blocks, rounds = 3, []
    times: dict[Key, float] = {}
    while True:
        done: list[Key] = []
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
                    use = (local(plan[1]) if plan[1] else None) if selecting else KEEP
                    variant = placement.apply(plan[0], use)
                    if device:
                        got = on_device.time_device(variant, name, s, blocks=blocks, target=target)
                    else:
                        got = measure.time(variant, name, s, cxx=cxx, arch=resolve_arch(arch), blocks=blocks)
                    if got["status"] != "measured":
                        raise ValueError(f"{label(name, plan)} did not run: {got}")
                    if recorder:
                        numbers = {k: got[k] for k in ("median_ns", "min_ns", "max_ns", "inner") if k in got}
                        recorder.put("measurement", plan, where, {"procedure": procedure, "sizes": s, **numbers})
                total += float(got["median_ns"])
            else:
                times[plan] = total
                done.append(plan)
        rounds.append({"blocks": blocks, "measured_ns": {label(name, p): round(times[p], 1) for p in done}})
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
    order = [keyed(p) for p in ranked if keyed(p) in times]  # predicted order, fastest first
    pairs = [(a, b) for i, a in enumerate(order) for b in order[i + 1 :]]
    agree = sum(times[a] <= times[b] for a, b in pairs)
    return {
        "measured": note,
        "rounds": rounds,
        "measured_best": label(name, best),
        "pairs_ordered_as_predicted": f"{agree} of {len(pairs)}",
        "chosen": {
            "plan": label(name, best),
            **dict(best[0]),
            **({"use": local(best[1])} if best[1] else {}),
            "measured_ns": round(times[best], 1),
        },
    }


def lines(result: dict[str, Any], shown_rows: int = 8) -> str:
    """The answer for a person: the space, the best-ranked few with what a compile read, what was measured, and what
    the budget spent and left undone."""
    from .report import duration, priced_on

    space_ = result["space"]
    out = [f"{result['function']}: {space_['configurations']} plans, {space_['legal']} accepted, "
           f"{space_['configurations'] - space_['legal']} refused or unchecked; now {result['current']}"]  # fmt: skip
    out += priced_on(result)
    for i, row in enumerate(result["candidates"][:shown_rows], 1):
        read = row.get("resources", {})
        seen = f"  {read['registers']} registers, {read['spill_bytes']} spilled" if "registers" in read else ""
        held = row.get("validated")
        held = "" if held is None else f"  {held['evidence']}" if isinstance(held, dict) else "  not validated"
        out.append(f"  {i:>2}  {row['plan']:<44} {duration(row['predicted_ns']):>10} predicted{seen}{held}")
    if len(result["candidates"]) > shown_rows:
        out.append(f"      and {len(result['candidates']) - shown_rows} more")
    out.append(f"chosen: {result['chosen']['plan']}")
    if isinstance(result.get("measured"), str) and not result.get("rounds"):
        out.append(result["measured"])
    for r in result.get("rounds", []):
        timed_ = ", ".join(f"{p} {duration(ns)}" for p, ns in r["measured_ns"].items())
        out.append(f"measured, median of {r['blocks']} blocks: {timed_}")
    spent = result["budget"]
    out.append(f"budget: {spent['compiles']['started']} of {spent['compiles']['allowed']} compiles, "
               f"{spent['compiles']['kept']} kept; {spent['seconds']['spent']} s of {spent['seconds']['allowed']}; "
               f"{spent['runs']['started']} runs, {spent['runs']['kept']} kept")  # fmt: skip
    out += [f"undone: {why} ({n})" for why, n in spent["undone"].items()]
    return "\n".join(out)


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What a search answered differently from an earlier answer: each candidate row that changed or is new, the
    plans no longer among the candidates, and every other part that differs. A row that differs only in whether its
    reading was kept from an earlier compile is the same row."""
    if not str(before.get("schema", "")).startswith("cairn.tune/") or before.get("function") != after["function"]:
        raise ValueError(f"A delta runs between two answers of cairn tune for {after['function']}.")

    def same(row: dict[str, Any]) -> dict[str, Any]:
        read = {k: v for k, v in row.get("resources", {}).items() if k != "kept"}
        return {**row, **({"resources": read} if read else {})}

    earlier = {row["plan"]: same(row) for row in before.get("candidates", [])}
    changed = [row for row in after["candidates"] if earlier.get(row["plan"]) != same(row)]
    rest = {k: v for k, v in after.items() if k != "candidates" and before.get(k) != v}
    return {**rest, "schema": "cairn.tune-delta/2", "function": after["function"], "candidates": changed,
            "unchanged": len(after["candidates"]) - len(changed),
            "gone": sorted(set(earlier) - {row["plan"] for row in after["candidates"]})}  # fmt: skip


def keyed(candidate: Any) -> Key:
    """A plan, or a plan and the implementation it selects, as a `Key`."""
    is_key = (
        isinstance(candidate, tuple) and len(candidate) == 2 and (candidate[1] is None or isinstance(candidate[1], str))
    )
    return candidate if is_key else (candidate, None)


def distinct(ranked: list[Any], keep: int) -> list[Any]:
    """The best-ranked `keep` candidates the model tells apart: plans it prices alike would spend the budget on
    noise, so one of each predicted time is timed, for each implementation a candidate selects."""
    picked: list[Any] = []
    seen: set[str] = set()
    for plan in ranked:
        items, use = dict(keyed(plan)[0]), keyed(plan)[1]
        key = f"{items.get('lanes', 0)}/{items.get('block', 0)}/{items.get('per_lane', 0)}/{use}"
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

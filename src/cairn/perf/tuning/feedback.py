"""A difference report between two candidates of one function, each line labelled by the kind of evidence it is.

- compiler observation: what a compiler reported with nothing run. ptxas gives registers, spills, stack and shared
  memory, cuobjdump the instructions in the code, and the model its prediction, which the line calls the model's.
- runtime measurement: a timed run with its procedure, from the history's current measurement records.
- profiler observation: a reading from an explicit profiling run, kept apart from timing, from the history's current
  profile records. Nothing here profiles.
- hypothesis: an explanation the lines above allow and nothing has confirmed, worded as a possibility.
- suggested experiment: a run that would confirm or refute a hypothesis. Nothing here runs it.

A register count, a spill or an instruction count is never reported as the reason one candidate is slower: that is a
hypothesis until a measurement or a profile supports it. A measured difference the model did not predict is said
to be one. The default report is these lines; `artifacts=True` adds the path of every file behind them: the emitted
program, the cubin, ptxas's log, the SASS, and the ids of the records.
"""

from __future__ import annotations

from typing import Any

from ...compiler.cairnc import compile_program
from ...projects.target import DeviceTarget, resolve
from .. import cooperative_model, model
from ..profile import Device, Profile, card, default
from ..work import count
from .plan_source import Placement, Plan, contract, written
from .resources import Inspector, device_identity, host_target
from .tune import label, variant

COMPILER, MEASURED, PROFILER, HYPOTHESIS, EXPERIMENT = (
    "compiler observation",
    "runtime measurement",
    "profiler observation",
    "hypothesis",
    "suggested experiment",
)
READ = (("registers", "registers per thread"), ("spill_bytes", "spilled bytes per thread"),
        ("stack_bytes", "stack frame bytes"), ("shared_bytes", "static shared memory bytes per block"),
        ("dynamic_shared_bytes", "staged tile bytes per block, computed from the plan"),
        ("instructions", "SASS instructions in the code"))  # fmt: skip


def parse_plan(text: str) -> Plan:
    """`grain 1; lanes 8`, `plan f { grain 1; }`, or `none`: the items of a plan written as the language writes them."""
    body = text.strip()
    if body in {"", "none"}:
        return ()
    if body.startswith("plan"):
        body = body[body.index("{") + 1 : body.rindex("}")] if "{" in body and "}" in body else ""
    items: dict[str, int] = {}
    for part in (p.strip() for p in body.split(";")):
        name, _, value = part.partition(" ")
        if part and (not value.strip().isdigit() or name in items):
            raise ValueError(f"Write a plan as items such as `grain 1; lanes 8`, not {text!r}.")
        if part:
            items[name] = int(value)
    plan = written(items)
    if len(plan) != len(items):
        raise ValueError(f"{', '.join(sorted(set(items) - set(dict(plan))))} is not a plan item.")
    return plan


def parse_candidate(text: str) -> tuple[Plan, str | None]:
    """A plan as `parse_plan` reads it, and `use g`, `use g[8]` or `plan f use g;` for the implementation, or the
    instance of one, it selects."""
    import re

    found = re.search(r"(?:\bplan\s+[A-Za-z_][\w.]*\s+)?\buse\s+([A-Za-z_][\w.]*)\s*(\[[\d\s,]*\])?\s*;?", text)
    rest = (text[: found.start()] + text[found.end() :]).strip(" ;") if found else text
    if not found:
        return parse_plan(rest or "none"), None
    values = re.findall(r"\d+", found.group(2) or "")
    return parse_plan(rest or "none"), found.group(1) + (f"[{', '.join(values)}]" if found.group(2) else "")


def line(kind: str, by: str, text: str, **values: Any) -> dict[str, Any]:
    return {"kind": kind, "by": by, "text": text, **values}


def compare(source: str, name: str, a: Any, b: Any, sizes: list[dict[str, float]], profile: Profile | None = None,
            arch: str | None = None, history: Any = None, target: DeviceTarget | None = None, compiles: int = 2,
            artifacts: bool = False, cxx: str = "clang++", vendored: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The difference report of candidate `b` against candidate `a` of `name` at `sizes`. Device candidates are
    compiled for `target` (at most `compiles` compiles; a kept inspection is free), and `history` is read for the
    measurements and profiles that still hold for this host (`arch`, `cxx`) or the device target, `target` or the
    one resolved here. A candidate is a plan, or a plan and the implementation it selects (`parse_candidate`);
    without one, the reference runs."""
    from ...agent import history as kept
    from ..report import targeted
    from .tune import keyed

    chosen, target = profile or default(), target or resolve(required=False)
    placement = Placement(source, name)
    module = placement.f.module
    sides = {k: (plan, f"{module}.{use}" if use and module and "." not in use else use)
             for k, (plan, use) in (("a", keyed(a)), ("b", keyed(b)))}  # fmt: skip
    runs = {k: use or name for k, (_, use) in sides.items()}  # whose code runs where it applies
    programs = {k: placement.apply(plan, use.rsplit(".", 1)[-1] if use else None) for k, (plan, use) in sides.items()}
    checked = {k: compile_program(text) for k, text in programs.items()}  # a refused candidate raises its diagnostic
    costs = {k: count(p, checker, {runs[k]})[runs[k]] for k, (p, checker, _) in checked.items()}
    lines: list[dict[str, Any]] = []
    for s in sizes:
        pa, pb = (model.predict(costs[k], chosen, s, arch) for k in "ab")
        where = ", ".join(f"{k}={v:g}" for k, v in s.items())
        text = (f"at {where}: predicted {pa['ns']:.4g} ns -> {pb['ns']:.4g} ns; the model's bound {pa['bound']} -> "
                f"{pb['bound']}; confidence {pb['confidence']}")  # fmt: skip
        lines.append(line(COMPILER, "cairn predict (the model, not a run)", text, sizes=s, a=pa["ns"], b=pb["ns"]))
    read: dict[str, dict[str, Any]] = {}
    lines += [
        line(COMPILER, "the checker", f"{what}: {x} -> {y}", a=x, b=y)
        for what, x, y in cooperative_model.checked(costs)
    ]
    device = any(r.kind == "device" or (r.coop is not None and r.coop.device) for k in "ab" for r in costs[k].regions)
    on = targeted(costs, chosen, target) if device else {}  # the card that prices a and b runs the target's code
    if device:
        from ..device import available

        inspector = Inspector(target, kept.History(history) if history is not None else None) if target else None
        for k in "ab" if inspector else ():
            found = inspector.kept(programs[k], runs[k], checked[k][0])
            if found is None and compiles > 0 and available():
                compiles -= 1
                found = inspector.inspect(programs[k], runs[k], checked[k][0], checked[k][1])
            if found is not None:
                read[k] = found
        if set(read) == {"a", "b"} and all(r["status"] == "read" for r in read.values()):
            same = read["a"].get("sass_sha256") and read["a"].get("sass_sha256") == read["b"].get("sass_sha256")
            if same:
                lines.append(line(COMPILER, "cuobjdump", f"the SASS of a and b is the same, digest "
                                  f"{read['a']['sass_sha256'][:16]}", same_code=True))  # fmt: skip
            for key, what in READ:
                if read["a"].get(key) != read["b"].get(key):
                    lines.append(line(COMPILER, "ptxas and cuobjdump" if key != "dynamic_shared_bytes" else "the plan",
                                      f"{what}: {read['a'][key]} -> {read['b'][key]}", a=read["a"][key],
                                      b=read["b"][key]))  # fmt: skip
            for key in sorted(set(read["a"]["memory"]) | set(read["b"]["memory"])):
                x, y = read["a"]["memory"].get(key, 0), read["b"]["memory"].get(key, 0)
                if x != y:
                    what = f"{key.replace('_', ' ')} instructions in the code: {x} -> {y}"
                    lines.append(line(COMPILER, "cuobjdump", what, a=x, b=y))
        else:
            missing = " and ".join(sorted({"a", "b"} - {k for k, r in read.items() if r["status"] == "read"}))
            why = "nvcc and cuobjdump are needed, or the compile budget was spent" if target else "no device target"
            lines.append(line(COMPILER, "this report", f"no device resources for {missing}: {why}"))
    targets = {kept.digest(host_target(arch, cxx)), device_identity(target)}
    table = kept.selectable(
        source, checked["a"][2].get(name, {}).get("implementations"), vendored
    )  # with what each calls
    variants = {k: variant(key, table) for k, key in sides.items()}
    held = history_lines(source, name, variants, sizes, history, targets) if history is not None else {}
    lines += held.get("lines", [])
    threads = {k: next((r.coop.threads for r in costs[k].regions if r.coop is not None), 0) for k in "ab"}
    lines += reasoning(lines, read, device, sides, name, threads, chosen.device)
    if history is not None:  # what the report only supposes goes into the history as that, and nothing more
        pair = {"compare": [variants["a"], variants["b"]]}
        made = kept.identity(kept.as_written(source, name), pair, contract(source, name),
                             device_identity(target) if device else kept.digest(host_target(arch, cxx)))  # fmt: skip
        named = f"{label(name, sides['b'])} against {label(name, sides['a'])}"
        claims = [kept.record(history, "hypothesis", name, named, made, {"claim": x["text"], "by": x["by"]}, pair)
                  for x in lines if x["kind"] == HYPOTHESIS]  # fmt: skip
        for x in lines:
            if x["kind"] == EXPERIMENT:
                tests = [c["id"] for c in claims] or ["which of a and b is faster"]
                kept.record(history, "experiment", name, named, made, {"run": x["text"], "tests": tests}, pair)
    report: dict[str, Any] = {
        "schema": "cairn.compare/1",
        "function": name,
        "a": label(name, sides["a"]),
        "b": label(name, sides["b"]),
        **{k: v for k, v in on.items() if k in {"device_card", "device_target"}},
        "lines": lines,
        "kinds": [COMPILER, MEASURED, PROFILER, HYPOTHESIS, EXPERIMENT],
    }
    if artifacts:
        report["artifacts"] = {k: r.get("artifacts", {}) for k, r in read.items()} | {"records": held.get("ids", [])}
    return report


def history_lines(source: str, name: str, sides: dict[str, Any], sizes: list[dict[str, float]], where: Any,
                  targets: set[str]) -> dict[str, Any]:  # fmt: skip
    """The measurements and profiles the history holds for either candidate that still hold now, for one of
    `targets`, as report lines; a record for another target or an older program is counted as no longer holding."""
    from ...agent import history as kept

    records = kept.History(where)
    split = records.judged(name, kept.as_written(source, name), {kept.digest(contract(source, name))}, targets)
    out, ids = [], []
    for k, made in sides.items():  # each side's variant, as a search records it
        for r in split["current"]:
            if r["variant"] != made or r["kind"] not in {"measurement", "profile"}:
                continue
            if r["kind"] == "measurement" and sizes and r["detail"].get("sizes") not in sizes:
                continue
            d = r["detail"]
            if r["kind"] == "measurement":
                at = ", ".join(f"{x}={v:g}" for x, v in d.get("sizes", {}).items())
                out.append(line(MEASURED, d["procedure"], f"{k} at {at}: median {d['median_ns']:.4g} ns "
                                f"(min {d.get('min_ns', 0):.4g}, max {d.get('max_ns', 0):.4g})", side=k,
                                sizes=d.get("sizes"), median_ns=d["median_ns"], record=r["id"]))  # fmt: skip
            else:
                out.append(line(PROFILER, f"{d['tool']}, {d['run']}", f"{k}: {d.get('reading', d)}", side=k,
                                record=r["id"]))  # fmt: skip
            ids.append(r["id"])
    stale = [r["id"] for r in split["stale"] if r["variant"] in sides.values()]
    if stale:
        out.append(line(COMPILER, "the history", f"{len(stale)} earlier records of these candidates no longer hold "
                        "(the function, its contract, the target or the compiler differ) and are left out",
                        stale=stale))  # fmt: skip
    return {"lines": out, "ids": ids}


def reasoning(lines: list[dict[str, Any]], read: dict[str, dict[str, Any]], device: bool, sides: dict[str, Any],
              name: str, threads: dict[str, int] | None = None, priced: Device | None = None) -> list[dict[str, Any]]:  # fmt: skip
    """Hypotheses the observations allow, each with the experiment that would test it, the occupancy ones on the card
    `priced`, the packaged default without one. None is stated as a cause."""

    out: list[dict[str, Any]] = []
    run = ("make tune-device FILE=... SYMBOL=" + name + " AT=...  (the owner's target; nothing here runs the device)"
           if device else f"cairn tune --symbol {name} --measure 2 --at ... with both plans on this host")  # fmt: skip
    got = {k: r for k, r in read.items() if r.get("status") == "read"}
    if any(x.get("same_code") for x in lines):
        out.append(line(HYPOTHESIS, "derived from the SASS digests", "a and b run the same device code, so a "
                        "difference measured between them may come from how the kernel is launched (the block and "
                        "the indices per thread) or from noise"))  # fmt: skip
    if len(got) == 2:
        a, b = got["a"], got["b"]
        spec = priced or card().device
        if spec is not None and a["registers"] != b["registers"]:
            block = {
                k: (threads or {}).get(k) or dict(sides[k][0]).get("block", 256) for k in "ab"
            }  # a block's threads
            resident = {k: spec.occupancy(got[k]["registers"], block[k], got[k]["shared_bytes"] +
                                          got[k]["dynamic_shared_bytes"]) for k in "ab"}  # fmt: skip
            if resident["a"] != resident["b"]:
                out.append(line(HYPOTHESIS, f"derived from the registers and the published limits of the {spec.name}",
                                f"at most {resident['a']:.0%} -> {resident['b']:.0%} of an SM's threads can be resident; "
                                "if the candidate with fewer is slower, fewer warps hiding memory latency may be why"))  # fmt: skip
        if b["spill_bytes"] != a["spill_bytes"]:
            more = "b" if b["spill_bytes"] > a["spill_bytes"] else "a"
            out.append(line(HYPOTHESIS, "derived from ptxas's spill report", f"{more} spills registers to local memory; "
                            "if it is slower, that traffic may be part of why"))  # fmt: skip
        tiled = [
            k for k in "ab" if got[k]["memory"].get("shared_load") and not got[other(k)]["memory"].get("shared_load")
        ]
        for k in tiled:  # counts are of instructions in the code, not of loads executed
            loads = {x: got[x]["memory"].get("global_load", 0) for x in "ab"}
            out.append(line(HYPOTHESIS, "derived from the SASS counts", f"{k} reads through shared memory (global load "
                            f"instructions in the code: {loads[k]} in {k}, {loads[other(k)]} in {other(k)}); {k} may "
                            "move fewer bytes from device memory, unless the caches already served the neighbours' "
                            "repeated reads"))  # fmt: skip
    measured = [x for x in lines if x["kind"] == MEASURED]
    predicted = [x for x in lines if x["kind"] == COMPILER and "a" in x and "b" in x and "sizes" in x]
    for p in predicted:
        times = {x["side"]: x["median_ns"] for x in measured if x.get("sizes") == p["sizes"]}
        if len(times) == 2 and p["a"] and times["a"]:
            model_ratio, run_ratio = p["b"] / p["a"], times["b"] / times["a"]
            if (model_ratio - 1) * (run_ratio - 1) < 0:
                out.append(line(HYPOTHESIS, "the model against the measurement", f"the model predicted x{model_ratio:.3g}"
                                f" and the runs measured x{run_ratio:.3g}: the model leaves out what separates them"))  # fmt: skip
    if out or {x["side"] for x in measured} != {"a", "b"}:  # a hypothesis to test, or a side nothing timed
        out.append(line(EXPERIMENT, "suggested, not run", f"time a and b at the same sizes, interleaved: {run}"))
    if device and any(x["kind"] == HYPOTHESIS for x in out):
        out.append(line(EXPERIMENT, "suggested, not run", "profile a and b in an explicit profiling run (Nsight "
                        "Compute's occupancy and memory sections), apart from timing, which only the owner runs"))  # fmt: skip
    return out


def other(side: str) -> str:
    return "b" if side == "a" else "a"


def lines_for_people(report: dict[str, Any]) -> str:
    """The report for a person: one line each, its label first."""
    from ..report import priced_on

    out = [f"{report['function']}: {report['a']}  ->  {report['b']}", *(f"  {x}" for x in priced_on(report))]
    out += [f"  [{x['kind']}] {x['by']}: {x['text']}" for x in report["lines"]]
    return "\n".join(out)

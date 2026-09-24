"""One function's investigation as a compact packet: enough for a fresh agent to go on without the conversation that
began it and without running again what was already run.

The packet names the function (signature, effect row, current plan, its regions by the names `perf/regions.py`
gives them) and the identity that holds now (the function as written, its contract, the compiler, the targets).
From the candidate history it takes only records that hold now: for each candidate, what was measured and how, what
a compile read, what failed and why, what was validated or profiled; the searches that ran and what they ranked best;
and the hypotheses and suggested experiments, an experiment marked done when the runs it asks for are kept. A
validation holds while the implementation, its reference and the compiler are as they were, under the tolerance and
on the host it names, and only under this numerical policy and the native compiler the packet's host builds with, as
`cairn tune` cites it.
Records that no longer hold are counted by the part of their identity that moved and never shown as facts.

`delta` gives only what changed since an earlier packet, and `apply` rebuilds the newer packet from it, as
`agent/state.py` does for the whole program.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..verify import agreement
from . import history as kept
from .state import sealed

PROTOCOL = "cairn.investigation/1"
DELTA = "cairn.investigation-delta/1"
SHOWN = 24  # candidates shown, the most recently recorded first; the rest are counted
TERMS = (
    "Every entry holds now for this function, contract, compiler and target. A measured entry names its procedure, "
    "a compiled one what ptxas and cuobjdump read, and a hypothesis is unconfirmed. Stale records are counted, never "
    "shown. Do not run again what is measured or compiled here."
)


def implemented(r: dict[str, Any]) -> str | None:
    """The identity (history.selectable) of the implementation a record is about, or None for the reference's own."""
    variant = r.get("variant")
    if isinstance(variant, dict):
        return variant.get("implementation") if variant.get("use") else None
    return variant if r["kind"] in {"validation", "failure"} and isinstance(variant, str) else None


def brief(r: dict[str, Any], procedures: dict[str, str] | None = None) -> dict[str, Any]:
    """A record as the packet shows it: its id, when, and its detail without what the packet already says. A
    procedure is named once under `procedures` and by its key after that."""
    detail = {k: v for k, v in r["detail"].items() if k not in {"analysis", "inner"}}
    if procedures is not None and "procedure" in detail:
        text = detail["procedure"]
        detail["procedure"] = next((k for k, v in procedures.items() if v == text), f"p{len(procedures)}")
        procedures[detail["procedure"]] = text
    return {"id": r["id"], "at": r.get("at", ""), **detail}


def investigation(source: str, symbol: str, where: str | Path, targets: dict[str, Any],
                  vendored: dict[str, str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The packet for `symbol` of `source` from the history at `where`, current for `targets`, a mapping from a
    name (host, device) to what `agent/history.py` keeps as a target: a description or its digest. `vendored`
    (history.vendored) pins the project's foreign sources."""
    from ..compiler import compilations
    from ..perf.plan_source import Placement, contract, shown, written
    from ..perf.regions import identified
    from ..perf.tune import label
    from .projection import signature

    checked = compilations.program(source)
    placement, receipts = Placement(source, symbol, checked[:2]), checked[2]
    base, promised = kept.as_written(source, symbol), contract(source, symbol, checked)
    held = {name: t if isinstance(t, str) else kept.digest(t) for name, t in targets.items()}
    split = kept.History(where).judged(symbol, base, {kept.digest(promised)}, set(held.values()))
    for r in [r for r in split["stale"] if r["kind"] == "validation" and set(r["stale"]) <= {"contract", "target"}]:
        split["stale"].remove(r)  # a validation holds under its own policy and host, as `cairn tune` cites it
        split["current"].append({k: v for k, v in r.items() if k != "stale"})
    host = targets.get("host")
    cxx = host.get("cxx") if isinstance(host, dict) else None
    for r in [r for r in split["current"] if r["kind"] == "validation"]:
        if parts := kept.unheld(r, agreement.DIGEST, cxx):  # made under another numerical policy or compiler
            split["current"].remove(r)
            split["stale"].append({**r, "stale": parts})
    implementations = receipts.get(placement.f.name, {}).get("implementations")
    now_implemented = {row["identity"] for row in kept.selectable(source, implementations, vendored).values()}
    for r in [r for r in split["current"] if implemented(r) not in (None, *now_implemented)]:
        split["current"].remove(r)  # it is about an implementation, or code it calls, that has changed since
        split["stale"].append({**r, "stale": ["source"]})
    candidates: dict[str, dict[str, Any]] = {}
    searches, hypotheses, experiments = [], [], []
    procedures: dict[str, str] = {}
    for r in split["current"]:
        if r["kind"] == "attempt" and r["detail"].get("by") == "cairn tune":
            searches.append(brief(r))
        elif r["kind"] == "hypothesis":
            hypotheses.append({"candidate": r["candidate"], **brief(r)})
        elif r["kind"] == "experiment":
            experiments.append({"candidate": r["candidate"], "variant": r["variant"], **brief(r)})
        else:
            entry = candidates.setdefault(r["candidate"], {})
            what = {"measurement": "measured", "observation": "compiled", "failure": "failed",
                    "validation": "validated", "profile": "profiled", "attempt": "tried"}[r["kind"]]  # fmt: skip
            entry.setdefault(what, []).append(brief(r, procedures))
    for x in experiments:  # done once both candidates it compares have a kept run of the kind it asks for
        pair = x.pop("variant", {}).get("compare")
        asks = "profiled" if "profil" in x["run"] else "measured"
        labels = [label(symbol, (written(v.get("plan", {})), v.get("use"))) for v in pair] if pair else []
        x["done"] = bool(labels) and all(candidates.get(label, {}).get(asks) for label in labels)
    recent = sorted(candidates, key=lambda c: max(e["at"] for es in candidates[c].values() for e in es), reverse=True)
    moved: dict[str, int] = {}
    for r in split["stale"]:
        for part in r["stale"]:
            moved[part] = moved.get(part, 0) + 1
    now = {"as_written": base[:16], "contract": kept.digest(promised)[:16], "compiler": kept.compiler()[:16],
           "targets": {name: t[:16] for name, t in sorted(held.items())}}  # fmt: skip
    return sealed(
        {
            "protocol": PROTOCOL,
            "function": symbol,
            "signature": signature(placement.f),
            "effects": receipts[placement.f.name]["effects"],
            "plan": shown(placement.name, written(receipts[placement.f.name].get("plan", {}))),
            "regions": identified(source, symbol),
            "identity": now,
            "searches": searches[-4:],
            "candidates": {c: candidates[c] for c in recent[:SHOWN]},
            "procedures": procedures,
            "not_shown": max(len(recent) - SHOWN, 0),
            "hypotheses": hypotheses,
            "experiments": experiments,
            "stale": {"records": len(split["stale"]), "by_part": dict(sorted(moved.items()))},
            "terms": TERMS,
        }
    )


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What changed from `before` to `after`: each changed candidate (None where one is gone) and every other part
    that differs. Applying it to `before` gives `after`."""
    if before.get("protocol") != PROTOCOL or after.get("protocol") != PROTOCOL:
        raise ValueError(f"A delta runs between two {PROTOCOL} packets.")
    moved = {c: after["candidates"].get(c) for c in sorted(set(before["candidates"]) | set(after["candidates"]))
             if before["candidates"].get(c) != after["candidates"].get(c)}  # fmt: skip
    rest = {k: v for k, v in after.items() if k not in {"candidates", "digest"} and before.get(k) != v}
    return {"protocol": DELTA, "since": before["digest"], "digest": after["digest"], "candidates": moved, **rest}


def apply(before: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """`before` with `change` applied, sealed again: the digest must come out as the one the delta names."""
    if change.get("protocol") != DELTA or change.get("since") != before.get("digest"):
        raise ValueError("This delta was taken from another packet.")
    body = {k: v for k, v in before.items() if k != "digest"}
    body.update({k: v for k, v in change.items() if k not in {"protocol", "since", "digest", "candidates"}})
    candidates = dict(before["candidates"])
    for c, entry in change["candidates"].items():
        if entry is None:
            candidates.pop(c, None)
        else:
            candidates[c] = entry
    body["candidates"] = candidates
    after = sealed(body)
    if after["digest"] != change["digest"]:
        raise ValueError("The delta does not reproduce the packet it names.")
    return after

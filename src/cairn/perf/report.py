"""What `cairn predict` answers: each function's work in its own sizes, its cost as a formula, a prediction at each
size asked for, and, given a second version, the difference the change makes. Nothing is built or run.
"""

from __future__ import annotations

from typing import Any

from ..compiler.cairnc import compile_program
from ..projects.target import DeviceTarget
from . import cooperative_model, model
from .counts import Cost, Work
from .profile import Device, Profile, default, packaged
from .work import count

LADDER = (1e3, 1e5, 1e7)  # the sizes a one-extent function is priced at when none are given


def work(w: Work) -> dict[str, Any]:
    return {
        "ops": {k: n.render() for k, n in sorted(w.ops.items())},
        "bytes_read": {k: n.render() for k, n in sorted(w.reads.items())},
        "bytes_written": {k: n.render() for k, n in sorted(w.writes.items())},
        **({"irregular_accesses": {k: n.render() for k, n in sorted(w.irregular.items())}} if w.irregular else {}),
    }


def described(c: Cost, card: Device | None = None, site: Any = None) -> dict[str, Any]:
    return {
        "extents": c.extents,
        "sequential": work(c.seq),
        "regions": [
            {
                "kind": r.kind,
                "line": r.line,
                "count": r.count.render(),
                "runs": r.runs.render(),
                "per_index": work(r.body),
                **({"plan": list(r.plan)} if any(r.plan) else {}),
                **({"cooperative": cooperative_model.described(r, card, site)} if r.coop is not None else {}),
            }
            for r in c.regions
        ],
        **({"tasks": [{"spawned": n.render(), "runs": t.name} for n, t in c.tasks]} if c.tasks else {}),
        **({"transfers": {d: b.render() for d, b in c.transfers.items()}} if c.transfers else {}),
        **({"allocated_bytes": c.allocated.render()} if c.allocated.terms else {}),
    }


def ladder(c: Cost, sizes: list[dict[str, float]]) -> list[dict[str, float]]:
    if sizes:
        return sizes
    if len(c.extents) == 1:
        return [{c.extents[0]: v} for v in LADDER]
    return [{}] if not c.extents else []


def costs(source: str, symbols: set[str] | None) -> dict[str, Cost]:
    p, checker, _ = compile_program(source)
    found = count(p, checker, symbols)
    if symbols and symbols - set(found):
        raise ValueError(f"No function {sorted(symbols - set(found))[0]} to predict.")
    return found


def targeted(found: dict[str, Cost], profile: Profile, device: DeviceTarget | None) -> dict[str, Any]:
    """The device target a prediction with device work is for, held to the device card that prices that work: the
    profile's own, or the packaged one the model falls back to. Without a target it names the card alone."""
    device_work = any(r.kind in {"device", "tensor"} or (r.coop is not None and r.coop.device)
                      for c in found.values() for r in c.regions)  # fmt: skip
    if not device_work and not any(c.transfers for c in found.values()):
        return {}
    card = (profile if profile.device else packaged("rtx-5070-ti")).source["device"]
    if device is None:
        return {"device_target": None, "device_card": {"name": card["name"], "compute_capability":
                                                       card.get("compute_capability")}}  # fmt: skip
    device.fits(card, f"The device card {card['name']!r}")
    return {"device_target": device.record()}


def inspected(source: str, found: dict[str, Cost], device: DeviceTarget | None) -> dict[str, Any]:
    """Compile `source`'s device code for `device` and give each cooperative region what ptxas read of its kernel."""
    from .device import available, kernels

    if device is None:
        return {"status": "not-run", "reason": "--inspect compiles for a device target; name one with --device-target."}
    if not available():
        return {"status": "not-run", "reason": "nvcc and cuobjdump are needed to read a kernel; neither was found."}
    read = kernels(source, device)
    if read["status"] != "read":
        return {"status": read["status"], "reason": read.get("stderr", read.get("reason", ""))[-2000:]}
    return {"status": "read", "by": "ptxas and cuobjdump, a compiler observation: nothing ran",
            "device_target": read["arch"], "regions": cooperative_model.read(found, read["kernels"])}  # fmt: skip


def report(source: str, sizes: list[dict[str, float]] | None = None, symbols: set[str] | None = None,
           profile: Profile | None = None, arch: str | None = None,
           device: DeviceTarget | None = None, inspect: bool = False, site: Any = None) -> dict[str, Any]:  # fmt: skip
    """Every function of `source`, or `symbols` alone, priced at each of `sizes`, device work for `device`. With
    `inspect`, the device code is compiled for `device` first and each cooperative region's kernel read by ptxas, so
    its registers count toward how many blocks an SM holds; nothing runs. `site` names a program line's file."""
    chosen = profile or default()
    out: dict[str, Any] = {}
    found = costs(source, symbols)
    target = targeted(found, chosen, device)
    if inspect:
        target["inspection"] = inspected(source, found, device)
    for name, c in found.items():
        entry = {
            "line": c.line,
            **described(c, chosen.device or packaged("rtx-5070-ti").device, site),
            "formula": model.formula(c, chosen, arch),
        }
        entry["predictions"] = [{"sizes": s, **model.predict(c, chosen, s, arch)} for s in ladder(c, sizes or [])]
        if c.unknown:
            entry["unknown"] = c.unknown
        out[name] = entry
    return {
        "schema": "cairn.predict/1",
        "predicted": "Priced from the checked program and a machine profile; nothing was built or run.",
        "profile": chosen.describe(),
        "arch": arch or model.measured(chosen),
        **target,
        "functions": out,
    }


def delta(before: str, after: str, sizes: list[dict[str, float]] | None = None, symbols: set[str] | None = None,
          profile: Profile | None = None, arch: str | None = None,
          device: DeviceTarget | None = None, inspect: bool = False) -> dict[str, Any]:  # fmt: skip
    """What changing `before` into `after` is predicted to do to every function both have, at each size, with what
    changes in each cooperative region's resources beside the time."""
    chosen = profile or default()
    old, new = costs(before, symbols), costs(after, symbols)
    target = targeted({**{f"old:{k}": v for k, v in old.items()}, **new}, chosen, device)
    if inspect:
        target["inspection"] = {"before": inspected(before, old, device), "after": inspected(after, new, device)}
    out: dict[str, Any] = {}
    for name in sorted(set(old) & set(new)):
        rows = []
        for s in ladder(new[name], sizes or []):
            a, b = model.predict(old[name], chosen, s, arch), model.predict(new[name], chosen, s, arch)
            rows.append({"sizes": s, "before_ns": a["ns"], "after_ns": b["ns"], "change_ns": round(b["ns"] - a["ns"], 1),
                         "ratio": round(b["ns"] / a["ns"], 3) if a["ns"] else None,
                         "bound": [a["bound"], b["bound"]], "confidence": min(a["confidence"], b["confidence"],
                                                                                 key=["low", "medium", "high"].index)})  # fmt: skip
            if changed := cooperative_model.changed(a["parts"], b["parts"]):
                rows[-1]["cooperative"] = changed
        out[name] = rows
    return {"schema": "cairn.predict.delta/1", "predicted": "Neither version was built or run.",
            "profile": chosen.describe(), "arch": arch or model.measured(chosen), **target, "functions": out,
            "only_before": sorted(set(old) - set(new)), "only_after": sorted(set(new) - set(old))}  # fmt: skip


def duration(ns: float) -> str:
    for unit, scale in (("s", 1e9), ("ms", 1e6), ("us", 1e3)):
        if ns >= scale:
            return f"{ns / scale:.3g} {unit}"
    return f"{ns:.3g} ns"


def lines(result: dict[str, Any]) -> str:
    """The same answer for a person: one line per function, one per size, the parts only when there are several."""
    out = [f"predicted, not measured: {result['profile']['name']} ({result['profile']['origin']}), {result['arch']}"]
    for name, entry in result["functions"].items():
        if "predictions" not in entry:  # a delta: before, after and the ratio at each size
            out.append(f"{name}")
            for row in entry:
                sizes = ", ".join(f"{k}={v:g}" for k, v in row["sizes"].items())
                ratio = f"x{row['ratio']}" if row["ratio"] is not None else ""
                out.append(f"  {sizes:<12} {duration(row['before_ns']):>10} -> {duration(row['after_ns']):<10} {ratio}"
                           f"  {row['bound'][1]}, {row['confidence']}")  # fmt: skip
                out += cooperative_model.said_changed(row.get("cooperative", []))
            continue
        out.append(f"{name}  {entry['formula']}")
        for r in entry["regions"]:
            out += cooperative_model.said(r) if "cooperative" in r else []
        for p in entry["predictions"]:
            sizes = ", ".join(f"{k}={v:g}" for k, v in p["sizes"].items()) or "-"
            light = f"{p['speed_of_light']:.0%} of speed of light" if p["speed_of_light"] else ""
            out.append(f"  {sizes:<12} {duration(p['ns']):>10}  {p['bound']:<20} {light:<24} {p['confidence']}")
            out += [cooperative_model.said_at(part) for part in p["parts"] if "cooperative region" in part["what"]]
            out += [f"    because {why}" for why in p["why"] if p["confidence"] == "low"]
    return "\n".join(out)


def parse_sizes(items: list[str]) -> list[dict[str, float]]:
    """`--at n=1e6,m=64` entries, one set of sizes each."""
    out = []
    for item in items:
        given: dict[str, float] = {}
        for part in item.split(","):
            name, sep, text = part.partition("=")
            if not sep or not name.strip():
                raise ValueError(f"Write --at NAME=SIZE[,NAME=SIZE], not {item!r}.")
            given[name.strip()] = float(text)
        out.append(given)
    return out

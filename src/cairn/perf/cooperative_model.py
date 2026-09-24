"""A cooperative region's predicted time and the resources its blocks hold, priced from what `cooperative_work.py`
counted and a device profile. The profile's figures are NVIDIA's published specification and a few assumptions it
names; none is a measurement until the owner's `make calibrate-device` replaces them.

Resources. A block holds its threads, the shared memory the checker laid out (every array and stage from a 128-byte
boundary: what ptxas reports as the kernel's static shared memory) and its registers, which only the compiler knows:
they count once something compiled the kernel, `cairn predict --inspect` or `cairn tune`. An SM holds as many blocks as
the tightest of its limits allows (`profile.Device.resident`): its threads, its registers and its shared memory, with
the 1 KB the system keeps beside each block.

Time. A launch, then the largest of four rates over the whole grid, since a running kernel overlaps them:

- device memory: the sectors the threads' accesses to outside arrays move, at the sustained bandwidth shared out over
  the part of the device its resident threads keep busy, as a `parallel` region is priced; and a pipeline's copies at
  the bytes its stages keep in flight over the memory's latency (Little's law) when that is less than the bandwidth.
  A wait that leaves N copies in flight (`cp.async.wait_group N`, counted by the checker) keeps N + 1 stages in
  flight in each resident block, so a deeper pipeline copies faster until the bandwidth caps it, and holds more
  shared memory, which can leave fewer blocks on each SM;
- issue: every thread's operations at the device's issue rate, over the same busy share;
- shared memory: every wavefront at one a clock on each SM the grid reaches;
- tensor cores: every fragment step's multiply-adds at the published dense peak for f16 and bf16 inputs.

Which rate is largest is the model's bound, a hypothesis about the kernel, never a measured bottleneck.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .cooperative_work import PHASE_BYTES, SECTOR
from .counts import Cost, Region, Work
from .model import Piece, footprint, price, value
from .profile import Device, Host

# cr::coop::blocks<THREADS, BYTES, the lambda>: its closure type numbered among the function's device regions
KERNEL = re.compile(r"6blocksILj(\d+)ELm(\d+)E.*?UlRNS\d+_6DeviceEmmE(\d*)_")
NOT_ISSUED = {"tensor", "shared_wavefront", "fill_bytes"}  # counted in operations, wavefronts or bytes, not issues


def resident(r: Region, card: Device) -> dict[str, Any]:
    """How many of this region's blocks one SM holds, by each limit, and which limit binds."""
    shape = r.coop
    shared = r.shared or shape.shared_bytes
    by = card.resident(r.registers, shape.threads, shared)
    blocks = min(by.values())
    return {
        "blocks_per_sm": blocks,
        "by_limit": by,
        "limited_by": [k for k, n in by.items() if n == blocks],
        "occupancy": round(blocks * shape.threads / card.threads_per_sm, 3),
        "shared_bytes_per_block": shared,
        "reserved_bytes_per_block": card.shared_reserved,
        "registers_per_thread": r.registers or None,
    }


def described(r: Region, card: Device | None = None, site: Any = None) -> dict[str, Any]:
    """What a region holds and does, with nothing that depends on the sizes: for `cairn predict`'s description.
    `site` maps a line of the program to its file and line, as a project's does; without one a place is its line."""

    def at(line: int) -> str:
        return "{}:{}".format(*site(line)) if site is not None else f"line {line}"

    shape = r.coop
    ops = r.body.ops
    out: dict[str, Any] = {
        "at": at(r.line),
        "placement": "device" if shape.device else "host",
        "threads_per_block": shape.threads,
        "thread_extents": list(shape.extents),
        "shared_bytes_per_block": shape.shared_bytes,
        "shared": [{"name": n, "at": at(line), "bytes": size} for n, line, size in shape.arrays],
        "pipelines": [
            {
                "name": st.name,
                "at": at(st.line),
                "depth": st.depth,
                "stage_bytes": st.stage_bytes,
                "waits": [{"at": at(line), "wait_group": n} for line, n in sorted(st.waits.items())],
            }
            for st in shape.stages
        ],
        "barriers_per_thread": ops["barrier"].render() if "barrier" in ops else "0",
        "barriers": [at(line) for line in sorted(set(shape.barriers))],
        "warp_collectives": [{"at": at(line), "operation": what} for line, what in sorted(shape.collectives.items())],
        "fragments": [{"at": at(line), "operation": what} for line, what in sorted(shape.fragments.items())],
        "registers_per_thread": r.registers or None,
        "evidence": {  # checked: counted from the checked program; ptxas: its report for the target, nothing ran
            "shared_bytes_per_block": "checked",
            "pipelines": "checked",
            "barriers_per_thread": "checked",
            "registers_per_thread": "ptxas" if r.registers else "not read: --inspect reads it",
        },
    }
    if shape.device and card is not None:
        out["resident"] = resident(r, card)
        out["evidence"]["resident"] = "the model, from the specification's limits" + (
            "" if r.registers else "; registers not read, so they limit nothing here"
        )
    if shape.census:
        out["census"] = shape.census
    return out


def priced(r: Region, card: Device, sizes: dict[str, float], missing: set[str]) -> Piece:
    """A device cooperative region at `sizes`: a launch and the largest of its memory, issue, shared-memory and
    tensor-core times, with the resources behind them."""
    shape = r.coop
    blocks, runs, threads = value(r.count, sizes, missing), value(r.runs, sizes, missing), shape.threads
    each = blocks * threads
    ops = {k: value(n, sizes, missing) * each for k, n in r.body.ops.items()}
    moved = each * sum(value(n, sizes, missing) for k, n in (*r.body.reads.items(), *r.body.writes.items())
                       if not k.startswith("shared "))  # fmt: skip
    moved += each * SECTOR * sum(value(n, sizes, missing) for n in r.body.irregular.values())
    copied = ops.get("fill_bytes", 0.0)
    held = resident(r, card)
    per_sm = held["blocks_per_sm"]
    concurrent = min(blocks, card.sms * per_sm)
    reached = max(1, min(card.sms, math.ceil(blocks)))
    busy = min(1.0, concurrent * threads / (card.sms * card.threads_per_sm * card.occupancy_to_saturate)) or 1e-9
    stream = card.dram_gbps * card.memory_efficiency
    flights = [{"name": st.name, "line": st.line, "depth": st.depth, "wait_group": st.in_flight,
                "stages_in_flight_per_block": st.in_flight + 1,
                "bytes_in_flight": round(concurrent * (st.in_flight + 1) * st.stage_bytes)} for st in shape.stages]  # fmt: skip
    flight = sum(f["bytes_in_flight"] for f in flights)
    rate = min(stream, flight / card.memory_latency_ns) if flight and card.memory_latency_ns else stream * busy
    memory = max(moved - copied, 0.0) / (stream * busy) + (copied / rate if copied else 0.0)
    issued = sum(n for k, n in ops.items() if k not in NOT_ISSUED)
    issue = issued / (card.flops["i32"] * busy)
    wavefronts = ops.get("shared_wavefront", 0.0)
    shared = wavefronts * PHASE_BYTES / (reached * card.shared_bytes_per_clock * card.ghz)
    peak = card.flops.get("tensor_f16", card.flops["f32"])
    multiplied = ops.get("tensor", 0.0)
    tensor = multiplied / (peak * reached / card.sms)
    rates = {"device memory": memory, "issue": issue, "shared memory": shared, "tensor cores": tensor}
    worst = max(rates, key=lambda k: rates[k])
    ns = card.launch_ns + rates[worst]
    bound = "launch" if card.launch_ns > rates[worst] else worst
    light = max(moved / card.dram_gbps, issued / card.flops["i32"], wavefronts / (card.sms * card.ghz),
                multiplied / peak)  # fmt: skip
    detail: dict[str, Any] = {
        "device": card.name,
        "blocks": round(blocks, 1),
        "threads_per_block": threads,
        "resident": held,
        "waves": math.ceil(blocks / (card.sms * per_sm)) if per_sm and blocks else 0,
        "busy": round(busy, 3),
        "memory_ns": round(memory, 1),
        "issue_ns": round(issue, 1),
        "shared_ns": round(shared, 1),
        "tensor_ns": round(tensor, 1),
        "launch_ns": card.launch_ns,
        "device_bytes": round(moved),
    }
    guesses = []
    if flights and copied:
        detail["pipelines"] = flights
        detail["copy_gbps"] = round(rate, 1)
        guesses.append(f"a pipeline's copies are priced at the bytes in flight over an assumed memory latency of "
                       f"{card.memory_latency_ns:g} ns, which no run has measured")  # fmt: skip
    if not r.registers:
        guesses.append("registers are not read until ptxas compiles the kernel (--inspect), so only threads and "
                       "shared memory limit the blocks an SM holds")  # fmt: skip
    if multiplied:
        detail["tensor_peak_ops_per_ns"] = peak
    return Piece(f"device cooperative region at line {r.line}", ns * runs, bound, light * runs, detail, guesses)


def on_host(r: Region, host: Host, arch: str, sizes: dict[str, float], missing: set[str]) -> Piece:
    """A cooperative region on host threads (runtime/cairn_coop.hpp): two teams of T threads, each started for the
    region, taking the blocks in turn. Its work is priced as a host region's on as many of those threads as there
    are lanes, and each thread's start at the measured cost of starting a task; its barriers are not priced."""
    shape = r.coop
    blocks, runs = value(r.count, sizes, missing), value(r.runs, sizes, missing)
    body = Work()
    body.merge(r.body)
    for kind in NOT_ISSUED:
        body.ops.pop(kind, None)
    for table in (body.reads, body.writes):
        for key in [k for k in table if k.startswith("shared ")]:
            table.pop(key)
    total = Work()
    total.merge(body, r.count * shape.threads)
    used = min(host.lanes, 2 * shape.threads)
    reach = footprint(total, sizes, missing)
    compute, memory, irregular, level = price(total, host, arch, sizes, missing, used, reach)
    start = 2 * shape.threads * host.spawn_ns
    ns = start + max(compute, memory, irregular)
    bound = "thread start" if start >= max(compute, memory, irregular) else (
        "compute" if compute >= max(memory, irregular) else f"memory ({level})")  # fmt: skip
    detail = {"blocks": round(blocks, 1), "threads_per_block": shape.threads, "threads": 2 * shape.threads,
              "lanes": used, "start_ns": round(start, 1), "compute_ns": round(compute, 1),
              "memory_ns": round(memory, 1)}  # fmt: skip
    return Piece(f"host cooperative region at line {r.line}", ns * runs, bound, 0.0, detail)


def read(costs: dict[str, Cost], kernels: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Give each device cooperative region the registers and static shared memory ptxas reported for its kernel,
    `cr::coop::blocks<THREADS, BYTES, ...>` of its function, told apart from the function's other regions by the
    number its lambda's type carries. What was read, beside what the checker laid out, for the report."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for c in costs.values():
        for r in c.regions:
            if r.coop is None or not r.coop.device:
                continue
            shape = r.coop
            found = [e for e in kernels.get(shape.function, []) if (m := KERNEL.search(e.get("symbol", ""))) and
                     int(m.group(1)) == shape.threads and (int(m.group(3)) + 1 if m.group(3) else 0) == shape.ordinal]  # fmt: skip
            key = (shape.function, r.line)
            if len(found) != 1:
                out.setdefault(key, {"function": shape.function, "line": r.line, "status": "no kernel matched"})
                continue
            e = found[0]
            r.registers, r.shared = e["registers"], e["shared_bytes"]
            out[key] = {"function": shape.function, "line": r.line, "status": "read", "registers": e["registers"],
                        "shared_bytes": e["shared_bytes"], "checked_shared_bytes": shape.shared_bytes,
                        "spill_bytes": e["spill_bytes"], "stack_bytes": e["stack_bytes"],
                        "instructions": e["instructions"]}  # fmt: skip
    return list(out.values())


def changed(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What differs between two predictions' cooperative regions, taken in order: shared memory, the blocks an SM
    holds and why, registers, each pipeline's depth and what it keeps in flight, the copy rate and the bound."""
    pairs = zip(*([p for p in parts if "cooperative region" in p["what"]] for parts in (before, after)), strict=False)
    out = []
    for a, b in pairs:

        def facts(p: dict[str, Any]) -> dict[str, Any]:
            held = p.get("resident", {})
            found = {k: held.get(k) for k in ("shared_bytes_per_block", "blocks_per_sm", "limited_by",
                                               "registers_per_thread", "occupancy")}  # fmt: skip
            found |= {k: p.get(k) for k in ("copy_gbps", "busy", "bound")}
            for f in p.get("pipelines", []):
                found |= {f"{f['name']}.{k}": f[k] for k in ("depth", "wait_group", "bytes_in_flight")}
            return found

        x, y = facts(a), facts(b)
        diff = {k: [x.get(k), y.get(k)] for k in {**x, **y} if x.get(k) != y.get(k)}
        if diff:
            out.append({"line": int(b["what"].rsplit(" ", 1)[-1]), **dict(sorted(diff.items()))})
    return out


# For a person -----------------------------------------------------------------------------------------------------


def said(region: dict[str, Any]) -> list[str]:
    """A cooperative region as `cairn predict` shows it to a person: each line says what its figures rest on."""
    d = region["cooperative"]
    home = d["at"].rsplit(":", 1)[0]

    def here(at: str) -> str:  # a place in the region's own file is its line
        where, _, line = at.rpartition(":")
        return f"line {line}" if where == home or at.startswith("line ") else at

    out = [f"  cooperative region at {d['at']}: {region['count']} blocks of {d['threads_per_block']} threads "
           f"({' x '.join(map(str, d['thread_extents']))}), on the {d['placement']}"]  # fmt: skip
    held = [f"{a['name']} {a['bytes']}" for a in d["shared"]]
    held += [f"{p['name']} {p['depth']} stages of {p['stage_bytes']}" for p in d["pipelines"]]
    out.append(
        f"    [checked] shared memory a block: {d['shared_bytes_per_block']} bytes ({', '.join(held) or 'none'})"
    )
    for p in d["pipelines"]:
        waits = "; ".join(f"the wait at {here(w['at'])} leaves {w['wait_group']} in flight" for w in p["waits"])
        out.append(f"    [checked] {p['name']}, {here(p['at'])}: depth {p['depth']}; {waits or 'no wait'}")
    shown = ", ".join(here(at) for at in d["barriers"]) or "none"
    out.append(f"    [checked] barriers a thread: {d['barriers_per_thread']}, at {shown}")
    for kind in ("warp_collectives", "fragments"):
        if d[kind]:
            listed = ", ".join(f"{x['operation']} at {here(x['at'])}" for x in d[kind])
            out.append(f"    [checked] {kind.replace('_', ' ')}: {listed}")
    registers = d["registers_per_thread"]
    if d["placement"] == "device":
        out.append(f"    [ptxas] registers a thread: {registers}" if registers else
                   "    [not read] registers a thread: --inspect reads them from ptxas")  # fmt: skip
    if "resident" in d:
        r = d["resident"]
        by = ", ".join(f"{k} {n}" for k, n in r["by_limit"].items())
        out.append(f"    [specification limits] an SM holds {r['blocks_per_sm']} blocks, {r['occupancy']:.0%} of its "
                   f"threads: by {by}, with {r['reserved_bytes_per_block']} bytes reserved a block")  # fmt: skip
    if d.get("census"):
        out.append(f"    [not counted] the census did not run: {d['census']}")
    return out


def took(ns: float) -> str:
    for unit, scale in (("ms", 1e6), ("us", 1e3)):
        if ns >= scale:
            return f"{ns / scale:.3g} {unit}"
    return f"{ns:.3g} ns"


def said_at(part: dict[str, Any]) -> str:
    """One size's figures for a cooperative region's part, labelled."""
    if "resident" not in part:
        return (f"    [model] {part.get('threads', 0)} host threads started at the measured cost of a task "
                f"({took(part.get('start_ns', 0))}), work on {part.get('lanes', 0)} lanes")  # fmt: skip
    rates = ", ".join(
        f"{k} {took(part[k + '_ns'])}" for k in ("memory", "issue", "shared", "tensor") if part[k + "_ns"]
    )
    waves = f"{part['waves']} wave{'s' if part['waves'] != 1 else ''}"
    text = (f"    [model] {part['blocks']:g} blocks in {waves}, {part['busy']:.0%} of the device busy; "
            f"{rates or 'nothing'} beside a {took(part['launch_ns'])} launch [assumed]")  # fmt: skip
    for f in part.get("pipelines", []):
        text += (f"; {f['name']} keeps {f['stages_in_flight_per_block']} stages in flight a block, "
                 f"{f['bytes_in_flight']} bytes on the device")  # fmt: skip
    if "copy_gbps" in part:
        text += f": copies at {part['copy_gbps']:g} GB/s [assumed latency]"
    return text


def said_changed(changes: list[dict[str, Any]]) -> list[str]:
    """What a change did to each cooperative region, for `cairn predict --against`."""
    return [f"    region at line {c['line']}: " + "; ".join(f"{k} {v[0]} -> {v[1]}" for k, v in c.items() if k != "line")
            for c in changes]  # fmt: skip


def checked(costs: dict[str, Cost]) -> list[tuple[str, Any, Any]]:
    """What the checker laid out differently in two candidates' cooperative regions, taken in order: the threads
    of a block, its shared memory, and each pipeline's depth and what its waits leave in flight."""
    out: list[tuple[str, Any, Any]] = []
    a, b = ([r.coop for r in costs[k].regions if r.coop is not None] for k in "ab")
    for x, y in zip(a, b, strict=False):
        pairs = [("threads a block", x.threads, y.threads), ("shared memory bytes a block", x.shared_bytes,
                 y.shared_bytes)]  # fmt: skip
        for p, q in zip(x.stages, y.stages, strict=False):
            pairs += [(f"{q.name}: stages", p.depth, q.depth), (f"{q.name}: copies a wait leaves in flight",
                      p.in_flight, q.in_flight)]  # fmt: skip
        out += [(what, u, v) for what, u, v in pairs if u != v]
    return out

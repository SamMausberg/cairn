"""A predicted time for one call: the work `work` counted, priced by what a machine `profile` can do.

Every piece of a call is priced on its own and says what bounds it. A sequential stretch or a small region costs the
larger of its compute and its memory traffic on one thread. A wide host region pays the pool's start and then shares
its work among the lanes it engages, bounded by the bandwidth of the level its data lives in. Tasks overlap until
they are waited on. Transfers and allocations are priced by bytes. The speed of light is the same work at the whole
machine's peak for the bound that applies: bandwidth for streams, every core for compute.

A prediction is never a measurement. Its confidence is `high` when every count is a size and every access a stream,
`medium` when something was approximated, and `low`, with the reason, when a count or an address rests on data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .counts import Cost, Poly, Region, Work
from .profile import Device, Host, Profile, card

# What calibration saw vectorize on the reference machine: anything else keeps a loop scalar, and so does anything a
# profile's own calibration names in `keeps_scalar` for the -march profile the code is built for.
VECTOR_SAFE = {"load", "store", "int", "mul", "compare", "branch", "f32", "f64", "f32_div", "f64_div", "convert",
               "call", "move", "index_checked", "bounds_guard", "shift_guard"}  # fmt: skip


@dataclass
class Piece:
    """One priced part of a call: where it is, how long it takes, and what sets that time."""

    what: str
    ns: float
    bound: str
    light_ns: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
    guesses: list[str] = field(default_factory=list)  # what makes this piece's number a guess, beyond its kind


def value(p: Poly, sizes: dict[str, float], missing: set[str]) -> float:
    v = p.value(sizes)
    if v is None:
        missing |= {s for s in p.symbols() if s not in sizes}
        return 0.0
    return max(v, 0.0)


def mode(work: Work, host: Host, arch: str | None = None) -> str:
    """Vector when nothing in the body keeps the loop scalar and every access is a stream; scalar otherwise."""
    kinds = {k for k, n in work.ops.items() if n.terms}
    keeps_scalar = kinds & set(host.table(arch).get("keeps_scalar", ())) or kinds - VECTOR_SAFE
    return "scalar" if keeps_scalar or any(n.terms for n in work.irregular.values()) else "vector"


def footprint(work: Work, sizes: dict[str, float], missing: set[str]) -> float:
    return sum(value(n, sizes, missing) for n in work.footprint.values())


def price(work: Work, host: Host, arch: str, sizes: dict[str, float], missing: set[str], threads: int,
          reach: float) -> tuple[float, float, float, str]:  # fmt: skip
    """(compute ns, memory ns, irregular ns, level) of `work` run once, on `threads` threads sharing it.

    A fold's step waits for the step before it, and an out-of-order core runs the rest of the body while it waits,
    so compute is the larger of the folds' chain and the rest, not their sum: a body whose only cost is its fold, or
    that has no fold, is priced as calibration fitted it. A vector loop's compute is shared among the physical cores
    the threads run on, since two sibling lanes share a core's vector units; a scalar loop, a chain and an irregular
    access leave room that a sibling fills. On more than one thread the pool claims chunks on demand, so a lane
    finds its chunk where its own core cached it on the call before about once in as many cores as the region
    engages, and every other chunk comes from the shared level that holds the whole working set. An irregular access
    costs the latency of the level its own view fits in, so a histogram's bins in the first cache are cheap however
    large the input that streams past them."""
    table = host.table(arch)
    chosen = mode(work, host, arch)
    counted = {k: value(n, sizes, missing) for k, n in work.ops.items() if k != "atomic" or threads == 1}
    costs = table.get(chosen, table["scalar"])
    each = {k: n * costs.get(k, table["scalar"].get(k, 0.0)) for k, n in counted.items()}
    chain = sum(ns for k, ns in each.items() if k.endswith("_fold"))
    rest = max(sum(each.values()) - chain, sum(counted.values()) * floor(host, chosen))
    compute = max(chain, rest)
    level = host.level(reach, threads, threads <= 1)
    read, written = (sum(value(n, sizes, missing) for n in t.values()) for t in (work.reads, work.writes))
    own = 1 / host.parallel(threads)  # the share of chunks a lane finds where its own core left them

    def moved(private: bool) -> float:
        return host.seconds("read", read, reach, threads, private) + host.seconds("write", written, reach, threads,
                                                                                    private)  # fmt: skip

    memory = moved(True) if threads <= 1 else own * moved(True) + (1 - own) * moved(False)
    irregular = sum(value(n, sizes, missing) * host.irregular_ns.get(
        host.level(value(work.footprint.get(k, Poly()), sizes, missing), threads), 0.0) for k, n in work.irregular.items())  # fmt: skip
    # A vector loop keeps its core's vector units busy, so its sibling lane adds nothing to it and only physical
    # cores add throughput. A scalar loop, or one waiting on its own chain, leaves room that a sibling fills.
    busy = chosen == "vector" and chain < rest
    return compute / (host.parallel(threads) if busy else threads), memory, irregular / threads, level


def floor(host: Host, chosen: str) -> float:
    """What a body's operations cost at least, whatever the fit gave them: an eighth of a cycle each alone, a sixty-fourth
    in a vector. A least-squares fit can leave a kind at zero when another absorbs its time, which is right for the
    sum of a loop it was fitted on and wrong for a body of nothing but that kind: zero would read as free."""
    return (1 / 8 if chosen == "scalar" else 1 / 64) / host.ghz if host.ghz else 0.0


def contended(work: Work, host: Host, sizes: dict[str, float], missing: set[str], threads: int) -> float:
    """Atomics that every lane reaches in one place run one after another, whatever the lane count."""
    if threads == 1 or "atomic" not in work.ops:
        return 0.0
    return value(work.ops["atomic"], sizes, missing) * host.atomic_ns.get("shared", 0.0)


def light(work: Work, host: Host, arch: str, sizes: dict[str, float], reach: float) -> float:
    """The same work at the whole machine's peak: vector compute on every core, the bandwidth of every lane."""
    missing: set[str] = set()
    table = host.table(arch)
    compute = sum(value(n, sizes, missing) * min(table["vector"].get(k, 0.0) or table["scalar"].get(k, 0.0),
                  table["scalar"].get(k, 0.0) or table["vector"].get(k, 0.0)) for k, n in work.ops.items())  # fmt: skip
    read, written = (sum(value(n, sizes, missing) for n in t.values()) for t in (work.reads, work.writes))
    memory = host.seconds("read", read, reach, host.lanes) + host.seconds("write", written, reach, host.lanes)
    return max(compute / host.parallel(host.lanes), memory)


def settled(compute: float, memory: float, serial: float, irregular: float, level: str) -> str:
    """The name of whichever of the four overlapping costs is the largest."""
    named = {"compute": compute, f"memory ({level})": memory, "a shared atomic": serial, "irregular access": irregular}
    return max(named, key=lambda k: named[k])


def vector_saving(r: Region) -> float:
    """The load and store instructions per index a vector plan saves: each chunked array's element accesses become
    one load, when its chunk is loaded, and one store, when it is stored, for every W indices."""
    return sum(uses - (loaded + stored) / r.vector for uses, loaded, stored, size in r.chunks if r.vector * size <= 16)


def launched(card: Device, memory: float, compute: float, units: str) -> tuple[float, str]:
    """A kernel's time, a launch and then the larger of its memory and its compute, and which of the three bounds it."""
    bound = "launch" if card.launch_ns > max(memory, compute) else "device memory" if memory >= compute else units
    return card.launch_ns + max(memory, compute), bound


def tensor(r: Region, card: Device, sizes: dict[str, float], missing: set[str]) -> Piece:
    """A tensor-core multiply by its roofline: a launch, then the larger of its bytes at the memory's sustained
    bandwidth and its operations at the published tensor peak. Every format mma_unordered takes runs at the f16
    rate, since an 8-bit float is widened to f16. How close the kernel comes to that peak is not known until the
    owner's device calibration runs, so the prediction is the roofline's, not the kernel's."""
    runs = value(r.runs, sizes, missing)
    moved = sum(value(b, sizes, missing) for b in (*r.body.reads.values(), *r.body.writes.values()))
    work = sum(value(k, sizes, missing) for k in r.body.ops.values())
    peak = card.flops.get("tensor_f16", card.flops["f32"])
    memory, compute = moved / (card.dram_gbps * card.memory_efficiency), work / peak
    ns, bound = launched(card, memory, compute, "tensor cores")
    light = max(moved / card.dram_gbps, work / peak)
    detail = {"device": card.name, "format": r.tensor, "memory_ns": round(memory, 1), "compute_ns": round(compute, 1),
              "launch_ns": card.launch_ns, "peak_ops_per_ns": peak}  # fmt: skip
    return Piece(f"device tensor-core multiply at line {r.line}", ns * runs, bound, light * runs, detail)


def lanes(r: Region, card: Device | None, sizes: dict[str, float], missing: set[str]) -> Piece:
    """A device region by its roofline: a launch, then the larger of its bytes at the memory's sustained bandwidth
    and its instructions at the device's issue rate, both shared out over the part of the device its grid keeps
    busy, so a plan whose per_lane leaves the device underfilled is priced as underfilled. Priced from the
    specification the profile names; its registers, and so its occupancy, count once ptxas has read them."""
    n, runs = value(r.count, sizes, missing), value(r.runs, sizes, missing)
    if card is None:
        return Piece(f"device region at line {r.line}", 0.0, "device (no device profile)", 0.0)
    total = Work()
    total.merge(r.body, r.count)
    moved = sum(value(b, sizes, missing) for b in (*total.reads.values(), *total.writes.values()))
    moved += 32 * sum(value(k, sizes, missing) for k in total.irregular.values())  # one sector per scattered access
    issued = sum(value(k, sizes, missing) for k in total.ops.values())
    issued -= n * vector_saving(r) if r.vector else 0.0  # a chunk's one wide access stands for W narrow ones
    block, per_lane, _ = r.launch
    threads = min(n / (per_lane or 1), 65535 * (block or 256))  # the grid the runtime launches
    busy = min(1.0, threads / (card.sms * card.threads_per_sm * card.occupancy_to_saturate)) if n else 1.0
    if r.registers:  # a kernel whose registers or shared memory keep few warps resident cannot keep memory busy
        busy = min(busy, card.occupancy(r.registers, block or 256, r.shared) / card.occupancy_to_saturate)
    memory, compute = moved / (card.dram_gbps * card.memory_efficiency * busy), issued / (card.flops["i32"] * busy)
    ns, bound = launched(card, memory, compute, "device compute")
    light = max(moved / card.dram_gbps, issued / card.flops["i32"])
    detail = {"count": r.count.render(), "device": card.name, "memory_ns": round(memory, 1), "compute_ns": round(compute, 1),
              "launch_ns": card.launch_ns, "threads": int(threads), "busy": round(busy, 3)}  # fmt: skip
    return Piece(f"device region at line {r.line}", ns * runs, bound, light * runs, detail)


def region(r: Region, host: Host, arch: str, sizes: dict[str, float], missing: set[str],
           device: Device | None = None) -> Piece:  # fmt: skip
    if r.kind == "cooperative":  # blocks of threads that share memory: perf/cooperative_model.py
        from . import cooperative_model

        if r.coop.device:
            return cooperative_model.priced(r, device or card().device, sizes, missing)
        return cooperative_model.on_host(r, host, arch, sizes, missing)
    n, runs = value(r.count, sizes, missing), value(r.runs, sizes, missing)
    each = dict(sizes)
    total = Work()
    total.merge(r.body, r.count)
    reach = footprint(r.body, each, missing)
    cutoff, grain = host.pool["cutoff"], host.pool["grain"]
    wide = r.kind in {"host", "pooled"} and n * max(r.weight, 1) >= cutoff and r.plan[1] != 1
    grain_given = r.plan[0] or 0
    if r.kind == "device":
        return lanes(r, device or card().device, sizes, missing)
    if r.kind == "tensor":
        return tensor(r, device or card().device, sizes, missing)
    if wide or (grain_given and n >= 2):
        least = grain_given or max(1, grain // max(r.weight, 1))
        used = min(host.lanes, max(1, int(n // least)), r.plan[1] or host.lanes)
    else:
        used = 1
    compute, memory, irregular, level = price(total, host, arch, each, missing, used, reach)
    serial = contended(total, host, each, missing, used)
    start = host.fork(used) if used > 1 else 0.0
    ns = start + max(compute, memory, serial, irregular)  # an out-of-order core overlaps all four
    bound = "pool start" if start > max(compute, memory, serial, irregular) else settled(compute, memory, serial,
                                                                                         irregular, level)  # fmt: skip
    peak = light(total, host, arch, each, reach)
    detail = {"count": r.count.render(), "lanes": used, "mode": mode(r.body, host, arch), "level": level,
              "compute_ns": round(compute, 1), "memory_ns": round(memory, 1), "start_ns": round(start, 1),
              "working_set_bytes": round(reach)}  # fmt: skip
    return Piece(f"{r.kind} region at line {r.line}", ns * runs, bound, peak * runs, detail)


def sequential(c: Cost, host: Host, arch: str, sizes: dict[str, float], missing: set[str]) -> Piece:
    reach = footprint(c.seq, sizes, missing)
    compute, memory, irregular, level = price(c.seq, host, arch, sizes, missing, 1, reach)
    ns = max(compute, memory, irregular)
    bound = settled(compute, memory, 0.0, irregular, level)
    detail = {"mode": mode(c.seq, host, arch), "level": level, "compute_ns": round(compute, 1),
              "memory_ns": round(memory, 1)}  # fmt: skip
    return Piece("sequential code", ns, bound, light(c.seq, host, arch, sizes, reach), detail)


def tasks(c: Cost, host: Host, arch: str, sizes: dict[str, float], missing: set[str]) -> Piece | None:
    if not c.tasks:
        return None
    started = sum(value(n, sizes, missing) for n, _ in c.tasks)
    each = [sum(p.ns for p in pieces(t, host, arch, sizes, missing)) for _, t in c.tasks]
    together = Work()
    for n, t in c.tasks:
        together.merge(t.seq, n)
    reach = footprint(together, sizes, missing)
    k = min(len(c.tasks), host.lanes)
    _, memory, _, level = price(together, host, arch, sizes, missing, k, reach)
    ns = started * host.spawn_ns + max(max(each), memory)
    bound = "task start" if started * host.spawn_ns > max(max(each), memory) else (
        f"memory ({level})" if memory >= max(each) else "the slowest task")  # fmt: skip
    return Piece(f"{len(c.tasks)} tasks", ns, bound, light(together, host, arch, sizes, reach), {"lanes": k})


def unpriced(r: Region, piece: Piece) -> Piece:
    """`piece` for a region whose kernel ptxas says spills, saying that its time leaves the spills out. ptxas counts
    the bytes of spill instructions in the code, not how often they run, and not where the traffic they make is
    served, so no price is put on them."""
    if r.spilled:
        piece.detail["spill_bytes"] = r.spilled
        piece.guesses.append(f"ptxas counts {r.spilled} bytes of spill stores and loads in a thread's code for the "
                             f"kernel at line {r.line}; the local-memory traffic they make is not priced, so the time "
                             "leaves it out")  # fmt: skip
    return piece


def pieces(c: Cost, host: Host, arch: str, sizes: dict[str, float], missing: set[str],
           device: Device | None = None) -> list[Piece]:  # fmt: skip
    out = [sequential(c, host, arch, sizes, missing)]
    out += [unpriced(r, region(r, host, arch, sizes, missing, device)) for r in c.regions]
    linked = device or (card().device if c.transfers else None)
    for way, moved in c.transfers.items():  # a transfer crosses the link, h2h copies on the host
        size = value(moved, sizes, missing)
        if way == "h2h" or linked is None:
            out.append(Piece(f"transfer {way}", size / host.bandwidth("write", host.level(size, 1), 1), "copy"))
        else:
            out.append(Piece(f"transfer {way}", linked.link_ns + size / linked.link_gbps, "the host-device link",
                             size / linked.link_gbps))  # fmt: skip
    if (t := tasks(c, host, arch, sizes, missing)) is not None:
        out.append(t)
    zeroed = value(c.allocated, sizes, missing)
    made = value(c.allocations, sizes, missing)
    if made:
        level = host.level(zeroed, 1)
        ns = made * host.alloc_ns + zeroed / host.bandwidth("write", level, 1)
        if zeroed >= made * host.mapped_bytes:  # mapped afresh on every call: each page faults on its first touch
            ns += zeroed / 4096 * host.page_ns
        out.append(Piece("allocation", ns, "allocation", zeroed / host.bandwidth("write", level, host.lanes)))
    return [p for p in out if p.ns or p.what != "sequential code"]


WIDE = (
    "a wide host region waits for its slowest lane, which the machine's load can hold up: at 1e5 to 1e7 elements the "
    "validation measured such regions up to five times slower than predicted"
)  # evidence/v1_0/perf_model
STREAMED = (
    "a wide region streams more than calibration measured the last-level cache at, though less than it holds, and is "
    "priced at that bandwidth; on a shared machine such streams ran up to five times slower"
)  # evidence/v1_0/perf_model: the working set from 32 to 96 MiB


def significant(ns: float) -> float:
    """Four significant digits: a time of a fraction of a nanosecond is not rounded to nothing."""
    return float(f"{ns:.4g}")


def measured(profile: Profile) -> str:
    return next(iter(profile.host.ops)) if profile.host and profile.host.ops else ""


def confidence(c: Cost, missing: set[str], found: list[Piece], profile: Profile, arch: str) -> tuple[str, list[str]]:
    """low when a number is a guess, medium when it is an approximation, high when neither: with every reason."""
    guesses = list(c.unknown)
    if missing:
        guesses.append("no size given for " + ", ".join(sorted(missing)))
    if any("irregular" in p.bound for p in found):
        guesses.append("an address rests on data, so its cache behaviour is a guess")
    approximations = list(c.approximate)
    wide = [p for p in found if p.detail.get("lanes", 1) > 1 and p.what.startswith(("host", "pooled"))]
    if wide:
        approximations.append(WIDE)
    host = profile.host
    if host and any(host.measured_bytes.get("l3", host.cache["l3"]) < p.detail.get("working_set_bytes", 0)
                    <= host.cache["l3"] for p in wide):  # fmt: skip
        approximations.append(STREAMED)
    if any(p.what.startswith(("device", "transfer h2d", "transfer d2h", "transfer d2d")) for p in found):
        guesses.append("device work is priced from the published specification, and no device run has checked it")
    guesses += [g for p in found for g in p.guesses if g not in guesses]
    if any(p.what.startswith("device tensor-core") for p in found):
        guesses.append(
            "a tensor-core multiply is priced at the published tensor peak, its roofline; the kernel's own "
            "efficiency, which only the owner's device calibration measures, makes it slower"
        )
    if profile.origin != "measured":
        approximations.append(f"the machine profile is a {profile.origin}, not a measurement")
    if profile.host and arch not in profile.host.ops:
        approximations.append(f"the profile measured operations for {', '.join(sorted(profile.host.ops))}, not {arch}")
    level = "low" if guesses else "medium" if approximations else "high"
    return level, guesses + approximations


def predict(c: Cost, profile: Profile, sizes: dict[str, float], arch: str | None = None) -> dict[str, Any]:
    """The predicted time of one call of `c` at `sizes`, what bounds each part, and how sure the model is. `arch` is
    the -march profile the code is built for; by default, the one the profile measured first."""
    arch = arch or measured(profile)
    host = profile.host
    if host is None:
        raise ValueError(f"{profile.name} describes no host.")
    missing: set[str] = set()
    found = pieces(c, host, arch, sizes, missing, profile.device)
    total = sum(p.ns for p in found)
    peak = sum(p.light_ns for p in found)
    level, why = confidence(c, missing, found, profile, arch)
    dominant = max(found, key=lambda p: p.ns) if found else None
    return {
        "ns": significant(total),
        "bound": dominant.bound if dominant else "nothing",
        "speed_of_light": round(peak / total, 3) if total and peak else None,
        "confidence": level,
        "why": why,
        "parts": [{"what": p.what, "ns": significant(p.ns), "bound": p.bound, **p.detail} for p in found],
        "measure": level == "low",
    }


def regimes(c: Cost, profile: Profile, arch: str | None = None) -> list[dict[str, Any]]:
    """The prediction in one extent, piece by piece: where the bound or the lane count changes, a new line begins,
    and each line is a fixed part plus a cost per element. Other extents are held at 1."""
    x, base = c.extents[0], dict.fromkeys(c.extents, 1.0)
    samples = []
    for k in range(6, 31):
        predicted = predict(c, profile, {**base, x: float(2**k)}, arch)
        lanes = max((p.get("lanes", 1) for p in predicted["parts"]), default=1)
        samples.append((float(2**k), predicted["ns"], (predicted["bound"], lanes > 1)))
    pieces: list[dict[str, Any]] = []
    start = 0
    for i in range(1, len(samples) + 1):
        if i == len(samples) or samples[i][2] != samples[start][2]:
            (n0, t0, (bound, wide)), (n1, t1, _) = samples[start], samples[i - 1]
            per = (t1 - t0) / (n1 - n0) if n1 > n0 else t0 / n0
            pieces.append({"from": n0, "to": n1, "fixed_ns": round(max(t0 - per * n0, 0.0), 1),
                           "per_element_ns": float(f"{per:.4g}"), "bound": bound, "pool": wide})  # fmt: skip
            start = i
    return pieces


def formula(c: Cost, profile: Profile, arch: str | None = None) -> str:
    """The regimes written as one line: `fixed + per*n` for each span of the first extent."""
    if not c.extents:
        return f"{predict(c, profile, {}, arch)['ns']} ns"
    x = c.extents[0]

    def span(r: dict[str, Any]) -> str:
        fixed = f"{r['fixed_ns'] / 1000:.3g} us + " if r["fixed_ns"] >= 100 else ""
        per = r["per_element_ns"]
        cost = f"{per * 1000:.3g} ps" if 0 < per < 0.01 else f"{per:.3g} ns"  # nothing that costs reads as free
        return f"{fixed}{cost}*{x} ({r['bound']}, up to {x}={r['to']:.3g})"

    return "; ".join(span(r) for r in regimes(c, profile, arch))

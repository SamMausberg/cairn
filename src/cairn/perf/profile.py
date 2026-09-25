"""What one machine can do, as the model needs it: bandwidth by cache level and thread count, the cost of each
kind of operation with and without vectors, and what the lane pool, a task and an allocation cost.

A profile is data. `origin` says where its numbers came from: `measured` by `cairn.perf.calibrate` on the machine it
names, or `specification` from a vendor's published figures, which no run has confirmed. The model carries the
origin into every prediction it makes from the profile.

A device card is a packaged profile with a device and no host: one GPU's published figures, each with the document
it came from. `card()` is how anything reads one, by its key, and `cards()` lists them.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILES = Path(__file__).resolve().parent / "profiles"
LEVELS = ("l1", "l2", "l3", "dram")


@dataclass
class Host:
    lanes: int
    cache: dict[str, int]  # l1, l2 per core; l3 shared: bytes
    read: dict[str, dict[str, float]]  # level -> {"1": GB/s one thread, "all": GB/s every lane}
    write: dict[str, dict[str, float]]
    # -march profile -> "vector" / "scalar" -> kind -> ns per element, and "keeps_scalar": the kinds whose presence
    # kept a calibration loop scalar on that profile.
    ops: dict[str, dict[str, Any]]
    pool: dict[str, float]  # fork_ns, per_lane_ns, cutoff, grain
    spawn_ns: float = 30_000.0
    alloc_ns: float = 200.0
    page_ns: float = 0.0  # a first touch of each 4 KiB page of an allocation the allocator maps afresh
    mapped_bytes: int = 32 << 20  # from this size up, every allocation is mapped afresh (glibc's largest threshold)
    irregular_ns: dict[str, float] = field(default_factory=dict)  # level -> ns per data-dependent access
    atomic_ns: dict[str, float] = field(default_factory=dict)  # "shared": ns per access when every lane hits one
    ghz: float = 0.0
    cores: int = 0  # physical cores; 0 when unknown, and then every lane counts as one
    measured_bytes: dict[str, int] = field(default_factory=dict)  # level -> the working set its bandwidth was timed at

    def level(self, footprint: float, threads: int, private: bool = True) -> str:
        """Where a working set of `footprint` bytes lives: per-core levels are shared out among the threads, and
        serve nothing when `private` is false, as for a wide region whose chunks move between cores."""
        if not private:
            return "l3" if footprint <= self.cache["l3"] else "dram"
        share = footprint / max(threads, 1)
        if share <= self.cache["l1"]:
            return "l1"
        if share <= self.cache["l2"]:
            return "l2"
        return "l3" if footprint <= self.cache["l3"] else "dram"

    def blend(self, footprint: float, threads: int, private: bool = True) -> list[tuple[str, float]]:
        """Which levels serve a working set, and in what share: all of it from the first level that holds it, or,
        when it overflows one, the part that fits from that level and the rest from the next one down. Without
        `private` only the shared levels serve it: a lane pool claims its chunks on demand, so the chunk a lane
        gets on one call is rarely the one its core cached on the last."""
        caps = [("l1", self.cache["l1"] * threads), ("l2", self.cache["l2"] * threads)] if private else []
        caps.append(("l3", self.cache["l3"]))
        for i, (level, cap) in enumerate(caps):
            if footprint <= cap:
                if i == 0:
                    return [(level, 1.0)]
                below, held = caps[i - 1]
                return [(below, held / footprint), (level, 1 - held / footprint)]
        return [("l3", caps[-1][1] / footprint), ("dram", 1 - caps[-1][1] / footprint)]

    def seconds(self, kind: str, bytes_moved: float, footprint: float, threads: int, private: bool = True) -> float:
        """Nanoseconds to move `bytes_moved` of a `footprint`-byte working set, level by level."""
        return sum(bytes_moved * share / self.bandwidth(kind, level, threads) for level, share in
                   self.blend(footprint, threads, private) if share > 0)  # fmt: skip

    def bandwidth(self, kind: str, level: str, threads: int) -> float:
        """Bytes per nanosecond (GB/s) at `level`, interpolated between one thread and every lane."""
        table = (self.read if kind == "read" else self.write)[level]
        one, every = table["1"], table["all"]
        if threads <= 1:
            return one
        return one + (every - one) * min(1.0, (threads - 1) / max(self.lanes - 1, 1))

    def table(self, arch: str | None) -> dict[str, Any]:
        """The operation costs measured for `arch`, or for the first profile measured when `arch` was not."""
        return self.ops.get(arch or "") or next(iter(self.ops.values()))

    def parallel(self, threads: int) -> int:
        """How many of `threads` lanes a vector loop computes on at once: one per physical core, since two sibling
        lanes share that core's vector units. On the reference machine a multiply-bound region ran no faster on
        sixteen lanes than on eight (evidence/v1_0/perf_model)."""
        return min(threads, self.cores) if self.cores else threads

    def fork(self, lanes: int) -> float:
        return self.pool["fork_ns"] + self.pool["per_lane_ns"] * max(lanes - 1, 0)


@dataclass
class Device:
    name: str
    sms: int
    cores_per_sm: int
    ghz: float
    dram_gbps: float
    flops: dict[str, float]  # f32, i32, f64: operations per ns across the device
    launch_ns: float  # one launch and the wait that follows it
    link_gbps: float  # host <-> device, each way
    link_ns: float  # fixed cost of one transfer
    memory_efficiency: float = 0.85  # the share of peak bandwidth a streaming kernel reaches
    occupancy_to_saturate: float = 0.5  # the resident share of threads that keeps the memory system busy
    registers_per_sm: int = 65536
    registers_per_block: int = 65536
    registers_per_thread: int = 255
    threads_per_sm: int = 1536
    threads_per_block: int = 1024
    blocks_per_sm: int = 24  # the blocks an SM holds at most, however little each uses
    shared_per_sm: int = 102400
    shared_per_block: int = 101376  # the most one block may use, once it opts in to more than 48 KB
    warp: int = 32
    partitions: int = 4  # the register file is split among this many parts of an SM, each holding whole warps
    register_unit: int = 256  # registers are allocated a warp at a time, in units of this many
    shared_unit: int = 128  # shared memory is allocated a block at a time, in units of this many bytes
    shared_reserved: int = 0  # shared memory the system keeps for each resident block, beside the block's own
    shared_bytes_per_clock: int = 128  # what one SM's shared memory serves a clock: 32 banks of 4 bytes
    memory_latency_ns: float = 0.0  # from a load's issue to its data, in device memory; 0 when not known
    compute_capability: str = ""  # of the device described: which device targets' code runs on it
    target: str = ""  # a measured card: the device target its figures were measured for (projects/target.py)

    def resident(self, registers: int, block: int = 256, shared: int = 0) -> dict[str, int]:
        """How many blocks of `block` threads one SM holds by each of its limits, as CUDA's occupancy calculator
        (cuda_occupancy.h) counts them: its threads, a block's rounded up to whole warps; its registers, when
        `registers` a thread is known, allocated a warp at a time in units of `register_unit` from each of the
        register file's `partitions`; its shared memory, `shared` bytes a block and the `shared_reserved` the system
        keeps beside each, in units of `shared_unit`; and the blocks it holds at all. A block that exceeds what one
        block may have, in threads, registers or shared memory, is held 0 times by that limit: it cannot launch."""
        warps = -(-block // self.warp)
        found = {"threads": self.threads_per_sm // self.warp // warps if block <= self.threads_per_block else 0}
        if registers:
            per_warp = -(-registers * self.warp // self.register_unit) * self.register_unit
            parts = -(-warps // self.partitions) * self.partitions  # a launch is checked as if warps filled each part
            fits = registers <= self.registers_per_thread and per_warp * parts <= self.registers_per_block
            each = self.registers_per_sm // self.partitions // per_warp * self.partitions  # warps, part by part
            found["registers"] = each // warps if fits else 0
        if shared or self.shared_reserved:
            taken = -(-(shared + self.shared_reserved) // self.shared_unit) * self.shared_unit
            found["shared memory"] = self.shared_per_sm // taken if shared <= self.shared_per_block else 0
        found["blocks"] = self.blocks_per_sm
        return found

    def occupancy(self, registers: int, block: int = 256, shared: int = 0) -> float:
        """The resident share of an SM's warps for blocks of `block` threads using `registers` each and `shared`
        bytes of shared memory a block."""
        warps = -(-block // self.warp)
        return min(self.resident(registers, block, shared).values()) * warps * self.warp / self.threads_per_sm

    def held(self, registers: int, block: int = 256, shared: int = 0) -> dict[str, Any]:
        """The blocks one SM holds, as a prediction reports them: by each limit, which limits bind, and the share of
        the SM's warps they keep resident. None held is a block that cannot launch."""
        by = self.resident(registers, block, shared)
        blocks = min(by.values())
        return {"blocks_per_sm": blocks, "by_limit": by, "limited_by": [k for k, n in by.items() if n == blocks],
                "occupancy": round(self.occupancy(registers, block, shared), 3)}  # fmt: skip


@dataclass
class Profile:
    name: str
    origin: str  # measured | specification
    notes: str
    host: Host | None
    device: Device | None
    source: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> Profile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != "cairn.machine/1":
            raise ValueError(f"{path} is not a cairn.machine/1 profile.")
        host = Host(**data["host"]) if data.get("host") else None
        device = Device(**data["device"]) if data.get("device") else None
        return Profile(data["name"], data["origin"], data.get("notes", ""), host, device, data)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "origin": self.origin, "notes": self.notes}


def packaged(name: str) -> Profile:
    return Profile.load(PROFILES / f"{name}.json")


DEFAULT_CARD = "rtx-5070-ti"  # what prices device work when neither the profile nor the command names a card


def cards() -> dict[str, Profile]:
    """Every packaged device card by its key, the file's name: a profile with a device and no host."""
    found = {path.stem: Profile.load(path) for path in sorted(PROFILES.glob("*.json"))}
    for key, p in found.items():
        p.source["card"] = key
    return {key: p for key, p in found.items() if p.device is not None and p.host is None}


def card(name: str = DEFAULT_CARD) -> Profile:
    """The packaged card `name`: its key, or the start of exactly one key before a hyphen, so `h100` is
    `h100-sxm5`. Any other name is E-DEVICE-CARD, with the keys there are. This is how anything reads a card."""
    from ..compiler.syntax.tree import Diagnostic

    known = cards()
    found = [name] if name in known else [key for key in known if key.startswith(f"{name}-")]
    if len(found) != 1:
        which = f"names {' and '.join(found)}" if found else "names no packaged card"
        raise Diagnostic("E-DEVICE-CARD", f"{name!r} {which}; the cards are {', '.join(known)}. `cairn cards` lists "
                         "them.", card=name)  # fmt: skip
    return known[found[0]]


def carrying(profile: Profile, chosen: Profile) -> Profile:
    """`profile`'s host with the device of the card `chosen`: what prices a program's host work and, on that card,
    its device work. The host's origin stays the profile's; the card's is its own."""
    return Profile(profile.name, profile.origin, profile.notes, profile.host, chosen.device,
                   {**profile.source, "device": chosen.source["device"], "card": chosen.source.get("card"),
                    "card_origin": chosen.origin})  # fmt: skip


def described(chosen: Profile) -> dict[str, Any]:
    """A card as `cairn cards` lists it and a prediction names it: its key, device, capability and headline
    figures, and where they came from."""
    d, said = chosen.device, chosen.source.get("source", {})
    assert d is not None
    return {"card": chosen.source.get("card") or chosen.name, "name": chosen.name, "device": d.name,
            "compute_capability": d.compute_capability, "origin": chosen.origin, "sms": d.sms, "ghz": d.ghz,
            "dram_gbps": d.dram_gbps, "flops": d.flops, "threads_per_sm": d.threads_per_sm,
            "shared_per_sm": d.shared_per_sm, "derived": said.get("derived", []), "assumed": said.get("assumed", []),
            "documents": said.get("documents", {})}  # fmt: skip


def listing(found: list[dict[str, Any]]) -> str:
    """`cairn cards` for a person: a line per card, dense rates in TFLOPS, and what none of them is."""
    out = [f"{'card':<16} {'cc':<5} {'SMs':>4} {'GHz':>6} {'GB/s':>6} {'f32':>6} {'f16 tensor':>10} {'f8 tensor':>9}  "
           "device"]  # fmt: skip
    for c in found:
        rate = {k: f"{v / 1000:g}" for k, v in c["flops"].items()}
        out.append(f"{c['card']:<16} {c['compute_capability']:<5} {c['sms']:>4} {c['ghz']:>6g} {c['dram_gbps']:>6g} "
                   f"{rate['f32']:>6} {rate.get('tensor_f16', '-'):>10} {rate.get('tensor_f8', '-'):>9}  "
                   f"{c['device']}")  # fmt: skip
    out.append("NVIDIA's published figures (a specification, dense tensor rates in TFLOPS), with the launch, latency and "
               "efficiency each card assumes; no card was measured. --format json gives every figure.")  # fmt: skip
    return "\n".join(out)


def default() -> Profile:
    """The profile this package ships for the machine it runs on: measured where one exists, else the closest.

    `CAIRN_PROFILE` names another file. Without a match the reference machine's profile is used and says so."""
    import os

    chosen = os.environ.get("CAIRN_PROFILE")
    if chosen:
        return Profile.load(Path(chosen))
    host = packaged("zen4-7800x3d")
    if platform.machine() not in {"x86_64", "AMD64"}:
        host.notes = f"Measured on another machine ({host.name}); calibrate this one for its own numbers."
    return host

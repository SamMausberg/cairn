"""What one machine can do, as the model needs it: bandwidth by cache level and thread count, the cost of each
kind of operation with and without vectors, and what the lane pool, a task and an allocation cost.

A profile is data. `origin` says where its numbers came from: `measured` by `cairn.perf.calibrate` on the machine it
names, or `specification` from a vendor's published figures, which no run has confirmed. The model carries the
origin into every prediction it makes from the profile.
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

    def level(self, footprint: float, threads: int) -> str:
        """Where a working set of `footprint` bytes lives: per-core levels are shared out among the threads."""
        share = footprint / max(threads, 1)
        if share <= self.cache["l1"]:
            return "l1"
        if share <= self.cache["l2"]:
            return "l2"
        return "l3" if footprint <= self.cache["l3"] else "dram"

    def blend(self, footprint: float, threads: int) -> list[tuple[str, float]]:
        """Which levels serve a working set, and in what share: all of it from the first level that holds it, or,
        when it overflows one, the part that fits from that level and the rest from the next one down."""
        caps = [("l1", self.cache["l1"] * threads), ("l2", self.cache["l2"] * threads), ("l3", self.cache["l3"])]
        for i, (level, cap) in enumerate(caps):
            if footprint <= cap:
                if i == 0:
                    return [(level, 1.0)]
                below, held = caps[i - 1]
                return [(below, held / footprint), (level, 1 - held / footprint)]
        return [("l3", caps[-1][1] / footprint), ("dram", 1 - caps[-1][1] / footprint)]

    def seconds(self, kind: str, bytes_moved: float, footprint: float, threads: int) -> float:
        """Nanoseconds to move `bytes_moved` of a `footprint`-byte working set, level by level."""
        return sum(bytes_moved * share / self.bandwidth(kind, level, threads) for level, share in
                   self.blend(footprint, threads) if share > 0)  # fmt: skip

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
    threads_per_sm: int = 1536
    shared_per_sm: int = 102400
    warp: int = 32

    def occupancy(self, registers: int, block: int = 256) -> float:
        """The resident share of an SM's threads for blocks of `block` threads using `registers` each."""
        by_threads = self.threads_per_sm // block
        by_registers = self.registers_per_sm // max(registers * block, 1)
        return min(by_threads, by_registers) * block / self.threads_per_sm


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


def device(name: str = "rtx-5070-ti") -> Device | None:
    found = packaged(name)
    return found.device

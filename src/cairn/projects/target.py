"""The one device target a build, an inspection, a tuning round, a prediction and a timing record share.

A device target is an NVIDIA compilation target, `sm_120`, `sm_120f` or `sm_120a`, kept apart from the CPU
architecture that `toolchain.py` resolves for the host pass. The number is a compute capability. No suffix is the
portable target: its code runs on every device of that major version at that minor version or later. `f` is the
family target: it adds the features a family shares, and runs on the family's devices at that minor version or
later. `a` is the arch-specific target: it adds every feature of exactly one compute capability, and runs only there.

A target is resolved once, from the command line's `--device-target`, else the manifest's `[build] device_target`,
else the one GPU `nvidia-smi` reports, which asks the driver's management library and launches nothing. The record
says which. Everything downstream receives the same object: the native command, the device inspector, tuning,
prediction's device profile, device timing and the build receipt. A result recorded for another target is refused
(`E-TARGET-MISMATCH`), never approximated, and a feature the target lacks is refused (`E-TARGET-FEATURE`).
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..compiler.tree import Diagnostic

SPELLING = re.compile(r"sm_([1-9]\d{1,2})([af]?)")  # the major version, then one digit of minor version
LEVELS = ("", "f", "a")  # what a target adds, in order: portable, family, arch-specific
VARIANTS = {"": "portable", "f": "family", "a": "arch-specific"}
FIRST = {"a": 90, "f": 100}  # the first compute capability with each suffix, as nvcc 12.9 and 13.x accept them


@dataclass(frozen=True)
class Feature:
    """What a device feature needs: a compute capability, the suffix level that adds it, and when it belongs to a
    few families rather than to every later target, which ones. `probe` is one PTX instruction only a target with
    the feature assembles, which the suite compiles to hold this table to the toolkit."""

    first: int
    level: str = ""
    families: tuple[int, ...] = ()
    probe: str = ""
    what: str = ""


# Adding a feature is a row here. The probes were assembled by ptxas of CUDA 13.2 for every target named in
# tests/tooling/test_target.py; a feature with no probe is held to the programming guide's table alone.
FEATURES = {
    "device_lanes": Feature(75, what="CUDA lanes, the runtime's reductions, scans and compaction"),
    "wmma": Feature(75, what="warp matrix multiply on f16 inputs"),
    "mma_sync": Feature(
        80,
        probe="{ .reg .f32 d<4>; .reg .b32 a<4>, b<2>; mma.sync.aligned.m16n8k16.row.col.f32.f16.f16.f32 "
        "{d0, d1, d2, d3}, {a0, a1, a2, a3}, {b0, b1}, {d0, d1, d2, d3}; }",
        what="warp matrix multiply-accumulate m16n8k16 by mma.sync, fragments loaded by ldmatrix",
    ),
    "bf16": Feature(80, probe="{ .reg .b16 a; mov.b16 a, 0; fma.rn.bf16 a, a, a, a; }", what="bf16 arithmetic"),
    "cp_async": Feature(80, probe="cp.async.commit_group;", what="asynchronous copies into shared memory"),
    "clusters": Feature(90, probe="barrier.cluster.arrive;", what="thread block clusters"),
    "tma": Feature(90, probe="cp.async.bulk.commit_group;", what="bulk asynchronous copies"),
    "wgmma": Feature(90, "a", (90,), "wgmma.fence.sync.aligned;", "warpgroup matrix multiply, sm_90a only"),
    "tcgen05": Feature(
        100,
        "f",
        (100, 103, 110),
        "tcgen05.fence::before_thread_sync;",
        "fifth-generation tensor cores and tensor memory, not on sm_120",
    ),
    "mma_f8f6f4": Feature(120, "f", (120, 121), what="warp matrix multiply on 4, 6 and 8-bit floats (kind::f8f6f4)"),
}


@dataclass(frozen=True)
class Limits:
    """What one block and one SM of a compute capability hold, from NVIDIA's published table."""

    registers_per_thread: int
    registers_per_sm: int
    threads_per_block: int
    warps_per_sm: int
    shared_per_block: int  # a block's static shared memory, and its dynamic share before it opts in to more
    shared_per_block_optin: int
    shared_per_sm: int


KIB = 1024
# CUDA C++ Programming Guide, "Technical Specifications per Compute Capability": a specification, not a
# measurement. A compute capability this table lacks has unknown limits, and whatever needs them says so.
LIMITS = {
    75: Limits(255, 65536, 1024, 32, 48 * KIB, 64 * KIB, 64 * KIB),
    80: Limits(255, 65536, 1024, 64, 48 * KIB, 163 * KIB, 164 * KIB),
    86: Limits(255, 65536, 1024, 48, 48 * KIB, 99 * KIB, 100 * KIB),
    87: Limits(255, 65536, 1024, 48, 48 * KIB, 163 * KIB, 164 * KIB),
    89: Limits(255, 65536, 1024, 48, 48 * KIB, 99 * KIB, 100 * KIB),
    90: Limits(255, 65536, 1024, 64, 48 * KIB, 227 * KIB, 228 * KIB),
    100: Limits(255, 65536, 1024, 64, 48 * KIB, 227 * KIB, 228 * KIB),
    120: Limits(255, 65536, 1024, 48, 48 * KIB, 99 * KIB, 100 * KIB),
}
LIMITS_ORIGIN = "specification: CUDA C++ Programming Guide, technical specifications per compute capability"


def refuse(code: str, message: str, **details) -> Diagnostic:
    return Diagnostic(code, message, **details)


@dataclass(frozen=True)
class DeviceTarget:
    sm: int  # the compute capability as nvcc spells it: 120 is 12.0
    suffix: str = ""  # "", "f" or "a"
    origin: str = field(default="given", compare=False)  # flag, manifest, detected (and by what), or given
    required: tuple[str, ...] = field(default=(), compare=False)  # the features the program asked of it

    @property
    def name(self) -> str:
        """The identity: what nvcc's -arch takes, ptxas reports and every record carries."""
        return f"sm_{self.sm}{self.suffix}"

    @property
    def compute_capability(self) -> str:
        return f"{self.sm // 10}.{self.sm % 10}"

    @property
    def variant(self) -> str:
        return VARIANTS[self.suffix]

    @property
    def limits(self) -> Limits | None:
        return LIMITS.get(self.sm)

    def provides(self, feature: str) -> bool:
        need = FEATURES[feature]
        if self.sm < need.first or LEVELS.index(self.suffix) < LEVELS.index(need.level):
            return False
        return not need.families or self.sm in need.families

    @property
    def features(self) -> tuple[str, ...]:
        return tuple(name for name in FEATURES if self.provides(name))

    def flags(self) -> list[str]:
        """What nvcc is given to compile for this target and nothing else: its code and its PTX."""
        return [f"-arch={self.name}"]

    def runs_on(self, capability: str) -> bool:
        """Whether this target's code runs on a device of `capability` ("12.0") without a recompile."""
        major, _, minor = capability.partition(".")
        if not (major.isdigit() and minor.isdigit()):
            return False
        sm = int(major) * 10 + int(minor)
        return sm == self.sm if self.suffix == "a" else sm // 10 == self.sm // 10 and sm >= self.sm

    def require(self, features: Iterable[str], what: str = "this program") -> DeviceTarget:
        """This target, holding `features`, or E-TARGET-FEATURE naming the first one it lacks."""
        wanted = tuple(dict.fromkeys(features))
        for name in wanted:
            if name not in FEATURES:
                raise refuse("E-TARGET-FEATURE", f"{what} asks for device feature {name!r}, which no target names; "
                             f"known: {', '.join(FEATURES)}.")  # fmt: skip
            if not self.provides(name):
                raise refuse("E-TARGET-FEATURE", f"{what} needs {name} ({FEATURES[name].what}), which "
                             f"{self.name} does not provide; {needs(name)}.", target=self.name, feature=name)  # fmt: skip
        return DeviceTarget(self.sm, self.suffix, self.origin, tuple(dict.fromkeys([*self.required, *wanted])))

    def accept(self, recorded: Any, what: str) -> None:
        """Refuse a result recorded for another target: `recorded` is a target record, a target's name, or a
        record that carries one under `device_target`. A result that names no target is refused too."""
        named = recorded
        if isinstance(named, Mapping):
            named = named.get("device_target", named)
        if isinstance(named, Mapping):
            named = named.get("name")
        if named != self.name:
            shown = f"for {named}" if named else "without a device target"
            raise refuse("E-TARGET-MISMATCH", f"{what} was recorded {shown}, and this run is for {self.name}; a "
                         "result is never carried across targets. Record it again for this one.",
                         target=self.name, recorded=named)  # fmt: skip

    def fits(self, card: Mapping[str, Any], what: str) -> None:
        """Refuse a device profile this target's code does not run on, or one whose figures were measured for
        another target: `card` is a profile's `device` table, with its `compute_capability` and, once measured,
        its `target`."""
        capability = str(card.get("compute_capability") or "")
        if not self.runs_on(capability):
            shown = capability or "unknown"
            raise refuse("E-TARGET-MISMATCH", f"{what} describes a device of compute capability {shown}, where code "
                         f"for {self.name} does not run; price it with a profile of a device it runs on.",
                         target=self.name, recorded=capability)  # fmt: skip
        if card.get("target"):
            self.accept(card["target"], what)

    def record(self) -> dict[str, Any]:
        """What a receipt, an inspection, a timing or a candidate record says about the target it was made for."""
        limits = self.limits
        return {
            "name": self.name,
            "compute_capability": self.compute_capability,
            "variant": self.variant,
            "origin": self.origin,
            "features": list(self.features),
            "required_features": list(self.required),
            "limits": limits.__dict__ if limits else None,
            "limits_origin": LIMITS_ORIGIN if limits else f"unknown: no published row for {self.name} here",
            "toolkit": toolkit_record(),
        }


def needs(feature: str) -> str:
    """Which targets provide `feature`, as a person writes them."""
    need = FEATURES[feature]
    suffixes = LEVELS[LEVELS.index(need.level) :]
    if need.families:
        names = [f"sm_{sm}{s}" for sm in need.families for s in suffixes if s]
        return "it needs " + " or ".join(names if need.level else [f"sm_{sm}" for sm in need.families])
    return f"it needs sm_{need.first} or later" + (
        f" with the {'/'.join(s for s in suffixes)} suffix" if need.level else ""
    )


def parse(spelling: Any, origin: str = "given") -> DeviceTarget:
    """`sm_120`, `sm_120f` or `sm_120a`, or E-TARGET. This is the spelling alone; whether the installed toolkit
    builds it is `supported`."""
    found = SPELLING.fullmatch(spelling) if isinstance(spelling, str) else None
    if not found:
        raise refuse("E-TARGET", f"A device target is written sm_ and a compute capability, with an optional f or a "
                     f"suffix, as sm_120, sm_120f or sm_120a, not {spelling!r}. It is not the CPU architecture.")  # fmt: skip
    sm, suffix = int(found.group(1)), found.group(2)
    if suffix and sm < FIRST[suffix]:
        raise refuse("E-TARGET", f"sm_{sm}{suffix}: the {VARIANTS[suffix]} suffix {suffix} starts at "
                     f"sm_{FIRST[suffix]}.")  # fmt: skip
    return DeviceTarget(sm, suffix, origin)


@functools.cache
def toolkit() -> dict[str, Any] | None:
    """The installed nvcc: its path, its release line and the compute capabilities it compiles; None without one."""
    nvcc = shutil.which("nvcc")
    if not nvcc:
        return None
    said = subprocess.run([nvcc, "--version"], capture_output=True, text=True, timeout=120).stdout
    codes = subprocess.run([nvcc, "--list-gpu-code"], capture_output=True, text=True, timeout=120).stdout
    release = re.search(r"release (\d+\.\d+), (V[\d.]+)", said)
    return {
        "nvcc": nvcc,
        "release": release.group(1) if release else "unknown",
        "version": release.group(2) if release else "unknown",
        "compiles": sorted({int(m) for m in re.findall(r"sm_(\d+)", codes)}),
    }


def toolkit_record() -> dict[str, Any] | None:
    found = toolkit()
    return {k: v for k, v in found.items() if k != "compiles"} if found else None


def supported(target: DeviceTarget) -> DeviceTarget:
    """`target`, when the installed nvcc compiles it, or E-TARGET-TOOLKIT."""
    found = toolkit()
    if found is None:
        raise refuse("E-TARGET-TOOLKIT", f"Building for {target.name} needs CUDA's nvcc, which is absent. Nothing "
                     "was downloaded.")  # fmt: skip
    if target.sm not in found["compiles"]:
        listed = ", ".join(f"sm_{sm}" for sm in found["compiles"])
        raise refuse("E-TARGET-TOOLKIT", f"nvcc {found['release']} does not compile {target.name}; it compiles "
                     f"{listed}.", target=target.name)  # fmt: skip
    return target


@functools.cache
def detect() -> tuple[str, str] | None:
    """The compute capability and name of the one GPU the driver reports, asked through nvidia-smi, which launches
    nothing; None when there is no nvidia-smi or no GPU. Several GPUs of different capabilities are E-TARGET."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        done = subprocess.run([smi, "--query-gpu=compute_cap,name", "--format=csv,noheader"], capture_output=True,
                              text=True, timeout=60)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    cells = [[part.strip() for part in line.split(",", 1)] for line in done.stdout.splitlines()]
    rows = [(row[0], row[1]) for row in cells if len(row) == 2 and re.fullmatch(r"\d+\.\d", row[0])]
    if done.returncode or not rows:
        return None
    if len({capability for capability, _ in rows}) > 1:
        shown = ", ".join(f"{name} ({capability})" for capability, name in rows)
        raise refuse("E-TARGET", f"This machine has GPUs of different compute capabilities: {shown}. Name the "
                     "device target with --device-target or [build] device_target.")  # fmt: skip
    return rows[0]


def resolve(flag: str | None = None, manifest: str | None = None, required: bool = True) -> DeviceTarget | None:
    """The device target of a run: `flag` (--device-target), else `manifest` ([build] device_target), else the GPU
    detected here. With nothing to go on, E-TARGET when `required`, else None."""
    if flag:
        return parse(flag, "flag")
    if manifest:
        return parse(manifest, "manifest")
    found = detect()
    if found:
        capability, name = found
        major, minor = capability.split(".")
        return parse(f"sm_{major}{minor}", f"detected: nvidia-smi reports compute capability {capability} for {name}")
    if required:
        raise refuse("E-TARGET", "No device target: name one with --device-target or [build] device_target, as "
                     "sm_120; no GPU was detected here to take it from.")  # fmt: skip
    return None

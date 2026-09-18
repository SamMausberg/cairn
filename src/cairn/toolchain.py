"""The single owner of native compilers, flags and architecture profiles.

Manifests and models never choose commands or flags; they choose a named
profile that this table resolves. Nothing is downloaded. Cross compilation is
not offered: an architecture outside the host family is rejected, not guessed.
"""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

FAMILIES = {"x86-64": ("x86-64", "x86-64-v2", "x86-64-v3", "x86-64-v4"), "armv8-a": ("armv8-a", "armv8.2-a", "armv9-a")}
MACHINES = {"x86_64": "x86-64", "AMD64": "x86-64", "aarch64": "armv8-a", "arm64": "armv8-a"}
ARCHS = {"baseline", *(arch for family in FAMILIES.values() for arch in family)}
KINDS = {"library", "exe"}

# Strict floating point and no unwinding are part of the language contract.
STRICT = ["-std=c++20", "-O3", "-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]
WARNINGS += ["-Wno-unused-but-set-variable"]

# Freestanding images: no C library, no C++ runtime, no start files, no dynamic loader, no unwinder.
# -Wno-unused-command-line-argument keeps -Werror from failing on the C++ options of the .S job.
BARE = ["-DCAIRN_FREESTANDING=1", "-ffreestanding", "-nostdlib", "-static", "-fno-stack-protector"]
BARE += ["-fno-threadsafe-statics", "-fno-PIC", "-fno-PIE", "-fno-unwind-tables"]
BARE += ["-fno-asynchronous-unwind-tables", "-Wl,--build-id=none", "-Wno-unused-command-line-argument"]
TARGET_ROOT = Path(__file__).parent / "targets"
# A target names a host family, a -march profile, extra flags, and the one command that runs its image.
# Adding a target is this row plus a targets/<name>/ directory holding start.S and link.ld.
TARGETS = {
    "hosted": {},
    "aarch64-virt": {
        "family": "armv8-a",
        "arch": "armv8-a",
        # The image runs with the MMU off, so every access is Device memory: never emit an unaligned one.
        "flags": ["-mstrict-align"],
        "run": ["qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72", "-nographic", "-semihosting", "-kernel"],
    },
}
# Effects a hosted runtime provides and a freestanding image does not.
HOSTED_EFFECTS = {"alloc", "free", "io"}
HOSTED_FAMILIES = ("gpu_", "transfer:", "par:", "ffi:")


class ProjectError(ValueError):
    """A malformed or unsafe project or host request, not a compiler proof failure."""


def host_family() -> str:
    if platform.system() != "Linux" or platform.machine() not in MACHINES:
        raise ProjectError("Native builds support Linux on x86-64 or AArch64 hosts only.")
    return MACHINES[platform.machine()]


def resolve_arch(arch: str | None = None) -> str:
    """Map a manifest/CLI architecture name to a concrete -march value for this host."""
    family = host_family()
    if arch in (None, "baseline"):
        return family
    if arch not in ARCHS:
        raise ProjectError(f"Unknown CPU architecture {arch!r}.")
    if arch not in FAMILIES[family]:
        raise ProjectError(f"{arch} is not a {family} profile; cross compilation is not supported.")
    return arch


def profile(target: str | None) -> dict:
    """Resolve a build target. 'hosted' is the native host; anything else is a freestanding image."""
    if target is None:
        target = "hosted"
    if target not in TARGETS:
        raise ProjectError(f"Unknown build target {target!r}; known targets are {', '.join(sorted(TARGETS))}.")
    spec = TARGETS[target]
    if spec and spec["family"] != host_family():
        raise ProjectError(f"{target} needs an {spec['family']} host; cross compilation is not supported.")
    return spec


def audit_effects(functions: dict) -> None:
    """A freestanding image has no allocator, scheduler, operating system or foreign library behind it."""
    for name, row in sorted(functions.items()):
        for effect in sorted(row.get("effects", ())):
            if effect in HOSTED_EFFECTS or effect.startswith(HOSTED_FAMILIES):
                raise ProjectError(f"A freestanding target has no hosted runtime: {name} has effect {effect!r}.")


def emulator(target: str | None, artifact: str) -> list[str] | None:
    """The one command that runs a freestanding image here; a hosted artifact runs natively instead."""
    spec = profile(target)
    if not spec:
        return None
    machine = shutil.which(spec["run"][0])
    if not machine:
        raise ProjectError(f"A {target} image runs under {spec['run'][0]}, which is absent. Nothing was downloaded.")
    return [machine, *spec["run"][1:], artifact]


def flags(arch: str | None = None, kind: str = "library", target: str | None = None) -> list[str]:
    if kind not in KINDS:
        raise ProjectError("Unsupported build kind.")
    spec = profile(target)
    if spec:
        return [*STRICT, *WARNINGS, *BARE, *spec["flags"], "-march=" + resolve_arch(spec["arch"])]
    shared = ["-shared", "-fPIC"] if kind == "library" else []
    return [*STRICT, *WARNINGS, "-march=" + resolve_arch(arch), *shared]


def command(
    cxx: str,
    source: str,
    artifact: str,
    arch: str | None = None,
    kind: str = "library",
    cuda=False,
    target: str | None = None,
):
    """The one native command line. Device programs go through nvcc with the same host contract."""
    if profile(target):
        start = TARGET_ROOT / str(target)
        script, boot = start / "link.ld", start / "start.S"
        return [find(cxx), *flags(arch, kind, target), f"-Wl,-T,{script}", source, str(boot), "-o", artifact]
    if not cuda:
        return [find(cxx), *flags(arch, kind), source, "-o", artifact]
    host = [f for f in flags(arch, kind) if not f.startswith(("-std", "-O", "-shared"))]
    # --fmad=false is the device half of -ffp-contract=off; relaxed constexpr lets guards use <limits>.
    device = ["-std=c++20", "-O3", "--fmad=false", "-arch=native", "--extended-lambda", "--expt-relaxed-constexpr"]
    shared = ["-shared"] if kind == "library" else []
    return [find("nvcc"), *device, "-Werror", "all-warnings", "-ccbin", find(cxx), "-x", "cu", *shared,
            "-Xcompiler", ",".join(host), source, "-o", artifact]  # fmt: skip


def unit_commands(cxx: str, arch: str | None, kind: str) -> tuple[list[str], list[str]]:
    """Per-module objects: the compile prefix (`... -c unit -o object`) and the link prefix (`... objects -o artifact`).
    The flags are the single-unit ones; what is lost is inlining across modules, which is why this is opt-in."""
    every = flags(arch, kind)
    return [find(cxx), *(f for f in every if f != "-shared"), "-c"], [find(cxx), *every]


def find(compiler: str) -> str:
    path = shutil.which(compiler)
    if not path:
        raise ProjectError(f"Native compiler unavailable: {compiler}. Nothing was downloaded.")
    return path

"""The single owner of native compilers, flags and architecture profiles.

Manifests and models never choose commands or flags; they choose a named
profile that this table resolves. Nothing is downloaded. Cross compilation is
not offered: an architecture outside the host family is rejected, not guessed.
"""

from __future__ import annotations

import platform
import shutil

FAMILIES = {"x86-64": ("x86-64", "x86-64-v2", "x86-64-v3", "x86-64-v4"), "armv8-a": ("armv8-a", "armv8.2-a", "armv9-a")}
MACHINES = {"x86_64": "x86-64", "AMD64": "x86-64", "aarch64": "armv8-a", "arm64": "armv8-a"}
ARCHS = {"baseline", *(arch for family in FAMILIES.values() for arch in family)}
KINDS = {"library", "exe"}

# Strict floating point and no unwinding are part of the language contract.
STRICT = ["-std=c++20", "-O3", "-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]
WARNINGS += ["-Wno-unused-but-set-variable"]


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


def flags(arch: str | None = None, kind: str = "library") -> list[str]:
    if kind not in KINDS:
        raise ProjectError("Unsupported build kind.")
    shared = ["-shared", "-fPIC"] if kind == "library" else []
    return [*STRICT, *WARNINGS, "-march=" + resolve_arch(arch), *shared]


def find(compiler: str) -> str:
    path = shutil.which(compiler)
    if not path:
        raise ProjectError(f"Native compiler unavailable: {compiler}. Nothing was downloaded.")
    return path

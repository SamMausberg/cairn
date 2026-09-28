"""The single owner of native compilers, flags and architecture profiles.

Manifests and models never choose commands or flags; they choose a named
profile that this table resolves. Nothing is downloaded. Cross compilation is
not offered: an architecture outside the host family is rejected, not guessed.
"""

from __future__ import annotations

import functools
import os
import platform
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .target import DeviceTarget, resolve, supported

FAMILIES = {"x86-64": ("x86-64", "x86-64-v2", "x86-64-v3", "x86-64-v4"), "armv8-a": ("armv8-a", "armv8.2-a", "armv9-a")}
MACHINES = {"x86_64": "x86-64", "AMD64": "x86-64", "aarch64": "armv8-a", "arm64": "armv8-a"}
ARCHS = {"baseline", *(arch for family in FAMILIES.values() for arch in family)}
KINDS = {"library", "exe"}

# Strict floating point and no unwinding are part of the language contract.
STRICT = ["-std=c++20", "-O3", "-ffp-contract=off", "-fno-fast-math", "-fno-exceptions", "-fno-rtti"]
WARNINGS = ["-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable"]
WARNINGS += ["-Wno-unused-but-set-variable"]
# nvcc's own front end makes every warning an error too, but for the two WARNINGS lets pass, since CAIRN accepts a
# local the program never reads: one declared and never read (177), and one set and never read (550).
DEVICE_WARNINGS = ["-Werror", "all-warnings", "-diag-suppress", "177,550"]
# `--sanitize`: a host build checked while it runs, as the 1.1 evaluation judged programs: -O1 with frame pointers so a
# report has its stack, every report fatal, and no -Werror, since such a build is run, never shipped.
SANITIZERS = {"address": ["-fsanitize=address,undefined", "-fno-sanitize-recover=all"], "thread": ["-fsanitize=thread"]}
SANITIZED = ["-O1", "-g", "-fno-omit-frame-pointer"]
# What each sanitizer reads from the environment of the program it checks: leaks count, and the first race ends it.
SANITIZER_ENVIRONMENT = {
    "address": {"ASAN_OPTIONS": "detect_leaks=1", "UBSAN_OPTIONS": "print_stacktrace=1"},
    "thread": {"TSAN_OPTIONS": "halt_on_error=1"},
}
# What `cairn explain` adds to a host build to read clang's vectorizer verdicts, with the `.cairn` line of each loop.
REMARKS = ["-gline-tables-only", "-fsave-optimization-record", "-foptimization-record-passes=loop-vectorize"]

# Freestanding images: no C library, no C++ runtime, no start files, no dynamic loader, no unwinder.
# -Wno-unused-command-line-argument keeps -Werror from failing on the C++ options of the .S job.
BARE = ["-DCAIRN_FREESTANDING=1", "-ffreestanding", "-nostdlib", "-static", "-fno-stack-protector"]
BARE += ["-fno-threadsafe-statics", "-fno-PIC", "-fno-PIE", "-fno-unwind-tables"]
BARE += ["-fno-math-errno"]  # no errno exists, so sqrt is the instruction, never a libm call
BARE += ["-fno-asynchronous-unwind-tables", "-Wl,--build-id=none", "-Wno-unused-command-line-argument"]
TARGET_ROOT = Path(__file__).parents[1] / "targets"
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
# System libraries a build may link, each a closed row like a target: the flags that link it, and the packaged
# modules whose externs call it, so importing one of them links it. A manifest may name a row for its own
# externs. Nothing outside this table is linked, and the library is found where the C compiler finds it.
# Adding a library is this row.
LIBRARIES = {"z": {"flags": ["-lz"], "modules": ("std.zlib",)}}
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


def linked(named, modules) -> list[str]:
    """The libraries a build links: those its manifest names and those its imported modules call, in table order."""
    unknown = sorted(set(named) - set(LIBRARIES))
    if unknown:
        raise ProjectError(f"Unknown system library {unknown[0]!r}; known: {', '.join(sorted(LIBRARIES))}.")
    return [n for n, row in LIBRARIES.items() if n in named or set(row["modules"]) & set(modules)]


def link_flags(libraries: list[str]) -> list[str]:
    """What the linker is given for `libraries`; it comes after every source and object on the command line."""
    return [flag for name in libraries for flag in LIBRARIES[name]["flags"]]


def flags(arch: str | None = None, kind: str = "library", target: str | None = None) -> list[str]:
    if kind not in KINDS:
        raise ProjectError("Unsupported build kind.")
    spec = profile(target)
    if spec:
        return [*STRICT, *WARNINGS, *BARE, *spec["flags"], "-march=" + resolve_arch(spec["arch"])]
    shared = ["-shared", "-fPIC"] if kind == "library" else []
    return [*STRICT, *WARNINGS, "-march=" + resolve_arch(arch), *shared]


def command(cxx: str, source: str, artifact: str, arch: str | None = None, kind: str = "library", cuda=False,
            target: str | None = None, device: DeviceTarget | None = None, emulate: bool = False):  # fmt: skip
    """The one native command line. Device programs go through nvcc with the same host contract, for `device`, the
    device target the caller resolved (projects/target.py), or the one detected here when it gives none; with
    `emulate`, the host compiler builds the same program with its device work on host threads, and no nvcc runs.
    `arch` is the CPU's, for the host pass; the two are never mixed."""
    if profile(target):
        start = TARGET_ROOT / str(target)
        script, boot = start / "link.ld", start / "start.S"
        return [find(cxx), *flags(arch, kind, target), f"-Wl,-T,{script}", source, str(boot), "-o", artifact]
    if not cuda or emulate:
        return [find(cxx), *flags(arch, kind), *(emulated(source) if cuda else []), source, "-o", artifact]
    chosen = supported(device or resolve())
    return [*device_prefix(cxx, arch, kind, chosen), source, "-o", artifact]


def sanitized(command: list[str], sanitizer: str) -> list[str]:
    """A host command line checked by `sanitizer`: its flags and -O1 in place of -O3, and no -Werror."""
    return [command[0], *SANITIZED, *SANITIZERS[sanitizer], *(a for a in command[1:] if a not in {"-O3", "-Werror"})]


def emulated(source: str) -> list[str]:
    """What the host compiler is given to build a device program with its device work on host threads
    (projects/emulation.py): CAIRN_EMULATE, which leaves CUDA out of cairn_gpu.hpp, and the machine that takes its
    place, runtime/cairn_emulate.hpp beside the program, read before the program's first line. The program's own
    text is the device build's, so the `#pragma unroll` a vector region writes for nvcc is one g++ does not know."""
    return ["-Wno-unknown-pragmas", "-DCAIRN_EMULATE=1", "-include", str(Path(source).parent / "cairn_emulate.hpp")]


def device_prefix(cxx: str, arch: str | None, kind: str, device: DeviceTarget) -> list[str]:
    """nvcc and every flag of a device program's build, for `device` and nothing else, up to the source."""
    host = [f for f in flags(arch, kind) if not f.startswith(("-std", "-O", "-shared"))]
    # CCCL 3 (CUDA 13) writes unguarded throw and catch inside headers CUB's dispatch requires, so a
    # device program's host pass must parse exceptions. Nothing in the runtime throws; guards still abort.
    host = [("-fexceptions" if f == "-fno-exceptions" else f) for f in host]
    # CUDA 13's cuda_pipeline.h and its barrier helpers define static inline functions clang reports as unused when
    # it is nvcc's host compiler; g++ does not, and nothing CAIRN writes is a static function.
    host.append("-Wno-unused-function")
    shared = ["-shared"] if kind == "library" else []
    return [find("nvcc"), *nvcc_flags(device), *DEVICE_WARNINGS, "-ccbin", find(cxx), "-x", "cu", *shared,
            "-Xcompiler", ",".join(host)]  # fmt: skip


def nvcc_flags(device: DeviceTarget) -> list[str]:
    """What nvcc is given for a CAIRN program's language and numerics on `device`, before any host flag. --fmad=false
    is the device half of -ffp-contract=off; relaxed constexpr lets guards use <limits>."""
    return ["-std=c++20", "-O3", "--fmad=false", *device.flags(), "--extended-lambda", "--expt-relaxed-constexpr"]


def extension_flags(device: DeviceTarget | None) -> dict[str, list[str]]:
    """What a PyTorch extension build (torch.utils.cpp_extension, as SOL-ExecBench, GPU MODE and KernelBench run it)
    adds for a CAIRN program and its binding: the language standard and the numerical contract, on the host and, for
    `device`, in nvcc with one -arch. torch puts -std=c++17 before these, which the later -std overrides, and adds no
    architecture once a flag names one. Exceptions and RTTI stay on, as pybind11 and the binding's checks need them;
    nothing in the runtime throws. Warnings are not errors here: torch's headers are not CAIRN's to hold to them."""
    host = [f for f in STRICT if f not in {"-fno-exceptions", "-fno-rtti"}]
    if device is None:
        return {"cflags": host, "cuda_cflags": [], "ld_flags": []}
    numerics = [f for f in host if f.startswith(("-ffp-contract", "-fno-fast-math"))]
    return {"cflags": host, "cuda_cflags": [*nvcc_flags(device), "-Xcompiler", ",".join(numerics)], "ld_flags": []}


def unit_commands(cxx: str, arch: str | None, kind: str) -> tuple[list[str], list[str]]:
    """Per-module objects: the compile prefix (`... -c unit -o object`) and the link prefix (`... objects -o artifact`).
    The flags are the single-unit ones; what is lost is inlining across modules, which is why this is opt-in."""
    every = flags(arch, kind)
    return [find(cxx), *(f for f in every if f != "-shared"), "-c"], [find(cxx), *every]


def foreign_unit(cxx: str, arch: str | None, kind: str, device: DeviceTarget | None, include: Path) -> list[str]:
    """How a vendored source compiles (projects/foreign.py): the program's own flags, for `device` when it is CUDA,
    as an object, with the runtime headers at `include` on the path; the unit and `-o object` follow."""
    if device is None:
        return [*unit_commands(cxx, arch, kind)[0], f"-I{include}"]
    return [*(f for f in device_prefix(cxx, arch, kind, device) if f != "-shared"), "-c", f"-I{include}"]


def precompiled(prefix: list[str], version_text: str, header: Path) -> tuple[list[str], list[str]]:
    """How an incremental build precompiles its shared header, and what each unit then adds to its command. Clang
    reads the PCH it is named. GCC refuses `#pragma once` in the file it precompiles, so it precompiles `pch.hpp`,
    which only includes the header, and each unit includes that first. The flags are the units' own, so the header
    means what it means when a unit reads it as text; if the PCH cannot be used, the compiler reads the text."""
    if "clang" in version_text:
        pch = header.with_name(header.name + ".pch")
        return [*prefix, "-x", "c++-header", str(header), "-o", str(pch)], ["-include-pch", str(pch)]
    wrapper = header.with_name("pch.hpp")
    wrapper.write_text(f'#include "{header.name}"\n', encoding="utf-8")
    return [*prefix, "-x", "c++-header", str(wrapper), "-o", str(wrapper) + ".gch"], ["-include", str(wrapper)]


def find(compiler: str) -> str:
    path = shutil.which(compiler)
    if not path:
        raise ProjectError(f"Native compiler unavailable: {compiler}. Nothing was downloaded.")
    return path


def bounded(command: list[str], seconds: float, check: bool = False, **options) -> subprocess.CompletedProcess:
    """`command` run to its end within `seconds`, its output captured as text, in a process group of its own. At
    the limit, or on any interruption, every process in the group is killed and the error raised: a compiler
    driver's children keep its pipes open after the driver alone is killed, so killing it would not end the wait.
    A limit already reached starts nothing and raises subprocess.TimeoutExpired."""
    if seconds <= 0:
        raise subprocess.TimeoutExpired(command, seconds)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True, **options) as running:  # fmt: skip
        try:
            out, err = running.communicate(timeout=seconds)
        except BaseException:
            os.killpg(running.pid, signal.SIGKILL)
            running.communicate()
            raise
    if check and running.returncode:
        raise subprocess.CalledProcessError(running.returncode, command, out, err)
    return subprocess.CompletedProcess(command, running.returncode, out, err)


def until(deadline: float | None, most: float) -> float:
    """The seconds a step may take: `most`, or less when the monotonic `deadline` comes first."""
    return most if deadline is None else min(most, deadline - time.monotonic())


@functools.cache
def version(compiler: str) -> str:
    """What `compiler --version` prints, asked once a process: a suite of thousands of builds asked it for each.
    A cold compiler on a loaded two-core runner took more than the five seconds this once allowed, so it has two
    minutes."""
    return subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True, timeout=120).stdout


def named(cxx: str) -> str:
    """A native compiler as a record names it: the first line of its `--version`, or that it was not found. A search's
    host target and a validation name it alike, so `cairn tune` can tell whether a validation was built by its
    compiler."""
    try:
        return version(find(cxx)).splitlines()[0]
    except (ProjectError, OSError, subprocess.SubprocessError, IndexError):
        return f"{cxx} (not found)"

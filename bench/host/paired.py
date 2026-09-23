#!/usr/bin/env python3
"""Paired CAIRN/C++ timing on one pinned core, plus the object-section comparison.

Every number below was measured on the machine and CPU profile recorded in
results/timing/benchmark_environment.json. Ratios above one favour CAIRN. A ratio measured
here says nothing about another host, another compiler or an expert hand-tuned baseline.
"""

import csv
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.projects.toolchain import find
from support import best_profile, compare_sections, environment, generate, profile_flags

os.chdir(ROOT)
KERNELS = ["saxpy", "dot", "sum_wrap", "prefix", "count_gt", "histogram", "compact_even", "lower_bound", "gcd"]
ARCH = best_profile("clang++")
FLAGS = profile_flags("exe", ARCH, add=["-ffunction-sections"])
commands = []


def run(cmd, **kw):
    commands.append(cmd)
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def describe(cmd):
    """A tool's report of this machine, or a note that it is unavailable here."""
    try:
        return subprocess.run(cmd, text=True, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return f"{cmd[0]} unavailable on this host"


generate(ROOT / "examples/basics/native.cairn", ROOT / "results/native")
(ROOT / "results/timing").mkdir(parents=True, exist_ok=True)
clang = find("clang++")
for file, out in [
    ("results/native/native.cpp", "results/native/native.o"),
    ("bench/host/reference.cpp", "results/native/reference.o"),
    ("bench/host/driver.cpp", "results/native/driver.o"),
]:
    run([clang, *FLAGS, "-c", file, "-o", out])
objects = [f"results/native/{stem}.o" for stem in ("native", "reference", "driver")]
run([clang, *objects, "-o", "results/native/benchmark"])
avail = sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0, {avail[0]})
raw = run(["results/native/benchmark"]).stdout
(ROOT / "results/timing/timing_raw.csv").write_text(raw)
rows = list(csv.DictReader(raw.splitlines()))
groups = {}
for row in rows:
    groups.setdefault((row["kernel"], int(row["n"]), row["pattern"]), []).append(row)
summary = []
for (name, n, pattern), rr in groups.items():
    aa = [float(x["cairn_ns"]) for x in rr]
    bb = [float(x["cpp_ns"]) for x in rr]
    ratios = [b / a for a, b in zip(aa, bb, strict=True)]
    summary.append(
        {
            "kernel": name,
            "n": n,
            "pattern": pattern,
            "pairs": len(rr),
            "cairn_median_ns": statistics.median(aa),
            "cpp_median_ns": statistics.median(bb),
            "speed_ratio_cpp_over_cairn": statistics.median(ratios),
            "ratio_min": min(ratios),
            "ratio_max": max(ratios),
        }
    )
(ROOT / "results/timing/timing_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
equivalence = compare_sections(KERNELS, lambda cmd: run(cmd).stdout)
(ROOT / "results/timing/codegen_equivalence.json").write_text(json.dumps(equivalence, indent=2) + "\n")
(ROOT / "results/timing/benchmark_environment.json").write_text(
    json.dumps(
        {
            **environment("clang++", arch=ARCH),
            "flags": FLAGS,
            "pinned_cpu": avail[0],
            "available_cpus": avail,
            "platform": platform.platform(),
            "cpu": describe(["lscpu"]),
            "commands": commands,
            "limitations": "Shared virtualized CPU; no frequency or host-isolation control. 11 alternating paired rounds per case, no LTO. Two integer-input patterns: LCG low-bit alternating parity and high-bit-mixed parity. Same FFI entry checks; C++ inner operations rely on algorithm invariants. Ratios above one favour CAIRN. Not expert hand-tuned CPU/GPU baselines. Timings and identical sections hold for this host and profile only.",
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps(summary, indent=2))
print(
    "identical_function_sections",
    sum(x["bytes_equal"] and x["relocations_equal"] for x in equivalence),
    "/",
    len(equivalence),
    "on",
    platform.machine(),
    ARCH,
)

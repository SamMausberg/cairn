#!/usr/bin/env python3
"""Rerun object-section comparisons only; does not measure execution speed.

One compiler and one flag profile, chosen for THIS host and recorded in the result.
A section is identical only if its bytes and its relocations both match.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.projects.toolchain import find
from support import REFERENCE_FUNCTIONS, best_profile, compare_sections, generate, profile_flags

os.chdir(ROOT)
ARCH = best_profile("clang++")
# -ffunction-sections is what makes one function one comparable section.
FLAGS = profile_flags("exe", ARCH, add=["-ffunction-sections"])
commands = []


def run(cmd):
    commands.append(cmd)
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout


generate(ROOT / "examples/basics/native.cairn", ROOT / "results/native")
(ROOT / "results/codegen").mkdir(parents=True, exist_ok=True)
for src, out in [
    ("results/native/native.cpp", "results/native/native.o"),
    ("bench/host/reference.cpp", "results/native/reference.o"),
]:
    run([find("clang++"), *FLAGS, "-c", src, "-o", out])
rows = compare_sections(REFERENCE_FUNCTIONS, run)
identical = sum(x["bytes_equal"] and x["relocations_equal"] for x in rows)
result = {
    "generated_source_sha256": hashlib.sha256((ROOT / "results/native/native.cpp").read_bytes()).hexdigest(),
    "runtime_sha256": hashlib.sha256((ROOT / "results/native/cairn_runtime.hpp").read_bytes()).hexdigest(),
    "reference_sha256": hashlib.sha256((ROOT / "bench/host/reference.cpp").read_bytes()).hexdigest(),
    "comparisons": rows,
    "identical_sections": identical,
    "sections": len(rows),
    "compiler": run([find("clang++"), "--version"]),
    "arch_profile": ARCH,
    "flags": FLAGS,
    "commands": commands,
    "platform": platform.platform(),
    "native_timing_performed": False,
    "boundary": "Ordinary C++ reference algorithms with equal entry guards, not expert baselines or a native-correctness proof. "
    "Section equality is a per-host, per-compiler, per-profile observation and does not carry to another machine.",
}
(ROOT / "results/codegen/codegen.json").write_text(json.dumps(result, indent=2) + "\n")
print("Identical function sections and relocations:", identical, "of", len(rows), "on", platform.machine(), ARCH)

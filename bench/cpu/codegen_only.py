#!/usr/bin/env python3
"""Rerun object-section comparisons only; does not measure execution speed.

One compiler and one flag profile, chosen for THIS host and recorded in the result.
A section is identical only if its bytes and its relocations both match.
"""

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.projects.toolchain import find
from support import best_profile, generate, profile_flags

os.chdir(ROOT)
NAMES = ["saxpy", "dot", "sum_wrap", "prefix", "count_gt", "histogram", "compact_even", "lower_bound", "gcd"]
ARCH = best_profile("clang++")
# -ffunction-sections is what makes one function one comparable section.
FLAGS = profile_flags("exe", ARCH, add=["-ffunction-sections"])
commands = []


def run(cmd):
    commands.append(cmd)
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout


generate(ROOT / "examples/basics/native.cairn", ROOT / "results")
for src, out in [("results/native.cpp", "results/native.o"), ("bench/cpu/reference.cpp", "results/reference.o")]:
    run([find("clang++"), *FLAGS, "-c", src, "-o", out])
rows = []
for name in NAMES:
    data = []
    rels = []
    for prefix, obj in [("cf", "native"), ("cc", "reference")]:
        out = f"results/{obj}_{name}.bin"
        sec = f".text.{prefix}_{name}"
        run(["objcopy", f"--dump-section={sec}={out}", f"results/{obj}.o"])
        data.append((ROOT / out).read_bytes())
        entries = []
        for line in run(["objdump", "-r", "-j", sec, f"results/{obj}.o"]).splitlines():
            m = re.match(r"^([0-9a-f]+)\s+(R_\S+)\s+(\S+)", line)
            if m:
                entries.append(tuple(x.replace("cf_", "FUNC_").replace("cc_", "FUNC_") for x in m.groups()))
        rels.append(entries)
    rows.append(
        {
            "function": name,
            "cairn_bytes": len(data[0]),
            "cpp_bytes": len(data[1]),
            "bytes_equal": data[0] == data[1],
            "relocations_equal": rels[0] == rels[1],
            "cairn_sha256": hashlib.sha256(data[0]).hexdigest(),
            "cpp_sha256": hashlib.sha256(data[1]).hexdigest(),
            "relocations": rels,
        }
    )
identical = sum(x["bytes_equal"] and x["relocations_equal"] for x in rows)
result = {
    "generated_source_sha256": hashlib.sha256((ROOT / "results/native.cpp").read_bytes()).hexdigest(),
    "runtime_sha256": hashlib.sha256((ROOT / "results/cairn_runtime.hpp").read_bytes()).hexdigest(),
    "reference_sha256": hashlib.sha256((ROOT / "bench/cpu/reference.cpp").read_bytes()).hexdigest(),
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
(ROOT / "results/codegen.json").write_text(json.dumps(result, indent=2) + "\n")
print("Identical function sections and relocations:", identical, "of", len(rows), "on", platform.machine(), ARCH)

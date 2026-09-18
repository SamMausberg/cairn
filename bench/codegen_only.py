#!/usr/bin/env python3
"""Rerun object-section comparisons only; does not measure execution speed."""

import hashlib
import json
import os
import platform
import re
import subprocess
from pathlib import Path

R = Path(__file__).resolve().parents[1]
os.chdir(R)
FLAGS = [
    "-std=c++20",
    "-O3",
    "-march=x86-64-v3",
    "-ffp-contract=off",
    "-fno-fast-math",
    "-fno-exceptions",
    "-fno-rtti",
    "-ffunction-sections",
    "-Wall",
    "-Wextra",
    "-Werror",
]
commands = []


def run(cmd):
    commands.append(cmd)
    return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout


for src, out in [
    ("results/native.cpp", "results/native.o"),
    ("bench/reference.cpp", "results/reference.o"),
]:
    run(["clang++", *FLAGS, "-c", src, "-o", out])
rows = []
for name in [
    "saxpy",
    "dot",
    "sum_wrap",
    "prefix",
    "count_gt",
    "histogram",
    "compact_even",
    "lower_bound",
    "gcd",
]:
    data = []
    rels = []
    for prefix, obj in [("cf", "native"), ("cc", "reference")]:
        out = f"results/{obj}_{name}.bin"
        sec = f".text.{prefix}_{name}"
        run(["objcopy", f"--dump-section={sec}={out}", f"results/{obj}.o"])
        data.append((R / out).read_bytes())
        entries = []
        for line in run(["objdump", "-r", "-j", sec, f"results/{obj}.o"]).splitlines():
            m = re.match(r"^([0-9a-f]+)\s+(R_\S+)\s+(\S+)", line)
            if m:
                entries.append(
                    tuple(x.replace("cf_", "FUNC_").replace("cc_", "FUNC_") for x in m.groups())
                )
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
result = {
    "generated_source_sha256": hashlib.sha256((R / "results/native.cpp").read_bytes()).hexdigest(),
    "runtime_sha256": hashlib.sha256((R / "results/cairn_runtime.hpp").read_bytes()).hexdigest(),
    "reference_sha256": hashlib.sha256((R / "bench/reference.cpp").read_bytes()).hexdigest(),
    "comparisons": rows,
    "compiler": run(["clang++", "--version"]),
    "flags": FLAGS,
    "commands": commands,
    "platform": platform.platform(),
    "native_timing_performed": False,
    "boundary": "Ordinary C++ reference algorithms with equal entry guards, not expert baselines or a native-correctness proof.",
}
(R / "results/codegen.json").write_text(json.dumps(result, indent=2) + "\n")
print(
    "Identical function sections and relocations:",
    sum(x["bytes_equal"] and x["relocations_equal"] for x in rows),
    "of",
    len(rows),
)

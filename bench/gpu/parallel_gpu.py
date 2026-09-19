#!/usr/bin/env python3
"""Compile and run bench/gpu/parallel_gpu.cu and record the result with its environment.

Writes results/gpu/benchmark.json. Refuses to write anything if nvcc or a device is
missing: an unmeasured benchmark is worse than none. A release copy of this record under
evidence/ is the release collector's to make, never this benchmark's to overwrite.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from support import best_profile, profile_flags

RUNTIME = ROOT / "src/cairn/runtime"
OUT = ROOT / "results/gpu/benchmark.json"

STRICT = ["-std=c++20", "-O3"]
# nvcc owns -std/-O for both halves; the rest of the host contract is the compiler's own table,
# except that CCCL 3 (CUDA 13) needs the host pass to parse exceptions; see cairn_gpu.hpp's note.
HOST = [f for f in profile_flags("exe", best_profile("g++")) if not f.startswith(("-std", "-O"))]
HOST = [("-fexceptions" if f == "-fno-exceptions" else f) for f in HOST]
DEVICE = ["--fmad=false", "-arch=native", "--extended-lambda", "--expt-relaxed-constexpr"]
DEVICE += ["-Werror", "all-warnings"]


def version(command: list[str], pick: str = "") -> str:
    """The first line of a tool's own version output, or the first line containing `pick`."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    lines = [line.strip() for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    if not lines:
        return "unavailable"
    return next((line for line in lines if pick and pick in line), lines[0])


def main() -> int:
    if not shutil.which("nvcc"):
        print("nvcc is not installed: nothing was measured", file=sys.stderr)
        return 1
    build = ROOT / "results/gpu/bench_parallel_gpu"
    build.parent.mkdir(parents=True, exist_ok=True)
    command = ["nvcc", *STRICT, *DEVICE, "-Xcompiler", ",".join(HOST)]
    command += [f"-I{RUNTIME}", str(ROOT / "bench/gpu/parallel_gpu.cu"), "-o", str(build)]
    subprocess.run(command, check=True)
    measured = json.loads(subprocess.run([str(build)], capture_output=True, text=True, check=True).stdout)
    smi = ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
    measured["environment"] = {
        "host": f"{platform.system()} {platform.machine()} {platform.release()}",
        "cpu_threads": len(os.sched_getaffinity(0)),
        "gpu_query": version(smi),
        "nvcc": version(["nvcc", "--version"], "release"),
        "host_compiler": version(["g++", "--version"]),
        "nvcc_flags": command[1:-3],
        "host_flags_via_Xcompiler": HOST,
        "note": (
            "Host buffers are pageable std::vector, as generated CAIRN host views are; pinned "
            "staging would shorten the transfer half of the end to end numbers."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(measured, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

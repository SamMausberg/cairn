#!/usr/bin/env python3
"""Compile and run bench/host_regions.cpp and record the result with its environment.

Writes evidence/v1_2/host_regions/benchmark.json. A run is filed under a label and merged into
whatever is already there, so a before and an after measured on the same machine with the same
flags sit side by side in one file. --runtime points at a directory of runtime headers, which is
how the "before" row is taken: check the older headers out of git into a directory of their own.
Nothing is written unless every case agreed with its sequential result.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from support import best_profile, profile_flags, version

OUT = ROOT / "evidence/v1_2/host_regions/benchmark.json"
COMPILERS = ("g++", "clang++")


def measure(cxx: str, runtime: Path, arch: str, build: Path) -> dict:
    """Build the benchmark against one set of headers with the project's own flags, then run it."""
    exe = build / f"host_regions_{cxx.replace('+', 'p')}"
    line = [cxx, *profile_flags("exe", arch), f"-I{runtime}", str(ROOT / "bench/host_regions.cpp"), "-o", str(exe)]
    made = subprocess.run(line, capture_output=True, text=True)
    if made.returncode != 0 or made.stderr.strip():
        raise SystemExit(f"{cxx} did not build the benchmark cleanly:\n{made.stderr[-4000:]}")
    done = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    measured = json.loads(done.stdout)
    if not measured["every_case_agreed_with_the_sequential_result"]:
        raise SystemExit(f"{cxx}: a case disagreed with its sequential result; nothing was recorded")
    measured["compiler"] = version(cxx).splitlines()[0]
    measured["flags"] = line[1:-4]
    return measured


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__)
    ask.add_argument(
        "--runtime", type=Path, default=ROOT / "src/cairn/runtime", help="Runtime headers to build against."
    )
    ask.add_argument("--label", default="lane_pool", help="What this run measures, e.g. per_statement_threads.")
    ask.add_argument("--out", type=Path, default=OUT)
    ask.add_argument("--note", default="", help="One sentence recorded beside the numbers.")
    args = ask.parse_args()

    arch = best_profile(*COMPILERS)
    build = ROOT / "results/bench_host_regions"
    build.mkdir(parents=True, exist_ok=True)
    runs = {cxx: measure(cxx, args.runtime.resolve(), arch, build) for cxx in COMPILERS}
    row = {
        "note": args.note,
        "runtime_headers": str(args.runtime.resolve()),
        "runtime_header_sha256": subprocess.run(
            ["sha256sum", str(args.runtime.resolve() / "cairn_parallel.hpp")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0],
        "by_compiler": runs,
    }
    whole = json.loads(args.out.read_text()) if args.out.exists() else {}
    whole.setdefault("runs", {})[args.label] = row
    whole["environment"] = {
        "host": f"{platform.system()} {platform.machine()} {platform.release()}",
        "cpu_threads": len(os.sched_getaffinity(0)),
        "arch_profile": arch,
        "cpus_were_not_pinned": True,  # a region wants every core; pinning would measure one
        "commit": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(whole, indent=2) + "\n")
    print(f"wrote {args.label} to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

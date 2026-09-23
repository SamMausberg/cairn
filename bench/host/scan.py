#!/usr/bin/env python3
"""Time the host scan against the loop it replaces, and the radix sort against the heapsort.

bench/host/scan.cairn does the timing inside one process, interleaved; this builds it the way `cairn build`
does, under each compiler, runs it, and writes the median of every kernel at every size to --out. The times
are wall clock on whatever else the machine is doing, so a record says what the machine was doing.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from cairn.projects.build import build
from cairn.projects.project import load_project


def measure(cxx: str, reps: int, work: Path) -> dict:
    source = (ROOT / "bench/host/scan.cairn").read_text().replace("REPS:usize = 15", f"REPS:usize = {reps}")
    work.mkdir(parents=True, exist_ok=True)
    (work / "scan.cairn").write_text(source)
    record = build(load_project(work / "scan.cairn"), kind="exe", cxx=cxx, timeout=300)
    if record["status"] != "native-built":
        raise SystemExit(record.get("stderr", "")[:4000])
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=3600)
    if done.returncode:
        raise SystemExit(f"the benchmark exited {done.returncode}: {done.stderr[-2000:]}")
    samples: dict[str, dict[int, list[int]]] = {}
    for row in done.stdout.split("\n"):
        if row and not row.startswith("checksum"):
            kernel, n, ns = row.split()
            samples.setdefault(kernel, {}).setdefault(int(n), []).append(int(ns))
    return {
        kernel: {
            str(n): {"median_ns": statistics.median(v), "min_ns": min(v), "runs": len(v)} for n, v in sizes.items()
        }
        for kernel, sizes in samples.items()
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--reps", type=int, default=15)
    p.add_argument("--note", default="", help="What else the machine was doing, recorded with the times.")
    a = p.parse_args()
    lanes = os.environ.get("CAIRN_LANES", "")
    result = {
        "schema": "cairn.bench.scan/1",
        "machine": {"platform": platform.platform(), "cpus": os.cpu_count(), "lanes": lanes or "hardware"},
        "note": a.note,
        "reps": a.reps,
        "compilers": {cxx: measure(cxx, a.reps, ROOT / "results/bench_scan" / cxx) for cxx in ("clang++", "g++")},
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["compilers"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

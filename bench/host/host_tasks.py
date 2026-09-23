#!/usr/bin/env python3
"""Compile and run bench/host/host_tasks.cpp against one or two sets of runtime headers, interleaved.

Writes evidence/v1_0/runtime/benchmark.json. `--before DIR` names a directory of older headers (check them out of
git into a directory of their own); the two builds then run in alternation, `--runs` times each, so load that
comes and goes on a shared machine falls on both. Each run reports the median of its own blocks, and a session
keeps every run, the median across them and the load average around it. A session is filed under `--label` and
merged into what the file already holds. Nothing is written unless every case agreed with its own result.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from support import best_profile, profile_flags, version

OUT = ROOT / "evidence/v1_0/runtime/benchmark.json"


def compile_against(cxx: str, runtime: Path, arch: str, exe: Path) -> list[str]:
    line = [cxx, *profile_flags("exe", arch), "-pthread", f"-I{runtime}", str(ROOT / "bench/host/host_tasks.cpp")]
    made = subprocess.run([*line, "-o", str(exe)], capture_output=True, text=True)
    if made.returncode != 0 or made.stderr.strip():
        raise SystemExit(f"{cxx} did not build the benchmark cleanly against {runtime}:\n{made.stderr[-4000:]}")
    return line[1:-2]


def run(exe: Path) -> dict:
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
    measured = json.loads(done.stdout)
    if done.returncode != 0 or not measured["every_case_agreed"]:
        raise SystemExit(f"{exe.name}: a case disagreed with its own result; nothing was recorded")
    return {"ns": measured["cases"], "threads_started": measured["threads_started"]}  # -1: the runtime keeps no count


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__)
    ask.add_argument("--runtime", type=Path, default=ROOT / "src/cairn/runtime", help="The headers measured as after.")
    ask.add_argument("--before", type=Path, help="Older headers, measured in alternation as before.")
    ask.add_argument("--cxx", default="clang++")
    ask.add_argument("--arch", help="A CPU profile the toolchain table names; default: the best this machine runs.")
    ask.add_argument("--runs", type=int, default=5)
    ask.add_argument("--out", type=Path, default=OUT)
    ask.add_argument("--note", default="", help="One sentence recorded beside the numbers.")
    ask.add_argument("--label", default="session", help="The name this session is filed under.")
    args = ask.parse_args()
    arms = {"after": args.runtime, **({"before": args.before} if args.before else {})}
    load_before = os.getloadavg()
    with tempfile.TemporaryDirectory() as scratch:
        exes = {name: Path(scratch) / f"host_tasks_{name}" for name in arms}
        arch = args.arch or best_profile(args.cxx)
        flags = {name: compile_against(args.cxx, where, arch, exes[name]) for name, where in arms.items()}
        runs: dict[str, list[dict]] = {name: [] for name in arms}
        for _ in range(args.runs):
            for name in sorted(arms):  # before, then after, then before again: alternation, not two blocks
                runs[name].append(run(exes[name]))
    session = {
        "compiler": version(args.cxx).splitlines()[0],
        "flags": flags["after"],
        "machine": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "load_average": {"before": [round(x, 2) for x in load_before], "after": [round(x, 2) for x in os.getloadavg()]},
        "note": args.note,
        "arms": {
            name: {
                "runtime": str(where.relative_to(ROOT)) if where.is_relative_to(ROOT) else str(where),
                "runs": runs[name],
                "median_ns": {
                    case: statistics.median(r["ns"][case] for r in runs[name]) for case in runs[name][0]["ns"]
                },
                "threads_started": runs[name][-1]["threads_started"],
            }
            for name, where in arms.items()
        },
    }
    record = json.loads(args.out.read_text()) if args.out.exists() else {"schema": "cairn.bench.host_tasks/1"}
    record.setdefault("sessions", {})[args.label] = session
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({name: arm["median_ns"] for name, arm in session["arms"].items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

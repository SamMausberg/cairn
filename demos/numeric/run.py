#!/usr/bin/env python3
"""Relax the plate on the host lanes, hold it to the f64 reference, and time it beside the same loop in C++.

Every build uses one compiler and the flags CAIRN builds with. Each round runs every program once, in a rotated
order, and the report gives the median and range of the sweeps' own time as each program measures it. Every
program must print the same fingerprint, so they all computed the same bits. The device half is not here: it runs
only under `make gpu` (tests/projects/test_demos.py), which writes results/demos/numeric/device.json.
"""

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cairn.projects.toolchain import flags  # noqa: E402

TIME = re.compile(r"sweeps.* in (\d+) us")
PRINT = re.compile(r"fingerprint of every cell's bits (\d+)")


def cpu() -> str:
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.machine()


def build(out: Path, cxx: str) -> dict[str, list[str]]:
    """Every program of the comparison, as the command that runs it."""
    done = subprocess.run([sys.executable, str(ROOT / "bin/cairn"), "build", str(HERE), "--cxx", cxx, "--out",
                           str(out / "cairn"), "--format", "json"], capture_output=True, text=True, cwd=ROOT)  # fmt: skip
    record = json.loads(done.stdout)
    if record.get("status") != "native-built":
        raise SystemExit(f"cairn build failed: {done.stdout[-2000:]}{done.stderr[-2000:]}")
    variants = {"c++ openmp, guards": ["-DGUARDS", "-fopenmp"], "c++ one thread, guards": ["-DGUARDS"],
                "c++ openmp, no guards": ["-fopenmp"]}  # fmt: skip
    programs = {"cairn lanes": [record["artifact"]], "cairn one lane": [record["artifact"]]}
    for name, extra in variants.items():
        binary = out / re.sub(r"\W+", "_", name)
        command = [cxx, *flags("baseline", "exe"), *extra, str(HERE / "baseline/plate.cpp"), "-o", str(binary)]
        tried = [[]] if "openmp" not in name else [[], *(["-L" + d, "-Wl,-rpath," + d] for d in openmp_libraries())]
        for more in tried:  # an OpenMP runtime where the compiler finds one, else where one is installed
            made = subprocess.run([*command, *more], capture_output=True, text=True)
            if made.returncode == 0:
                break
        else:
            raise SystemExit(f"{' '.join(command)}\n{made.stderr[-3000:]}")
        programs[name] = [str(binary)]
    return programs


def openmp_libraries() -> list[str]:
    """Directories that hold an OpenMP runtime, as bench/suite finds them; nothing is downloaded."""
    bases = [Path(b) for b in ("/usr/lib", "/usr/local/lib", "/usr/lib64") if Path(b).is_dir()]
    found = [c for b in bases for c in [b, *sorted(b.glob("llvm-*/lib"))] if any(c.glob("libomp.so*"))]
    return [str(c) for c in found]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "results/demos/numeric", help="Where builds and timings go.")
    p.add_argument("--cxx", default="clang++", help="The compiler for every build.")
    p.add_argument("--rounds", type=int, default=5, help="Runs of each program, interleaved.")
    p.add_argument("--lanes", type=int, default=os.cpu_count(), help="CAIRN_LANES and OMP_NUM_THREADS.")
    a = p.parse_args()
    if not shutil.which(a.cxx):
        raise SystemExit(f"{a.cxx} is not installed")
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    programs = build(out, a.cxx)
    load_before = os.getloadavg()
    times: dict[str, list[int]] = {n: [] for n in programs}
    prints: dict[str, set[str]] = {n: set() for n in programs}
    first = ""
    for k in range(a.rounds):
        names = list(programs)
        for name in names[k % len(names) :] + names[: k % len(names)]:
            lanes = "1" if "one" in name else str(a.lanes)
            env = {**os.environ, "CAIRN_LANES": lanes, "OMP_NUM_THREADS": lanes}
            done = subprocess.run(programs[name], capture_output=True, text=True, env=env, timeout=600)
            if done.returncode:
                raise SystemExit(f"{name} exited with {done.returncode}:\n{done.stdout}{done.stderr}")
            if "openmp" in name and f"openmp threads {lanes}" not in done.stdout:
                raise SystemExit(f"{name} did not run on {lanes} OpenMP threads:\n{done.stdout}")
            times[name].append(int(TIME.search(done.stdout).group(1)))
            prints[name].add(PRINT.search(done.stdout).group(1))
            if name == "cairn lanes" and not first:
                first = done.stdout
    load_after = os.getloadavg()
    print(first, end="")
    same = len(set().union(*prints.values())) == 1
    print(f"\nEvery program printed the same fingerprint: {'yes' if same else 'NO'}.")
    print(f"Sweep time over {a.rounds} interleaved rounds, {a.lanes} lanes or threads, {a.cxx}, on {cpu()}:")
    fastest = min(statistics.median(t) for n, t in times.items() if "no guards" not in n)
    rows = []
    for name, t in times.items():
        median = statistics.median(t)
        rows.append({"program": name, "median_us": median, "min_us": min(t), "max_us": max(t), "runs_us": t})
        print(f"  {name:24} {median / 1000:8.1f} ms  (range {min(t) / 1000:.1f} to {max(t) / 1000:.1f})"
              f"  {median / fastest:5.2f}x the fastest guarded")  # fmt: skip
    print(f"Load average before and after: {load_before[0]:.1f}, {load_after[0]:.1f} (other work shares the machine).")
    record = {"schema": "cairn.demo-numeric/1", "cpu": cpu(), "cxx": a.cxx, "lanes": a.lanes, "rounds": a.rounds,
              "cells": 1024 * 1024, "sweeps": 200, "same_bits": same, "load_average": [load_before, load_after],
              "programs": rows, "cairn_output": first}  # fmt: skip
    (out / "host.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"The record is {out / 'host.json'}.")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Device runs of pairs built at several revisions, interleaved: every build of a pair in turn, the order rotating each
round, then a summary of what the rounds measured.

    python3 bench/device/interleave.py run ROUNDS LABEL=OUT[,LABEL=OUT ...] PAIR ... --record DIR
    python3 bench/device/interleave.py summary DIR

`run` builds nothing: each OUT is a directory `device.py build --out OUT` wrote, at the revision LABEL names. It runs
code on the GPU, so it refuses unless CAIRN_GPU_TESTS=1 is set for it, and passes that on to each process it starts
but never CAIRN_GPU_TRAPS. Each process is `device.py run PAIR --out OUT`: it holds /tmp/cairn-gpu.lock, stops after
30 seconds, and times that build's CAIRN variants and the CUDA ones interleaved on one stream. Round r
runs the builds in the order given, rotated by r. Each process's JSON goes to DIR/runs.jsonl beside what
nvidia-smi said of the GPU just before it started and the host's load average.

`summary` reads DIR/runs.jsonl and writes DIR/summary.json: for each pair, size, variant and build, the median over
the processes of each process's median `gpu_us`, the lowest and highest of those medians, and the fastest call.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

DEVICE = Path(__file__).resolve().parent / "device.py"
GPU = "utilization.gpu,memory.used,clocks.sm,temperature.gpu,power.draw"


def run(rounds: int, builds: dict[str, str], pairs: list[str], record: Path) -> int:
    sys.path.insert(0, str(DEVICE.parents[2] / "tools"))
    from support import device_reason

    if reason := device_reason():
        print(f"{reason}: nothing ran", file=sys.stderr)
        return 1
    record.mkdir(parents=True, exist_ok=True)
    labels, failed = list(builds), 0
    for r in range(rounds):
        for pair in pairs:
            for label in labels[r % len(labels) :] + labels[: r % len(labels)]:
                state = subprocess.run(["nvidia-smi", f"--query-gpu={GPU}", "--format=csv,noheader"],
                                       capture_output=True, text=True).stdout.strip()  # fmt: skip
                env = {k: v for k, v in os.environ.items() if k != "CAIRN_GPU_TRAPS"}
                row = {"round": r, "pair": pair, "build": label, "start": time.time(), "gpu_before": state,
                       "load": os.getloadavg()}  # fmt: skip
                done = subprocess.run([sys.executable, str(DEVICE), "run", pair, "--out", builds[label]],
                                      capture_output=True, text=True, env=env, timeout=300)  # fmt: skip
                row["returncode"] = done.returncode
                try:
                    row["result"] = json.loads(done.stdout)
                except json.JSONDecodeError:
                    row["stdout"], row["stderr"] = done.stdout[-3000:], done.stderr[-3000:]
                failed += done.returncode != 0 or "result" not in row
                with (record / "runs.jsonl").open("a") as f:
                    f.write(json.dumps(row) + "\n")
                print(r, pair, label, done.returncode, flush=True)
    return 1 if failed else 0


def summary(record: Path) -> int:
    rows = [json.loads(line) for line in (record / "runs.jsonl").read_text().splitlines()]
    kept = [r for r in rows if r["returncode"] == 0 and "result" in r]
    per = defaultdict(list)
    for r in kept:
        for v in r["result"]["rows"]:
            per[(r["pair"], v["n"], v["side"], v["variant"], r["build"])].append((v["gpu_us"], v["gpu_us_min"]))
    out = []
    for (pair, n, side, variant, build), got in sorted(per.items()):
        medians = [m for m, _ in got]
        out.append({"pair": pair, "n": n, "side": side, "variant": variant, "build": build, "processes": len(got),
                    "median_gpu_us": statistics.median(medians), "lowest_median": min(medians),
                    "highest_median": max(medians), "fastest_call_us": min(f for _, f in got)})  # fmt: skip
    (record / "summary.json").write_text(json.dumps({"processes": len(rows), "failed": len(rows) - len(kept),
                                                     "rows": out}, indent=1) + "\n")  # fmt: skip
    for o in out:
        print(
            f"{o['pair']} n={o['n']} {o['side']} {o['variant']} {o['build']}: {o['median_gpu_us']:.2f} us "
            f"({o['lowest_median']:.2f} to {o['highest_median']:.2f}, {o['processes']} processes)"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["run", "summary"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--record", type=Path)
    a = parser.parse_args()
    if a.action == "summary":
        return summary(Path(a.args[0]))
    if len(a.args) < 3 or a.record is None:
        parser.error("run takes ROUNDS, LABEL=OUT[,LABEL=OUT ...], at least one pair and --record")
    builds = dict(item.split("=", 1) for item in a.args[1].split(","))
    return run(int(a.args[0]), builds, a.args[2:], a.record)


if __name__ == "__main__":
    raise SystemExit(main())

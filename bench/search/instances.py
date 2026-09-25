#!/usr/bin/env python3
"""The bounded search over a parameterized implementation, examples/implementations' `prefix_by[K]`.

On a copy of the project: each implementation of `prefix` is validated with `cairn validate --history` under the
policy the project's regressions file pinned, one instance of `prefix_by` at a time; then `cairn tune --symbol prefix
--at n=1e6` searches with that history, first predicted only, then with `--measure 4` on this host, then the same
measurement again, which the history answers without a run; then every selection is timed again in five interleaved
rounds, for the spread the search's own rounds do not show; last, `--write` writes the chosen selection into the
copy. A device program whose implementation lists two block sizes is then searched with 4 compiles for sm_120: each
instance is compiled and read by ptxas and cuobjdump, and nothing runs on a GPU. The load average is recorded before
and after, since the machine is shared.

    python3 bench/search/instances.py --out evidence/v1_0/search/instances.json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.agent.history import History
from cairn.cli import main
from cairn.perf import measure
from cairn.perf.calibrate import cpu_model
from cairn.perf.tuning.plan_source import Placement
from cairn.projects.project import load_project
from cairn.projects.target import toolkit_record
from cairn.projects.toolchain import resolve_arch

DEVICE = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) effects(pure, write:out, par:device, zero_init) {
  parallel i in n { out[i] = 2.0 * x[i]; }
}

// Blocks of K threads, one element each, for a length K divides.
fn scale_blocks[K:nat](n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) implements scale when n % K == 0
  tune K in [64, 256] {
  blocks b in n / K threads t in K { out[b * K + t] = 2.0 * x[b * K + t]; }
}
"""


def cairn(*args: str) -> tuple[int, dict, float]:
    """One `cairn` command in this process, its exit status, its JSON answer and its wall time."""
    out = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(out):
        code = main([*args, "--format", "json"])
    return code, json.loads(out.getvalue()), round(time.monotonic() - started, 2)


def rows(answer: dict) -> list[dict]:
    keep = ("plan", "predicted_ns", "parameters", "validated", "resources")
    return [{k: row[k] for k in keep if k in row} for row in answer["candidates"]]


def searched(answer: dict, wall: float) -> dict:
    out = {"wall_s": wall, "space": answer["space"], "candidates": rows(answer), "chosen": answer["chosen"]["plan"],
           "budget": answer["budget"]}  # fmt: skip
    for key in ("rounds", "measured_best", "pairs_ordered_as_predicted", "measured", "written"):
        if key in answer:
            out[key] = answer[key]
    return out


def interleaved(project: Path, uses: list[str | None], rounds: int = 5) -> dict:
    """Each selection timed once per round, the rounds interleaved, so load that comes and goes falls on all alike."""
    placement = Placement(load_project(project).source, "prefix")
    times: dict[str, list[float]] = {use or "reference": [] for use in uses}
    for _ in range(rounds):
        for use in uses:
            got = measure.time(placement.apply((), use), "prefix", {"n": 1e6}, arch=resolve_arch("baseline"), blocks=3)
            times[use or "reference"].append(round(float(got["median_ns"]), 1))
    return {name: {"median_ns": statistics.median(v), "min_ns": min(v), "max_ns": max(v), "rounds": v}
            for name, v in times.items()}  # fmt: skip


def run(out: Path) -> dict:
    record: dict = {"schema": "cairn.search-instances/1", "machine": {"cpu": cpu_model(), "platform": platform.platform(),
                    "toolkit": toolkit_record()}, "load_average": {"before": list(os.getloadavg())}}  # fmt: skip
    with tempfile.TemporaryDirectory(prefix="cairn-instances-") as scratch:
        project = Path(scratch) / "implementations"
        shutil.copytree(ROOT / "examples/implementations", project, ignore=shutil.ignore_patterns("build", ".cairn"))
        history = project / ".cairn" / "history"
        validations = {}
        for symbol in ("prefix_by4", "prefix_lanes", *(f"prefix_by[{k}]" for k in (4, 8, 16, 32))):
            code, answer, wall = cairn("validate", str(project), "--symbol", symbol, "--history", str(history))
            finite = answer.get("finite", {})
            validations[symbol] = {"exit": code, "status": answer["status"], "wall_s": wall,
                                   "tiles": answer.get("tiles"), "cases": finite.get("cases"),
                                   "implementation_ran": finite.get("implementation_ran"),
                                   "smt": answer.get("smt", {}).get("status"), "reason": finite.get("reason")}  # fmt: skip
        record["validations"] = validations
        ask = ["tune", str(project), "--symbol", "prefix", "--at", "n=1e6", "--history", str(history)]
        code, answer, wall = cairn(*ask)
        record["predicted"] = searched(answer, wall)
        code, answer, wall = cairn(*ask, "--measure", "4")
        record["measured"] = searched(answer, wall)
        code, answer, wall = cairn(*ask, "--measure", "4")
        record["measured_again"] = searched(answer, wall)
        uses = [None, "prefix_by4", *(f"prefix_by[{k}]" for k in (4, 8, 16, 32))]
        record["interleaved"] = {"sizes": {"n": 1e6}, "rounds": 5, "blocks": 3, "times": interleaved(project, uses)}
        code, answer, wall = cairn(*ask, "--measure", "4", "--write")
        record["written"] = {"chosen": answer["chosen"]["plan"], "file": answer.get("written"),
                             "selection": [line for line in (project / "src/prefix.cairn").read_text().splitlines()
                                           if line.startswith("plan prefix")]}  # fmt: skip
        kinds: dict[str, dict[str, int]] = {}
        for r in History(history).records("prefix"):
            kinds.setdefault(r["candidate"], {}).setdefault(r["kind"], 0)
            kinds[r["candidate"]][r["kind"]] += 1
        record["history"] = kinds
        device = Path(scratch) / "scale.cairn"
        device.write_text(DEVICE)
        code, answer, wall = cairn("tune", str(device), "--symbol", "scale", "--at", "n=1e7", "--device-target",
                                   "sm_120", "--budget-compiles", "4", "--history", str(Path(scratch) / "device"))  # fmt: skip
        record["device"] = searched(answer, wall)
        record["device"]["candidates"] = record["device"]["candidates"][:6]
    record["load_average"]["after"] = list(os.getloadavg())
    out.write_text(json.dumps(record, indent=1) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "results/search/instances.json")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    run(args.out)

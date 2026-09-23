#!/usr/bin/env python3
"""How well `cairn predict` foresees measured times it was never fitted to.

The measurements are the CAIRN arms of the preregistered suite's first run (`evidence/v1_3/bench/suite.json`): eight
kernels, six sizes, both compilers, sixteen lanes, on the machine the profile was calibrated on. Calibration never
sees these kernels; it fits its own. For every measured point this prints the prediction beside it, and over all of
them the median relative error and Kendall's tau, since a tuner needs the order of candidates more than their times.

    python3 tools/checks/perf_validation.py --profile PROFILE.json --out evidence/v1_4/perf_model/validation.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from cairn.compiler.cairnc import compile_program
from cairn.perf import model
from cairn.perf.profile import Profile, default
from cairn.perf.work import count
from support import best_profile

SUITE = ROOT / "bench/suite"
RECORD = ROOT / "evidence/v1_3/bench/suite.json"


def entry(kernel: str, arm: str) -> str | None:
    found = re.search(r"cf_(\w+)\(", (SUITE / "kernels" / kernel / f"{arm}.cpp").read_text(encoding="utf-8"))
    return found.group(1) if found else None


def kendall(xs: list[float], ys: list[float]) -> float:
    """Kendall's tau-b over paired values: +1 when every pair is ordered alike, -1 when every pair is reversed."""
    concordant = discordant = tied_x = tied_y = 0
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            a, b = xs[i] - xs[j], ys[i] - ys[j]
            if a == 0 and b == 0:
                continue
            if a == 0:
                tied_x += 1
            elif b == 0:
                tied_y += 1
            elif (a > 0) == (b > 0):
                concordant += 1
            else:
                discordant += 1
    pairs = concordant + discordant
    denominator = ((pairs + tied_x) * (pairs + tied_y)) ** 0.5
    return (concordant - discordant) / denominator if denominator else 0.0


FRESH_SIZES = (1000, 10000, 100000, 1000000, 10000000, 100000000)


def fresh(record: Path, out: Path, lanes: int = 16) -> Path:
    """The same suite entries timed now by `cairn.perf.measure`, written in the record's shape, for a second look."""
    from cairn.perf.measure import time

    runs: dict[str, list] = {}
    for kernel in json.loads(record.read_text(encoding="utf-8"))["runs"]:
        source = (SUITE / "kernels" / kernel / "kernel.cairn").read_text(encoding="utf-8")
        for arm in sorted(p.stem for p in (SUITE / "kernels" / kernel).glob("cairn*.cpp")):
            name = entry(kernel, arm)
            sweep = []
            for n in FRESH_SIZES:
                try:
                    got = time(source, name, {"n": n}, arch=best_profile("clang++"), lanes=lanes)
                except ValueError:
                    break  # a parameter the host timer cannot supply, such as an atomic
                if got["status"] == "measured":
                    sweep.append({"n": n, "median_ms": got["median_ns"] / 1e6, "min_ms": got["min_ns"] / 1e6})
            if sweep:
                runs.setdefault(kernel, []).append({"arm": arm, "compiler": "clang++",
                                                    "timings": {"status": "measured", "sweep": sweep}})  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    measured = {
        "measured_by": "cairn.perf.measure on a shared machine",
        "environment": {"arch_profile": best_profile("clang++")},
    }
    out.write_text(json.dumps({**measured, "runs": runs}, indent=1) + "\n")
    return out


def validate(profile: Profile, record: Path = RECORD) -> dict:
    """Every measured point beside its prediction, for code built for the -march profile the record names."""
    data = json.loads(record.read_text(encoding="utf-8"))
    arch = data.get("environment", {}).get("arch_profile") or model.measured(profile)
    points = []
    for kernel, runs in data["runs"].items():
        source = (SUITE / "kernels" / kernel / "kernel.cairn").read_text(encoding="utf-8")
        p, checker, _ = compile_program(source)
        costs = count(p, checker)
        for run in runs:
            timings = run.get("timings", {})
            if not run["arm"].startswith("cairn") or timings.get("status") != "measured":
                continue
            name = entry(kernel, run["arm"])
            if name not in costs:
                continue
            for row in timings["sweep"]:
                predicted = model.predict(costs[name], profile, {"n": row["n"]}, arch)
                points.append({"kernel": kernel, "function": name, "compiler": run["compiler"], "n": row["n"],
                               "measured_ns": row["median_ms"] * 1e6, "predicted_ns": predicted["ns"],
                               "bound": predicted["bound"], "confidence": predicted["confidence"],
                               **({"least_ns": row["min_ms"] * 1e6} if "min_ms" in row else {})})  # fmt: skip
    for point in points:
        point["relative_error"] = round(abs(point["predicted_ns"] - point["measured_ns"]) / point["measured_ns"], 3)
        point["ratio"] = round(point["predicted_ns"] / point["measured_ns"], 3)
    summary = {}
    for compiler in sorted({p["compiler"] for p in points}):
        mine = [p for p in points if p["compiler"] == compiler]
        confident = [p for p in mine if p["confidence"] != "low"]
        summary[compiler] = {
            "points": len(mine),
            "median_relative_error": round(statistics.median(p["relative_error"] for p in mine), 3),
            "within_2x": sum(0.5 <= p["ratio"] <= 2 for p in mine),
            "kendall_tau": round(kendall([p["measured_ns"] for p in mine], [p["predicted_ns"] for p in mine]), 3),
            "kendall_tau_within_each_size": {
                str(n): round(
                    kendall(
                        [p["measured_ns"] for p in mine if p["n"] == n],
                        [p["predicted_ns"] for p in mine if p["n"] == n],
                    ),
                    3,
                )
                for n in sorted({p["n"] for p in mine})
            },
            "median_relative_error_where_confident": round(statistics.median(p["relative_error"] for p in confident), 3)
            if confident
            else None,
        }
        if all("least_ns" in p for p in mine):  # the statistic calibration keeps: a shared machine only adds time
            least = [abs(p["predicted_ns"] - p["least_ns"]) / p["least_ns"] for p in mine]
            summary[compiler]["against_the_least_disturbed_block"] = {
                "median_relative_error": round(statistics.median(least), 3),
                "within_2x": sum(0.5 <= p["predicted_ns"] / p["least_ns"] <= 2 for p in mine),
                "kendall_tau": round(kendall([p["least_ns"] for p in mine], [p["predicted_ns"] for p in mine]), 3),
            }
    return {
        "schema": "cairn.perf-validation/1",
        "profile": profile.describe(),
        "measurements": str(record.resolve().relative_to(ROOT))
        if record.resolve().is_relative_to(ROOT)
        else str(record),
        "held_out": "Calibration fits its own kernels (cairn.perf.calibrate); none of these was used to fit.",
        "summary": summary,
        "points": points,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile", type=Path)
    ap.add_argument("--record", type=Path, default=RECORD)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--fresh", type=Path, metavar="RECORD.json", help="Time the suite's entries now into this file, "
                    "then validate against it instead.")  # fmt: skip
    a = ap.parse_args(argv)
    profile = Profile.load(a.profile) if a.profile else default()
    record = fresh(a.record, a.fresh) if a.fresh else a.record
    result = validate(profile, record)
    text = json.dumps(result, indent=2) + "\n"
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""What the bounded search of `cairn tune` costs, and what its history saves when a search is asked again.

For each program: a cold search with a fresh history and a compile budget; the same search again, where the kept
compiles answer and the budget reaches further down the ranking; twice the budget; and no budget at all, where only
kept compiles answer. Device programs are compiled for the device target and read by ptxas and cuobjdump; nothing
runs on a GPU. One host program is also timed with --measure, cold and again, to show that a kept measurement is not
run twice. The cubins the compiles wrote are compared by digest, which says how many distinct kernels they were. The
load average is recorded before and after, since the machine is shared.

    python3 bench/search/search.py --out evidence/v1_0/search/search.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.agent.history import History
from cairn.perf.calibrate import cpu_model
from cairn.perf.device import available
from cairn.perf.profile import default
from cairn.perf.search import Budget
from cairn.perf.tune import tune
from cairn.projects.target import parse, toolkit_record

PROGRAMS = {
    "spread": (
        "host",
        """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
""",
        {"n": 1e6},
    ),
    "blend": (
        "host",
        """fn blend(n:usize, out:rw<f64>[n], x:ro<f64>[n], a:f64, b:f64) {
  buffer scaled:f64[n] = zeroed;
  parallel i in n { scaled[i] = a * x[i]; }
  parallel j in n { out[j] = scaled[j] + b; }
}
""",
        {"n": 1e7},
    ),
    "blur": (
        "device",
        """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}
""",
        {"n": 1e7},
    ),
    "saxpy": (
        "device",
        """fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
""",
        {"n": 1e7},
    ),
    "two": (
        "device",
        """fn two(n:usize, out:rw<f32>[n]@device, y:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { y[i] = 2.0 * x[i]; }
  parallel j in n { out[j] = y[j] + 1.0; }
}
""",
        {"n": 1e7},
    ),
}


def searched(source: str, name: str, sizes: dict, where: Path, compiles: int, measure: int = 0) -> dict:
    started = time.perf_counter()
    target = parse("sm_120") if "@device" in source else None
    result = tune(source, name, [sizes], default(), measure=measure, budget=Budget(compiles=compiles, seconds=1800),
                  history=where, device_target=target)  # fmt: skip
    wall = time.perf_counter() - started
    refused = sum(r["configurations"] for r in result["space"]["refused"])
    return {"wall_s": round(wall, 2), "configurations": result["space"]["configurations"],
            "legal": result["space"]["legal"], "refused": refused, **result["budget"],
            "measured_best": result.get("measured_best")}  # fmt: skip


def cubins(where: Path) -> dict:
    """How many compiles the history keeps, and how many distinct cubins they wrote."""
    kept = [json.loads(p.read_text()) for p in (where / "analysis").glob("*/*/result.json")]
    read = [k for k in kept if k.get("status") == "read"]
    return {"analyses": len(kept), "read": len(read), "distinct_cubins": len({k["cubin_sha256"] for k in read}),
            "distinct_sass": len({k["sass_sha256"] for k in read})}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--compiles", type=int, default=8)
    a = ap.parse_args()
    rows = {}
    load = [os.getloadavg()]
    with tempfile.TemporaryDirectory(prefix="cairn-search-") as scratch:
        for name, (kind, source, sizes) in PROGRAMS.items():
            if kind == "device" and not available():
                rows[name] = {"skipped": "nvcc and cuobjdump are needed to compile for the device"}
                continue
            where = Path(scratch) / name
            row = {"kind": kind, "sizes": sizes}
            row["cold"] = searched(source, name, sizes, where, a.compiles)
            row["again"] = searched(source, name, sizes, where, a.compiles)
            row["wider"] = searched(source, name, sizes, where, 2 * a.compiles)
            row["kept_only"] = searched(source, name, sizes, where, 0)  # every compile answered from the history
            if kind == "device":
                row["cubins"] = cubins(where)
            if name == "spread":
                timed = Path(scratch) / "spread-timed"
                row["measure_cold"] = searched(source, name, {"n": 20000}, timed, 0, measure=2)
                row["measure_again"] = searched(source, name, {"n": 20000}, timed, 0, measure=2)
            row["records"] = len(History(where).records(name))
            rows[name] = row
            print(name, json.dumps(row), flush=True)
    record = {
        "schema": "cairn.search-evidence/1",
        "machine": {"cpu": cpu_model(), "platform": platform.platform(), "toolkit": toolkit_record()},
        "compile_budget": a.compiles,
        "load_average": {"before": load[0], "after": os.getloadavg()},
        "programs": rows,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

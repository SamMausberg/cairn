#!/usr/bin/env python3
"""The wall time of `cairn tune`'s search on the suite's kernels, the tuned cooperative example and three device
programs, for whichever CAIRN `--src` names, so one run before a change and one after compare the same work.

Each program is searched with no history, three times with no compile allowed (the search's own cost: generating,
checking, pricing and naming candidates), and, where nvcc is present, once with a compile budget, which compiles
device candidates for sm_120 and reads them with ptxas and cuobjdump. Nothing runs on a GPU and nothing is timed on
the host. `both` has a host and a device region, 8640 plans on sixteen lanes, and is searched with 20 seconds. On
the tuned example, blur and stencil_1d it also times the part of one check a plan cannot reach, the parse, link,
bodies and implementation instances, against the whole check. `--every` searches only blur and two, with compiles
enough to give every candidate a reading. The load average is recorded, since the machine is shared.

    python3 bench/search/wall.py --out results/search/wall.json [--src OTHER/src] [--compiles 4] [--every]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEVICE = {
    "blur": """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}
""",
    "two": """fn two(n:usize, out:rw<f32>[n]@device, y:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { y[i] = 2.0 * x[i]; }
  parallel j in n { out[j] = y[j] + 1.0; }
}
""",
    "both": """fn both(n:usize, h:rw<u64>[n], out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { h[i] = u64(i) * 3; }
  parallel j in n { out[j] = 2.0 * x[j]; }
}
""",
}
SUITE = {  # the suite's kernels with a region a plan schedules
    "mixed_u64": "mixed_u64",
    "saxpy_f32": "saxpy_f32",
    "stencil_1d": "stencil_1d",
    "stencil_1d_wrap": "stencil_1d",
    "histogram_u32_blocks": "histogram_u32",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--src", type=Path, default=ROOT / "src", help="the CAIRN whose search is timed")
    ap.add_argument("--compiles", type=int, default=4)
    ap.add_argument("--every", action="store_true", help="only blur and two, with compiles enough for every kernel")
    a = ap.parse_args()
    sys.path.insert(0, str(a.src.resolve()))
    from cairn.perf.calibrate import cpu_model
    from cairn.perf.device import available
    from cairn.perf.profile import default
    from cairn.perf.search import Budget
    from cairn.perf.tune import tune
    from cairn.projects.project import load_project
    from cairn.projects.target import parse, toolkit_record

    def searched(source: str, name: str, sizes: dict, compiles: int, seconds: float = 1800) -> dict:
        started = time.perf_counter()
        target = parse("sm_120") if "@device" in source else None
        result = tune(source, name, [sizes], default(), budget=Budget(compiles=compiles, seconds=seconds),
                      device_target=target)  # fmt: skip
        wall = time.perf_counter() - started
        rows = result["candidates"]
        read = {row["resources"]["key"] for row in rows if row.get("resources", {}).get("registers") is not None}
        return {"wall_s": round(wall, 2), **{k: result["space"][k] for k in ("configurations", "checked", "legal")},
                "rows": len(rows), "compiles": result["budget"]["compiles"], "readings": len(read),
                "rows_read": sum("registers" in row.get("resources", {}) for row in rows),
                "best_predicted_ns": rows[0]["predicted_ns"], "chosen": result["chosen"]["plan"],
                "undone": result["budget"]["undone"]}  # fmt: skip

    programs: dict[str, tuple[str, str, dict, float]] = {}
    for symbol, kernel in SUITE.items():
        source = (ROOT / "bench" / "suite" / "kernels" / kernel / "kernel.cairn").read_text()
        programs[f"suite/{symbol}"] = (source, symbol, {"n": 1e7}, 1800)
    tuned = load_project(ROOT / "examples" / "cooperative" / "tuned.toml").source
    programs["tuned/row_totals"] = (tuned, "row_totals", {"rows": 64, "cols": 1e5}, 1800)
    for name, source in DEVICE.items():
        programs[f"device/{name}"] = (source, name, {"n": 1e7}, 20 if name == "both" else 1800)
    rows: dict[str, dict] = {}
    load = [os.getloadavg()]
    if a.every:  # how many compiles give every candidate a reading
        programs = {k: v for k, v in programs.items() if k in {"device/blur", "device/two"}}
        rows = {k: {"every_kernel": searched(source, name, sizes, 256)} for k, (source, name, sizes, _) in
                programs.items()}  # fmt: skip
        programs = {}
    for key, (source, name, sizes, seconds) in programs.items():
        runs = [searched(source, name, sizes, 0, seconds) for _ in range(3)]
        row = {"sizes": sizes, "seconds_allowed": seconds, "no_compiles": runs[0],
               "wall_s": [r["wall_s"] for r in runs], "median_wall_s": statistics.median(r["wall_s"] for r in runs)}  # fmt: skip
        if "@device" in source and available() and name != "both":
            row["with_compiles"] = searched(source, name, sizes, a.compiles, seconds)
        rows[key] = row
        print(key, json.dumps(row), flush=True)
    if not a.every:
        rows["check_split"] = {key: split(source) for key, (source, *_) in programs.items()
                               if key in {"tuned/row_totals", "device/blur", "suite/stencil_1d"}}  # fmt: skip
    print(json.dumps(rows.get("check_split", rows)), flush=True)
    record = {
        "schema": "cairn.search-wall/1",
        "src": str(a.src.resolve()),
        "machine": {"cpu": cpu_model(), "platform": platform.platform(), "toolkit": toolkit_record()},
        "compile_budget": a.compiles,
        "load_average": {"before": load[0], "after": os.getloadavg()},
        "programs": rows,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return 0


def split(source: str) -> dict:
    """The least of twenty runs of one whole check of `source`, and of the part of it before the checker reads a
    plan: parsing, linking, every body and every implementation instance, with no selection made. The least is the
    run the loaded machine disturbed least."""
    from cairn.compiler.cairnc import compile_program
    from cairn.compiler.check.checking import Checker
    from cairn.compiler.derive.expansion import derive, specialize
    from cairn.compiler.plans import implementations
    from cairn.compiler.syntax.modules import link
    from cairn.compiler.syntax.parser import Parser

    whole, before = [], []
    for _ in range(20):
        began = time.perf_counter()
        compile_program(source)
        whole.append(time.perf_counter() - began)
        began = time.perf_counter()
        checker = Checker(specialize(derive(link(Parser(source).parse()))))
        checker.p.selections.clear()
        checker.bodies()
        implementations.check(checker)
        before.append(time.perf_counter() - began)
    return {"whole_check_s": round(min(whole), 4), "before_plans_s": round(min(before), 4)}


if __name__ == "__main__":
    raise SystemExit(main())

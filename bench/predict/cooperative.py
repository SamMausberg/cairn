"""The runs behind evidence/v0_9/predict_cooperative: what `cairn predict`, `cairn explain` and `cairn tune` say of
the cooperative and tensor-core examples, and what ptxas reports of the same kernels for sm_120. Kernels are compiled
and read; none is run, and nothing here touches a GPU.

    python bench/predict/cooperative.py --out evidence/v0_9/predict_cooperative
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.agent.explain import explain
from cairn.perf import report
from cairn.perf.search import Budget
from cairn.perf.tune import tune
from cairn.projects.project import load_project
from cairn.projects.target import parse, toolkit_record

COOPERATIVE = ROOT / "examples" / "cooperative"
KERNELS = [
    (COOPERATIVE / "gpu.toml", {"transpose", "block_sums", "row_sums[2]", "row_sums[3]"}),
    (COOPERATIVE / "tuned.toml", {"row_totals", "row_totals_tiled[128, 2]", "row_totals_tiled[128, 3]",
                                  "row_totals_tiled[256, 2]", "row_totals_tiled[256, 3]"}),
    (ROOT / "examples" / "tensor" / "tile32.cairn", {"tile32"}),
    (ROOT / "examples" / "tensor" / "tile64.cairn", {"tile64"}),
]  # fmt: skip
STAGES = ROOT / "evidence" / "v0_9" / "predict_cooperative" / "stages.cairn"
TARGET = parse("sm_120")


def shown(path: Path) -> str:
    return str(path.relative_to(ROOT))


def resources() -> dict:
    """Each cooperative kernel: the shared memory the checker laid out beside what ptxas reports, and its registers."""
    rows = []
    for path, names in KERNELS:
        project = load_project(path)
        read = report.report(project.source, [], names, device=TARGET, inspect=True, site=project.site)["inspection"]
        rows += [{"program": shown(path), **entry} for entry in read["regions"]]
    return {"by": "cairn predict --inspect: ptxas -v and cuobjdump for sm_120, a compiler observation; nothing ran",
            "toolkit": toolkit_record(), "regions": rows}  # fmt: skip


def depth() -> dict:
    """The same region at two depths, at a grid too small to fill the device and at one that fills it."""
    out = {}
    for path, names, sizes in ((STAGES, {"sums[2]", "sums[3]"}, [{"rows": 8, "cols": 1e6}, {"rows": 20000, "cols": 1e6}]),
                               (COOPERATIVE / "gpu.toml", {"row_sums[2]", "row_sums[3]"},
                                [{"rows": 3, "cols": 1e5}, {"rows": 1000, "cols": 1e5}])):  # fmt: skip
        project = load_project(path)
        answer = report.report(project.source, sizes, names, device=TARGET, inspect=True, site=project.site)
        for name in sorted(names):
            entry = answer["functions"][name]
            (region,) = entry["regions"]
            out[f"{shown(path)} {name}"] = {
                "region": region["cooperative"],
                "predictions": [{"sizes": p["sizes"], "ns": p["ns"], "bound": p["bound"], "confidence": p["confidence"],
                                 "part": next(x for x in p["parts"] if "cooperative region" in x["what"])}
                                for p in entry["predictions"]],
            }  # fmt: skip
    return out


def searched() -> dict:
    """cairn tune over the four instances of row_totals_tiled, five compiles, no history."""
    source = load_project(COOPERATIVE / "tuned.toml").source
    started = time.monotonic()
    answer = tune(source, "row_totals", [{"rows": 64, "cols": 1e5}], None, None, 0, "clang++", False, TARGET,
                  Budget(compiles=5), None)  # fmt: skip
    answer["seconds"] = round(time.monotonic() - started, 1)
    return answer


def explained() -> dict:
    project = load_project(COOPERATIVE / "gpu.toml")
    found = explain(project.source, project.origin, {"row_sums[2]", "row_sums[3]"}, root=project.root)
    return {name: {"cooperative": f["cooperative"], "synchronization": f["synchronization"]}
            for name, f in found["functions"].items()}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for name, made in (("resources", resources), ("depth", depth), ("tune", searched), ("explain", explained)):
        (a.out / f"{name}.json").write_text(json.dumps(made(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {a.out / name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

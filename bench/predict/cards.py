"""The runs behind evidence/v1_1/catalog: `cairn predict --card all` over the suite's kernels with their views placed
on the device, and over the cooperative and tensor-core examples, with the device code compiled for each card's own
target and read by ptxas. Every figure is a prediction from NVIDIA's published specifications; kernels are compiled
and read, none is run, and nothing here touches a GPU.

    python bench/predict/cards.py --out evidence/v1_1/catalog
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.compiler.cairnc import Diagnostic
from cairn.perf import report
from cairn.perf.profile import cards, described
from cairn.projects.project import load_project
from cairn.projects.target import toolkit_record

SUITE = ROOT / "bench" / "suite" / "kernels"
VIEW = re.compile(r"\b(r[ow]<\w+>\[[^\]]*\])")  # a view parameter's type, to which @device is added
SQUARE = {"m": 4096, "n": 4096, "k": 4096, "cn": 4096**2, "an": 4096**2, "bn": 4096**2}  # 4096 x 4096 matrices
EXAMPLES = [  # a program, the functions priced and the sizes, each large enough to fill every card
    (ROOT / "examples" / "cooperative" / "gpu.toml", {"transpose"}, {"gx": 128, "gy": 128, "n": 4096**2}),
    (ROOT / "examples" / "cooperative" / "gpu.toml", {"block_sums"}, {"n": 1e8, "g": 390625}),
    (ROOT / "examples" / "cooperative" / "gpu.toml", {"row_sums[2]", "row_sums[3]"}, {"rows": 1000, "cols": 1e5,
                                                                                      "n": 1e8}),
    (ROOT / "examples" / "cooperative" / "tuned.toml", {"row_totals", "row_totals_tiled[128, 2]",
                                                       "row_totals_tiled[256, 3]"}, {"rows": 64, "cols": 1e5, "n": 6.4e6}),
    (ROOT / "examples" / "tensor" / "tile32.cairn", {"tile32"}, SQUARE),
    (ROOT / "examples" / "tensor" / "tile64.cairn", {"tile64"}, SQUARE),
]  # fmt: skip


def placed(kernel: str) -> str:
    """A suite kernel with every view parameter placed on the device, so its regions run there."""
    return VIEW.sub(r"\1@device", (SUITE / kernel / "kernel.cairn").read_text(encoding="utf-8"))


def suite() -> dict:
    """Each suite kernel on the device at 1e7 elements, or the checker's refusal of that placement."""
    out = {}
    for kernel in sorted(p.name for p in SUITE.iterdir() if (p / "kernel.cairn").is_file()):
        try:
            out[kernel] = report.across(placed(kernel), [{"n": 1e7}], inspect=True)
        except Diagnostic as refused:
            out[kernel] = {"refused": {"code": refused.data["code"], "message": refused.data["message"]}}
    return out


def examples() -> dict:
    """The cooperative and tensor-core examples at sizes that fill every card."""
    out = {}
    for path, names, sizes in EXAMPLES:
        project = load_project(path)
        out[f"{path.relative_to(ROOT)} {', '.join(sorted(names))}"] = report.across(project.source, [sizes], names,
                                                                                      inspect=True)  # fmt: skip
    return out


def table(found: dict) -> list[str]:
    """Each answer as its human lines, under the program's name."""
    out = []
    for name, answer in found.items():
        out.append(f"== {name}")
        out += [f"refused: {answer['refused']['code']} {answer['refused']['message']}"] if "refused" in answer else [
            report.lines_across(answer)]  # fmt: skip
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout
    made = {"suite": suite(), "examples": examples()}
    record = {"by": "cairn predict --card all --inspect: predictions from published specifications and ptxas's "
              "reading of each target's code; nothing ran", "commit": commit.strip(), "toolkit": toolkit_record(),
              "cards": [described(c) for c in cards().values()], **made}  # fmt: skip
    (a.out / "predictions.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    lines = table(made["suite"]) + table(made["examples"])
    (a.out / "predictions.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {a.out / 'predictions.json'} and {a.out / 'predictions.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

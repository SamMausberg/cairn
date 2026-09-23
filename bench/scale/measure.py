#!/usr/bin/env python3
"""Measure how the tools scale with a project's size, each step in a cold process (bench/scale/driver.py).

For each size: generate the project (generate.py), then time `cairn check`, a whole-program `cairn build`, a cold
`cairn build --incremental`, and warm incremental rebuilds after changing one function body in a leaf module, one
body in the module every other one imports, and that module's interface; then one language-server refresh of an
edited leaf file. Peak resident memory comes with each step. Past the compiler's size limits the steps run with
the limits lifted (driver.py --unlimited), and the record says so.

    python3 bench/scale/measure.py --modules 185 370 740 --native 185 370 --out results/scale/measure.json

Writes the record to --out and a Markdown table to standard output. Nothing here runs on a device.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import generate

RESULTS = ROOT / "results" / "scale"


def step(*args: str, unlimited: bool) -> dict:
    """One cold process: `driver.py [--unlimited] -- ARGS`, or `driver.py --lsp ...`."""
    flag = ["--unlimited"] if unlimited else []
    line = [sys.executable, str(HERE / "driver.py"), *(["--lsp", *flag, *args[1:]] if args[0] == "--lsp" else
                                                       [*flag, "--", *args])]  # fmt: skip
    done = subprocess.run(line, capture_output=True, text=True, timeout=3600)
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"status": "crashed", "stderr": done.stderr[-2000:]}


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def measure(modules: int, native: bool, editor: bool = True) -> dict:
    root = RESULTS / f"p{modules}"
    shutil.rmtree(root, ignore_errors=True)
    project = generate.write(root, modules)
    row: dict = {
        "modules": modules,
        "lines": project["lines"],
        "bytes": project["bytes"],
        "files": len(project["files"]),
    }
    row["check"] = step("check", str(root), "--format", "json", unlimited=False)
    unlimited = row["check"]["status"] != 0 and "limit" in row["check"].get("tail", "").lower()
    if unlimited:  # refused for its size at the compiler's own limits: measure it with them lifted
        row["refused_at_limits"] = row["check"]
        row["check"] = step("check", str(root), "--format", "json", unlimited=True)
    row["limits_lifted"] = unlimited
    leaf = root / project["leaf_file"]
    k = modules  # the last module is in the last part file and nothing imports it
    if editor:
        row["lsp"] = step("--lsp", str(leaf), f"xs[i] ^ {k})", f"xs[i] ^ {k + 1})", unlimited=unlimited)
    if native:
        out = RESULTS / f"out{modules}"
        shutil.rmtree(out, ignore_errors=True)
        build = ["build", str(root), "--format", "json", "--timeout", "300", "--out"]
        row["build"] = step(*build, str(out / "whole"), unlimited=unlimited)
        row["incremental_cold"] = step(*build, str(out / "units"), "--incremental", unlimited=unlimited)
        row["incremental_again"] = step(*build, str(out / "units"), "--incremental", unlimited=unlimited)
        edit(leaf, f"xs[i] ^ {k})", f"xs[i] ^ {k + 1})")
        row["incremental_leaf_body"] = step(*build, str(out / "units"), "--incremental", unlimited=unlimited)
        edit(root / "src/core.cairn", "^ SALT;", "^ (SALT + 1);")
        row["incremental_core_body"] = step(*build, str(out / "units"), "--incremental", unlimited=unlimited)
        edit(root / "src/core.cairn", "pub fn mix(p:Pair)", "pub fn mix(p:ro<Pair>)")  # the shared header changes
        row["incremental_core_interface"] = step(*build, str(out / "units"), "--incremental", unlimited=unlimited)
        shutil.rmtree(out, ignore_errors=True)
    return row


def table(rows: list[dict]) -> str:
    def cell(r: dict, key: str) -> str:
        m = r.get(key)
        if not m:
            return ""
        if m.get("status") != 0:
            return "failed"
        return f"{m['seconds']:.1f} s, {m['peak_mib']:.0f} MiB"

    keys = ["check", "lsp", "build", "incremental_cold", "incremental_again", "incremental_leaf_body",
            "incremental_core_body", "incremental_core_interface"]  # fmt: skip
    head = "| modules | lines | " + " | ".join(keys) + " |"
    out = [head, "|" + "---|" * (len(keys) + 2)]
    for r in rows:
        lsp = r.get("lsp") or {}
        cells = [cell(r, k) if k != "lsp" or lsp.get("status") != 0 else f"{lsp['change_seconds']:.1f} s refresh, "
                 f"{lsp['peak_mib']:.0f} MiB" for k in keys]  # fmt: skip
        mark = "*" if r["limits_lifted"] else ""
        out.append(f"| {r['modules']} | {r['lines']}{mark} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modules", type=int, nargs="+", required=True, help="Project sizes, in generated modules.")
    ap.add_argument("--native", type=int, nargs="*", default=[], help="The sizes that also build natively.")
    ap.add_argument("--editor", type=int, nargs="*", help="The sizes that time a language-server refresh; default all.")
    ap.add_argument("--out", type=Path, default=RESULTS / "measure.json")
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    rows = []
    for modules in args.modules:
        rows.append(measure(modules, modules in args.native, args.editor is None or modules in args.editor))
        print(json.dumps(rows[-1]), file=sys.stderr, flush=True)
    load = os.getloadavg()
    record = {"started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "host": platform.node(),
              "machine": platform.machine(), "cpus": os.cpu_count(), "python": platform.python_version(),
              "load_average_at_end": load, "rows": rows}  # fmt: skip
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

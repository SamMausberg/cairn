"""Write evidence/v1_1/emulation/runs.json: every device example built with --emulate and run, under both compilers,
and a seeded kernel bug validated with --emulate, as docs/devices.md#emulating-device-code-on-the-host describes.

Nothing here runs on a GPU. Run from the repository root: python3 tools/checks/emulation_runs.py [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.projects.build import build
from cairn.projects.project import load_project
from cairn.projects.target import parse
from cairn.projects.toolchain import version
from cairn.verify.validation.validation import validate

EXAMPLES = [
    "examples/cooperative/gpu.toml",
    "examples/apps/gpu_pipeline",
    "examples/apps/matmul/gpu.toml",
    "examples/apps/analytics/gpu.toml",
    "examples/apps/simulator",
    "demos/numeric/gpu.toml",
]
SCALE = """fn scale(
  n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device
) effects(read:x, write:out, trap, ffi_precondition, par:device, local_read, local_write, zero_init) {
  parallel i in n { out[i] = mul_wrap(x[i], 3); }
}
"""
TILES = """fn scale_tiles(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) implements scale {
  let g = n / 64;
  blocks b in g threads t in 64 {
    shared tile:u32[64] = zeroed;
    let i = b * 64 + t;
    if i < n { tile[t] = x[i]; }
    barrier;
    if i < n { out[i] = mul_wrap(tile[t], 3); }
  }
}
"""


def runs(out: Path) -> list[dict]:
    found = []
    for path in EXAMPLES:
        for cxx in ("clang++", "g++"):
            started = time.monotonic()
            record = build(load_project(ROOT / path), output=out, cxx=cxx, kind="exe", timeout=300,
                           device_target="sm_120", emulate=True)  # fmt: skip
            built = time.monotonic() - started
            done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=600)
            found.append({"example": path, "cxx": cxx, "build": record["status"], "judged_against":
                          record["emulation"]["judged_against"], "exit_code": done.returncode,
                          "last_line": done.stdout.strip().splitlines()[-1], "build_seconds": round(built, 1)})  # fmt: skip
    return found


def seeded() -> dict:
    policy = {"budget": 48, "domain": {"largest_extent": 300}}
    wrong = validate(SCALE + TILES, "scale", "scale_tiles", policy, emulate=parse("sm_120"))
    fixed = SCALE + TILES.replace("let g = n / 64;", "let g = (n + 63) / 64;")
    right = validate(fixed, "scale", "scale_tiles", policy, emulate=parse("sm_120"))
    unemulated = validate(SCALE + TILES, "scale", "scale_tiles", policy)
    return {"without_emulate": unemulated["status"], "wrong_at_a_partial_tile": {k: wrong[k] for k in ("status", "evidence")}
            | {"shrunk_to": wrong["finite"]["failed"]["inputs"], "cases": wrong["finite"]["cases"]},
            "corrected": {k: right[k] for k in ("status", "evidence")} | {"cases": right["finite"]["cases"]}}  # fmt: skip


def main() -> int:
    ask = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ask.add_argument(
        "--out", type=Path, default=ROOT / "evidence/v1_1/emulation/runs.json", help="where the record goes"
    )
    out = ask.parse_args().out
    with tempfile.TemporaryDirectory(prefix="cairn-emulation-") as tmp:
        record = {
            "schema": "cairn.evidence.emulation/1",
            "machine": {"platform": platform.platform(), "processor": platform.processor() or platform.machine()},
            "compilers": {cxx: version(shutil.which(cxx) or cxx).splitlines()[0] for cxx in ("clang++", "g++")},
            "runs": runs(Path(tmp)),
            "seeded_validation": seeded(),
            "gpu": "nothing ran on a GPU",
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

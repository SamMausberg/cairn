"""Build every device example of the repository with `cairn build`'s own command line for each named device target,
and run nothing: what the CI device jobs hold each CUDA toolkit and host compiler to.

A device example is a manifest under examples/ or demos/ whose program the compiler says needs CUDA or that vendors
CUDA, or a single `.cairn` file there whose program needs CUDA and that belongs to no manifest. A target the installed nvcc does not
compile is skipped and says so; a target the program's device features rule out is refused by the compiler, which
is its answer, not a failure. Every other outcome but `native-built` fails the run.

    python3 tools/checks/device_examples.py --cxx clang++ --targets sm_80 sm_90a sm_100a sm_120
    python3 tools/checks/device_examples.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.compiler.cairnc import compile_source
from cairn.compiler.syntax.tree import Diagnostic
from cairn.projects.build import build
from cairn.projects.foreign import cuda
from cairn.projects.project import load_project
from cairn.projects.target import parse, toolkit, toolkit_record
from cairn.projects.toolchain import find, version

TARGETS = ("sm_80", "sm_90a", "sm_100a", "sm_120")
PLACES = ("examples", "demos")


def device_program(source: str, vendored=()) -> bool:
    """Whether the compiler's receipt for `source` requires CUDA, or the manifest vendors a CUDA source. Only a
    program that names `@device` is compiled to ask."""
    if any(cuda(path) for path, _ in vendored):
        return True
    return "@device" in source and "cuda" in compile_source(source)[1]["requires"]


def discover() -> list[str]:
    """Every device example, as the path `cairn build` takes, relative to the repository."""
    found, covered = [], set()
    manifests = sorted(p for place in PLACES for p in (ROOT / place).rglob("*.toml"))
    for manifest in manifests:
        if "[project]" not in manifest.read_text(encoding="utf-8"):
            continue
        project = load_project(manifest)
        covered |= {(project.root / unit.path).resolve() for unit in project.units}
        if device_program(project.source, project.foreign):
            found.append(manifest)
    for single in sorted(p for place in PLACES for p in (ROOT / place).rglob("*.cairn")):
        if single.resolve() not in covered and device_program(single.read_text(encoding="utf-8")):
            found.append(single)
    return [str(path.relative_to(ROOT)) for path in found]


def attempt(job: tuple[str, str, str, str]) -> dict:
    """One example built for one target into its own directory under `out`; the row the report prints."""
    example, spelling, cxx, out = job
    row = {"example": example, "target": spelling}
    started = time.monotonic()
    try:
        record = build(load_project(ROOT / example), output=Path(out), cxx=cxx, timeout=300, device_target=spelling)
    except Diagnostic as refused:
        # A feature the target lacks is the compiler's answer for that pair; any other refusal is a failure.
        feature = refused.data["code"] == "E-TARGET-FEATURE"
        return row | {"status": "refused" if feature else "failed", "code": refused.data["code"],
                      "message": refused.data["message"]}  # fmt: skip
    row |= {"status": record["status"], "seconds": round(time.monotonic() - started, 1)}
    if record["status"] != "native-built":
        row |= {"status": "failed", "built": record["status"], "stderr": record.get("stderr", "")[-3000:]}
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cxx", default="clang++", help="nvcc's host compiler (default: clang++, as cairn build)")
    parser.add_argument("--targets", nargs="+", default=list(TARGETS), help="device targets, spelled as nvcc does")
    parser.add_argument("--only", nargs="+", help="these examples instead of every one discovered")
    parser.add_argument("--jobs", type=int, default=4, help="builds at once")
    parser.add_argument("--list", action="store_true", help="print the device examples discovered and build nothing")
    options = parser.parse_args(argv)
    examples = options.only or discover()
    if options.list:
        print(json.dumps({"examples": examples}, indent=2))
        return 0
    targets = [parse(spelling) for spelling in options.targets]
    found = toolkit()
    if found is None:
        print(json.dumps({"error": "nvcc is absent; nothing was built"}))
        return 2
    compiled = [t.name for t in targets if t.sm in found["compiles"]]
    skipped = [t.name for t in targets if t.name not in compiled]
    with tempfile.TemporaryDirectory(prefix="cairn-device-examples-") as out:
        jobs = [(example, spelling, options.cxx, out) for example in examples for spelling in compiled]
        with ProcessPoolExecutor(max_workers=max(1, options.jobs)) as pool:
            rows = list(pool.map(attempt, jobs))
    failed = [row for row in rows if row["status"] == "failed"]
    print(json.dumps({
        "schema": "cairn.device-examples/1",
        "toolkit": toolkit_record(),
        "host_compiler": version(find(options.cxx)).splitlines()[0],
        "targets": compiled,
        "skipped_targets": dict.fromkeys(skipped, f"nvcc {found['release']} does not compile it"),
        "built": sum(row["status"] == "native-built" for row in rows),
        "refused": sum(row["status"] == "refused" for row in rows),
        "failed": len(failed),
        "rows": rows,
        "ran_on_a_device": False,
    }, indent=2))  # fmt: skip
    return 1 if failed or not rows else 0


if __name__ == "__main__":
    raise SystemExit(main())

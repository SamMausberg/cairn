#!/usr/bin/env python3
"""Write evidence/v1_0/execution/counts.json: what the repeated device pipeline of tests/runtime/test_execution.py
makes, allocates and waits for on the host machine of tests/runtime/gpu_host.hpp, pass by pass, and the CUDA runtime
symbols its sm_120 object references. It compiles and runs host code only: nothing is launched on a GPU."""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cairn.compiler.cairnc import compile_source, write_program
from cairn.projects.target import parse, toolkit_record
from cairn.projects.toolchain import command
from cairn.projects.toolchain import version as compiler_version


def quoted(text: str, name: str) -> str:
    """A program text the test file holds as a triple-quoted string assigned to `name`."""
    return text.split(f'{name} = r"""' if f'{name} = r"""' in text else f'{name} = """')[1].split('"""')[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "evidence/v1_0/execution")
    ap.add_argument("--passes", type=int, default=20)
    a = ap.parse_args(argv)
    text = (ROOT / "tests/runtime/test_execution.py").read_text(encoding="utf-8")
    pipeline, harness, main_fn = quoted(text, "PIPELINE"), quoted(text, "HARNESS"), quoted(text, "MAIN")
    runtime, host = ROOT / "src/cairn/runtime", ROOT / "tests/runtime"
    runs = {}
    with tempfile.TemporaryDirectory(prefix="cairn-execution-") as scratch:
        work = Path(scratch)
        cpp = compile_source(pipeline)[0].replace('#include "cairn_gpu.hpp"', '#include "gpu_host.hpp"')
        (work / "pipeline.cpp").write_text(cpp)
        (work / "harness.cpp").write_text('#include "gpu_host.hpp"\n' + harness.replace("PASSES", str(a.passes)))
        for cxx in ("g++", "clang++"):
            exe = work / f"pipeline-{cxx}"
            line = [cxx, "-std=c++20", "-O2", "-fno-exceptions", "-ffp-contract=off", f"-I{runtime}", f"-I{host}",
                    str(work / "pipeline.cpp"), str(work / "harness.cpp"), "-o", str(exe)]  # fmt: skip
            subprocess.run(line, check=True)
            done = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
            runs[cxx] = [json.loads(row) for row in done.stdout.splitlines()]
        device = parse("sm_120")
        source = compile_source(pipeline + main_fn)[0] + "int main() { return static_cast<int>(cf_main()); }\n"
        unit, obj = write_program(work, "pipeline.cu", source), work / "pipeline.o"
        line = [x for x in command("g++", str(unit), str(obj), kind="exe", cuda=True, device=device) if x != "-shared"]
        line.insert(line.index("-o"), "-c")
        subprocess.run(line, check=True)
        undefined = subprocess.run(["nm", "-u", str(obj)], capture_output=True, text=True, check=True).stdout
    called = sorted({row.split()[-1] for row in undefined.splitlines() if row.split()[-1].startswith("cuda")})
    record = {
        "schema": "cairn.evidence.execution/1",
        "date": datetime.date.today().isoformat(),
        "machine": {"platform": platform.platform(), "python": platform.python_version()},
        "compilers": {c: compiler_version(c).splitlines()[0] for c in ("g++", "clang++")} | {"nvcc": toolkit_record()},
        "what": f"the generated code of tests/runtime/test_execution.py's pipeline, {a.passes} passes on the host "
        "machine of tests/runtime/gpu_host.hpp; counts are cumulative after each pass",
        "host_runs": runs,
        "sm_120_object": {"command": [Path(x).name if x.startswith(scratch) else x for x in line],
                          "device_target": device.record(), "cuda_symbols_referenced": called, "ran_on_a_gpu": False},
    }  # fmt: skip
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "counts.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "path": str(a.out / "counts.json"), "rows": len(runs["g++"])}))
    return 0 if all(rows[-1] == {"wrong": 0} for rows in runs.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

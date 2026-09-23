"""Writes this folder's records from a checkout: python3 evidence/v0_9/foreign/collect.py. Nothing it runs launches
a kernel. Device code is compiled for sm_120 and read with cuobjdump; host code is built and run on this machine."""

import json
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "tools")]

from cairn.compiler.cairnc import compile_source, write_program  # noqa: E402
from cairn.projects.foreign import inspect  # noqa: E402
from cairn.projects.project import load_project  # noqa: E402
from cairn.projects.target import parse  # noqa: E402
from cairn.projects.toolchain import command  # noqa: E402
from cairn.verify.foreign import report  # noqa: E402
from language.test_assembly import PTX, X86, oracle  # noqa: E402

SANITIZE = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]


def version(tool: str) -> str:
    return subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.strip().splitlines()[-1]


def assembly() -> dict:
    """The x86-64 statements built by each compiler under the sanitizers and run against Python's values, and the
    PTX statements compiled for sm_120 with the SASS instructions they became."""
    out: dict = {"x86_64": {}, "ptx": {}}
    with tempfile.TemporaryDirectory() as scratch:
        directory = Path(scratch)
        program = write_program(directory, "x86.cpp", compile_source(X86)[0] + "\nint main() { return cf_main(); }\n")
        for cxx in ("g++", "clang++"):
            exe = directory / f"x86_{cxx}"
            subprocess.run([cxx, *SANITIZE, str(program), "-o", str(exe)], check=True)
            done = subprocess.run([str(exe)], capture_output=True, text=True)
            out["x86_64"][cxx] = {"exit": done.returncode, "stdout": done.stdout, "expected": oracle(),
                                  "agrees": done.stdout == oracle(), "sanitizer_reports": "Sanitizer" in done.stderr}  # fmt: skip
        source = write_program(directory, "ptx.cu", compile_source(PTX)[0])
        target = directory / "ptx.o"
        line = [
            p for p in command("g++", str(source), str(target), cuda=True, device=parse("sm_120")) if p != "-shared"
        ]
        line.insert(line.index("-o"), "-c")
        subprocess.run(line, check=True, capture_output=True)
        sass = subprocess.run(["cuobjdump", "-sass", str(target)], capture_output=True, text=True).stdout
        for word in ("BREV", "LDG.E.CONSTANT", "MEMBAR"):
            out["ptx"][word] = [" ".join(s.split()) for s in sass.splitlines() if word in s]
    return out


def main() -> int:
    machine = {"host": f"{platform.system()} {platform.machine()} {platform.release()}", "nvcc": version("nvcc"),
               "g++": version("g++").split("\n")[0], "cuobjdump": shutil.which("cuobjdump") is not None}  # fmt: skip
    host = report(load_project(ROOT / "examples/foreign/host"), "histogram_interleaved")
    device = report(load_project(ROOT / "examples/foreign/device"), "stencil_tiled")
    with tempfile.TemporaryDirectory() as scratch:
        write_program(Path(scratch), "runtime.cpp", "")
        sources = inspect(load_project(ROOT / "examples/foreign/device"), parse("sm_120"), Path(scratch))
    records = {"host.json": host, "device.json": device, "inspection.json": sources, "assembly.json": assembly()}
    for name, record in records.items():
        (HERE / name).write_text(json.dumps({"machine": machine, **record}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A device kernel's resources and instruction mix, read from the CUDA toolchain's output without running it.

`kernels` compiles a program's device code to a cubin for one architecture (`sm_120` unless told otherwise), never
for `native`, which would ask the driver which device is present. ptxas reports each kernel's registers, spills,
stack and shared memory, and cuobjdump disassembles it, so the model can count instructions and derive occupancy.
Nothing is launched and no device is touched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..compiler.cairnc import RUNTIME_FILES, compile_source
from ..projects.toolchain import find

ENTRY = re.compile(r"Compiling entry function '([^']+)' for '(sm_\d+)'")
USED = re.compile(r"Used (\d+) registers")
SPILL = re.compile(r"(\d+) bytes spill stores, (\d+) bytes spill loads")
STACK = re.compile(r"(\d+) bytes stack frame")
SHARED = re.compile(r"(\d+) bytes smem")
FUNCTION = re.compile(r"^\s+Function : (\S+)")
INSTRUCTION = re.compile(r"/\*[0-9a-f]{4,}\*/\s+(?:@!?U?P\w+\s+)?([A-Z][A-Z0-9_.]*)")
MEMORY = {"LDG": "global_load", "STG": "global_store", "LDS": "shared_load", "STS": "shared_store"}


def available() -> bool:
    return bool(shutil.which("nvcc") and shutil.which("cuobjdump"))


def owner(symbol: str, names: set[str]) -> str | None:
    for size, rest in re.findall(r"(\d+)(c[fi]_\w+)", symbol):  # a checked entry or the lean body a lane runs in
        found = rest[: int(size)].removeprefix("cf_").removeprefix("ci_")
        if found in names:
            return found
    return None


def resources(log: str) -> dict[str, dict[str, Any]]:
    """ptxas -v, one entry per kernel: registers, spilled bytes, stack frame, shared memory."""
    out: dict[str, dict[str, Any]] = {}
    current = None
    for line in log.splitlines():
        if m := ENTRY.search(line):
            current = m.group(1)
            out[current] = {"arch": m.group(2), "registers": 0, "spill_bytes": 0, "stack_bytes": 0, "shared_bytes": 0}
        elif current:
            if m := USED.search(line):
                out[current]["registers"] = int(m.group(1))
            if m := SPILL.search(line):
                out[current]["spill_bytes"] = int(m.group(1)) + int(m.group(2))
            if m := STACK.search(line):
                out[current]["stack_bytes"] = int(m.group(1))
            if m := SHARED.search(line):
                out[current]["shared_bytes"] = int(m.group(1))
    return out


def mix(sass: str) -> dict[str, Counter]:
    """cuobjdump -sass, one opcode count per kernel."""
    out: dict[str, Counter] = {}
    current = None
    for line in sass.splitlines():
        if m := FUNCTION.match(line):
            current = m.group(1)
            out[current] = Counter()
        elif current and (m := INSTRUCTION.search(line)):
            out[current][m.group(1).split(".")[0]] += 1
    return out


def kernels(source: str, arch: str = "sm_120", timeout: int = 600) -> dict[str, Any]:
    """Per CAIRN function with device lanes: each kernel's resources, instruction mix and memory instructions."""
    if not available():
        return {"status": "not-run", "reason": "nvcc and cuobjdump are needed to read a kernel; neither was found."}
    from ..compiler.cairnc import compile_program
    from ..compiler.codegen import mangle

    cpp, receipt = compile_source(source)
    if "cuda" not in receipt["requires"]:
        return {"status": "no-device-code", "kernels": {}}
    p, _, _ = compile_program(source)
    names = {mangle(f.name): f.name for f in p.functions}
    with tempfile.TemporaryDirectory(prefix="cairn-cubin-") as scratch:
        directory = Path(scratch)
        (directory / "program.cu").write_text(cpp, encoding="utf-8")
        for name, text in RUNTIME_FILES.items():
            (directory / name).write_text(text, encoding="utf-8")
        cubin = directory / "program.cubin"
        command = [find("nvcc"), "-std=c++20", "-O3", "--fmad=false", f"-arch={arch}", "--extended-lambda",
                   "--expt-relaxed-constexpr", "-cubin", "-Xptxas", "-v", str(directory / "program.cu"), "-o", str(cubin)]  # fmt: skip
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        if done.returncode:
            return {"status": "compile-failed", "stderr": done.stderr[-4000:]}
        dump = subprocess.run([find("cuobjdump"), "-sass", str(cubin)], capture_output=True, text=True, timeout=120)
    used, counted = resources(done.stderr + done.stdout), mix(dump.stdout)
    found: dict[str, list[dict[str, Any]]] = {}
    for symbol, info in used.items():
        mangled = owner(symbol, set(names))
        opcodes = counted.get(symbol, Counter())
        entry = {**info, "instructions": sum(opcodes.values()), "memory": {k: opcodes[o] for o, k in MEMORY.items() if opcodes[o]},
                 "top": dict(opcodes.most_common(8))}  # fmt: skip
        found.setdefault(names[mangled] if mangled else "(runtime)", []).append(entry)
    return {"status": "read", "arch": arch, "kernels": found}

"""A device kernel's resources and instruction mix, read from the CUDA toolchain's output without running it.

`kernels` compiles a program's device code to a cubin for one device target (projects/target.py), the one the
caller resolved or else the one resolved here, never for `native`. ptxas reports each kernel's registers, spills,
stack and shared memory, and cuobjdump disassembles it, so the model can count instructions and derive occupancy.
A report ptxas makes for any other target is refused. Nothing is launched and no device is touched.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..compiler.cairnc import compile_source, write_program
from ..projects.target import DeviceTarget, resolve, supported
from ..projects.toolchain import bounded, find, until

ENTRY = re.compile(r"Compiling entry function '([^']+)' for '(sm_\d+[af]?)'")
USED = re.compile(r"Used (\d+) registers")
SPILL = re.compile(r"(\d+) bytes spill stores, (\d+) bytes spill loads")
STACK = re.compile(r"(\d+) bytes stack frame")
SHARED = re.compile(r"(\d+) bytes smem")
FUNCTION = re.compile(r"^\s+Function : (\S+)")
INSTRUCTION = re.compile(r"/\*[0-9a-f]{4,}\*/\s+(?:@!?U?P\w+\s+)?([A-Z][A-Z0-9_.]*)")
MEMORY = {
    "LDG": "global_load",
    "STG": "global_store",
    "LDS": "shared_load",
    "STS": "shared_store",
    "LDL": "local_load",
    "STL": "local_store",
}  # local memory holds what spills: its instructions are where the spills go


def available() -> bool:
    return bool(shutil.which("nvcc") and shutil.which("cuobjdump"))


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


def listings(sass: str) -> dict[str, str]:
    """cuobjdump -sass, each kernel's instructions as a digest: its code, not its name, so two kernels that compile
    alike have one digest wherever they sit in a program."""
    out: dict[str, list[str]] = {}
    current = None
    for line in sass.splitlines():
        if m := FUNCTION.match(line):
            current = m.group(1)
            out[current] = []
        elif current and INSTRUCTION.search(line):
            out[current].append(line.strip())
    return {name: hashlib.sha256("\n".join(lines).encode()).hexdigest() for name, lines in out.items()}


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


def kernels(source: str, target: DeviceTarget | None = None, timeout: float = 600,
            keep: dict[str, bytes] | None = None) -> dict[str, Any]:  # fmt: skip
    """Per CAIRN function with device lanes: each kernel's resources, instruction mix and memory instructions, for
    `target`, or the target resolved here when none is given. `keep`, when given, receives the compiled program, its
    cubin, ptxas's log and the SASS, by file name. nvcc and cuobjdump together get `timeout` seconds, and are
    stopped there with every process they started (subprocess.TimeoutExpired)."""
    deadline = time.monotonic() + timeout
    if not available():
        return {"status": "not-run", "reason": "nvcc and cuobjdump are needed to read a kernel; neither was found."}
    chosen = supported(target or resolve())
    from ..compiler.cairnc import compile_program
    from ..compiler.codegen import demangled, mangle

    cpp, receipt = compile_source(source)
    if "cuda" not in receipt["requires"]:
        return {"status": "no-device-code", "kernels": {}, "device_target": chosen.record()}
    chosen = chosen.require(receipt["device_features"])
    p, _, _ = compile_program(source)
    names = {mangle(f.name): f.name for f in p.functions}
    with tempfile.TemporaryDirectory(prefix="cairn-cubin-") as scratch:
        directory = Path(scratch)
        program, cubin = write_program(directory, "program.cu", cpp), directory / "program.cubin"
        command = [find("nvcc"), "-std=c++20", "-O3", "--fmad=false", *chosen.flags(), "--extended-lambda",
                   "--expt-relaxed-constexpr", "-cubin", "-Xptxas", "-v", str(program), "-o", str(cubin)]  # fmt: skip
        done = bounded(command, until(deadline, timeout))
        if done.returncode:
            return {"status": "compile-failed", "stderr": done.stderr[-4000:]}
        dump = bounded([find("cuobjdump"), "-sass", str(cubin)], until(deadline, 120))
        if keep is not None:
            keep |= {"program.cu": program.read_bytes(), "program.cubin": cubin.read_bytes(),
                     "ptxas.log": (done.stderr + done.stdout).encode(), "sass.txt": dump.stdout.encode()}  # fmt: skip
    used, counted, code = resources(done.stderr + done.stdout), mix(dump.stdout), listings(dump.stdout)
    found: dict[str, list[dict[str, Any]]] = {}
    for symbol, info in used.items():
        chosen.accept(info["arch"], f"ptxas's report of {symbol}")
        mangled = demangled(symbol, names)
        opcodes = counted.get(symbol, Counter())
        entry = {**info, "symbol": symbol, "sass_sha256": code.get(symbol, ""), "instructions": sum(opcodes.values()),
                 "memory": {k: opcodes[o] for o, k in MEMORY.items() if opcodes[o]},
                 "top": dict(opcodes.most_common(8))}  # fmt: skip
        found.setdefault(names[mangled] if mangled else "(runtime)", []).append(entry)
    return {"status": "read", "arch": chosen.name, "device_target": chosen.record(), "kernels": found}

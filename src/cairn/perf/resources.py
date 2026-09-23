"""What one compiled candidate uses on the device, read without running it, and kept by what was compiled.

`Inspector.inspect` compiles a candidate program's device code once for its target (`perf/device.py`) and returns,
for the function's kernels, what ptxas reports (registers, spilled bytes, stack frame, static shared memory) and what
cuobjdump's SASS counts (instructions, memory instructions). A staged region's tiles are dynamic shared memory,
which ptxas does not see, so they are computed from the plan: each staged array's tile of block + 2R elements,
rounded up to 16 bytes, at the block the plan asks for (256 when it asks for none).

The key of an inspection is the digest of everything the compile read: the emitted program, the runtime headers,
the target, the toolkit's version and the inspector itself. An inspection kept under a key is the answer for every
candidate with that key, and one kept for another target has another key, so it is never used for this one. With a
history, the answer and its artifacts (the program, the cubin, ptxas's log, the SASS) are kept in it; without one,
for this process only. Nothing is launched and no device is touched.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_source
from ..projects.target import DeviceTarget, toolkit_record
from . import device

INSPECTOR = hashlib.sha256(Path(device.__file__).read_bytes()).hexdigest()  # a changed inspector reads afresh
BLOCK = 256  # the block a device region launches with when its plan names none (runtime/cairn_gpu.hpp)


def device_identity(target: DeviceTarget | None) -> str:
    """What the history keeps as a device target (projects/target.py): its name and the toolkit that compiles for
    it. Without a target, words that say so, which no target's identity equals."""
    if target is None:
        return "no device target"
    return hashlib.sha256(repr([target.name, toolkit_record()]).encode()).hexdigest()


def host_target(arch: str | None, cxx: str) -> dict[str, str]:
    """What a host candidate is built and run for: the processor, the -march profile and the compiler."""
    from ..projects.project import ProjectError
    from ..projects.toolchain import find, resolve_arch, version
    from .calibrate import cpu_model

    try:
        compiler = version(find(cxx)).splitlines()[0]
    except (ProjectError, OSError, subprocess.SubprocessError):
        compiler = f"{cxx} (not found)"
    return {"kind": "host", "cpu": cpu_model(), "arch": resolve_arch(arch) or "baseline", "cxx": compiler}


def tiles(program: Any, checker: Any, name: str) -> int:
    """The dynamic shared memory one block of `name`'s staged regions asks for, the largest over its regions."""
    from .regions import walked

    f = next(f for f in program.functions if f.name == name and not f.bindings)
    most = 0
    for s in walked(f.body):
        if s.tag == "parallel" and s.ref == "device" and s.stage:
            width = (s.launch[0] or BLOCK) + 2 * s.stage
            most = max(most, sum((width * checker.sizeof(element) + 15) // 16 * 16 for _, element, _ in s.staged))
    return most


def summed(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The function's kernels as one: the most any uses of each resource, and the instructions of all of them."""
    memory: dict[str, int] = {}
    for e in entries:
        for k, n in e.get("memory", {}).items():
            memory[k] = memory.get(k, 0) + n
    most = {k: max((e[k] for e in entries), default=0) for k in ("registers", "spill_bytes", "stack_bytes",
                                                                   "shared_bytes")}  # fmt: skip
    return {**most, "instructions": sum(e["instructions"] for e in entries), "memory": memory,
            "kernels": len(entries)}  # fmt: skip


class Inspector:
    """Device inspections for one target, kept in `history` (agent/history.py) when there is one."""

    def __init__(self, target: DeviceTarget, history: Any = None):
        self.target, self.history = target, history
        self.memory: dict[str, dict[str, Any]] = {}

    def key(self, source: str) -> str:
        cpp, receipt = compile_source(source)
        parts = [hashlib.sha256(cpp.encode()).hexdigest(), receipt["runtime_sha256"], device_identity(self.target),
                 INSPECTOR]  # fmt: skip
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()

    def kept(self, source: str, name: str, program: Any) -> dict[str, Any] | None:
        """The inspection kept for `source` on this target, or None. One that names another target is refused
        (E-TARGET-MISMATCH), which only a history written by hand can hold."""
        key = self.key(source)
        found = self.memory.get(key) or (self.history.analysis(key) if self.history is not None else None)
        if found:
            self.target.accept(found.get("device_target"), "A kept inspection")
        return {**found, "kept": True} if found else None

    def inspect(self, source: str, name: str, program: Any, checker: Any = None) -> dict[str, Any]:
        """Compile `source` for the target and read `name`'s kernels; the answer is kept under its key."""
        key = self.key(source)
        files: dict[str, bytes] = {}
        answer: dict[str, Any] = {"device_target": self.target.name, "key": key}
        try:
            read = device.kernels(source, self.target, keep=files)
        except Diagnostic as refused:  # the program needs a feature the target lacks, or nvcc cannot build it
            read = {"status": "target-refused", "reason": f"{refused.data['code']}: {refused.data['message']}"}
        answer["status"] = read["status"]
        if read["status"] == "read":
            entries = read["kernels"].get(name, [])
            answer |= summed(entries)
            answer["dynamic_shared_bytes"] = tiles(program, checker, name) if checker is not None else 0
            answer["cubin_sha256"] = hashlib.sha256(files.get("program.cubin", b"")).hexdigest()
            if not entries:
                answer["status"] = "no-kernel"
        elif "stderr" in read or "reason" in read:
            answer["why"] = read.get("stderr", read.get("reason", ""))[-2000:]
        if self.history is not None and answer["status"] in {"read", "compile-failed", "no-kernel", "target-refused"}:
            answer = self.history.keep(key, answer, files)
        self.memory[key] = answer
        return answer

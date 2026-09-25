#!/usr/bin/env python3
"""Differential check: `Device.resident` (src/cairn/perf/profile.py) against CUDA's own occupancy calculator.

CUDA ships `cuda_occupancy.h`, the occupancy calculator as arithmetic in one host header. This check builds a driver
against it with the host compiler, gives it each packaged card's limits, and asks how many blocks one SM holds over a
grid of block sizes (whole warps and not), registers a thread and shared bytes a block. The card's `resident` must
give the same count by every limit and the same least, and name the same limits as binding. Nothing touches a GPU.

The kernel described to the calculator is one CAIRN writes: one block barrier (`__syncthreads`), shared memory it may
opt in to up to the card's per-block limit, and the default split of the SM between shared memory and L1.

    python3 tools/checks/occupancy.py [--out evidence/v1_2/occupancy/comparison.json]

It exits 0 when every answer agrees, 1 when one differs, and 3 when there is no header to compare against: a skip,
which is neither.

`--device` asks the GPU itself for the same limits, `cudaDevAttrMaxBlocksPerMultiprocessor` among them, and compares
them with the card of its compute capability. The query starts a CUDA context and launches nothing. It runs only
under the owner's `make device-limits`, holding the device lock.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]

from cairn.perf.profile import Device, cards
from cairn.projects.target import toolkit_record
from cairn.projects.toolchain import command, find, version

BLOCKS = (1, 16, 32, 33, 48, 64, 96, 100, 128, 160, 192, 200, 256, 288, 320, 384, 480, 512, 640, 768, 1000, 1024, 1025)
REGISTERS = (0, 16, 24, 32, 37, 40, 48, 56, 64, 72, 80, 96, 128, 168, 200, 255)
KIB = 1024
SHARED = (0, 1, 100, 1000, 4096, 4224, 8192, 16000, 24576, 40000, 48 * KIB, 48 * KIB + 1, 64 * KIB, 99 * KIB,
          99 * KIB + 1, 100 * KIB, 163 * KIB, 163 * KIB + 1, 200_000, 227 * KIB, 227 * KIB + 1)  # fmt: skip
LIMITS = {"threads": 0x01, "registers": 0x02, "shared memory": 0x04, "blocks": 0x08}  # cudaOccLimitingFactor
UNLIMITED = 2**31 - 1  # INT_MAX: the calculator's count by a limit a kernel does not use
# What a GPU reports of a card's limits.
COMPARED = ("warp", "blocks_per_sm", "threads_per_sm", "threads_per_block", "registers_per_sm", "registers_per_block",
            "shared_per_sm", "shared_per_block", "shared_reserved")  # fmt: skip

DRIVER = r"""
#include <cuda_occupancy.h>
#include <cstdio>

// A question a line: a device's limits, then a block's threads, registers a thread and shared bytes. An answer a
// line: the calculator's status, its blocks, and its blocks by threads, registers, shared memory and blocks.
int main() {
  int major, minor, block, registers;
  cudaOccDeviceProp device;
  size_t shared;
  while (std::scanf("%d %d %d %d %d %d %d %zu %zu %zu %d %d %zu", &major, &minor, &device.warpSize,
                    &device.maxThreadsPerBlock, &device.maxThreadsPerMultiprocessor, &device.regsPerBlock,
                    &device.regsPerMultiprocessor, &device.sharedMemPerMultiprocessor, &device.sharedMemPerBlockOptin,
                    &device.reservedSharedMemPerBlock, &block, &registers, &shared) == 13) {
    device.computeMajor = major;
    device.computeMinor = minor;
    device.sharedMemPerBlock = 48 * 1024;  // static shared memory, and dynamic before a kernel opts in to more
    device.numSms = 1;
    cudaOccFuncAttributes kernel;
    kernel.maxThreadsPerBlock = device.maxThreadsPerBlock;
    kernel.numRegs = registers;
    kernel.shmemLimitConfig = FUNC_SHMEM_LIMIT_OPTIN;
    kernel.maxDynamicSharedSizeBytes = device.sharedMemPerBlockOptin;
    kernel.numBlockBarriers = 1;
    cudaOccDeviceState state;
    cudaOccResult r{};
    int status = cudaOccMaxActiveBlocksPerMultiprocessor(&r, &device, &kernel, &state, block, shared);
    std::printf("%d %d %d %d %d %d %u\n", status, r.activeBlocksPerMultiprocessor, r.blockLimitWarps,
                r.blockLimitRegs, r.blockLimitSharedMem, r.blockLimitBlocks, r.limitingFactors);
  }
  return 0;
}
"""

QUERY = r"""
#include <cuda_runtime.h>
#include <cstdio>

// The limits device 0 reports, as JSON; no kernel is launched.
int main() {
  cudaDeviceProp p;
  if (cudaGetDeviceProperties(&p, 0) != cudaSuccess) return 2;
  int blocks = 0;
  if (cudaDeviceGetAttribute(&blocks, cudaDevAttrMaxBlocksPerMultiprocessor, 0) != cudaSuccess) return 2;
  std::printf("{\"name\": \"%s\", \"compute_capability\": \"%d.%d\", \"sms\": %d, \"warp\": %d, "
              "\"blocks_per_sm\": %d, \"threads_per_sm\": %d, \"threads_per_block\": %d, \"registers_per_sm\": %d, "
              "\"registers_per_block\": %d, \"shared_per_sm\": %zu, \"shared_per_block\": %zu, "
              "\"shared_reserved\": %zu}\n", p.name, p.major, p.minor, p.multiProcessorCount, p.warpSize, blocks,
              p.maxThreadsPerMultiProcessor, p.maxThreadsPerBlock, p.regsPerMultiprocessor, p.regsPerBlock,
              p.sharedMemPerMultiprocessor, p.sharedMemPerBlockOptin, p.reservedSharedMemPerBlock);
  return 0;
}
"""


def header() -> Path | None:
    """The installed toolkit's cuda_occupancy.h, beside its nvcc, or None without one."""
    nvcc = shutil.which("nvcc")
    found = Path(nvcc).resolve().parents[1] / "include" / "cuda_occupancy.h" if nvcc else None
    return found if found and found.is_file() else None


def question(d: Device, block: int, registers: int, shared: int) -> str:
    major, minor = d.compute_capability.split(".")
    return " ".join(map(str, (major, minor, d.warp, d.threads_per_block, d.threads_per_sm, d.registers_per_block,
                              d.registers_per_sm, d.shared_per_sm, d.shared_per_block, d.shared_reserved, block,
                              registers, shared)))  # fmt: skip


def calculator(questions: list[str], include: Path, cxx: str = "g++") -> list[list[int]]:
    """The calculator's answer to each question, from a driver built against the header at `include`."""
    with tempfile.TemporaryDirectory(prefix="cairn-occupancy-") as scratch:
        source, exe = Path(scratch) / "occupancy.cpp", Path(scratch) / "occupancy"
        source.write_text(DRIVER, encoding="utf-8")
        built = command(cxx, str(source), str(exe), kind="exe")
        subprocess.run([*built[:-3], f"-I{include}", *built[-3:]], check=True, capture_output=True, text=True)
        done = subprocess.run([str(exe)], input="\n".join(questions) + "\n", capture_output=True, text=True, check=True)
    return [[int(x) for x in line.split()] for line in done.stdout.splitlines()]


def ours(d: Device, block: int, registers: int, shared: int) -> list[int]:
    """What the card says, in the calculator's shape: its blocks, by each limit, and the bits of the binding ones."""
    by = d.resident(registers, block, shared)
    least = min(by.values())
    bits = sum(bit for name, bit in LIMITS.items() if by.get(name) == least)
    return [least, by["threads"], by.get("registers", UNLIMITED), by["shared memory"], by["blocks"], bits]


def compare(cxx: str = "g++") -> dict:
    """Every card over the grid, beside the calculator: the questions asked, and each disagreement; or a skip, without
    the header."""
    include = header()
    if include is None:
        return {"schema": "cairn.occupancy/1", "status": "skipped", "reason": "no cuda_occupancy.h beside an nvcc"}
    asked = [(key, spec.device, b, r, s) for key, spec in cards().items() for b in BLOCKS for r in REGISTERS
             for s in SHARED]  # fmt: skip
    answers = calculator([question(d, b, r, s) for _, d, b, r, s in asked], include.parent, cxx)
    per_card: dict[str, dict] = {}
    differ = []
    for (key, d, b, r, s), (status, *theirs) in zip(asked, answers, strict=True):
        theirs[-1] &= sum(LIMITS.values())  # the barrier and virtual resource bits: CAIRN's kernels use neither
        mine = ours(d, b, r, s)
        row = per_card.setdefault(key, {"compute_capability": d.compute_capability, "questions": 0, "agree": 0,
                                        "blocks_per_sm": d.blocks_per_sm, "limited_by": {}})  # fmt: skip
        row["questions"] += 1
        row["agree"] += status == 0 and mine == theirs
        for name, bit in LIMITS.items():
            row["limited_by"][name] = row["limited_by"].get(name, 0) + bool(mine[-1] & bit and mine[0])
        if status or mine != theirs:
            differ.append({"card": key, "block": b, "registers": r, "shared": s, "status": status,
                           "calculator": theirs, "card_says": mine})  # fmt: skip
    return {
        "schema": "cairn.occupancy/1",
        "status": "agree" if not differ else "differ",
        "header": str(include),
        "toolkit": toolkit_record(),
        "compiler": version(find(cxx)).splitlines()[0],
        "grid": {"threads_per_block": BLOCKS, "registers_per_thread": REGISTERS, "shared_bytes_per_block": SHARED},
        "fields": ["blocks", "by threads", "by registers", "by shared memory", "by blocks", "binding limits"],
        "cards": per_card,
        "questions": len(asked),
        "differ": differ,
    }


def device(cxx: str = "g++") -> dict:
    """The limits the GPU reports beside the card of its compute capability; only under `make device-limits`."""
    from support import device_lock, device_reason

    if reason := device_reason():
        raise SystemExit(json.dumps({"status": "refused", "reason": reason}))
    with tempfile.TemporaryDirectory(prefix="cairn-device-limits-") as scratch:
        source, exe = Path(scratch) / "limits.cu", Path(scratch) / "limits"
        source.write_text(QUERY, encoding="utf-8")
        subprocess.run([find("nvcc"), "-ccbin", find(cxx), str(source), "-o", str(exe)], check=True)
        with device_lock():
            said = json.loads(subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout)
    matching = {key: spec.device for key, spec in cards().items() if spec.device and
                spec.device.compute_capability == said["compute_capability"]}  # fmt: skip
    differ = {key: {k: {"card": getattr(d, k), "device": said[k]} for k in COMPARED if getattr(d, k) != said[k]}
              for key, d in matching.items()}  # fmt: skip
    return {"schema": "cairn.device-limits/1", "status": "agree" if matching and not any(differ.values()) else "differ",
            "device": said, "cards": differ}  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write the record here as well")
    ap.add_argument("--cxx", default="g++")
    ap.add_argument("--device", action="store_true", help="ask the GPU (make device-limits only)")
    a = ap.parse_args(argv)
    found = device(a.cxx) if a.device else compare(a.cxx)
    if found["status"] == "skipped":
        print(json.dumps(found, indent=1))
        return 3
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    shown = found if a.device else {k: found[k] for k in ("status", "header", "questions")} | {
        "agree": {key: f"{row['agree']} of {row['questions']}" for key, row in found["cards"].items()},
        "differ": found["differ"][:10]}  # fmt: skip
    print(json.dumps(shown, indent=1))
    return 0 if found["status"] == "agree" else 1


if __name__ == "__main__":
    raise SystemExit(main())

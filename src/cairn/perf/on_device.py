"""Time a CAIRN function on the device: only under the owner's make target, one run at a time, holding the lock.

Device runs on the reference machine have reset its display driver and crashed the host, so nothing here runs device
code unless `CAIRN_GPU_TESTS=1`, which only `make gpu`, `make tune-device` and `make calibrate-device` set, and every
run holds the machine-wide device lock (`/tmp/cairn-gpu.lock`, the one `tools/support.py` holds). `program` writes the
timed program without building or running it, which is what the suite compiles to check it. Device views are filled on
the host and copied across once before timing; each timed call is the function's own launch and the wait after it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import tempfile
import time as clock
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..compiler.cairnc import RUNTIME_FILES, compile_program, compile_source
from ..compiler.tree import CPP, FLOAT, is_view
from ..projects.toolchain import command
from .measure import DRIVER, fill

DEVICE_LOCK = Path("/tmp/cairn-gpu.lock")
COOLDOWN_S = 2.0  # between two device runs: the driver's engine gets a rest
BUDGET = 64  # device runs one process may make; a tuning round that needs more is refused, not continued
MEMORY = {"device": "cudaMalloc", "pinned": "cudaMallocHost", "unified": "cudaMallocManaged"}
ran = 0


def allowed() -> str:
    """Why device code may not run here, or "" when the owner's target has allowed it."""
    if os.environ.get("CAIRN_GPU_TESTS") != "1":
        return "device code runs only under the owner's make targets (CAIRN_GPU_TESTS=1): make gpu, make tune-device"
    return ""


@contextlib.contextmanager
def locked():
    DEVICE_LOCK.touch(exist_ok=True)
    with DEVICE_LOCK.open("r") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


def driver(f: Any, sizes: Mapping[str, float], fills: dict[str, str], block_ns: float, blocks: int) -> str:
    setup, args, params = [], [], []
    for name, ty in f.params:
        if is_view(ty):
            extent = int(ty.extent) if ty.extent.isdigit() else int(sizes[ty.extent])
            element = CPP[ty.name]
            setup.append(f"  Held<{element}> h_{name}({extent});")
            setup.append(f"  for(std::size_t k = 0; k < {extent}; ++k) h_{name}.p[k] = {fill(ty, fills.get(name))};")
            if ty.place in MEMORY:
                setup.append(f"  {element}* b_{name} = nullptr;")
                setup.append(f"  if({MEMORY[ty.place]}(reinterpret_cast<void**>(&b_{name}), {extent} * sizeof({element}))"
                             f" != cudaSuccess) return 3;")  # fmt: skip
                setup.append(f"  if(cudaMemcpy(b_{name}, h_{name}.p, {extent} * sizeof({element}), cudaMemcpyDefault)"
                             f" != cudaSuccess) return 3;")  # fmt: skip
                args.append(f"b_{name}")
            else:
                args.append(f"h_{name}.p")
            params.append(f"{'const ' if ty.mode == 'ro' else ''}{element}*")
        elif ty.name in CPP and ty.name != "void":
            value = int(sizes.get(name, 3)) if ty.name not in FLOAT else sizes.get(name, 3)
            args.append(f"static_cast<{CPP[ty.name]}>({value})")
            params.append(CPP[ty.name])
        else:
            raise ValueError(f"{f.name} takes {ty.display()}, which the device timer cannot supply.")
    ret = CPP.get(f.ret.name, "void")
    call = f"cf_{f.name}({', '.join(args)});"
    if ret != "void":
        call = f"sink = sink + static_cast<std::uint64_t>({call[:-1]});"
    declaration = f'extern "C" {ret} cf_{f.name}({", ".join(params)}) noexcept;'
    return DRIVER.format(declaration=declaration, setup="\n".join(setup), call=call, block_ns=block_ns, blocks=blocks)


def program(source: str, symbol: str, sizes: Mapping[str, float], fills: dict[str, str] | None = None,
            block_ns: float = 2e6, blocks: int = 9) -> str:  # fmt: skip
    """The emitted program and its timing driver, as one CUDA translation unit; nothing is built or run."""
    p, _, _ = compile_program(source)
    f = next((f for f in p.functions if f.name == symbol), None)
    if f is None:
        raise ValueError(f"No function {symbol} to time.")
    cpp, receipt = compile_source(source)
    if "cuda" not in receipt["requires"]:
        raise ValueError(f"{symbol} has no device code; time it on the host with cairn.perf.measure.")
    return cpp + driver(f, sizes, fills or {}, block_ns, blocks)


def time_device(source: str, symbol: str, sizes: Mapping[str, float], *, fills: dict[str, str] | None = None,
                cxx: str = "g++", block_ns: float = 2e6, blocks: int = 9, timeout: int = 300) -> dict[str, Any]:  # fmt: skip
    """The median time of one call of `symbol` at `sizes` on the device, only where `allowed()` says it may run."""
    global ran
    if reason := allowed():
        raise ValueError(reason)
    if ran >= BUDGET:
        raise ValueError(f"This process has made its {BUDGET} device runs; start another tuning round later.")
    text = program(source, symbol, sizes, fills, block_ns, blocks)
    with tempfile.TemporaryDirectory(prefix="cairn-device-time-") as scratch:
        directory = Path(scratch)
        (directory / "timed.cu").write_text(text, encoding="utf-8")
        for name, header in RUNTIME_FILES.items():
            (directory / name).write_text(header, encoding="utf-8")
        exe = str(directory / "timed")
        subprocess.run(command(cxx, str(directory / "timed.cu"), exe, kind="exe", cuda=True), check=True,
                       capture_output=True, text=True, timeout=600)  # fmt: skip
        with locked():
            ran += 1
            done = subprocess.run([exe], capture_output=True, text=True, timeout=timeout)
            clock.sleep(COOLDOWN_S)
    if done.returncode:
        return {"status": "trapped" if done.returncode < 0 else "failed", "exit": done.returncode,
                "stderr": done.stderr[:2000]}  # fmt: skip
    return {"status": "measured", **json.loads(done.stdout)}


# What a device profile measures: a copy for the memory's sustained bandwidth, one element for a launch and the wait
# after it, and a pinned transfer at two sizes for the link's fixed cost and its bandwidth.
KERNELS = """
fn stream(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { out[i] = x[i]; } }
fn touch(n:usize, out:rw<f32>[n]@device) { parallel i in n { out[i] = 1.0; } }
fn cross(n:usize, dev:rw<f32>[n]@device, host:ro<f32>[n]@pinned) { transfer(dev, host); }
"""


def calibrate_device(base: str = "rtx-5070-ti") -> dict[str, Any]:
    """The packaged specification with its assumed figures replaced by measured ones, where `allowed()` lets it run."""
    from .profile import PROFILES

    profile = json.loads((PROFILES / f"{base}.json").read_text(encoding="utf-8"))
    big, small = 1 << 26, 1 << 16
    copied = time_device(KERNELS, "stream", {"n": big})
    launched = time_device(KERNELS, "touch", {"n": 1})
    near, far = (time_device(KERNELS, "cross", {"n": n}) for n in (small, big))
    if any(r["status"] != "measured" for r in (copied, launched, near, far)):
        raise RuntimeError(f"A device calibration run failed: {[copied, launched, near, far]}")
    per_byte = (far["min_ns"] - near["min_ns"]) / ((big - small) * 4)
    card = profile["device"]
    card |= {"dram_gbps": round(big * 8 / copied["min_ns"], 1), "memory_efficiency": 1.0,
             "launch_ns": round(launched["min_ns"], 1), "link_gbps": round(1 / per_byte, 2),
             "link_ns": round(max(near["min_ns"] - small * 4 * per_byte, 0.0), 1)}  # fmt: skip
    profile |= {"origin": "measured", "notes": "Measured by make calibrate-device: the least-disturbed of nine blocks "
                "per kernel; the SM count, clock and register file are still the published figures."}  # fmt: skip
    profile["source"]["measured"] = ["dram_gbps", "memory_efficiency", "launch_ns", "link_gbps", "link_ns"]
    profile["source"]["assumed"] = [a for a in profile["source"]["assumed"] if a not in profile["source"]["measured"]]
    return profile


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Measure the device into a profile; only under make calibrate-device.")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    if reason := allowed():
        print(json.dumps({"status": "refused", "reason": reason}))
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(calibrate_device(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "calibrated", "profile": str(a.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

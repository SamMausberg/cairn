#!/usr/bin/env python3
"""Hand-written CUDA beside its CAIRN twin: build both, compare their SASS, and time them on a device.

    python3 bench/device/device.py build [PAIR ...]   compile every pair for sm_120 under results/device/
    python3 bench/device/device.py sass [PAIR ...]    the kernels' instructions, registers and local memory, as JSON
    python3 bench/device/device.py run PAIR [REPS [SKIP]]  one device process, only with CAIRN_GPU_TESTS=1

`build` and `sass` launch nothing. `run` runs one built pair on the GPU, holding /tmp/cairn-gpu.lock, under a
30-second timeout, and prints its JSON; it refuses unless CAIRN_GPU_TESTS=1 is set for this one process. `--src`
names the `src` directory whose compiler and runtime build the CAIRN side, so a pair can be built at another
revision; `--out` names where the builds go.

The CAIRN side is the generated C++ of PAIR.cairn and its C header, compiled by CAIRN's own device command line
(projects/toolchain.py) with g++ as nvcc's host compiler. The CUDA side is PAIR.cu, compiled the way its author
would, `nvcc -O3 -arch=sm_120`, with the pair's main. Both link into one program, so the two run in one process on
one stream, interleaved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PAIRS = ["overheads", "reduce", "reduce_wide", "reduction", "saxpy", "layernorm", "transpose", "stencil", "capture"]
# A pair whose CAIRN side is not PAIR.cairn: its sources, compiled as one program.
SOURCES = {"reduction": [ROOT / "examples/reduction/src/device_sum.cairn", HERE / "reduce_wide.cairn"]}
ARCH = "sm_120"
PLAIN = ["-std=c++20", "-O3", f"-arch={ARCH}"]  # how the hand-written side is compiled
# The hand-written side's CUB lives in a namespace of its own: two objects of one program that instantiate the same
# CUB kernel under the same name fail at run time with "invalid device function".
WRAPPED = ["-DTHRUST_CUB_WRAPPED_NAMESPACE=bench"]


def load(src: Path):
    """The compiler under `src`, imported ahead of any other."""
    sys.path.insert(0, str(src))
    from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
    from cairn.compiler.header import header
    from cairn.projects.target import parse
    from cairn.projects.toolchain import device_prefix

    return RUNTIME_FILES, compile_source, header, parse, device_prefix


def build(pair: str, src: Path, out: Path) -> Path:
    """PAIR's program under out/PAIR/, and the two objects its SASS is read from."""
    runtime, compile_source, header, parse, device_prefix = load(src)
    where = out / pair
    where.mkdir(parents=True, exist_ok=True)
    objects, enqueue = [], False
    sources = SOURCES.get(pair, [HERE / f"{pair}.cairn"])
    if all(source.exists() for source in sources):
        text = "\n".join(source.read_text() for source in sources)
        declared, checks = header(text, pair, device=True)
        enqueue = "cq_" in declared
        (where / f"{pair}.h").write_text(declared)
        (where / f"{pair}_cairn.cpp").write_text(compile_source(text)[0] + "\n" + checks)
        for name, body in runtime.items():
            (where / name).write_text(body)
        line = [p for p in device_prefix("g++", None, "exe", parse(ARCH)) if p != "-shared"]
        line += ["-c", f"-I{where}", str(where / f"{pair}_cairn.cpp"), "-o", str(where / "cairn.o")]
        subprocess.run(line, check=True, timeout=900)
        objects.append(where / "cairn.o")
    line = [
        "nvcc",
        *PLAIN,
        *WRAPPED,
        f"-DBENCH_ENQUEUE={int(enqueue)}",
        f"-I{where}",
        f"-I{HERE}",
        "-c",
        str(HERE / f"{pair}.cu"),
    ]
    subprocess.run([*line, "-o", str(where / "cuda.o")], check=True, timeout=900)
    objects.append(where / "cuda.o")
    subprocess.run(["nvcc", f"-arch={ARCH}", *map(str, objects), "-o", str(where / pair)], check=True, timeout=300)
    return where / pair


FUNCTION = re.compile(r"^\s*Function : (\S+)", re.M)


def demangled(names: list[str]) -> list[str]:
    tool = shutil.which("cu++filt")
    if not tool or not names:
        return names
    done = subprocess.run([tool], input="\n".join(names), capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def hot_loop(lines: list[tuple[str, str, str]]) -> int:
    """The instructions of the innermost loop that loads from global memory: from a backward branch's target to the
    branch, the smallest such span holding an LDG; 0 when no loop loads."""
    at = [int(address, 16) for address, _, _ in lines]
    best = 0
    for k, (_, op, rest) in enumerate(lines):
        target = re.match(r"\s*(?:\S+,\s*)?(0x[0-9a-f]+)", rest)
        if not op.startswith("BRA") or target is None or int(target.group(1), 16) > at[k]:
            continue
        start = at.index(int(target.group(1), 16)) if int(target.group(1), 16) in at else k
        span = [o for _, o, _ in lines[start : k + 1] if o != "NOP"]
        if any(o.startswith("LDG") for o in span) and (best == 0 or len(span) < best):
            best = len(span)
    return best


def kernels(obj: Path) -> dict[str, dict]:
    """Each kernel of an object: its instruction mix from `cuobjdump -sass`, its resources from `-res-usage`."""
    text = subprocess.run(["cuobjdump", "-sass", str(obj)], capture_output=True, text=True, check=True).stdout
    parts = FUNCTION.split(text)[1:]
    mangled = parts[0::2]
    found: dict[str, dict] = {}
    for name, (raw, body) in zip(demangled(mangled), zip(mangled, parts[1::2], strict=True), strict=True):
        lines = re.findall(r"/\*([0-9a-f]{4,})\*/\s+(?:@!?U?P\w+\s+)?([A-Z][A-Z0-9_.]*)([^;]*)", body)
        ops = [op for _, op, _ in lines]
        ops = [op for op in ops if op != "NOP"]
        first = [op.split(".")[0] for op in ops]
        found[raw] = {
            "name": name,
            "instructions": len(ops),
            "global_loads": sum(op.startswith("LDG") for op in ops),
            "wide_loads": sum(op.startswith("LDG") and ".128" in op for op in ops),
            "branches": first.count("BRA"),
            "traps": first.count("BPT"),
            "calls": first.count("CALL"),
            "carry_ops": sum("X" in op.split(".")[1:] for op in ops),  # the high halves of 64-bit arithmetic
            "wide_multiplies": sum(op.startswith(("IMAD.WIDE", "IMAD.HI")) for op in ops),
            "fma": first.count("FFMA"),
            "fmul": first.count("FMUL"),
            "fadd": first.count("FADD"),
            "barriers": first.count("BAR"),
            "shuffles": first.count("SHFL"),
            "shared_stores": first.count("STS"),
            "loop": hot_loop(lines),
        }
    usage = subprocess.run(["cuobjdump", "-res-usage", str(obj)], capture_output=True, text=True, check=True).stdout
    for raw, rest in re.findall(r"Function (\S+):\n\s*(.*)", usage):
        if raw in found:
            fields = dict(re.findall(r"(REG|STACK|SHARED|LOCAL):(\d+)", rest))
            found[raw].update({k.lower(): int(v) for k, v in fields.items()})
    return found


def sass(pair: str, out: Path) -> dict:
    where = out / pair
    sides = {side: kernels(where / f"{side}.o") for side in ("cairn", "cuda") if (where / f"{side}.o").exists()}
    return {side: sorted(found.values(), key=lambda k: k["name"]) for side, found in sides.items()}


def run(pair: str, out: Path, extra: list[str]) -> int:
    sys.path.insert(0, str(ROOT / "tools"))
    from support import device_lock, device_reason

    if reason := device_reason():
        print(f"{reason}: nothing ran", file=sys.stderr)
        return 1
    program = out / pair / pair
    with device_lock():
        done = subprocess.run(["timeout", "30", str(program), *extra], capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["build", "sass", "run"])
    parser.add_argument("pairs", nargs="*")
    parser.add_argument("--src", type=Path, default=ROOT / "src")
    parser.add_argument("--out", type=Path, default=ROOT / "results/device")
    args = parser.parse_args()
    if args.action == "run":
        if not args.pairs:
            parser.error("run takes one pair, optional reps and a variant to leave out")
        return run(args.pairs[0], args.out, args.pairs[1:])
    chosen = args.pairs or PAIRS
    if args.action == "build":
        for pair in chosen:
            print(build(pair, args.src.resolve(), args.out))
        return 0
    print(json.dumps({pair: sass(pair, args.out) for pair in chosen}, indent=1))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())

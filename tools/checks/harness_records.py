#!/usr/bin/env python3
"""Records for evidence/v1_1/harness: the submissions `cairn export --harness` writes, built as each benchmark builds
them against a CUDA torch, and never loaded or run.

Run it with a Python that has torch (the CPU wheel suffices) and, for the schema check, sol-execbench installed from
its repository with --no-deps:

    python tools/checks/harness_records.py --upstream DIR --torch-root DIR

--upstream holds clones of NVIDIA/SOL-ExecBench, gpu-mode/reference-kernels and ScalingIntelligence/KernelBench as
sol/, gpumode/ and kernelbench/, each at the commit projects/harness.py pins. --torch-root is the torch directory of a
CUDA wheel, unpacked. Each build writes the ninja file the running torch writes (torch.utils.cpp_extension) and swaps
in that root's headers and libraries, so it compiles and links what a GPU machine's torch would. Four SOL-ExecBench
problems of the pinned repository become projects through `cairn new --from-sol-execbench`, one with an RMSNorm
kernel written in CAIRN; GPU MODE's vectoradd_v2 and KernelBench's level-1 ReLU get CAIRN kernels of their own.
Nothing here loads what it built, runs device code, runs an evaluator or submits. The record goes to --out.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import torch
import torch.utils.cpp_extension as ext

from cairn.projects import harness
from cairn.projects.harness_import import create
from cairn.projects.project import load_project
from cairn.projects.target import toolkit_record

TARGET = "sm_100a"
SOL = [
    ("examples/cuda_cpp/rmsnorm", "rmsnorm"),
    ("examples/cuda_cpp/flux_rope", "reference"),
    ("tests/sol_execbench/samples/gqa_paged_decode", "reference"),
    ("tests/sol_execbench/samples/gemma3_swiglu", "reference"),
]
# RMSNorm over rows of 4096 as the definition's reference computes it: the mean of squares in f32, x * rsqrt(mean +
# 1e-5) * weight, rounded to bf16. One block of 256 threads per row; the sum's order is the block's tree, not torch's.
RMSNORM = """{
  blocks b in batch_size threads t in 256 {
    shared partial:f32[256] = zeroed;
    let row = b * HIDDEN_SIZE;
    let mut sum:f32 = 0.0;
    for k in 0..16 {
      let x = f32(hidden_states[row + k * 256 + t]);
      sum += x * x;
    }
    partial[t] = sum;
    barrier;
    for k in 0..3 {
      let s:usize = shr(128, k);
      if t < s { partial[t] = partial[t] + partial[t + s]; }
      barrier;
    }
    if t < 32 {
      let total = reduce + warp yield partial[t];
      if t == 0 { partial[0] = total; }
    }
    barrier;
    let inv = 1.0 / sqrt(partial[0] / 4096.0 + 0.00001);
    for k in 0..16 {
      let i = row + k * 256 + t;
      output[i] = bf16(f32(hidden_states[i]) * inv * f32(weight[k * 256 + t]));
    }
  }
}
"""
VECTORADD = """// GPU MODE's vectoradd_v2: output = A + B over float16, each sum rounded once to f16.
pub fn vectoradd(n:usize, a:ro<f16>[n]@device, b:ro<f16>[n]@device, output:rw<f16>[n]@device) {
  parallel i in n { output[i] = f16(f32(a[i]) + f32(b[i])); }
}
"""
VECTORADD_MAP = """[benchmark]
leaderboard = "vectoradd_v2"
gpu = "B200"
problem = "problem"

[extents]
n = "size * size"

[[argument]]
name = "A"
parameter = "a"
dtype = "float16"
shape = ["size", "size"]

[[argument]]
name = "B"
parameter = "b"
dtype = "float16"
shape = ["size", "size"]

[[argument]]
name = "output"
parameter = "output"
dtype = "float16"
shape = ["size", "size"]
output = true
"""
RELU = """// KernelBench level 1, problem 19: ReLU, which keeps a NaN a NaN.
pub fn relu(n:usize, x:ro<f32>[n]@device, out:rw<f32>[n]@device) {
  parallel i in n {
    let v = x[i];
    if v < 0.0 { out[i] = 0.0; } else { out[i] = v; }
  }
}
"""
RELU_MAP = """[benchmark]
problem = "problem/19_ReLU.py"

[extents]
n = "batch_size * dim"

[[argument]]
name = "x"
parameter = "x"
dtype = "float32"
shape = ["batch_size", "dim"]

[[result]]
name = "out"
parameter = "out"
dtype = "float32"
shape = ["batch_size", "dim"]
"""


def ninja(directory: Path, name: str, sources: list[str], flags: dict, includes: list[str], root: Path) -> dict:
    """Write torch's own ninja file for these sources, point it at `root`'s headers and libraries, and build it."""
    with_cuda = any(s.endswith(".cu") for s in sources)
    ld = ext._prepare_ldflags(list(flags["ld_flags"]), with_cuda, False, False)
    path = directory / "build.ninja"
    ext._write_ninja_file_to_build_library(str(path), name, sources, flags["cflags"], flags["cuda_cflags"], [], ld,
                                           includes, with_cuda, False, False)  # fmt: skip
    path.write_text(path.read_text().replace(str(Path(torch.__file__).parent), str(root)))
    started = time.monotonic()
    done = subprocess.run(["ninja"], cwd=directory, capture_output=True, text=True, timeout=1800)
    built = sorted(directory.glob("*.so"))
    return {"status": "compiled-and-linked" if done.returncode == 0 and built else "build-failed",
            "seconds": round(time.monotonic() - started, 1), "with_cuda": with_cuda,
            "objects": sorted(p.name for p in directory.glob("*.o")),
            **({"library_sha256": hashlib.sha256(built[0].read_bytes()).hexdigest()} if built else {}),
            **({"stderr": (done.stdout + done.stderr)[-3000:]} if done.returncode else {})}  # fmt: skip


def solution_build(out: Path, stage: Path, root: Path) -> dict:
    """solution.json staged as SOL-ExecBench's driver/templates/build_ext.py stages it, then built."""
    solution = json.loads((out / "solution.json").read_text())
    stage.mkdir(parents=True)
    for source in solution["sources"]:
        (stage / source["path"]).write_text(source["content"])
    sources = sorted(str(p) for p in stage.iterdir() if p.suffix in (".cu", ".cpp", ".cc", ".cxx", ".c"))
    return ninja(stage, "benchmark_kernel", sources, solution["spec"]["compile_options"], [str(stage)], root)


def inline_build(path: Path, stage: Path, root: Path) -> dict:
    """The Python file imported with load_inline's last step replaced: load_inline writes main.cpp and cuda.cu as it
    always does, and they are built as above instead of loaded."""
    result: dict = {}

    def jit(name, sources, cflags, cuda_cflags, sycl, ldflags, includes, directory, *rest, **more):
        flags = {"cflags": cflags or [], "cuda_cflags": cuda_cflags or [], "ld_flags": ldflags or []}
        result.update(ninja(Path(directory), name, sources, flags, includes or [], root))
        return None

    real_jit, real_inline = ext._jit_compile, ext.load_inline
    ext._jit_compile = jit
    ext.load_inline = lambda *a, **k: real_inline(*a, **{**k, "build_directory": str(stage)})
    stage.mkdir(parents=True)
    try:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    finally:
        ext._jit_compile, ext.load_inline = real_jit, real_inline
    return result


def schema(out: Path, definition: Path) -> str:
    """Whether SOL-ExecBench's own pydantic models accept the solution and the definition."""
    if importlib.util.find_spec("sol_execbench") is None:
        return "not-checked: sol_execbench is not installed"
    from sol_execbench.core import Definition, Solution

    Definition(**json.loads(definition.read_text()))
    Solution(**json.loads((out / "solution.json").read_text()))
    return "accepted by sol_execbench.core.Solution and Definition"


def static_check(path: Path, upstream: Path) -> dict:
    """KernelBench's static checker, from the pinned clone, over a generated file."""
    spec = importlib.util.spec_from_file_location("checker", upstream / "src/kernelbench/kernel_static_checker.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    valid, errors, warnings = checker.validate_kernel_static(path.read_text(), backend="cuda", precision="fp32")
    return {"valid": valid, "errors": errors, "warnings": warnings}


def main() -> int:
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("--upstream", type=Path, required=True)
    a.add_argument("--torch-root", type=Path, required=True)
    a.add_argument("--out", type=Path, default=ROOT / "evidence/v1_1/harness/builds.json")
    args = a.parse_args()
    ext.CUDA_HOME = ext.CUDA_HOME or str(Path(toolkit_record()["nvcc"]).parents[1])  # a CPU wheel records none
    pinned = {"sol": "sol-execbench", "gpumode": "gpumode", "kernelbench": "kernelbench"}
    upstream = {}
    for folder, fmt in pinned.items():
        head = subprocess.run(["git", "-C", str(args.upstream / folder), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()  # fmt: skip
        if head != harness.FORMATS[fmt]["upstream"]["commit"]:
            raise SystemExit(
                f"{folder} is at {head}; projects/harness.py pins {harness.FORMATS[fmt]['upstream']['commit']}"
            )
        upstream[fmt] = head
    version = (args.torch_root / "version.py").read_text()
    record: dict = {
        "schema": "cairn.evidence.harness/1",
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "torch_running": torch.__version__,
        "torch_built_against": next(
            line.split("'")[1] for line in version.splitlines() if line.startswith("__version__")
        ),
        "nvcc": toolkit_record(),
        "cxx": subprocess.run(["c++", "--version"], capture_output=True, text=True).stdout.splitlines()[0],
        "upstream": upstream,
        "device_target": TARGET,
        "solutions": [],
        "submissions": [],
        "not_run": ["nothing built here was loaded", "no device code ran", "no evaluator ran", "nothing was submitted"],
    }
    with tempfile.TemporaryDirectory(prefix="cairn-harness-") as scratch:
        work = Path(scratch)
        for folder, function in SOL:
            definition = args.upstream / "sol" / folder / "definition.json"
            name = Path(folder).name
            made = create(work / name, definition, TARGET)
            if name == "rmsnorm":  # the reference's body, written in CAIRN
                source = work / name / "src/rmsnorm.cairn"
                text = source.read_text()
                source.write_text(text[: text.rindex("{\n}")] + RMSNORM)
            out = work / f"{name}-sol"
            written = harness.write(load_project(work / name), "sol-execbench", function, out, cxx="g++")
            record["solutions"].append({
                "problem": f"sol/{folder}", "definition": made["definition"], "function": function,
                "kernel": "RMSNorm in a cooperative region" if name == "rmsnorm" else "none: the body is empty",
                "tolerance": made["tolerance"], "harness": written["identity"], "export": written["export"],
                "entry": written["entry"]["symbol"], "schema": schema(out, definition),
                "build": solution_build(out, work / f"{name}-stage", args.torch_root),
            })  # fmt: skip
        for fmt, source, mapping, symbol, problem in (
            ("gpumode", VECTORADD, VECTORADD_MAP, "vectoradd", "problems/pmpp_v2/vectoradd_py"),
            ("kernelbench", RELU, RELU_MAP, "relu", "KernelBench/level1/19_ReLU.py"),
        ):
            project = work / fmt
            (project / "src").mkdir(parents=True)
            (project / "src/main.cairn").write_text(source)
            (project / "cairn.toml").write_text(f'[project]\nname = "{symbol}"\nsources = ["src/main.cairn"]\n\n'
                                                f'[build]\nkind = "library"\ndevice_target = "{TARGET}"\n')  # fmt: skip
            (project / "harness.toml").write_text(mapping)
            out = work / f"{fmt}-out"
            written = harness.write(load_project(project), fmt, symbol, out, cxx="g++")
            file = out / harness.FORMATS[fmt]["file"]
            row = {"format": fmt, "problem": f"{'gpumode' if fmt == 'gpumode' else 'kernelbench'}/{problem}",
                   "function": symbol, "harness": written["identity"], "export": written["export"],
                   "entry": written["entry"]["symbol"],
                   "kernelbench_static_check": static_check(file, args.upstream / "kernelbench"),
                   "build": inline_build(file, work / f"{fmt}-stage", args.torch_root)}  # fmt: skip
            record["submissions"].append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    failed = [
        r for r in record["solutions"] + record["submissions"] if r["build"].get("status") != "compiled-and-linked"
    ]
    print(json.dumps({"written": str(args.out), "built": len(record["solutions"]) + len(record["submissions"]) - len(failed),
                      "failed": len(failed)}))  # fmt: skip
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

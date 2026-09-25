#!/usr/bin/env python3
"""Records for evidence/v1_1/harness: the submissions `cairn export --harness` writes, built as each benchmark builds
them against a CUDA torch, and never loaded or run.

Run it with a Python that has torch (the CPU wheel suffices) and, for the schema check, sol-execbench installed from
its repository with --no-deps:

    python tools/checks/harness_records.py --upstream DIR --torch-root DIR

--upstream holds clones of NVIDIA/SOL-ExecBench, gpu-mode/reference-kernels and ScalingIntelligence/KernelBench as
sol/, gpumode/ and kernelbench/, each at the commit projects/harness/harness.py pins. --torch-root is the torch directory of a
CUDA wheel, unpacked. Each build writes the ninja file the running torch writes (torch.utils.cpp_extension) and swaps
in that root's headers and libraries, so it compiles and links what a GPU machine's torch would. Four SOL-ExecBench
problems of the pinned repository become projects through `cairn new --from-sol-execbench`, one with an RMSNorm
kernel written in CAIRN; examples/harness packages GPU MODE's vectoradd_v2 and KernelBench's level-1 ReLU.
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

from cairn.projects.harness import harness
from cairn.projects.harness.importing import create
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


def emulated(project: Path, problem: Path, largest: int = 256) -> dict:
    """The RMSNorm kernel built with --emulate, its device work on host threads and its device memory host memory,
    called through its ctypes binding on CPU tensors, and compared under SOL-ExecBench's own formula and tolerance
    with the definition's PyTorch reference, for each workload of at most `largest` rows: an emulated cooperative
    region runs a block's 256 threads as host threads, too slowly for the rest."""
    import ctypes

    from cairn.compiler.lower.header import binding
    from cairn.projects.build import build

    loaded = load_project(project)
    built = build(loaded, output=project / "build", cxx="g++", kind="library", header=True, emulate=True, timeout=300)
    namespace: dict = {}
    exec(binding(loaded.source, loaded.name, lambda f: loaded.wrote(f.line), True), namespace)
    lib = namespace["load"](built["artifact"])
    definition = json.loads((problem / "definition.json").read_text())
    reference: dict = {}
    exec(definition["reference"], reference)
    torch.manual_seed(200)  # SOL-ExecBench's BenchmarkConfig seed
    rows = []
    for line in (problem / "workload.jsonl").read_text().splitlines():
        workload = json.loads(line)
        batch = workload["axes"]["batch_size"]
        if batch > largest:
            continue
        x, w = torch.randn(batch, 4096, dtype=torch.bfloat16), torch.randn(4096, dtype=torch.bfloat16)
        out = torch.empty_like(x)
        pointer = ctypes.POINTER(namespace["cairn_bf16"])
        lib.cf_rmsnorm(batch, x.numel(), *(ctypes.cast(t.data_ptr(), pointer) for t in (x, w, out)))
        tolerance = {"max_atol": 1e-2, "max_rtol": 1e-2, **(workload.get("tolerance") or {})}
        got, want = out.float(), reference["run"](x, w).float()
        within = (got - want).abs() <= tolerance["max_atol"] + tolerance["max_rtol"] * want.abs()
        rows.append({"batch_size": batch, "matched_ratio": within.float().mean().item(),
                     "max_abs_error": (got - want).abs().max().item(),
                     "bit_equal_ratio": (got == want).float().mean().item()})  # fmt: skip
    return {"built": built["status"], "emulation": built.get("emulation"), "workloads": rows,
            "skipped": f"workloads of more than {largest} rows"}  # fmt: skip


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
                f"{folder} is at {head}; projects/harness/harness.py pins {harness.FORMATS[fmt]['upstream']['commit']}"
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
            if name == "rmsnorm":
                record["rmsnorm_emulated"] = emulated(work / name, work / name / "problem")
            record["solutions"].append({
                "problem": f"sol/{folder}", "definition": made["definition"], "function": function,
                "kernel": "RMSNorm in a cooperative region" if name == "rmsnorm" else "none: the body is empty",
                "tolerance": made["tolerance"], "harness": written["identity"], "export": written["export"],
                "entry": written["entry"]["symbol"], "schema": schema(out, definition),
                "build": solution_build(out, work / f"{name}-stage", args.torch_root),
            })  # fmt: skip
        example = load_project(ROOT / "examples/harness")
        for fmt, symbol, mapping, problem in (
            ("gpumode", "vectoradd", None, "gpumode/problems/pmpp_v2/vectoradd_py"),
            (
                "kernelbench",
                "relu",
                ROOT / "examples/harness/kernelbench.toml",
                "kernelbench/KernelBench/level1/19_ReLU.py",
            ),
        ):
            out = work / f"{fmt}-out"
            written = harness.write(example, fmt, symbol, out, cxx="g++", mapping_path=mapping)
            file = out / harness.FORMATS[fmt]["file"]
            row = {"format": fmt, "problem": problem, "project": "examples/harness", "function": symbol,
                   "harness": written["identity"], "export": written["export"], "entry": written["entry"]["symbol"],
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

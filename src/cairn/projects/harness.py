"""`cairn export --harness`: a CAIRN function as a SOL-ExecBench solution, a GPU MODE submission or a KernelBench
`ModelNew`, beside the export it embeds and a record, `cairn.harness/1`, that pins both.

The adapter reads `harness.toml` (projects/harness_mapping.py), writes the export (projects/export.py) and the
submission around it (projects/harness_sources.py), and records the format and the upstream revision it follows,
the CAIRN function and its signature, the argument mapping, the entry the binding calls, the flags and why, and the
export's identity. It never runs an evaluator, never submits and never reaches the network. It prints the commands
that would, which are the user's to run: GPU MODE's `--mode leaderboard` is a public ranked submission.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..compiler.lower.codegen import mangle
from ..compiler.lower.header import Header
from ..version import VERSION
from . import export
from .harness_mapping import Mapping, load
from .harness_sources import Library, binding, gpumode, kernelbench, python, signature, solution
from .project import Project, ProjectError, digest
from .target import parse, refuse
from .toolchain import extension_flags

SCHEMA = "cairn.harness/1"
RECORD = "harness.json"
MAPPING = "harness.toml"
# Each format: the file it reads, the upstream revision this adapter follows and the files it follows there, what
# its score means, and the commands that would evaluate or submit, which the adapter prints and never runs.
FORMATS: dict[str, dict[str, Any]] = {
    "sol-execbench": {
        "file": "solution.json",
        "upstream": {
            "repository": "https://github.com/NVIDIA/SOL-ExecBench",
            "commit": "a9fa0804c793d438e70850c33fe34426e66d53dd",
            "follows": [
                "docs/solution.md",
                "src/sol_execbench/core/data/solution.py",
                "src/sol_execbench/driver/templates/build_ext.py",
            ],
        },
        "metric": "Each workload scores S = 1 / (1 + (T_k - T_SOL) / (T_b - T_SOL)), with T_k the kernel's time, "
        "T_b the benchmark's optimized PyTorch time and T_SOL SOLAR's modelled speed of light: 0.5 at the baseline "
        "and 1.0 at T_SOL. It is not a utilization, and not the speed-of-light fraction cairn predict gives.",
    },
    "gpumode": {
        "file": "submission.py",
        "upstream": {
            "repository": "https://github.com/gpu-mode/reference-kernels",
            "commit": "f3295bb6bd559bae4659d2d8fc45edb1394251b1",
            "follows": [
                "problems/pmpp_v2/vectoradd_py/solutions/correct/submission_cuda_inline.py",
                "problems/pmpp_v2/eval.py",
            ],
            "cli": {
                "repository": "https://github.com/gpu-mode/popcorn-cli",
                "commit": "036b3ead604f1e9f404cbee5cf0e48377ef0766f",
            },
        },
        "metric": "A leaderboard ranks by each benchmark's mean time in seconds, lower first: the one benchmark's "
        "under ranking_by last, their arithmetic mean under mean, their geometric mean under geom.",
    },
    "kernelbench": {
        "file": "model_new.py",
        "upstream": {
            "repository": "https://github.com/ScalingIntelligence/KernelBench",
            "commit": "423217d9fda91e0c2d67e4a43bf62f96f6d104f1",
            "follows": [
                "src/kernelbench/prompts/model_new_ex_add.py",
                "src/kernelbench/eval.py",
                "src/kernelbench/kernel_static_checker.py",
            ],
        },
        "metric": "fast_p is the fraction of problems whose ModelNew is correct and faster than the PyTorch Model "
        "by more than p, speedup being PyTorch time over kernel time; fast_0 is correctness alone.",
    },
}
# A GPU a benchmark names, and its compute capability, so a target whose code would not load there is refused.
GPUS = {"B200": "10.0", "H100": "9.0", "H200": "9.0", "A100": "8.0", "L4": "8.9", "L40S": "8.9", "T4": "7.5"}
WHY_FLAGS = (
    "CAIRN's numerical contract is IEEE arithmetic with one rounding per operation: no contraction into fused "
    "multiply-adds (--fmad=false on the device, -ffp-contract=off on the host) and no fast math. SOL-ExecBench's "
    "default cuda_cflags are -O3 --use_fast_math, so compile_options replaces them. The program needs C++20, which a "
    "later -std overrides torch's -std=c++17 with, and nvcc's extended lambdas; one -arch names the device target, "
    "so torch adds no architecture of its own."
)
NOT_RUN = ["nothing ran on a GPU", "no official evaluator ran", "nothing was submitted", "no score was measured"]


def library(export_dir: Path, record: dict[str, Any], mapping: Mapping, header: Header) -> Library:
    """What the binding calls, read from the export's own C header."""
    name = next(n for n in record["files"] if n.endswith(".h"))[:-2]
    declared = (export_dir / (name + ".h")).read_text(encoding="utf-8")
    entry = f"cf_{mangle(mapping.function.name)}"
    stream = re.search(r"void (cairn_\w+_device_stream)\(void \*stream\);", declared)
    no_wait = f"cq_{mangle(mapping.function.name)}"
    ctypes = {n: header.ctype(t) for n, t in mapping.function.params}
    cuda = record.get("device_target") is not None
    return Library(name, entry, stream.group(1) if stream else None,
                   no_wait if re.search(rf"\b{no_wait}\(", declared) else None, cuda, ctypes,
                   waiting(declared, mapping.function.name))  # fmt: skip


def waiting(declared: str, function: str) -> str:
    """Why the header gives `function` no enqueued entry, as its E-ENQUEUE comment says, or ""."""
    if "No enqueued entry" not in declared:
        return ""
    said: list[str] = []
    for line in declared.split("No enqueued entry", 1)[1].splitlines()[1:]:
        text = line.removeprefix(" * ")
        if line.strip() == "*/" or (said and said[-1].endswith(".")):
            break
        if said or text.startswith(f"{function}: "):
            said.append(text)
    return " ".join(said).removeprefix(f"{function}: ").removesuffix(".")


def entry_record(lib: Library) -> dict[str, Any]:
    if not lib.cuda:
        return {"symbol": lib.entry, "stream": None, "waits": "a host library: the call returns when its work is done"}
    if lib.no_wait:
        return {"symbol": lib.no_wait, "stream": "torch.cuda.current_stream().cuda_stream",
                "waits": "none: the work is queued on the stream and the call returns",
                "failure": "a guard that fails in a device lane traps, which poisons the CUDA context; torch's next "
                "synchronization raises cudaErrorLaunchFailure"}  # fmt: skip
    return {"symbol": lib.entry, "stream": f"torch.cuda.current_stream().cuda_stream, bound by {lib.stream}",
            "waits": f"{lib.entry} returns once its own work on the stream has finished",
            "no_wait_entry": f"the header declares no {lib.no_wait_name}"
            + (f" ({lib.why_waits}, E-ENQUEUE)" if lib.why_waits else "")}  # fmt: skip


def hardware(fmt: str, mapping: Mapping, record: dict[str, Any]) -> list[str]:
    """The GPUs the submission names, checked against the device target: a target whose code would not load on a
    GPU the format or the mapping names is E-TARGET-MISMATCH."""
    target = parse(record["device_target"]) if record.get("device_target") else None
    if fmt == "sol-execbench":
        return ["B200", "LOCAL"] if target is None or target.runs_on(GPUS["B200"]) else ["LOCAL"]
    gpu = mapping.benchmark.get("gpu")
    if target and gpu in GPUS and not target.runs_on(GPUS[gpu]):
        raise refuse("E-TARGET-MISMATCH", f"The export is compiled for {target.name}, whose code does not load on "
                     f"the {gpu} (compute capability {GPUS[gpu]}) the mapping names; export for that GPU's target.")  # fmt: skip
    return [gpu] if gpu else []


def commands(fmt: str, mapping: Mapping, out: Path, root: Path) -> list[dict[str, str]]:
    """What would evaluate or submit the submission: printed for the user, never run by CAIRN. A problem the mapping
    names is a path from the project."""
    bench, where = mapping.benchmark, out.resolve() / FORMATS[fmt]["file"]
    problem = str(root.resolve() / bench["problem"]) if "problem" in bench else None
    if fmt == "sol-execbench":
        problem = problem or "PROBLEM_DIR"
        return [{"command": f"sol-execbench {problem} --solution {where}",
                 "does": "runs the official evaluator on your GPU: correctness and timing of every workload"}]  # fmt: skip
    if fmt == "kernelbench":
        reference = problem or "REFERENCE.py"
        return [{"command": f"uv run python scripts/run_and_check.py ref_origin=local ref_arch_src_path={reference} "
                 f"kernel_src_path={where} eval_mode=local", "does": "KernelBench's correctness and speedup check "
                 "on your GPU, run from its repository"}]  # fmt: skip
    board, gpu = bench.get("leaderboard", "LEADERBOARD"), bench.get("gpu", "GPU")
    does = {"test": "a correctness run on GPU MODE's machines", "benchmark": "a timed run, off the leaderboard",
            "profile": "an Nsight Compute profile on GPU MODE's machines",
            "leaderboard": "a public ranked submission under your name on gpumode.com"}  # fmt: skip
    return [{"command": f"popcorn submit --no-tui --leaderboard {board} --gpu {gpu} --mode {mode} {where}",
             "does": said} for mode, said in does.items()]  # fmt: skip


def write(project: Project, fmt: str, symbol: str, out: Path, *, mapping_path: Path | None = None,
          cxx: str = "clang++", device_target: str | None = None, keep_guards: bool = False) -> dict[str, Any]:  # fmt: skip
    """Check the mapping against `symbol`'s signature, then write the export, the binding and the submission into
    `out`, which must not exist, and return the record. Nothing is written when anything is refused."""
    if fmt not in FORMATS:
        raise ProjectError(f"--harness is one of {', '.join(FORMATS)}.")
    if out.exists() or out.is_symlink():
        raise ProjectError(f"{out} exists; a harness is written into a new directory, never over another.")
    header = Header(project.source, project.name, lambda f: project.wrote(f.line))
    header.render()
    found = [f for f in header.p.functions if f.name == symbol and project.wrote(f.line)]
    if not found:
        raise refuse("E-HARNESS-SYMBOL", f"The project declares no function {symbol}.")
    if not any(f is found[0] for f in header.exports):
        raise refuse("E-HARNESS-SYMBOL", f"{symbol} has no C entry for a binding to call: {header.refusal(found[0]) or 'it is not public'}.")  # fmt: skip
    mapping = load(mapping_path or project.root / MAPPING, found[0], fmt)
    if fmt == "sol-execbench" and "definition" not in mapping.benchmark:
        raise refuse("E-HARNESS-FORMAT", "A SOL-ExecBench solution names the definition it solves: set [benchmark] "
                     "definition in the mapping.")  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        made = written(project, fmt, mapping, staging, cxx, device_target, keep_guards, header, out)
        staging.rename(out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return made | {"directory": str(out)}


def written(project: Project, fmt: str, mapping: Mapping, staging: Path, cxx: str, device_target: str | None,
            keep_guards: bool, header: Header, out: Path) -> dict[str, Any]:  # fmt: skip
    export.export(project, staging / "export", cxx=cxx, kind="library", header=True, device_target=device_target,
                  keep_guards=keep_guards)  # fmt: skip
    record = export.check(staging / "export")
    gpus = hardware(fmt, mapping, record)
    lib = library(staging / "export", record, mapping, header)
    flags = extension_flags(parse(record["device_target"]) if lib.cuda else None)
    program = "program.cu" if lib.cuda else "program.cpp"
    files = {n: (staging / "export" / n).read_text(encoding="utf-8") for n in record["files"]}
    files = {program: files.pop(program), **files}
    doc = (f"The CAIRN function {mapping.function.name} as a {fmt} submission, generated by {VERSION} from the export "
           f"{record['identity'][:16]}. Do not edit: edit the CAIRN source or harness.toml, and export again.")  # fmt: skip
    if fmt == "sol-execbench":
        glue = binding(mapping, lib, fmt)
        text = solution(mapping, {**files, "main.cpp": glue}, record["identity"], flags, gpus, mapping.function.name)
    else:
        glue = binding(mapping, lib, fmt, single=not lib.cuda)
        module = f"cairn_{mangle(mapping.function.name)}_{record['identity'][:12]}"
        shared = python(mapping, files, glue, program, flags, module, lib.cuda,
                        "import torch.nn as nn\n" if fmt == "kernelbench" else "")  # fmt: skip
        text = (gpumode if fmt == "gpumode" else kernelbench)(mapping, shared, doc)
    target = staging / FORMATS[fmt]["file"]
    target.write_text(text, encoding="utf-8")
    harness: dict[str, Any] = {
        "schema": SCHEMA,
        "compiler": VERSION,
        "format": fmt,
        "upstream": FORMATS[fmt]["upstream"],
        "function": {
            "name": mapping.function.name,
            "signature": signature(mapping),
            "effects": header.receipt[mapping.function.name]["effects"],
        },
        "mapping": mapping.record(),
        "entry": entry_record(lib),
        "binding": {
            "source": "main.cpp" if fmt == "sol-execbench" else "binding.cpp, embedded",
            "sha256": hashlib.sha256(glue.encode()).hexdigest(),
            "translation_units": "the program and the binding apart"
            if lib.cuda or fmt == "sol-execbench"
            else "one: the program, then the binding",
        },
        "flags": {**flags, "why": WHY_FLAGS},
        "gpus": gpus,
        "export": {
            "directory": "export",
            "identity": record["identity"],
            "device_target": record["device_target"],
            "files": record["files"],
        },
        "files": {target.name: digest(target)},
        "metric": FORMATS[fmt]["metric"],
        "commands": commands(fmt, mapping, out, project.root),
        "not_run": NOT_RUN,
    }
    harness["identity"] = export.identity(harness)
    (staging / RECORD).write_text(json.dumps(harness, indent=2) + "\n", encoding="utf-8")
    return {"status": "exported-harness", "format": fmt, "file": FORMATS[fmt]["file"], "identity": harness["identity"],
            "export": record["identity"], "entry": harness["entry"], "commands": harness["commands"],
            "not_run": NOT_RUN}  # fmt: skip


def check(directory: Path) -> dict[str, Any]:
    """The harness record, when its identity still covers it, the export beside it is intact and the submission is
    the file it recorded; else E-EXPORT-TAMPERED naming what differs."""
    path = directory / RECORD
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise refuse("E-EXPORT-TAMPERED", f"{RECORD} is not the record a harness export writes: {error}.") from error
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise refuse("E-EXPORT", f"{RECORD} is not a {SCHEMA} record.")
    if export.identity(record) != record.get("identity"):
        raise refuse("E-EXPORT-TAMPERED", f"{RECORD} no longer hashes to its identity.")
    inner = export.check(directory / "export")
    if inner["identity"] != record["export"]["identity"]:
        raise refuse("E-EXPORT-TAMPERED", "The export beside the submission is not the one the harness embeds.")
    changed = sorted(
        n for n, h in record["files"].items() if not (directory / n).is_file() or digest(directory / n) != h
    )
    if changed:
        raise refuse("E-EXPORT-TAMPERED", f"{', '.join(changed)} is not the file the harness wrote.", changed=changed)
    return record


def command(a: Any) -> tuple[dict[str, Any], int]:
    """`cairn export PATH --harness FORMAT --symbol F --out DIR`, or `cairn export DIR` of a harness export."""
    where = Path(a.path)
    if (where / RECORD).is_file():
        record = check(where)
        return {"status": "harness-intact", "format": record["format"], "identity": record["identity"],
                "export": record["export"]["identity"], "commands": record["commands"]}, 0  # fmt: skip
    from .project import load_project

    if not a.out or not a.symbol:
        raise ProjectError("Name the function and the new directory: cairn export PATH --harness FORMAT --symbol F "
                           "--out DIR.")  # fmt: skip
    made = write(load_project(a.path), a.harness, a.symbol, a.out, mapping_path=a.mapping, cxx=a.cxx,
                 device_target=a.device_target, keep_guards=a.keep_guards)  # fmt: skip
    return made, 0

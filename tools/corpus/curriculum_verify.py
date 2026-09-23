#!/usr/bin/env python3
"""Validate all authored teaching programs against independent finite oracles.

Build the combined corpus once per compiler, then run each contract in a child.
No model is invoked, trained, or scored. Alpha-renamed variants are not new
independent tasks. All answer keys are shipped for audit, not secret evaluation.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R / "src"), str(R / "tools")]
from ai.task_eval import FLAGS, validate_contract
from cairn.agent.agent_tools import digest, load_json_strict, stable_json
from cairn.compiler.cairnc import compile_source
from support import environment, runtime_headers


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gcc", action="store_true")
    a = p.parse_args()
    source = (R / "tools/corpus/lessons/corpus.cairn").read_text()
    tasks = load_json_strict((R / "tools/corpus/lessons/all_tasks_with_oracles.json").read_text())
    generated, _ = compile_source(source)
    compilers = ["clang++"] + (["g++"] if a.gcc else [])
    outputs = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cairn-curriculum-") as tmp:
        t = Path(tmp)
        (t / "corpus.cpp").write_text(generated)
        (t / "source.cairn").write_text(source)
        runtime_headers(t)
        for compiler_name in compilers:
            compiler = shutil.which(compiler_name)
            if compiler is None:
                raise RuntimeError("Requested compiler not installed: " + compiler_name)
            version = subprocess.run([compiler, "--version"], text=True, capture_output=True, check=True).stdout
            cmd = [compiler, *FLAGS, str(t / "corpus.cpp"), "-o", str(t / "libcorpus.so")]
            cp = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
            if cp.returncode:
                raise RuntimeError(cp.stderr)
            entries = []
            for task in tasks:
                contract = task["contract"]
                validate_contract(source, contract)
                (t / "contract.json").write_text(stable_json(contract))
                cp = subprocess.run(
                    [
                        sys.executable,
                        str(R / "tools/ai/task_eval.py"),
                        "--child",
                        str(t / "libcorpus.so"),
                        str(t / "source.cairn"),
                        str(t / "contract.json"),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                events = [json.loads(line) for line in cp.stdout.splitlines()]
                verdict = next((x for x in reversed(events) if "status" in x), {"status": "no-verdict"})
                entry = {
                    "id": task["id"],
                    "family": task["family"],
                    "split": task["split"],
                    **verdict,
                    "exit_code": cp.returncode,
                }
                entries.append(entry)
                if cp.returncode or verdict.get("status") != "passed-finite-tests":
                    raise RuntimeError(str(entry) + "\n" + cp.stderr)
            outputs.append(
                {
                    "compiler": compiler,
                    "version": version,
                    "flags": FLAGS,
                    "entries": entries,
                    "tasks": len(entries),
                    "cases": sum(e["cases"] for e in entries),
                }
            )
    result = {
        "source_sha256": digest(source),
        "answers_sha256": digest(stable_json(tasks)),
        "environment": environment(*compilers),
        "results": outputs,
        "elapsed_seconds": time.monotonic() - started,
        "model_runs": 0,
        "formal_status": "not-verified",
        "status": "all finite teaching cases passed",
    }
    (R / "results/agent").mkdir(parents=True, exist_ok=True)
    (R / "results/agent/curriculum_validation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))
    print([(x["compiler"], x["tasks"], x["cases"]) for x in outputs])


if __name__ == "__main__":
    main()

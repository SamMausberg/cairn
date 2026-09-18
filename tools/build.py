#!/usr/bin/env python3
"""One-command host-library build. No package downloads or implicit fast math."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cairn.cairnc import RUNTIME, Diagnostic, compile_source


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "build")
    p.add_argument("--cxx", default="clang++")
    p.add_argument("--arch", default="x86-64-v3", choices=["x86-64", "x86-64-v3"])
    a = p.parse_args()
    cxx = shutil.which(a.cxx)
    if not cxx:
        raise SystemExit(f"Compiler not found: {a.cxx}. Install Clang or GCC; no files downloaded.")
    source = a.source.resolve()
    try:
        generated, receipt = compile_source(source.read_text())
    except Diagnostic as e:
        print(json.dumps(e.data), file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    cpp = a.out / (source.stem + ".cpp")
    cpp.write_text(generated)
    (a.out / "cairn_runtime.hpp").write_text(RUNTIME)
    lib = a.out / ("lib" + source.stem + ".so")
    flags = [
        "-std=c++20",
        "-O3",
        f"-march={a.arch}",
        "-ffp-contract=off",
        "-fno-fast-math",
        "-fno-exceptions",
        "-fno-rtti",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wno-unused-parameter",
        "-Wno-unused-variable",
        "-Wno-unused-but-set-variable",
        "-shared",
        "-fPIC",
    ]
    command = [cxx, *flags, str(cpp), "-o", str(lib)]
    start = time.monotonic()
    result = subprocess.run(command, text=True, capture_output=True)
    receipt["native_build"] = {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": time.monotonic() - start,
        "compiler_version": subprocess.run([cxx, "--version"], text=True, capture_output=True, check=True).stdout,
    }
    if result.returncode == 0:
        receipt["native_build"]["library_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
    (a.out / (source.stem + "_receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return 1
    print(json.dumps({"status": "built", "library": str(lib.resolve()), "formal_status": "not-verified"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

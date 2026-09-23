#!/usr/bin/env python3
"""One-command host-library build. No package downloads or implicit fast math."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.toolchain import ProjectError, command
from support import ARCHS, best_profile, runtime_headers, version


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("--out", type=Path, default=ROOT / "build")
    p.add_argument("--cxx", default="clang++")
    p.add_argument("--arch", choices=ARCHS, help="Host CPU profile; the default is the best this host builds and runs.")
    a = p.parse_args()
    source = a.source.resolve()
    try:
        arch = a.arch or best_profile(a.cxx)
        generated, receipt = compile_source(source.read_text())
    except Diagnostic as e:
        print(json.dumps(e.data), file=sys.stderr)
        return 1
    except ProjectError as e:
        print(json.dumps({"status": "unknown", "message": str(e)}), file=sys.stderr)
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    cpp = a.out / (source.stem + ".cpp")
    cpp.write_text(generated)
    runtime_headers(a.out)
    lib = a.out / ("lib" + source.stem + ".so")
    argv = command(a.cxx, str(cpp), str(lib), arch, "library")
    start = time.monotonic()
    result = subprocess.run(argv, text=True, capture_output=True)
    receipt["native_build"] = {
        "command": argv,
        "arch": arch,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": time.monotonic() - start,
        "compiler_version": version(a.cxx),
    }
    if result.returncode == 0:
        receipt["native_build"]["library_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
    (a.out / (source.stem + "_receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    if result.returncode:
        print(result.stderr, file=sys.stderr)
        return 1
    print(json.dumps({"status": "built", "library": str(lib.resolve()), "arch": arch, "formal_status": "not-verified"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

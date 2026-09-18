"""Explicit native builds into fresh directories, never stale output reuse."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .cairnc import RUNTIME_FILES, Parser, compile_source
from .project import Project, ProjectError
from .toolchain import find, flags


def build(
    project: Project,
    *,
    output: Path | None = None,
    cxx: str = "clang++",
    arch: str | None = None,
    kind: str | None = None,
    timeout: int = 60,
) -> dict:
    kind = kind or project.kind
    options = flags(arch or project.arch, kind)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ProjectError("Build timeout must be 1..300 seconds.")
    compiler = find(cxx)
    generated, receipt = compile_source(project.source)
    if kind == "exe":
        functions = {f.name: f for f in Parser(project.source).parse().functions}
        main = functions.get("main")
        if main is None or main.static or main.params or main.ret.name != "i32" or main.ret.mode != "value":
            raise ProjectError("An executable needs fn main() -> i32 with no arguments.")
        generated += "\nint main() { return static_cast<int>(cf_main()); }\n"
    # No manifest can select a compiler executable, flags, build script, or output path.
    out = output or project.root / "build"
    if out.is_symlink():
        raise ProjectError("Build output must not be a symbolic link.")
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", project.name)[:64] or "program"
    directory = Path(tempfile.mkdtemp(prefix=name + "-", dir=out.resolve()))
    cpp = directory / "program.cpp"
    cpp.write_text(generated, encoding="utf-8")
    for header, text in RUNTIME_FILES.items():
        (directory / header).write_text(text, encoding="utf-8")
    artifact = directory / ("lib" + name + ".so" if kind == "library" else name)
    command = [compiler, *options, str(cpp), "-o", str(artifact)]
    started = time.monotonic()
    record = {
        "schema": "cairn.build/1",
        "status": "unknown",
        "formal_status": "not-verified",
        "project": project.receipt(),
        "frontend": receipt,
        "kind": kind,
        "command": command,
        "generated_sha256": hashlib.sha256(generated.encode()).hexdigest(),
        "artifact": str(artifact),
        "directory": str(directory),
    }
    try:
        record["compiler_version"] = subprocess.run(
            [compiler, "--version"], check=True, capture_output=True, text=True, timeout=5
        ).stdout[:10000]
        cp = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        record.update(
            status="native-built" if cp.returncode == 0 else "native-build-failed",
            exit_code=cp.returncode,
            stdout=cp.stdout[:32000],
            stderr=cp.stderr[:32000],
        )
        if cp.returncode == 0:
            record["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as error:
        record.update(status="unknown", message=str(error))
    record["elapsed_seconds"] = time.monotonic() - started
    (directory / "receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    return record

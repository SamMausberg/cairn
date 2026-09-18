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
from .codegen import mangle
from .project import Project, ProjectError
from .toolchain import audit_effects, find, flags, profile
from .toolchain import command as native_command


def build(
    project: Project,
    *,
    output: Path | None = None,
    cxx: str = "clang++",
    arch: str | None = None,
    kind: str | None = None,
    timeout: int = 60,
    target: str | None = None,
    debug: bool = False,
) -> dict:
    kind = kind or project.kind
    target = target or project.target
    bare = bool(profile(target))
    flags(arch or project.arch, kind, target)  # Reject an unknown kind, architecture or target before any work.
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ProjectError("Build timeout must be 1..300 seconds.")
    if bare and kind != "exe":
        raise ProjectError(f'Target {target} builds one image: set kind = "exe".')
    compiler = find(cxx)
    entry = ""
    if kind == "exe":  # The entry point is `main` of the root module, else the only module-level `main`.
        mains = [f for f in Parser(project.source).parse().functions if f.name.rsplit(".", 1)[-1] == "main"]
        main = next((f for f in mains if f.name == "main"), mains[0] if len(mains) == 1 else None)
        if main is None or main.static or main.params or main.ret.name != "i32" or main.ret.mode != "value":
            raise ProjectError("An executable needs exactly one fn main() -> i32 with no arguments.")
        entry = main.name
    # A library exports everything; a program contains only what its entry point reaches.
    generated, receipt = compile_source(project.source, project.origin if debug else "", (entry,) if entry else ())
    if bare:  # No hosted runtime stands behind the image, so no effect may assume one.
        audit_effects(receipt["functions"])
    if entry and (bare or entry != "main"):  # Start-up code calls cf_main, wherever main was written.
        generated += f'\nextern "C" std::int32_t cf_main() noexcept {{ return cf_{mangle(entry)}(); }}\n' * (
            entry != "main"
        )
    if entry and not bare:
        generated += "\nint main() { return static_cast<int>(cf_" + mangle(entry) + "()); }\n"
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
    artifact = directory / (name + ".elf" if bare else "lib" + name + ".so" if kind == "library" else name)
    command = native_command(
        cxx, str(cpp), str(artifact), arch or project.arch, kind, "cuda" in receipt["requires"], target
    )
    if debug:  # Symbols plus #line directives: a debugger steps through the .cairn files.
        command.insert(1, "-g")
    started = time.monotonic()
    record = {
        "schema": "cairn.build/1",
        "status": "unknown",
        "formal_status": "not-verified",
        "project": project.receipt(),
        "frontend": receipt,
        "kind": kind,
        "target": target,
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

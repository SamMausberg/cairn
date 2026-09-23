"""Explicit native builds into fresh directories, never stale output reuse."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..compiler.cairnc import RUNTIME_FILES, Parser, compile_source, compile_units, write_program
from ..compiler.codegen import mangle
from ..compiler.header import header as c_header
from .project import Project, ProjectError
from .toolchain import audit_effects, find, flags, link_flags, linked, profile, unit_commands
from .toolchain import command as native_command


def intact(target: Path, digest: Path) -> bool:
    """A cached object is what was compiled under its key only while the digest written beside it still matches its
    bytes; a truncated, half-written or replaced file is not reused, it is compiled again."""
    if not target.is_file() or not digest.is_file() or digest.stat().st_size != 64:
        return False
    return digest.read_bytes() == hashlib.sha256(target.read_bytes()).hexdigest().encode()


def store(target: Path, digest: Path, fresh: Path) -> None:
    """Publish an object under its key: a rename, then its digest by a rename, so an interrupted build leaves an
    object without a digest, which is compiled again, and never a half object under a valid key."""
    recorded = hashlib.sha256(fresh.read_bytes()).hexdigest().encode()
    digest.unlink(missing_ok=True)  # No object is ever trusted under a digest of the bytes it replaced.
    fresh.replace(target)  # Atomic: a concurrent build sees a whole object or none.
    temporary = digest.with_name(f"{digest.name}.{os.getpid()}")
    temporary.write_bytes(recorded)
    temporary.replace(digest)


def objects(
    project, directory, out, compiler, cxx, arch, kind, debug, entry, stub, timeout, keep=False
) -> tuple[list[str], list]:
    """One object per module, reused only when the unit, the shared interface, the command and the compiler all hash
    to the same key and the stored bytes still match the digest beside them, so nothing stale, truncated or replaced
    is ever linked. The cache is a directory of the project's own build output, never a link out of it. Missing
    objects compile concurrently."""
    files, _ = compile_units(
        project.source, project.origin if debug else "", (entry,) if entry else (), keep, project.site
    )
    files["0start.cpp"] = '#include "program.hpp"\n' + stub  # No module's unit can be named with a leading digit.
    compile_prefix, link = unit_commands(cxx, arch or project.arch, kind)
    compile_prefix += ["-g"] if debug else []
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=5).stdout
    cache = out.resolve() / "objects"
    if cache.is_symlink() or (cache.exists() and not cache.is_dir()):
        raise ProjectError("The object cache build/objects must be a directory and not a symbolic link.")
    cache.mkdir(exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")

    def one(name: str) -> dict:
        salt = "\0".join(
            [files["program.hpp"], files[name], " ".join(compile_prefix), version, *RUNTIME_FILES.values()]
        )
        key = hashlib.sha256(salt.encode()).hexdigest()[:40]
        target, digest = cache / (key + ".o"), cache / (key + ".sha256")
        if any(path.is_symlink() or path.is_dir() for path in (target, digest)):
            raise ProjectError(f"A cached object and its digest are plain files of the cache: {target.name} is not.")
        unit = {"unit": name, "object": str(target), "reused": intact(target, digest)}
        if not unit["reused"]:
            fresh = directory / (name + ".o")
            done = subprocess.run([*compile_prefix, str(directory / name), "-o", str(fresh)],
                                  capture_output=True, text=True, timeout=timeout)  # fmt: skip
            if done.returncode:
                return unit | {"error": done}
            store(target, digest, fresh)
        return unit

    with ThreadPoolExecutor(max_workers=8) as pool:
        units = list(pool.map(one, [n for n in files if n.endswith(".cpp")]))
    return [*link, *(unit["object"] for unit in units)], units


def dispatcher(tests: tuple[str, ...]) -> str:
    """The `main` of a test build: argv[1] is the index of the one test this process runs, so a test that traps
    fails alone. A missing or bad index exits 2, which no passing test does."""
    table = ", ".join(f"ctest_{mangle(name)}" for name in tests)
    return f"""
// tests
int main(int argc, char** argv) {{
  void (*const tests[])() noexcept = {{{table}}};
  std::size_t which = 0;
  if (argc != 2 || !argv[1][0]) return 2;
  for (const char* p = argv[1]; *p; ++p) {{
    if (*p < '0' || *p > '9' || which > 1000000) return 2;
    which = which * 10 + static_cast<std::size_t>(*p - '0');
  }}
  if (which >= sizeof(tests) / sizeof(tests[0])) return 2;
  tests[which]();
  return 0;
}}
"""


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
    incremental: bool = False,
    keep_guards: bool = False,
    tests: tuple[str, ...] = (),
    header: bool = False,
) -> dict:
    kind = "exe" if tests else kind or project.kind  # A test build is an executable whose main runs one test.
    target = target or project.target
    bare = bool(profile(target))
    flags(arch or project.arch, kind, target)  # Reject an unknown kind, architecture or target before any work.
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ProjectError("Build timeout must be 1..300 seconds.")
    if bare and kind != "exe":
        raise ProjectError(f'Target {target} builds one image: set kind = "exe".')
    if bare and tests:
        raise ProjectError(f"Tests run as host processes; target {target} has no host to run them on.")
    if header and (kind != "library" or bare or incremental):
        raise ProjectError("--header describes a hosted library built as one unit: use --kind library.")
    compiler = find(cxx)
    entry = ""
    if kind == "exe" and not tests:  # The entry point is `main` of the root module, else the only module-level `main`.
        # A vendored dependency is a library: it neither supplies the program's entry point nor denies it one.
        written = Parser(project.source).parse().functions
        mains = [f for f in written if f.name.rsplit(".", 1)[-1] == "main" and project.wrote(f.line)]
        main = next((f for f in mains if f.name == "main"), mains[0] if len(mains) == 1 else None)
        if main is None or main.static or main.params or main.ret.name != "i32" or main.ret.mode != "value":
            raise ProjectError("An executable needs exactly one fn main() -> i32 with no arguments.")
        entry = main.name
    # A library exports everything; a program contains only what its entry point reaches.
    roots = tests or ((entry,) if entry else ())
    origin = project.origin if debug else ""
    generated, receipt = compile_source(project.source, origin, roots, keep_guards, sites=project.site)
    if bare:  # No hosted runtime stands behind the image, so no effect may assume one; no test is in the image.
        audit_effects({name: row for name, row in receipt["functions"].items() if not row.get("test")})
    generated += "\n// entry\n" if entry else dispatcher(tests) if tests else ""
    declared = ""
    if header:  # The library carries the layouts its header states, so the two cannot disagree and still link.
        declared, checks = c_header(project.source, project.name, lambda f: project.wrote(f.line))
        generated += "\n" + checks
    if entry and entry != "main":  # Start-up code calls cf_main, wherever main was written.
        generated += f'\nextern "C" std::int32_t cf_main() noexcept {{ return cf_{mangle(entry)}(); }}\n'
    if entry and not bare:
        generated += "\nint main() { return static_cast<int>(cf_" + mangle(entry) + "()); }\n"
    # No manifest can select a compiler executable, flags, build script, or output path.
    out = output or project.root / "build"
    if out.is_symlink():
        raise ProjectError("Build output must not be a symbolic link.")
    out.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_-]", "_", project.name)[:64] or "program"
    directory = Path(tempfile.mkdtemp(prefix=name + "-", dir=out.resolve()))
    cpp = write_program(directory, "program.cpp", generated)
    if declared:
        (directory / (name + ".h")).write_text(declared, encoding="utf-8")
    artifact = directory / (name + ".elf" if bare else "lib" + name + ".so" if kind == "library" else name)
    command = native_command(
        cxx, str(cpp), str(artifact), arch or project.arch, kind, "cuda" in receipt["requires"], target
    )
    libraries = [] if bare else linked(project.libraries, receipt["modules"])  # an image refuses ffi effects above
    command += link_flags(libraries)
    if debug:  # Symbols plus #line directives: a debugger steps through the .cairn files.
        command.insert(1, "-g")
    units: list[dict] = []
    started = time.monotonic()
    record = {
        "schema": "cairn.build/1",
        "status": "unknown",
        "formal_status": "not-verified",
        "project": project.receipt(),
        "frontend": receipt,
        "kind": kind,
        "target": target,
        **({"libraries": libraries} if libraries else {}),
        "command": command,
        "generated_sha256": hashlib.sha256(generated.encode()).hexdigest(),
        "units": [],
        "artifact": str(artifact),
        **({"header": str(directory / (name + ".h"))} if declared else {}),
        "directory": str(directory),
    }
    try:
        record["compiler_version"] = subprocess.run(
            [compiler, "--version"], check=True, capture_output=True, text=True, timeout=5
        ).stdout[:10000]
        if incremental and not bare and "cuda" not in receipt["requires"]:  # Device code and images stay one unit.
            stub = generated[generated.rindex("\n// entry\n") :] if "\n// entry\n" in generated else ""
            command, units = objects(
                project, directory, out, compiler, cxx, arch, kind, debug, entry, stub, timeout, keep_guards
            )
            command += ["-o", str(artifact), *link_flags(libraries)]
            record["command"] = command
            record["units"] = [{k: v for k, v in unit.items() if k != "error"} for unit in units]
        failed = next((unit["error"] for unit in units if unit.get("error")), None)
        cp = failed or subprocess.run(command, capture_output=True, text=True, timeout=timeout)
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

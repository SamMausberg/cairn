"""`cairn export`: one directory holding exactly what a build compiles, and the record that pins it.

The directory holds the generated C++ (CUDA for a device program), exactly the runtime headers it includes, the C
header of a library, and `export.json`: the command line `toolchain.command` gives for it, the device target, the
compilers and their versions, a sha256 per file, and one identity over all of these. Each runtime header has a role:
the device implementation (`cairn_kernels.hpp` and the guards a lane calls), the optional CAIRN launch wrappers
(`cairn_gpu.hpp`, `cairn_exec.hpp`, `cairn_reuse.hpp`), or the host runtime.

What validates or measures an export takes the directory itself. `check` refuses it when any file, the command, the
compilers or the target differ from what the identity covers, or when a file was added (`E-EXPORT-TAMPERED`); a build
uses exactly the recorded command and compilers (`E-EXPORT-TOOLCHAIN`); and every record a build, a run, a test run or
a timing writes carries the identity. A later rewrite of the output therefore has another identity, or is refused,
and is never covered by an earlier record. `compare` says whether two exports are the same code, function by function
as `verify/emission.py` decides it, and says that their speed is compared only under the same harness.

The identity is a digest anyone can recompute, not a signature, so the record is data and never a build script: a
build runs the compiler this machine finds under the name the record gives it, clang++ or g++, never a path the record
names, and only the command `toolchain.command` gives for the record's kind, architecture, target and libraries.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ..compiler.cairnc import RUNTIME_FILES
from ..compiler.machine import unbuildable
from ..version import VERSION
from .project import Project, ProjectError, digest
from .target import parse, refuse, resolve, toolkit_record
from .toolchain import KINDS, find, host_family, link_flags, linked
from .toolchain import command as native_command
from .toolchain import version as compiler_version

RECORD = "export.json"
SCHEMA = "cairn.export/1"
BUILT = "build"  # where builds of an export go, inside it; never part of what the identity covers
INCLUDE = re.compile(r'^#include "([^"]+)"', re.M)
COMPILER = re.compile(r"(clang\+\+|g\+\+)(-[0-9][0-9.]*)?")  # the names a build finds its C++ compiler by
PLAIN = re.compile(r"[A-Za-z0-9_-]{1,64}")  # an artifact's name, as `export` makes it from the project's
DEVICE_SIDE = {"cairn_kernels.hpp", "cairn_runtime.hpp", "cairn_assert.hpp", "cairn_float.hpp", "cairn_layout.hpp",
               "cairn_access.hpp"}  # fmt: skip
LAUNCH = {"cairn_gpu.hpp", "cairn_exec.hpp", "cairn_reuse.hpp"}


def closure(text: str) -> list[str]:
    """The runtime headers `text` includes, and those they include, in first-include order."""
    found: list[str] = []
    todo = INCLUDE.findall(text)
    while todo:
        name = todo.pop(0)
        if name in RUNTIME_FILES and name not in found:
            found.append(name)
            todo += INCLUDE.findall(RUNTIME_FILES[name])
    return found


def role(name: str, program: str, cuda: bool) -> str:
    """What a file of an export is: the program, or a runtime header of the device implementation, of the CAIRN launch
    wrappers or of the host runtime; a host program has only the program and its runtime."""
    if name == program or name.endswith(".h"):
        return "program"
    if not cuda:
        return "runtime"
    return "device" if name in DEVICE_SIDE else "launch" if name in LAUNCH else "host runtime"


def identity(record: dict[str, Any]) -> str:
    """One sha256 over the whole record but itself: every file's hash, the command, the compilers, the target, the
    harness and what each function emits. Editing any of them, or any file, changes it."""
    pinned = {k: v for k, v in record.items() if k != "identity"}
    return hashlib.sha256(json.dumps(pinned, sort_keys=True).encode()).hexdigest()


def identities(source: str) -> dict[str, str]:
    """Each function's emitted C++ in the canonical form `cairn diff` compares (verify/emission.py), hashed."""
    from ..verify.emission import canonical, emitted

    _, _, code, types = emitted(source)
    return {
        name: hashlib.sha256(canonical(name, text, types).encode()).hexdigest() for name, text in sorted(code.items())
    }


def export(project: Project, out: Path, *, cxx: str = "clang++", arch: str | None = None, kind: str | None = None,
           header: bool = False, tests: bool = False, timed: tuple[str, dict[str, float]] | None = None,
           device_target: str | None = None, keep_guards: bool = False) -> dict[str, Any]:  # fmt: skip
    """Write the export of `project` into `out`, which must not exist yet, and return its record."""
    from ..verify.runner import label, written
    from .build import emitted

    if out.exists() or out.is_symlink():
        raise ProjectError(f"{out} exists; an export is written into a new directory, never over another.")
    if project.target != "hosted":
        raise ProjectError(f"An export is a hosted program; target {project.target} is built by cairn build.")
    if project.foreign:  # vendored C++ and CUDA are compiled by cairn build (projects/foreign.py), not exported yet
        raise ProjectError("An export holds the program CAIRN generates; a project with [foreign] sources is built by "
                           "cairn build.")  # fmt: skip
    chosen = tuple(f.name for f in written(project)) if tests else ()
    if tests and not chosen:
        raise ProjectError("--tests exports the project's test blocks, and it has none.")
    if timed and (tests or header):
        raise ProjectError("An export holds one program: the timing harness, the test blocks or a library.")
    made = emitted(project, kind="library" if timed else "exe" if tests else kind or project.kind, tests=chosen,
                   header=header, keep_guards=keep_guards)  # fmt: skip
    kind = "exe" if timed else made.kind
    generated, receipt = made.generated, made.receipt
    cuda = "cuda" in receipt["requires"]
    harness = None
    if timed:  # the timing driver is part of the program, so what is timed is what is exported
        from ..perf import measure, on_device

        symbol, sizes = timed
        f = measure.timed_function(project.source, symbol)
        generated += measure.driver(f, sizes, {}, 2e6, 9, on_device.MEMORY if cuda else None)
        harness = {"symbol": symbol, "sizes": sizes, "timer": "device" if cuda else "host", "blocks": 9}
    device = resolve(device_target, project.device_target).require(receipt["device_features"]) if cuda else None
    if why := unbuildable(receipt["requires"], host_family(), device.name if device else ""):
        raise refuse("E-ASM-TARGET", why)  # assembly builds only for the machine it names, as in cairn build
    name = re.sub(r"[^A-Za-z0-9_-]", "_", project.name)[:64] or "program"
    program = "program.cu" if cuda else "program.cpp"
    artifact = "lib" + name + ".so" if kind == "library" else name
    out.mkdir(parents=True)
    (out / program).write_text(generated, encoding="utf-8")
    for header_name in closure(generated):
        (out / header_name).write_text(RUNTIME_FILES[header_name], encoding="utf-8")
    if made.declared:
        (out / (name + ".h")).write_text(made.declared, encoding="utf-8")
    libraries = linked(project.libraries, receipt["modules"])
    line = native_command(cxx, program, artifact, arch or project.arch, kind, cuda, None, device) + link_flags(
        libraries
    )
    compilers = {"cxx": {"path": find(cxx), "version": compiler_version(find(cxx)).splitlines()[0]}}
    if cuda:
        compilers["nvcc"] = toolkit_record() or {}
    files = {p.name: digest(p) for p in sorted(out.iterdir())}
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "compiler": VERSION,
        "project": project.receipt(),
        "kind": kind,
        "files": files,
        "roles": {n: role(n, program, cuda) for n in files},
        "command": line,
        "arch": arch or project.arch,
        "libraries": libraries,
        "compilers": compilers,
        "device_target": device.name if device else None,
        "device": device.record() if device else None,
        "tests": [label(f) for f in written(project) if f.name in chosen] if tests else None,
        "harness": harness,
        "artifact": artifact,
        "functions": identities(project.source),
    }
    record["identity"] = identity(record)
    (out / RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"status": "exported", "directory": str(out), **{k: record[k] for k in ("identity", "files", "roles")}}


def load(directory: Path) -> dict[str, Any]:
    path = directory / RECORD
    if not path.is_file() or path.is_symlink():
        raise refuse("E-EXPORT", f"{directory} holds no {RECORD}: it is not an export.")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise refuse("E-EXPORT-TAMPERED", f"{RECORD} is not the JSON an export writes: {error}.") from error
    if not isinstance(record, dict) or record.get("schema") != SCHEMA or not isinstance(record.get("files"), dict):
        raise refuse("E-EXPORT", f"{RECORD} is not a {SCHEMA} record.")
    return record


def check(directory: Path) -> dict[str, Any]:
    """The export's record, when every file still has its hash, no file was added and the identity still covers
    the record; else E-EXPORT-TAMPERED naming what differs."""
    record = load(directory)
    present = {p.name for p in directory.iterdir() if p.name not in {RECORD, BUILT}}
    listed = set(record["files"])
    changed = sorted(
        n
        for n in listed & present
        if (directory / n).is_symlink() or not (directory / n).is_file() or digest(directory / n) != record["files"][n]
    )
    missing, added = sorted(listed - present), sorted(present - listed)
    if changed or missing or added:
        said = "; ".join(f"{what}: {', '.join(names)}" for what, names in
                         (("changed", changed), ("missing", missing), ("added", added)) if names)  # fmt: skip
        raise refuse("E-EXPORT-TAMPERED", f"The export at {directory} is not the one recorded ({said}); nothing "
                     "recorded for it covers this tree.", changed=changed, missing=missing, added=added)  # fmt: skip
    if identity(record) != record.get("identity"):
        raise refuse("E-EXPORT-TAMPERED", f"{RECORD} no longer hashes to its identity: its command, compilers, target "
                     "or file list was edited.")  # fmt: skip
    return record


def compiler(record: dict[str, Any]) -> str:
    """The name of the C++ compiler the export was made with, when this machine's PATH finds it where the record
    says; else E-EXPORT-TOOLCHAIN. A build runs a compiler this machine finds by name, never a path from the record,
    so an export cannot bring the program that builds it."""
    path = str(record["compilers"]["cxx"]["path"])
    name = Path(path).name
    if not COMPILER.fullmatch(name):
        raise refuse("E-EXPORT-TOOLCHAIN", f"The export names {path} as its compiler; a build finds clang++ or g++ "
                     "on this machine's PATH, and runs nothing an export names.")  # fmt: skip
    if (here := shutil.which(name)) != path:
        raise refuse("E-EXPORT-TOOLCHAIN", f"The export was made with {path}; here {name} is {here or 'absent'}. A "
                     "build with another compiler is another artifact: export again here.")  # fmt: skip
    return name


def rebuilt(record: dict[str, Any]) -> list[str]:
    """The command `toolchain.command` gives for the export's kind, architecture, device target and libraries, with
    the compiler found here: the only command a build runs, so the record's command is never a script."""
    kind, artifact = record.get("kind"), record.get("artifact")
    stem = artifact[3:-3] if kind == "library" and isinstance(artifact, str) and artifact[:3] + artifact[-3:] == (
        "lib.so") else artifact  # fmt: skip
    if kind not in KINDS or not isinstance(stem, str) or not PLAIN.fullmatch(stem):
        raise refuse("E-EXPORT-TAMPERED", f"{RECORD} names the artifact {artifact!r} of kind {kind!r}, which no export "
                     "writes.")  # fmt: skip
    device = parse(record["device_target"]) if record.get("device_target") else None
    program = "program.cu" if device else "program.cpp"
    line = native_command(
        compiler(record), program, artifact, record.get("arch"), kind, device is not None, None, device
    )
    return line + link_flags(linked(record.get("libraries") or [], ()))


def toolchain(record: dict[str, Any]) -> None:
    """E-EXPORT-TOOLCHAIN unless the compilers here are the ones the export was made with."""
    cxx = record["compilers"]["cxx"]
    found = shutil.which(compiler(record))
    here = compiler_version(found).splitlines()[0] if found else None
    if here != cxx["version"]:
        raise refuse("E-EXPORT-TOOLCHAIN", f"The export was made with {cxx['path']} ({cxx['version']}); here it is "
                     f"{here or 'absent'}. A build with another compiler is another artifact: export again here.")  # fmt: skip
    nvcc = record["compilers"].get("nvcc")
    if nvcc is not None and (toolkit_record() or {}).get("version") != nvcc.get("version"):
        raise refuse("E-EXPORT-TOOLCHAIN", f"The export was made with nvcc {nvcc.get('version')}; here it is "
                     f"{(toolkit_record() or {}).get('version', 'absent')}.")  # fmt: skip


def build(directory: Path, output: Path | None = None, timeout: int = 300) -> dict[str, Any]:
    """Check the export, copy it into a fresh directory, check the copy, and run exactly its recorded command there."""
    record = check(directory)
    toolchain(record)
    if record["command"] != rebuilt(record):
        raise refuse("E-EXPORT-TAMPERED", f"The command {RECORD} records is not the one cairn builds this export "
                     "with; a record is data, and a build runs only the command toolchain.py gives for it.")  # fmt: skip
    base = output or directory / BUILT
    base.mkdir(parents=True, exist_ok=True)
    fresh = Path(tempfile.mkdtemp(prefix="build-", dir=base.resolve()))
    for name in record["files"]:
        shutil.copy2(directory / name, fresh / name)
    shutil.copy2(directory / RECORD, fresh / RECORD)
    check(fresh)
    started = time.monotonic()
    done = subprocess.run(record["command"], cwd=fresh, capture_output=True, text=True, timeout=timeout)
    artifact = fresh / record["artifact"]
    built = {
        "schema": "cairn.export.build/1",
        "export": record["identity"],
        "status": "native-built" if done.returncode == 0 and artifact.is_file() else "native-build-failed",
        "command": record["command"],
        "directory": str(fresh),
        "artifact": str(artifact),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        **({"artifact_sha256": digest(artifact)} if done.returncode == 0 and artifact.is_file() else {}),
        **({"exit_code": done.returncode, "stderr": done.stderr[-8000:]} if done.returncode else {}),
    }
    (fresh / "build.json").write_text(json.dumps(built, indent=2) + "\n", encoding="utf-8")
    return built


def runnable(record: dict[str, Any]) -> str:
    """Why this export's program may not run here, or "": device code runs only under the owner's make targets."""
    if record.get("device_target"):
        from ..perf.on_device import allowed

        return allowed()
    return ""


def run(directory: Path, arguments: tuple[str, ...] = (), timeout: int = 60, memory_mib: int = 1024,
        output: Path | None = None) -> dict[str, Any]:  # fmt: skip
    """Build the export, then run what it built once; with a timing harness, what it printed is the measurement."""
    from ..verify.testing import limited

    record = check(directory)
    if record["kind"] != "exe":
        raise ProjectError("A library export is built, not run; export the program that calls it.")
    if reason := runnable(record):
        return {"schema": "cairn.export.run/1", "export": record["identity"], "status": "not-run", "reason": reason}
    built = build(directory, output)
    if built["status"] != "native-built":
        return {"schema": "cairn.export.run/1", "export": record["identity"], "status": built["status"], "build": built}
    memory = None if record.get("device_target") else memory_mib
    done = subprocess.run([built["artifact"], *arguments], capture_output=True, text=True, errors="backslashreplace",
                          timeout=timeout, stdin=subprocess.DEVNULL,
                          preexec_fn=functools.partial(limited, timeout, memory))  # fmt: skip
    ran: dict[str, Any] = {"schema": "cairn.export.run/1", "export": record["identity"], "status": "program-exited",
                           "exit_code": done.returncode, "stdout": done.stdout[:32000], "stderr": done.stderr[:8000],
                           "artifact_sha256": built["artifact_sha256"]}  # fmt: skip
    if record.get("harness") and done.returncode == 0:
        from ..perf.measure import outcome

        ran["measured"] = {**outcome(done), **record["harness"],
                           "note": "Timed by the harness the export holds; compare only with another export timed by "
                           "the same harness on the same machine."}  # fmt: skip
    (Path(built["directory"]) / "run.json").write_text(json.dumps(ran, indent=2) + "\n", encoding="utf-8")
    return ran


def test(directory: Path, jobs: int = 0, timeout: int = 60, memory_mib: int = 1024) -> dict[str, Any]:
    """Build a `--tests` export and run each test block in a process of its own, as `cairn test` does."""
    from concurrent.futures import ThreadPoolExecutor

    from ..verify.runner import reason
    from ..verify.testing import limited

    record = check(directory)
    if not record.get("tests"):
        raise ProjectError("This export holds no test blocks: export with --tests.")
    if why := runnable(record):
        return {"schema": "cairn.export.test/1", "export": record["identity"], "status": "not-run", "reason": why}
    built = build(directory)
    if built["status"] != "native-built":
        return {
            "schema": "cairn.export.test/1",
            "export": record["identity"],
            "status": built["status"],
            "build": built,
        }
    limits = functools.partial(limited, timeout, None if record.get("device_target") else memory_mib)

    def one(index: int) -> dict[str, Any]:
        name = record["tests"][index]
        try:
            done = subprocess.run([built["artifact"], str(index)], capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, preexec_fn=limits)  # fmt: skip
        except subprocess.TimeoutExpired:
            return {"name": name, "status": "failed", "reason": f"timed out after {timeout} s"}
        if done.returncode == 0:
            return {"name": name, "status": "passed"}
        return {"name": name, "status": "failed", "exit_code": done.returncode, "reason": reason(done)}

    with ThreadPoolExecutor(max_workers=jobs or min(8, os.cpu_count() or 1)) as pool:
        results = list(pool.map(one, range(len(record["tests"]))))
    failed = sum(r["status"] != "passed" for r in results)
    tested = {"schema": "cairn.export.test/1", "export": record["identity"],
              "status": "test-blocks-failed" if failed else "passed-test-blocks", "tests": results,
              "passed": len(results) - failed, "failed": failed, "artifact_sha256": built["artifact_sha256"],
              "formal_status": "not-verified"}  # fmt: skip
    (Path(built["directory"]) / "test.json").write_text(json.dumps(tested, indent=2) + "\n", encoding="utf-8")
    return tested


def compare(one: Path, other: Path) -> dict[str, Any]:
    """Whether two exports are the same code: each function's canonical emission, each runtime header, the command,
    the compilers and the target. Their speed is a different question, answered only by the same harness."""
    a, b = check(one), check(other)
    functions = {}
    for name in sorted(set(a["functions"]) | set(b["functions"])):
        x, y = a["functions"].get(name), b["functions"].get(name)
        functions[name] = "only-first" if y is None else "only-second" if x is None else (
            "identical-code" if x == y else "changed")  # fmt: skip
    headers = sorted(n for n in set(a["files"]) | set(b["files"]) if a["roles"].get(n, b["roles"].get(n)) != "program"
                     and a["files"].get(n) != b["files"].get(n))  # fmt: skip
    built_alike = {k: a[k] == b[k] for k in ("command", "compilers", "device_target")}
    same = all(v == "identical-code" for v in functions.values()) and not headers and all(built_alike.values())
    return {
        "schema": "cairn.export.compare/1",
        "exports": [a["identity"], b["identity"]],
        "verdict": "same-code" if same else "differs",
        "functions": functions,
        "runtime_headers_differ": headers,
        "built_alike": built_alike,
        "harness": "same" if a.get("harness") and a.get("harness") == b.get("harness") else "none-or-different",
        "timing": "Same code is not a timing. Compare speed only between exports that hold the same harness "
        "(export --time with one symbol and one set of sizes), built alike and run on the same machine.",
    }


def command(a: Any) -> tuple[dict[str, Any], int]:
    """What `cairn export`, and `cairn build`, `run` and `test` of an export directory, answer, and the exit status."""
    where = Path(a.path)
    if a.command == "export" and (a.harness or (where / "harness.json").is_file()):
        from .harness import command as harness

        return harness(a)
    if a.command == "export" and not (where / RECORD).is_file():
        from ..perf.report import parse_sizes
        from .project import load_project

        if not a.out:
            raise ProjectError("Name the new directory: cairn export PATH --out DIR.")
        sizes = parse_sizes(a.at)
        if len(sizes) > 1:
            raise ProjectError("A timing harness is exported for one set of sizes: give --at once.")
        timed = (a.time, sizes[0] if sizes else {}) if a.time else None
        made = export(load_project(a.path), a.out, cxx=a.cxx, arch=a.arch, kind=a.kind, header=a.header,
                      tests=a.tests, timed=timed, device_target=a.device_target, keep_guards=a.keep_guards)  # fmt: skip
        return made, 0
    if a.command == "export":
        if a.compare:
            compared = compare(where, a.compare)
            return compared, 0 if compared["verdict"] == "same-code" else 1
        record = check(where)
        return {"status": "export-intact", "identity": record["identity"], "files": len(record["files"])}, 0
    if a.command == "build":
        built = build(where, a.out, a.timeout)
        return built, 0 if built["status"] == "native-built" else 2
    if a.command == "run":
        ran = run(where, tuple(a.arguments), a.timeout, a.memory_mib, a.out)
        return ran, 0 if ran.get("exit_code") == 0 else 1
    tested = test(where, a.jobs, a.timeout, a.memory_mib)
    return tested, 0 if tested["status"] == "passed-test-blocks" else 1

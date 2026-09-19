"""One command for native compilation, projects, tests and scalar comparison."""

from __future__ import annotations

import argparse
import ctypes.util
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import __version__
from .cairnc import Diagnostic, certify_templates, compile_source
from .project import ProjectError, contained_file, load_project, read_text
from .toolchain import ARCHS, TARGETS, emulator, host_family


def report(value: dict) -> None:
    print(json.dumps(value, indent=2, allow_nan=False))


def create_project(destination: Path) -> dict:
    """Create only; never overwrite a directory, even when it is empty."""
    name = destination.name
    import re

    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ProjectError("Choose an ASCII project name of 1..64 characters.")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "src").mkdir()
    (destination / "tests").mkdir()
    (destination / "cairn.toml").write_text(f'''[project]
name = "{name}"
sources = ["src/math.cairn", "src/main.cairn"]
tests = ["tests/average.json"]

[build]
kind = "exe"
arch = "baseline"
''')
    (destination / "src/math.cairn").write_text("""// Floor average without overflowing the intermediate sum.
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);
""")
    (destination / "src/main.cairn").write_text("""fn main() -> i32 {
  if average(10, 20) == 15 { return 0; }
  return 1;
}
""")
    values = [0, 1, 2, 255, 256, 2**63 - 1, 2**63, 2**64 - 2, 2**64 - 1]
    contract = {
        "schema": "cairn.task/1",
        "symbol": "average",
        "cases": [{"args": {"x": x, "y": y}, "return": (x + y) // 2} for x in values for y in values],
    }
    (destination / "tests/average.json").write_text(json.dumps(contract, indent=2) + "\n")
    (destination / ".gitignore").write_text("build/\n")
    return {"status": "created", "project": str(destination.resolve()), "network_access": False}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cairn", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report local tools; never downloads them.")
    new = sub.add_parser("new", help="Create a data-only example project.")
    new.add_argument("directory", type=Path)
    for name in ["check", "emit", "expand", "build", "run", "test", "inspect", "doc"]:
        c = sub.add_parser(name)
        c.add_argument("path", nargs="?", default=".")
        if name in {"build", "run", "test"}:
            c.add_argument("--cxx", default="clang++")
        if name == "doc":
            c.add_argument(
                "--module", action="append", help="Document this module (repeatable); default: the project's own."
            )
            c.add_argument("--std", action="store_true", help="Document the packaged standard library instead.")
        if name == "check":
            c.add_argument("--generics", action="store_true",
                           help="Also check each generic function once against its bounds; fail if one needs more.")  # fmt: skip
        if name in {"build", "run"}:
            c.add_argument("--out", type=Path)
            c.add_argument("--arch", choices=sorted(ARCHS))
            c.add_argument("--target", choices=sorted(TARGETS), help="Freestanding profile; default hosted.")
            c.add_argument("--timeout", type=int, default=60)
            c.add_argument("--debug", action="store_true", help="Debug symbols that point at the CAIRN source.")
            c.add_argument("--incremental", action="store_true",
                           help="One object per module, reused by content hash; gives up inlining across modules.")  # fmt: skip
        if name == "run":
            c.add_argument(
                "--memory-mib", type=int, default=1024, help="Native address-space cap, 64..65536 MiB; not a sandbox."
            )
        if name == "build":
            c.add_argument("--kind", choices=["library", "exe"])
        if name == "test":
            c.add_argument("--contract", type=Path)
        if name == "inspect":
            c.add_argument("--symbol", required=True)
    v = sub.add_parser("verify", help="SMT source equivalence, not native or Lean verification.")
    v.add_argument("reference", type=Path)
    v.add_argument("candidate", type=Path)
    mode = v.add_mutually_exclusive_group(required=True)
    mode.add_argument("--symbol")
    mode.add_argument("--all", action="store_true", help="Require scalar equivalence for every declared function.")
    v.add_argument("--timeout-ms", type=int, default=3000)
    sub.add_parser("certificates", help="Check collector arithmetic certificates; not a Lean/compiler proof.")
    f = sub.add_parser("fmt", help="Format CAIRN sources in place; refuses any change to the token stream.")
    f.add_argument("paths", nargs="+", type=Path, help="Files, or directories searched for *.cairn.")
    f.add_argument("--check", action="store_true", help="Write nothing; exit 1 if any file would change.")
    f.add_argument("--diff", action="store_true", help="Write nothing; print a unified diff of what would change.")
    sub.add_parser("lsp", help="Speak the Language Server Protocol over stdin/stdout.")
    a = p.parse_args(argv)
    project = None
    try:
        if a.command == "doctor":
            elan = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".elan/bin")])
            report(
                {
                    "version": __version__,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "clang++": shutil.which("clang++"),
                    "g++": shutil.which("g++"),
                    "z3": ctypes.util.find_library("z3"),
                    "nvcc": shutil.which("nvcc"),  # builds programs that index @device views
                    "lean": shutil.which("lean", path=elan),  # rebuilds proofs/
                    "lake": shutil.which("lake", path=elan),
                    "qemu-system-aarch64": shutil.which("qemu-system-aarch64"),  # runs aarch64-virt images
                    "formal_status": "not-verified",
                    "native_platform": "Linux " + host_family(),
                    "network_access": False,
                }
            )
            return 0
        if a.command == "certificates":
            from .linear_certificates import audit_collector

            report(audit_collector())
            return 0
        if a.command == "new":
            report(create_project(a.directory))
            return 0
        if a.command == "fmt":
            from .formatting import format_paths

            return format_paths(a.paths, a.check, a.diff)
        if a.command == "lsp":
            from .lsp import serve

            return serve()
        if a.command == "verify" and a.all:
            from .verification import verify_module

            result = verify_module(read_text(a.reference, 64000), read_text(a.candidate, 64000), a.timeout_ms)
            report(result)
            return 0 if result["status"] == "smt-module-equivalent" else 2
        if a.command == "verify":
            from .scalar_semantics import equivalent

            result = equivalent(
                read_text(a.reference, 64000), read_text(a.candidate, 64000), a.symbol, timeout_ms=a.timeout_ms
            )
            report(result)
            return (
                0
                if result["status"] == "smt-equivalent"
                else 1
                if result["status"]
                in {"counterexample", "rejected", "invalid-contract", "invalid-domain", "invalid-reference"}
                else 2
            )
        if a.command == "run" and not 64 <= a.memory_mib <= 65536:
            raise ProjectError("Native memory limit must be 64..65536 MiB.")
        if a.command == "doc" and a.std:  # The packaged library needs no project.
            from .docs import standard_library

            print(standard_library(), end="")
            return 0
        project = load_project(a.path)
        if a.command in {"check", "emit"}:
            generated, receipt = compile_source(project.source)
            if a.command == "emit":
                print(generated, end="")
                return 0
            result = {"status": "typed", "functions": receipt["function_count"], "formal_status": "not-verified",
                      "project": project.receipt()}  # fmt: skip
            if (
                a.generics
            ):  # "ok": every instance that satisfies the bounds checks; else what the body needed beyond them.
                linked = tuple(module + "." for module in receipt["modules"] if module.startswith("std."))
                result["generics"] = {
                    n: v for n, v in certify_templates(project.source).items() if not n.startswith(linked)
                }
            report(result)
            return 1 if any(v != "ok" for v in result.get("generics", {}).values()) else 0
        if a.command == "expand":  # What the derivations generated, as source.
            from .agent_tools import expanded_source

            print(expanded_source(project.source), end="")
            return 0
        if a.command == "doc":
            from .docs import document

            print(document(project.source, a.module), end="")
            return 0
        if a.command == "inspect":
            from .agent_tools import EditSession

            report(EditSession(project.source, a.symbol).packet())
            return 0
        if a.command == "test":
            from .agent_tools import load_json_strict
            from .testing import evaluate

            paths = (
                [a.contract] if a.contract else [contained_file(project.root, x, ".json") for x in project.contracts]
            )
            if not paths:
                raise ProjectError("No test contracts. Add project.tests or supply --contract.")
            results = []
            for path in paths:
                result = evaluate(project.source, load_json_strict(read_text(path, 2_000_000)), a.cxx)
                results.append({"contract": path.name, **result})
            passed = all(x["status"] == "passed-finite-tests" for x in results)
            report(
                {
                    "status": "passed-finite-tests" if passed else "tests-not-passed",
                    "tests": results,
                    "formal_status": "not-verified",
                }
            )
            return 0 if passed else 1
        from .build import build

        result = build(
            project,
            output=a.out,
            cxx=a.cxx,
            arch=a.arch,
            kind="exe" if a.command == "run" else a.kind,
            timeout=a.timeout,
            target=a.target,
            debug=a.debug,
            incremental=a.incremental,
        )
        if a.command == "build" or result["status"] != "native-built":
            report(result)
            return 0 if result["status"] == "native-built" else 2
        # Execution is explicit. Process timeout is not an OS security sandbox.
        from .testing import resource

        # A freestanding image is not a host process: it runs in the emulator its target names.
        machine = emulator(result["target"], result["artifact"])

        def limits():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (a.timeout, a.timeout))
            if "cuda" not in result["frontend"]["requires"]:  # Unified addressing reserves far more than it uses.
                memory = a.memory_mib * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

        cp = subprocess.run(
            machine or [result["artifact"]],
            capture_output=True,
            text=True,
            timeout=a.timeout,
            stdin=subprocess.DEVNULL if machine else None,
            preexec_fn=None if machine else limits,
        )
        report(
            {
                "status": "program-exited",
                "exit_code": cp.returncode,
                "stdout": cp.stdout,
                "stderr": cp.stderr,
                "build_directory": result["directory"],
                "security_sandbox": False,
                "memory_limit_mib": None if machine else a.memory_mib,
                "emulator": machine,
            }
        )
        return 0 if cp.returncode == 0 else 1
    except Diagnostic as error:
        report(project.locate(error) if project else error.data)
        return 1
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        report({"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT", "message": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

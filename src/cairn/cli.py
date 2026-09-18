"""One command for native compilation, projects, tests and scalar comparison."""

from __future__ import annotations

import argparse
import ctypes.util
import json
import platform
import shutil
import subprocess
from pathlib import Path

from . import __version__
from .cairnc import Diagnostic, compile_source
from .project import ProjectError, contained_file, load_project, read_text
from .toolchain import ARCHS, host_family


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
    for name in ["check", "emit", "build", "run", "test", "inspect"]:
        c = sub.add_parser(name)
        c.add_argument("path", nargs="?", default=".")
        if name in {"build", "run", "test"}:
            c.add_argument("--cxx", default="clang++")
        if name in {"build", "run"}:
            c.add_argument("--out", type=Path)
            c.add_argument("--arch", choices=sorted(ARCHS))
            c.add_argument("--timeout", type=int, default=60)
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
    a = p.parse_args(argv)
    project = None
    try:
        if a.command == "doctor":
            report(
                {
                    "version": __version__,
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "clang++": shutil.which("clang++"),
                    "g++": shutil.which("g++"),
                    "z3": ctypes.util.find_library("z3"),
                    "lean": shutil.which("lean"),
                    "lake": shutil.which("lake"),
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
        project = load_project(a.path)
        if a.command in {"check", "emit"}:
            generated, receipt = compile_source(project.source)
            if a.command == "emit":
                print(generated, end="")
            else:
                report(
                    {
                        "status": "typed",
                        "functions": receipt["function_count"],
                        "formal_status": "not-verified",
                        "project": project.receipt(),
                    }
                )
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
        )
        if a.command == "build" or result["status"] != "native-built":
            report(result)
            return 0 if result["status"] == "native-built" else 2
        # Execution is explicit. Process timeout is not an OS security sandbox.
        from .testing import resource

        def limits():
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_CPU, (a.timeout, a.timeout))
            memory = a.memory_mib * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

        cp = subprocess.run([result["artifact"]], capture_output=True, text=True, timeout=a.timeout, preexec_fn=limits)
        report(
            {
                "status": "program-exited",
                "exit_code": cp.returncode,
                "stdout": cp.stdout,
                "stderr": cp.stderr,
                "build_directory": result["directory"],
                "security_sandbox": False,
                "memory_limit_mib": a.memory_mib,
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

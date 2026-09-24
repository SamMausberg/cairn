"""One command for native compilation, projects, tests and scalar comparison."""

from __future__ import annotations

import functools
import gc
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .commands import parser
from .compiler.cairnc import Diagnostic, certify_templates, compile_source
from .compiler.modules import library_source
from .editor import terminal
from .projects.new import create_project
from .projects.project import ProjectError, contained_file, load_project, read_text
from .projects.target import resolve as resolve_device
from .projects.toolchain import emulator, host_family, resolve_arch

FORMAT: str | None = None  # --format as given; None lets the stream decide (see editor/terminal.py)
LINES = False  # a watched check's records, one per line (JSON Lines), so a reader can take each as it comes


def report(value: dict, brief: bool = False) -> None:
    """The JSON record, or one line for a person when `brief` results are rendered for a terminal."""
    if brief and terminal.human(FORMAT):
        terminal.summary(value)
        return
    print(json.dumps(value, allow_nan=False) if LINES else json.dumps(value, indent=2, allow_nan=False))


def preconditions(items: list[str]) -> dict[str, str]:
    """`--assume symbol=expression` entries, one per function; the expression keeps its own `=` signs."""
    given: dict[str, str] = {}
    for item in items:
        name, sep, text = item.partition("=")
        if not sep or not name.strip() or not text.strip():
            raise ProjectError(f"Write --assume SYMBOL=EXPRESSION, not {item!r}.")
        if name.strip() in given:
            raise ProjectError(f"--assume gives {name.strip()} two preconditions; write one.")
        given[name.strip()] = text.strip()
    return given


def written(d: dict, own: str) -> str:
    """The text a refusal's line counts in: a linked library module's own file, or the program's."""
    return (library_source(d["module"]) if d.get("module", "").startswith("std.") else None) or own


REFUSED = {"counterexample", "rejected", "invalid-contract", "invalid-domain", "invalid-reference"}  # verify exits 1


def carded(supplied, name: str | None) -> tuple:
    """`--card NAME`: the profile that prices host work on this machine and device work on that card, and the
    card's compute capability and key, from which the device target is resolved when nothing names one."""
    if not name:
        return supplied, None
    if name == "all":
        raise ProjectError("--card all is for cairn predict; a search or a comparison prices one card.")
    from .perf.profile import card, carrying, default

    chosen = card(name)
    return carrying(supplied or default(), chosen), (chosen.device.compute_capability, chosen.source["card"])


def stamps(path: str) -> tuple:
    """What a watched check compares between rounds: each file the project reads, with its time and size."""
    try:
        project = load_project(path)
        files = [project.root / "cairn.toml", *(project.root / u.path for u in project.units)]
    except (ProjectError, OSError, ValueError, Diagnostic):  # a broken manifest is watched too, until it is fixed
        where = Path(path)
        files = [where / "cairn.toml" if where.is_dir() else where]
    return tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) if f.is_file() else (str(f),) for f in files)


def watch(path: str, again: list[str]) -> int:
    """`cairn check --watch`: the same check as without it, run again each time a file it reads changes. Every
    round's JSON record is one line, so a watching editor or agent reads JSON Lines."""
    global LINES
    seen, LINES = None, True
    try:
        while True:
            now = stamps(path)
            if now != seen:
                seen = now
                if terminal.human(FORMAT):
                    print(f"-- {time.strftime('%H:%M:%S')} {path}", flush=True)
                main(["check", path, *again])
                sys.stdout.flush()
                sys.stderr.flush()
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 0
    finally:
        LINES = False


def main(argv: list[str] | None = None) -> int:
    global FORMAT
    # A compile builds millions of tree objects that live until it ends, so the collector looks at old ones rarely.
    # Interleaved checks of a 77,000-line project ran 10 to 20 percent faster at the same peak memory; the loaded
    # scale run of evidence/v1_0/scale does not separate that from its noise.
    gc.set_threshold(50_000, 50, 100)
    p = parser()
    argv = sys.argv[1:] if argv is None else argv
    given = argv.index("--") if "--" in argv else len(argv)  # what follows is the program's, for `cairn run`
    a = p.parse_args(argv[:given])
    a.arguments = argv[given + 1 :]
    if a.arguments and a.command != "run":
        p.error("only `cairn run PATH -- ARGS` passes arguments on to a program")
    FORMAT = getattr(a, "format", None)
    project = None
    try:
        assumed = preconditions(a.assume) if a.command == "verify" else {}
        if a.command == "doctor":
            import ctypes.util  # Only the doctor looks for libz3, so no other command pays to load ctypes.

            elan = os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".elan/bin")])
            tools = {
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
            }  # fmt: skip
            if terminal.human(FORMAT):
                print("\n".join(f"{k:<20} {'missing' if v is None else v}" for k, v in tools.items()))
            else:
                report(tools)
            return 0
        if a.command == "cards":
            from .perf.profile import cards, described, listing

            found = [described(c) for c in cards().values()]
            print(listing(found)) if terminal.human(FORMAT) else report({"schema": "cairn.cards/1", "cards": found})
            return 0
        if a.command == "certificates":
            from .verify.linear_certificates import audit_collector

            report(audit_collector())
            return 0
        if a.command == "new":
            report(create_project(a.directory, a.template), brief=True)
            return 0
        if a.command == "fmt":
            from .editor.formatting import format_paths

            return format_paths(a.paths, a.check, a.diff)
        if a.command == "completions":
            from .editor.shells import completion_script

            print(completion_script(p, a.shell), end="")
            return 0
        if a.command == "lsp":
            from .editor.lsp import serve

            return serve()
        if a.command == "mcp":
            from .agent.mcp import serve as serve_mcp

            return serve_mcp()
        if a.command == "diff":
            from .editor import changes
            from .projects.revision import read
            from .verify.diff import diff, holds

            older, newer = read(a.old, a.within, a.std), read(a.new, a.within, a.std)
            try:
                record = diff(older.source, newer.source, timeout_ms=a.timeout_ms, budget_s=a.budget_s,
                              replays=a.replays, predict=not a.no_predict)  # fmt: skip
            except Diagnostic as error:  # placed in the version that is refused
                refused = older if error.data.get("side") == "old" else newer
                if terminal.human(FORMAT):
                    terminal.diagnostic({**refused.locate(error), "source_line": error.data["line"]}, refused.source)
                else:
                    report(refused.locate(error))
                return 1
            record["old"], record["new"] = (
                {"named": v.named, "kind": v.kind, "commit": v.commit} for v in (older, newer)
            )
            if a.markdown:
                a.markdown.write_text(changes.markdown(record, a.old, a.new), encoding="utf-8")
            print(changes.lines(record, a.old, a.new)) if terminal.human(FORMAT) else report(record)
            return 0 if holds(record, a.require) else 1
        if a.command == "verify" and a.all:
            from .verify.verification import verify_module

            result = verify_module(read_text(a.reference, 64000), read_text(a.candidate, 64000), a.timeout_ms, assumed)
            report(result)
            return 0 if result["status"] == "smt-module-equivalent" else 2
        if a.command == "verify":
            from .verify.scalar_semantics import equivalent

            if set(assumed) - {a.symbol}:
                raise ProjectError(f"--assume names a function other than the selected --symbol {a.symbol}.")
            reference, candidate = read_text(a.reference, 64000), read_text(a.candidate, 64000)
            result = equivalent(
                reference, candidate, a.symbol, assume=assumed.get(a.symbol, "true"), timeout_ms=a.timeout_ms
            )
            report(result)
            return 0 if result["status"] == "smt-equivalent" else 1 if result["status"] in REFUSED else 2
        if a.command == "test" and (not 0 <= a.jobs <= 64 or not 1 <= a.timeout <= 300):  # 0 jobs: as many as cores
            raise ProjectError("A test run takes 1..64 jobs and a timeout of 1..300 seconds per test.")
        if a.command in {"run", "test"} and not 64 <= a.memory_mib <= 65536:
            raise ProjectError("Native memory limit must be 64..65536 MiB.")
        if a.command == "doc" and a.std:  # The packaged library needs no project.
            from .editor.docs import standard_library, standard_library_pages

            if not a.pages:
                print(standard_library(), end="")
                return 0
            pages = standard_library_pages()
            (a.pages / "std").mkdir(parents=True, exist_ok=True)
            for stale in set((a.pages / "std").glob("*.md")) - {a.pages / name for name in pages}:
                stale.unlink()
            for name, text in pages.items():
                (a.pages / name).write_text(text, encoding="utf-8")
            report({"status": "documented", "pages": sorted(pages)})
            return 0
        if a.command == "check" and a.watch:  # before the project loads: a broken manifest is watched until fixed
            return watch(a.path, [*(["--format", FORMAT] if FORMAT else []), *(["--generics"] if a.generics else [])])
        if a.command == "export" or (
            a.command in {"build", "run", "test"} and (Path(a.path) / "export.json").is_file()
        ):
            from .projects import export

            if getattr(a, "emulate", False):
                raise ProjectError("An export builds as its record pins it; --emulate builds a project, not an export.")

            exported, exit_status = export.command(a)
            report(exported)
            return exit_status
        project = load_project(a.path)
        if a.command == "emit" and (a.header or a.ctypes):  # What a C, C++ or Python program uses to call it.
            from .compiler.header import binding, header

            mine = lambda f: project.wrote(f.line)  # noqa: E731
            print(
                binding(project.source, project.name, mine)
                if a.ctypes
                else header(
                    project.source, project.name, mine, "cuda" in compile_source(project.source)[1]["requires"]
                )[0],
                end="",
            )
            return 0
        if a.command in {"check", "emit"}:
            generated, receipt = compile_source(
                project.source, keep_guards=getattr(a, "keep_guards", False), sites=project.site, every=True
            )
            if a.command == "emit":
                print(generated, end="")
                return 0
            library = sum(1 for name in receipt["functions"] if name.startswith("std."))  # what the imports bring in
            result = {"status": "typed", "functions": receipt["function_count"], "library_functions": library,
                      "formal_status": "not-verified", "project": project.receipt()}  # fmt: skip
            if a.generics:  # "ok": every instance within the bounds checks; else what the body needed beyond them.
                linked = tuple(module + "." for module in receipt["modules"] if module.startswith("std."))
                verdicts = certify_templates(project.source).items()
                result["generics"] = {n: v for n, v in verdicts if not n.startswith(linked)}
            report(result, brief=True)
            return 1 if any(v != "ok" for v in result.get("generics", {}).values()) else 0
        if a.command == "expand":  # What the derivations generated, as source.
            from .agent.projection import expanded_source

            print(expanded_source(project.source), end="")
            return 0
        if a.command == "doc":
            from .editor.docs import document

            print(document(project.source, a.module), end="")
            return 0
        if a.command == "inspect":
            from .agent.agent_tools import EditSession

            session = EditSession(project.source, a.symbol, scope=a.scope)
            if a.expand:
                session.expand(a.expand)
            report({**session.packet(), **({"performance": session.explain()} if a.explain else {})})
            return 0
        if a.command == "migrate":  # A refusal names the file of the new text itself, so it is reported as it is.
            from .agent.migration import Migration

            if bad := [x for x in a.also if not x.partition("=")[1]]:
                raise ProjectError(f"Write --also NAME=SIGNATURE, not {bad[0]!r}.")
            try:
                m = Migration(a.path, a.symbol, a.to, dict(x.split("=", 1) for x in a.also), tuple(a.allow))
                report(m.apply(read_text(a.reply, 16_000_000)) if a.reply else m.packet())
            except Diagnostic as error:
                report(error.data)
                return 1
            return 0
        if a.command == "foreign":  # a device implementation is built and inspected here, and never run
            from .verify import foreign

            record = foreign.report(project, a.implementation, device_target=a.device_target)
            print(foreign.summary(record), end="") if terminal.human(FORMAT) else report(record)
            return 0 if foreign.passed(record) else 1
        if a.command == "graph":
            from .projects.graph import graph, summary

            record = graph(project, a.interfaces)
            print(summary(record), end="") if terminal.human(FORMAT) else report(record)
            return 0
        if a.command == "state" and a.symbol:  # one function's investigation, from its candidate history
            from .agent import investigation
            from .agent.history import vendored
            from .perf.resources import device_identity, host_target

            where = a.history or project.root / ".cairn" / "history"
            device = resolve_device(a.device_target, project.device_target, required=False)
            targets = {"host": host_target(resolve_arch(a.arch or project.arch), a.cxx),
                       "device": device_identity(device)}  # fmt: skip
            packet = investigation.investigation(project.source, a.symbol, where, targets, vendored(project))
            earlier = json.loads(read_text(a.since, 16_000_000)) if a.since else None
            report(investigation.delta(earlier, packet) if earlier else packet)
            return 0
        if a.command == "state":
            from .agent.state import delta, state

            now = state(project.source, locate=project.locate)
            report(delta(json.loads(read_text(a.since, 16_000_000)), now) if a.since else now)
            return 0 if now["status"] == "typed" else 1
        if a.command == "explain":
            from .agent.explain import explain

            chosen = set(a.symbol) if a.symbol else None
            result = explain(project.source, project.origin, chosen, a.cxx, a.arch or project.arch, project.root)
            if chosen and chosen - set(result["functions"]):
                raise ProjectError(f"No function {sorted(chosen - set(result['functions']))[0]} to explain.")
            report(result)
            return 0
        if a.command == "shot":
            from .agent.shot import lines, shot

            before = load_project(a.against).source if a.against else None
            taken = shot(project, a.symbol or [], before, cxx=a.cxx, timeout=a.timeout)
            print(lines(taken)) if terminal.human(FORMAT) else report(taken)
            return 0 if taken["status"] == "shot" else 1
        if a.command == "predict":
            from .perf import report as priced
            from .perf.profile import Profile

            chosen = set(a.symbol) if a.symbol else None
            sizes, supplied = priced.parse_sizes(a.at), Profile.load(a.profile) if a.profile else None
            arch = resolve_arch(a.arch or project.arch)
            if a.card == "all":  # a row per card, each for its own target unless one is named
                if a.against:
                    raise ProjectError("--card all prices one version on every card; compare two on one card.")
                answer = priced.across(project.source, sizes, chosen, supplied, arch, a.device_target,
                                       project.device_target, a.inspect)  # fmt: skip
                print(priced.lines_across(answer)) if terminal.human(FORMAT) else report(answer)
                return 0
            supplied, card = carded(supplied, a.card)
            device = resolve_device(a.device_target, project.device_target, required=a.inspect, card=card)
            if a.against:
                before = load_project(a.against).source
                answer = priced.delta(before, project.source, sizes, chosen, supplied, arch, device, a.inspect)
            else:
                answer = priced.report(project.source, sizes, chosen, supplied, arch, device, a.inspect, project.site)
            print(priced.lines(answer)) if terminal.human(FORMAT) else report(answer)
            return 0
        if a.command == "tune":
            from .agent.history import vendored
            from .perf import report as priced
            from .perf.plan_source import KEEP, write_plan
            from .perf.profile import Profile
            from .perf.tune import Budget, tune
            from .perf.tune import delta as tune_delta
            from .perf.tune import lines as tune_lines

            supplied, card = carded(Profile.load(a.profile) if a.profile else None, a.card)
            arch = resolve_arch(a.arch or project.arch)
            device = resolve_device(a.device_target, project.device_target, required=False, card=card)
            budget = Budget(a.budget_compiles, a.budget_seconds, a.budget_runs)
            kept = None if a.no_history else a.history or project.root / ".cairn" / "history"
            if a.compare:  # a difference report between two plans, in place of a search
                from .perf import feedback

                if len(a.compare) != 2:
                    raise ProjectError("--compare names two plans: the one to compare against, then the other.")
                first, second = (feedback.parse_candidate(x) for x in a.compare)
                answer = feedback.compare(project.source, a.symbol[0], first, second, priced.parse_sizes(a.at),
                                          supplied, arch, kept, device, a.budget_compiles, a.artifacts, a.cxx,
                                          vendored(project))  # fmt: skip
                print(feedback.lines_for_people(answer)) if terminal.human(FORMAT) else report(answer)
                return 0
            answer = tune(project.source, a.symbol[0], priced.parse_sizes(a.at), supplied, arch, a.measure, a.cxx,
                          a.device, device, budget, kept, vendored(project), a.accept_emulated)  # fmt: skip
            if a.write:  # Only the plan line changes, in the file that declares the function, and only if it checks.
                use = answer["chosen"].get("use") if "implementations" in answer else KEEP  # the reference: none
                answer["written"] = write_plan(a.path, a.symbol[0], answer["chosen"], use)
            earlier = json.loads(read_text(a.since, 16_000_000)) if a.since else None
            if earlier:
                report(tune_delta(earlier, answer))
            else:
                print(tune_lines(answer)) if terminal.human(FORMAT) else report(answer)
            return 0
        if a.command == "validate":
            from .agent.agent_tools import load_json_strict
            from .verify.validation import validate_project

            given = load_json_strict(read_text(a.policy, 200_000)) if a.policy else None
            device = resolve_device(a.device_target, project.device_target) if a.emulate else None
            record = validate_project(project, a.symbol, given, a.cxx, a.regressions, a.history, device)
            report(record, brief=True)
            return 0 if record["status"] == "passed" else 1 if record["status"] in {"failed", "rejected"} else 2
        if a.command == "test":
            from .agent.agent_tools import load_json_strict
            from .verify.runner import run_tests
            from .verify.validation import evaluate

            paths = (
                [a.contract] if a.contract else [contained_file(project.root, x, ".json") for x in project.contracts]
            )
            paths = [path for path in paths if a.filter in path.name and not a.test]
            blocks = {"status": "no-test-blocks", "tests": []}  # --contract runs that contract alone
            if not a.contract:
                blocks = run_tests(project, cxx=a.cxx, chosen=a.test or a.filter, exact=bool(a.test), jobs=a.jobs,
                                   timeout=a.timeout, memory_mib=a.memory_mib, device_target=a.device_target,
                                   emulate=a.emulate)  # fmt: skip
            if not paths and not blocks["tests"] and blocks["status"] == "no-test-blocks":
                named = f" named {a.test!r}" if a.test else f" whose name contains {a.filter!r}" if a.filter else ""
                raise ProjectError(f"No tests{named}: write a test block, add project.tests or supply --contract.")
            emulating = resolve_device(a.device_target, project.device_target, required=False) if a.emulate else None
            results = [{"contract": path.name, **evaluate(project.source, load_json_strict(read_text(path, 2_000_000)),
                                                          a.cxx, project.libraries, emulating)} for path in paths]  # fmt: skip
            passed = all(x["status"] == "passed-finite-tests" for x in results)
            passed &= blocks["status"] in {"passed-test-blocks", "no-test-blocks"}
            status = "passed-finite-tests" if passed else "tests-not-passed"
            report({"status": status, "tests": results, "blocks": blocks, "formal_status": "not-verified"}, brief=True)
            return 0 if passed else 1
        from .projects.build import build

        result = build(project, output=a.out, cxx=a.cxx, arch=a.arch, kind="exe" if a.command == "run" else a.kind,
                       timeout=a.timeout, target=a.target, debug=a.debug, incremental=a.incremental,
                       keep_guards=a.keep_guards, header=getattr(a, "header", False),
                       device_target=a.device_target, emulate=a.emulate)  # fmt: skip
        if a.command == "build" or result["status"] != "native-built":
            report(result, brief=True)
            return 0 if result["status"] == "native-built" else 2
        # Execution is explicit. Process timeout is not an OS security sandbox.
        from .verify.testing import limited

        # A freestanding image is not a host process: it runs in the emulator its target names.
        machine = emulator(result["target"], result["artifact"])
        # Unified addressing reserves far more than it uses; an emulated program's device memory is host memory.
        cuda = "cuda" in result["frontend"]["requires"] and "emulation" not in result
        limits = functools.partial(limited, a.timeout, None if cuda else a.memory_mib)
        emulated = {"emulation": result["emulation"]} if "emulation" in result else {}

        if machine and a.arguments:
            raise ProjectError("A freestanding image is started by its board, with no arguments.")
        started = machine or [result["artifact"], *a.arguments]
        run: dict = {"stdin": subprocess.DEVNULL} if machine else {"preexec_fn": limits}
        if terminal.human(FORMAT):  # A person sees the program itself: its streams are the terminal's.
            if emulated:
                print(f"note: {result['emulation']['claim']}", file=sys.stderr, flush=True)
            code = subprocess.run(started, timeout=a.timeout, check=False, **run).returncode
            if code:
                print(f"error: {project.name} {terminal.ended(code)}", file=sys.stderr)
            return 0 if code == 0 else 1
        # A program may print bytes that are not UTF-8 (an image, a zlib stream); the record escapes them.
        cp = subprocess.run(
            started, capture_output=True, text=True, errors="backslashreplace", timeout=a.timeout, **run
        )
        report({"status": "program-exited", "exit_code": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr,
                "build_directory": result["directory"], "security_sandbox": False,
                "memory_limit_mib": None if machine else a.memory_mib, "emulator": machine,
                **emulated})  # fmt: skip
        return 0 if cp.returncode == 0 else 1
    except Diagnostic as error:
        located = project.locate(error) if project else error.data
        if terminal.human(FORMAT):
            own = project.source if project else ""
            terminal.refusals(located, error.data, lambda d: written(d, own))
        else:
            report(located)
        return 1
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        unknown = {"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT", "message": str(error)}
        if terminal.human(FORMAT):
            terminal.diagnostic(unknown, "")
        else:
            report(unknown)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

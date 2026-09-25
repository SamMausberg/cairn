"""One command for native compilation, projects, tests and scalar comparison.

`main` parses the command line and runs `cairn_<command>` of this module: with the arguments alone for a command in
`ALONE`, and otherwise with the project the path names, loaded once, which also places every refusal in its file.
"""

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import __version__
from .agent.diagnostics import declared, taught
from .commands import parser
from .compiler.cairnc import Diagnostic, certify_templates, compile_source
from .compiler.syntax.modules import library_source
from .editor import terminal
from .projects.new import create_project
from .projects.project import Project, ProjectError, contained_file, load_project, read_text
from .projects.target import resolve as resolve_device
from .projects.toolchain import SANITIZER_ENVIRONMENT, emulator, host_family, resolve_arch

FORMAT: str | None = None  # --format as given; None lets the stream decide (see editor/terminal.py)
LINES = False  # a watched check's records, one per line (JSON Lines), so a reader can take each as it comes
# The commands that read no project: their function takes the arguments alone.
ALONE = {"doctor", "cards", "certificates", "rules", "find", "new", "fmt", "completions", "lsp", "mcp", "diff", "verify"}  # fmt: skip
REFUSED = {"counterexample", "rejected", "invalid-contract", "invalid-domain", "invalid-reference"}  # verify exits 1


def report(value: dict, brief: bool = False) -> None:
    """The JSON record, or one line for a person when `brief` results are rendered for a terminal."""
    if brief and terminal.human(FORMAT):
        terminal.summary(value)
        return
    print(json.dumps(value, allow_nan=False) if LINES else json.dumps(value, indent=2, allow_nan=False))


def show(record: dict, human: Callable[[dict], str], end: str = "\n") -> None:
    """The record as `human` renders it for a person at a terminal, else the JSON record."""
    print(human(record), end=end) if terminal.human(FORMAT) else report(record)


def since(a: Any) -> Any:
    """The record saved at `--since`, which an answer is then given as a change from; None without one."""
    return json.loads(read_text(a.since, 16_000_000)) if a.since else None


def printed(built: dict) -> dict:
    """What `cairn build` prints: its record without the checker's receipt of every function, which `receipt.json` in
    the build directory keeps whole. That receipt was 54 KB for an 85-line program and buried the fields a caller
    reads next, the status, the command and the artifact."""
    kept = {k: v for k, v in built.items() if k != "frontend"}
    return {**kept, "receipt": str(Path(built["directory"]) / "receipt.json")} if "directory" in built else kept


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


# The commands that read no project ------------------------------------------------------------------------------


def cairn_doctor(a: Any) -> int:
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
    }
    show(tools, lambda t: "\n".join(f"{k:<20} {'missing' if v is None else v}" for k, v in t.items()))
    return 0


def cairn_cards(a: Any) -> int:
    from .perf.profile import cards, described, listing

    show({"schema": "cairn.cards/1", "cards": [described(c) for c in cards().values()]}, lambda r: listing(r["cards"]))
    return 0


def cairn_certificates(a: Any) -> int:
    from .verify.linear_certificates import audit_collector

    report(audit_collector())
    return 0


def cairn_rules(a: Any) -> int:
    """The cards, offline: nothing is compiled."""
    from .agent.teaching import CODE, every_card, rules

    asked = "" if a.list else a.asked
    path = asked and asked not in every_card() and not CODE.fullmatch(asked) and Path(asked).exists()
    record = rules(asked, load_project(asked).source if path else None)
    if record is None and CODE.fullmatch(asked):
        raise Diagnostic("E-RULE", f"No card states {asked}: the compiler, the hosts and cairn never emit it. "
                         "A code a recipe's require chooses is explained by its message.")  # fmt: skip
    if record is None:
        raise Diagnostic("E-RULE", f"{asked!r} is no diagnostic code, card or path; cairn rules --list names "
                         "every card.")  # fmt: skip
    terminal.rules(record) if terminal.human(FORMAT) else report(record)
    return 0


def cairn_find(a: Any) -> int:
    """The program searched is the one --in names, else a project here; with neither, the library alone."""
    from .agent.find import find, lines

    within = a.within or ("." if Path("cairn.toml").is_file() else None)
    source = load_project(within).source if within else None
    show(find(source, " ".join(a.words), a.takes, a.returns, a.effects, a.limit), lines)
    return 0


def cairn_new(a: Any) -> int:
    if a.from_sol_execbench:
        from .projects.harness.importing import create

        if a.template != "default":
            raise ProjectError("--from-sol-execbench writes its own project; it takes no --template.")
        report(create(a.directory, a.from_sol_execbench, a.device_target), brief=True)
        return 0
    if a.device_target:
        raise ProjectError("--device-target names the target of a --from-sol-execbench project.")
    report(create_project(a.directory, a.template), brief=True)
    return 0


def cairn_fmt(a: Any) -> int:
    from .editor.formatting import format_paths

    return format_paths(a.paths, a.check, a.diff)


def cairn_completions(a: Any) -> int:
    from .editor.shells import completion_script

    print(completion_script(parser(), a.shell), end="")
    return 0


def cairn_lsp(a: Any) -> int:
    from .editor.lsp.server import serve

    return serve()


def cairn_mcp(a: Any) -> int:
    from .agent.mcp.server import serve

    return serve()


def cairn_diff(a: Any) -> int:
    from .editor import changes
    from .projects.revision import read
    from .verify.diff import diff, holds

    older, newer = read(a.old, a.within, a.std), read(a.new, a.within, a.std)
    try:
        record = diff(older.source, newer.source, timeout_ms=a.timeout_ms, budget_s=a.budget_s, replays=a.replays,
                      predict=not a.no_predict)  # fmt: skip
    except Diagnostic as error:  # placed in the version that is refused
        refused = older if error.data.get("side") == "old" else newer
        located = taught(refused.locate(error))
        if terminal.human(FORMAT):
            terminal.diagnostic({**located, "source_line": error.data["line"]}, refused.source)
        else:
            report(located)
        return 1
    record["old"], record["new"] = ({"named": v.named, "kind": v.kind, "commit": v.commit} for v in (older, newer))
    if a.markdown:
        a.markdown.write_text(changes.markdown(record, a.old, a.new), encoding="utf-8")
    show(record, lambda r: changes.lines(r, a.old, a.new))
    return 0 if holds(record, a.require) else 1


def cairn_verify(a: Any) -> int:
    assumed = preconditions(a.assume)
    if a.all:
        from .verify.verification import verify_module

        result = verify_module(read_text(a.reference, 64000), read_text(a.candidate, 64000), a.timeout_ms, assumed)
        report(result)
        return 0 if result["status"] == "smt-module-equivalent" else 2
    from .verify.scalar.semantics import equivalent

    if set(assumed) - {a.symbol}:
        raise ProjectError(f"--assume names a function other than the selected --symbol {a.symbol}.")
    reference, candidate = read_text(a.reference, 64000), read_text(a.candidate, 64000)
    result = equivalent(reference, candidate, a.symbol, assume=assumed.get(a.symbol, "true"), timeout_ms=a.timeout_ms)
    report(result)
    return 0 if result["status"] == "smt-equivalent" else 1 if result["status"] in REFUSED else 2


# What answers before a project loads ---------------------------------------------------------------------------


def standard_library_docs(a: Any) -> int:
    """`cairn doc --std`: the packaged library needs no project."""
    from .editor.docs import library_names, standard_library, standard_library_pages

    wanted = [m if m.startswith("std.") else "std." + m for m in a.module or []]  # `text` is `std.text`
    if missing := [m for m in wanted if m not in library_names()]:
        raise ProjectError(f"No packaged module {missing[0]}: the library has {', '.join(library_names())}.")
    if wanted and a.pages:
        raise ProjectError("--pages writes every module's page; --module prints the modules it names.")
    if not a.pages:
        print(standard_library(wanted), end="")
        return 0
    pages = standard_library_pages()
    (a.pages / "std").mkdir(parents=True, exist_ok=True)
    for stale in set((a.pages / "std").glob("*.md")) - {a.pages / name for name in pages}:
        stale.unlink()
    for name, text in pages.items():
        (a.pages / name).write_text(text, encoding="utf-8")
    report({"status": "documented", "pages": sorted(pages)})
    return 0


def exported(a: Any) -> int:
    """`cairn export`, and `cairn build`, `run` or `test` of an export directory."""
    from .projects import export

    if getattr(a, "emulate", False):
        raise ProjectError("An export builds as its record pins it; --emulate builds a project, not an export.")
    record, exit_status = export.command(a)
    report(record)
    return exit_status


# The commands that read a project -------------------------------------------------------------------------------


def cairn_emit(a: Any, project: Project) -> int:
    if a.header or a.ctypes:  # What a C, C++ or Python program uses to call it.
        from .compiler.lower.header import binding, header

        mine = lambda f: project.wrote(f.line)  # noqa: E731
        cuda = "cuda" in compile_source(project.source)[1]["requires"]
        print(
            binding(project.source, project.name, mine, cuda)
            if a.ctypes
            else header(project.source, project.name, mine, cuda)[0],
            end="",
        )
        return 0
    print(compile_source(project.source, keep_guards=a.keep_guards, sites=project.site, every=True)[0], end="")
    return 0


def cairn_check(a: Any, project: Project) -> int:
    receipt = compile_source(project.source, sites=project.site, every=True)[1]
    library = sum(1 for name in receipt["functions"] if name.startswith("std."))  # what the imports bring in
    result = {"status": "typed", "functions": receipt["function_count"], "library_functions": library,
              "formal_status": "not-verified", "project": project.receipt()}  # fmt: skip
    if a.generics:  # "ok": every instance within the bounds checks; else what the body needed beyond them.
        linked = tuple(module + "." for module in receipt["modules"] if module.startswith("std."))
        verdicts = certify_templates(project.source).items()
        result["generics"] = {n: v for n, v in verdicts if not n.startswith(linked)}
    report(result, brief=True)
    return 1 if any(v != "ok" for v in result.get("generics", {}).values()) else 0


def cairn_expand(a: Any, project: Project) -> int:
    """What the derivations generated, as source."""
    from .agent.projection import expanded_source

    print(expanded_source(project.source), end="")
    return 0


def cairn_doc(a: Any, project: Project) -> int:
    from .editor.docs import document

    print(document(project.source, a.module), end="")
    return 0


def cairn_inspect(a: Any, project: Project) -> int:
    from .agent.hosts.edits import EditSession

    session = EditSession(project.source, a.symbol, scope=a.scope)
    if a.expand:
        session.expand(a.expand)
    report({**session.packet(), **({"performance": session.explain()} if a.explain else {})})
    return 0


def cairn_migrate(a: Any, project: Project) -> int:
    """A refusal names the file of the new text itself, so it is reported as it is."""
    from .agent.hosts.migration import Migration

    if bad := [x for x in a.also if not x.partition("=")[1]]:
        raise ProjectError(f"Write --also NAME=SIGNATURE, not {bad[0]!r}.")
    try:
        m = Migration(a.path, a.symbol, a.to, dict(x.split("=", 1) for x in a.also), tuple(a.allow))
        report(m.apply(read_text(a.reply, 16_000_000)) if a.reply else m.packet())
    except Diagnostic as error:
        report(taught(error.data, host=True))
        return 1
    return 0


def cairn_foreign(a: Any, project: Project) -> int:
    """A device implementation is built and inspected here, and never run."""
    from .verify import foreign

    record = foreign.report(project, a.implementation, device_target=a.device_target)
    show(record, foreign.summary, end="")
    return 0 if foreign.passed(record) else 1


def cairn_graph(a: Any, project: Project) -> int:
    from .projects.graph import graph, summary

    show(graph(project, a.interfaces), summary, end="")
    return 0


def cairn_state(a: Any, project: Project) -> int:
    if a.symbol:  # one function's investigation, from its candidate history
        from .agent import investigation

        packet = investigation.of_project(project, a.symbol, a.history, a.cxx, a.arch, a.device_target)
        earlier = since(a)
        report(investigation.delta(earlier, packet) if earlier else packet)
        return 0
    from .agent.state import delta, state

    now = state(project.source, locate=project.locate)
    report(delta(since(a), now) if a.since else now)
    return 0 if now["status"] == "typed" else 1


def cairn_explain(a: Any, project: Project) -> int:
    from .agent.explain import explain

    chosen = set(a.symbol) if a.symbol else None
    result = explain(project.source, project.origin, chosen, a.cxx, a.arch or project.arch, project.root)
    if chosen and chosen - set(result["functions"]):
        raise ProjectError(f"No function {sorted(chosen - set(result['functions']))[0]} to explain.")
    report(result)
    return 0


def cairn_shot(a: Any, project: Project) -> int:
    from .agent.shot import lines, shot

    before = load_project(a.against).source if a.against else None
    taken = shot(project, a.symbol or [], before, cxx=a.cxx, timeout=a.timeout)
    show(taken, lines)
    return 0 if taken["status"] == "shot" else 1


def cairn_predict(a: Any, project: Project) -> int:
    from .perf import report as priced
    from .perf.profile import Profile

    chosen = set(a.symbol) if a.symbol else None
    sizes, supplied = priced.parse_sizes(a.at), Profile.load(a.profile) if a.profile else None
    arch = resolve_arch(a.arch or project.arch)
    if a.card == "all":  # a row per card, each for its own target unless one is named
        if a.against:
            raise ProjectError("--card all prices one version on every card; compare two on one card.")
        answer = priced.across(project.source, sizes, chosen, supplied, arch, a.device_target, project.device_target,
                               a.inspect)  # fmt: skip
        show(answer, priced.lines_across)
        return 0
    supplied, card = carded(supplied, a.card)
    device = resolve_device(a.device_target, project.device_target, required=a.inspect, card=card)
    if a.against:
        before = load_project(a.against).source
        answer = priced.delta(before, project.source, sizes, chosen, supplied, arch, device, a.inspect)
    else:
        answer = priced.report(project.source, sizes, chosen, supplied, arch, device, a.inspect, project.site)
    show(answer, priced.lines)
    return 0


def cairn_tune(a: Any, project: Project) -> int:
    from .agent.history import beside, vendored
    from .perf import report as priced
    from .perf.profile import Profile
    from .perf.tuning.plan_source import KEEP, write_plan
    from .perf.tuning.tune import Budget, tune
    from .perf.tuning.tune import delta as tune_delta
    from .perf.tuning.tune import lines as tune_lines
    from .verify.validation.validation import pinned_policy, regressions_of

    supplied, card = carded(Profile.load(a.profile) if a.profile else None, a.card)
    arch = resolve_arch(a.arch or project.arch)
    device = resolve_device(a.device_target, project.device_target, required=False, card=card)
    budget = Budget(a.budget_compiles, a.budget_seconds, a.budget_runs)
    kept = None if a.no_history else a.history or beside(project.root)
    sizes = priced.parse_sizes(a.at)
    weights = [1.0] * len(sizes)
    if a.shapes:  # after the --at sizes, each with its weight
        from .agent.hosts.edits import load_json_strict
        from .perf.tuning.objective import shapes

        more, heavy = shapes(load_json_strict(read_text(a.shapes, 1_000_000)))
        sizes, weights = sizes + more, weights + heavy
    if a.compare:  # a difference report between two plans, in place of a search
        from .perf.tuning import feedback

        if len(a.compare) != 2:
            raise ProjectError("--compare names two plans: the one to compare against, then the other.")
        first, second = (feedback.parse_candidate(x) for x in a.compare)
        answer = feedback.compare(project.source, a.symbol[0], first, second, priced.parse_sizes(a.at), supplied,
                                  arch, kept, device, a.budget_compiles, a.artifacts, a.cxx,
                                  vendored(project))  # fmt: skip
        show(answer, feedback.lines_for_people)
        return 0
    pinned = pinned_policy(regressions_of(project.root, a.symbol[0]), a.symbol[0])
    answer = tune(project.source, a.symbol[0], sizes, supplied, arch, a.measure, a.cxx, a.device, device, budget,
                  kept, vendored(project), a.accept_emulated, weights, a.objective, pinned)  # fmt: skip
    if a.write:  # Only the plan line changes, in the file that declares the function, and only if it checks.
        use = answer["chosen"].get("use") if "implementations" in answer else KEEP  # the reference: none
        answer["written"] = write_plan(a.path, a.symbol[0], answer["chosen"], use)
    earlier = since(a)
    if earlier:
        report(tune_delta(earlier, answer))
    else:
        show(answer, tune_lines)
    return 0


def cairn_validate(a: Any, project: Project) -> int:
    from .agent.hosts.edits import load_json_strict
    from .verify.validation.validation import validate_project

    given = load_json_strict(read_text(a.policy, 200_000)) if a.policy else None
    device = resolve_device(a.device_target, project.device_target) if a.emulate else None
    record = validate_project(project, a.symbol, given, a.cxx, a.regressions, a.history, device)
    report(record, brief=True)
    return 0 if record["status"] == "passed" else 1 if record["status"] in {"failed", "rejected"} else 2


def cairn_test(a: Any, project: Project) -> int:
    from .agent.hosts.edits import load_json_strict
    from .verify.runner import run_tests
    from .verify.validation.validation import evaluate

    paths = [a.contract] if a.contract else [contained_file(project.root, x, ".json") for x in project.contracts]
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
    results = [{"contract": path.name, **evaluate(project.source, load_json_strict(read_text(path, 2_000_000)), a.cxx,
                                                  project.libraries, emulating)} for path in paths]  # fmt: skip
    passed = all(x["status"] == "passed-finite-tests" for x in results)
    passed &= blocks["status"] in {"passed-test-blocks", "no-test-blocks"}
    status = "passed-finite-tests" if passed else "tests-not-passed"
    report({"status": status, "tests": results, "blocks": blocks, "formal_status": "not-verified"}, brief=True)
    return 0 if passed else 1


def cairn_build(a: Any, project: Project) -> int:
    """`cairn build`, and the build `cairn run` starts from: what it built, or with `run` what the program did."""
    from .projects.build import build

    result = build(project, output=a.out, cxx=a.cxx, arch=a.arch, kind="exe" if a.command == "run" else a.kind,
                   timeout=a.timeout, target=a.target, debug=a.debug, incremental=a.incremental,
                   keep_guards=a.keep_guards, header=getattr(a, "header", False), device_target=a.device_target,
                   emulate=a.emulate, sanitizer=a.sanitize)  # fmt: skip
    if a.command == "build" or result["status"] != "native-built":
        report(printed(result), brief=True)
        return 0 if result["status"] == "native-built" else 2
    # Execution is explicit. Process timeout is not an OS security sandbox.
    from .verify.testing import limited

    # A freestanding image is not a host process: it runs in the emulator its target names.
    machine = emulator(result["target"], result["artifact"])
    # Unified addressing reserves far more than it uses; an emulated program's device memory is host memory.
    cuda = "cuda" in result["frontend"]["requires"] and "emulation" not in result
    # A sanitizer maps shadow memory many times the program's size, so no memory cap holds it.
    capped = not (cuda or a.sanitize)
    limits = functools.partial(limited, a.timeout, a.memory_mib if capped else None)
    emulated = {"emulation": result["emulation"]} if "emulation" in result else {}

    if machine and a.arguments:
        raise ProjectError("A freestanding image is started by its board, with no arguments.")
    started = machine or [result["artifact"], *a.arguments]
    run: dict = {"stdin": subprocess.DEVNULL} if machine else {"preexec_fn": limits}
    if a.sanitize:
        run["env"] = {**os.environ, **SANITIZER_ENVIRONMENT[a.sanitize]}
    if terminal.human(FORMAT):  # A person sees the program itself: its streams are the terminal's.
        if emulated:
            print(f"note: {result['emulation']['claim']}", file=sys.stderr, flush=True)
        code = subprocess.run(started, timeout=a.timeout, check=False, **run).returncode
        if code:
            print(f"error: {project.name} {terminal.ended(code)}", file=sys.stderr)
        return 0 if code == 0 else 1
    # A program may print bytes that are not UTF-8 (an image, a zlib stream); the record escapes them.
    cp = subprocess.run(started, capture_output=True, text=True, errors="backslashreplace", timeout=a.timeout, **run)
    report({"status": "program-exited", "exit_code": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr,
            "build_directory": result["directory"], "security_sandbox": False,
            "memory_limit_mib": None if machine or not capped else a.memory_mib, "emulator": machine,
            **({"sanitizer": a.sanitize} if a.sanitize else {}), **emulated})  # fmt: skip
    return 0 if cp.returncode == 0 else 1


cairn_run = cairn_build


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
        if a.command in ALONE:
            return globals()["cairn_" + a.command](a)
        if a.command == "test" and (not 0 <= a.jobs <= 64 or not 1 <= a.timeout <= 300):  # 0 jobs: as many as cores
            raise ProjectError("A test run takes 1..64 jobs and a timeout of 1..300 seconds per test.")
        if a.command in {"run", "test"} and not 64 <= a.memory_mib <= 65536:
            raise ProjectError("Native memory limit must be 64..65536 MiB.")
        if a.command == "doc" and a.std:
            return standard_library_docs(a)
        if a.command == "check" and a.watch:  # before the project loads: a broken manifest is watched until fixed
            return watch(a.path, [*(["--format", FORMAT] if FORMAT else []), *(["--generics"] if a.generics else [])])
        if a.command == "export" or (
            a.command in {"build", "run", "test"} and (Path(a.path) / "export.json").is_file()
        ):
            return exported(a)
        project = load_project(a.path)
        return globals()["cairn_" + a.command](a, project)
    except Diagnostic as error:  # the refusal with its card and the fix the compiler can state, where it can
        codes = {d.get("code") for d in (error.data, *error.data.get("further", []))}
        known = declared(project.source) if project and "E-CALLEE" in codes else ()
        located = taught(project.locate(error) if project else error.data, known)
        if terminal.human(FORMAT):
            own = project.source if project else ""
            terminal.refusals(located, error.data, lambda d: written(d, own))
        else:
            report(located)
        return 1
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError) as error:
        unknown = taught({"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT", "message": str(error)})
        if terminal.human(FORMAT):
            terminal.diagnostic(unknown, "")
        else:
            report(unknown)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

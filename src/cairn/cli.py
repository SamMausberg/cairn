"""One command for native compilation, projects, tests and scalar comparison."""

from __future__ import annotations

import argparse
import functools
import gc
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .compiler.cairnc import Diagnostic, certify_templates, compile_source
from .compiler.modules import library_source
from .editor import terminal
from .projects.project import ProjectError, contained_file, load_project, read_text
from .projects.target import resolve as resolve_device
from .projects.toolchain import ARCHS, TARGETS, emulator, host_family, resolve_arch

FORMAT: str | None = None  # --format as given; None lets the stream decide (see editor/terminal.py)
LINES = False  # a watched check's records, one per line (JSON Lines), so a reader can take each as it comes


def report(value: dict, brief: bool = False) -> None:
    """The JSON record, or one line for a person when `brief` results are rendered for a terminal."""
    if brief and terminal.human(FORMAT):
        terminal.summary(value)
        return
    print(json.dumps(value, allow_nan=False) if LINES else json.dumps(value, indent=2, allow_nan=False))


TEMPLATES = Path(__file__).parent / "templates"  # each a whole project the suite builds, runs and tests as it is
GUIDE = TEMPLATES / "AGENTS.md"  # what every new project tells an agent: the loop, the rules, what not to widen


def templates() -> list[str]:
    return ["default", *sorted(p.name for p in TEMPLATES.iterdir() if (p / "cairn.toml").is_file())]


def create_project(destination: Path, template: str = "default") -> dict:
    """Create only; never overwrite a directory, even when it is empty. `default` is the average project the guide
    walks through; any other template is copied from `templates/`, under the new project's name."""
    name = destination.name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ProjectError("Choose an ASCII project name of 1..64 characters.")
    if template not in templates():
        raise ProjectError(f"No template {template!r}; there are {', '.join(templates())}.")
    if template != "default":
        destination.mkdir(parents=True, exist_ok=False)
        source = TEMPLATES / template
        for path in sorted(p for p in source.rglob("*") if p.is_file() and "build" not in p.relative_to(source).parts):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text(encoding="utf-8")
            if path.name == "cairn.toml":
                text = text.replace(f'name = "{template}"', f'name = "{name}"', 1)
            target.write_text(text, encoding="utf-8")
        (destination / ".gitignore").write_text("build/\n")
        (destination / "AGENTS.md").write_text(GUIDE.read_text(encoding="utf-8"), encoding="utf-8")
        return {
            "status": "created",
            "project": str(destination.resolve()),
            "template": template,
            "network_access": False,
        }
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

test average {
  assert_eq(average(10, 20), 15);
  assert_eq(average(1, 2), 1, "rounds down");
}
""")
    (destination / "src/main.cairn").write_text("""// Prints the average it checks, and exits 0 only when it is right.
fn main() -> i32 {
  let mean = average(10, 20);
  println("average(10, 20) = ", mean);
  if mean != 15 { return 1; }
  return 0;
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
    (destination / "AGENTS.md").write_text(GUIDE.read_text(encoding="utf-8"), encoding="utf-8")
    return {"status": "created", "project": str(destination.resolve()), "network_access": False}


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


COMMANDS = {
    "check": "Accept or refuse a program: syntax, types, ownership, leases, lanes, placement, effects.",
    "emit": "Print the C++ the program lowers to.",
    "expand": "Print what every derive generated, as CAIRN source.",
    "build": "Build a native artifact in a fresh directory, with a receipt.",
    "run": "Build, then run under process limits, or under the target's emulator; ARGS after -- go to the program.",
    "shot": "Run headless and collect every frame std.draw captured: its PNG, its layout record and its time.",
    "test": "Run the project's test blocks, each in a process of its own, and its finite task contracts.",
    "inspect": "Print the packet an editing agent gets for one symbol.",
    "state": "Print the program's state for an agent: every signature and effect row by module, under a digest; with "
    "--symbol, one function's investigation from its candidate history.",
    "migrate": "Change one function's interface through every caller, in all the files or in none.",
    "explain": "Where each function pays at run time: guards, allocations, waits and loop vectorization.",
    "predict": "How long each function will take, from its checked work and a machine profile; nothing runs.",
    "tune": "Search a function's plans within compile, time and run budgets; --measure times the best-ranked few on "
    "this host, and --compare reports how two plans differ.",
    "validate": "Test one implementation against its reference on boundary inputs its contract gives; finite, not proof.",
    "doc": "Generate the API reference of the checked program, as Markdown.",
    "graph": "Print the module graph: each file's modules, each module's imports, exports and dependents, hashes.",
    "export": "Write the program a build compiles, the runtime headers it includes and a record pinning them to "
    "--out; given an export, check it. build, run and test take an export too.",
    "foreign": "What a foreign implementation has: its declared contract, native build, device inspection, and "
    "finite tests against its reference.",
}
OPTIONS: list[tuple[set[str], str, dict[str, Any]]] = [  # (the commands that take it, the option, its keywords)
    ({"build", "run", "test", "explain", "tune", "shot", "validate", "state", "export"}, "--cxx", {"default": "clang++"}),
    ({"validate"}, "--symbol", {"required": True, "help": "The implementation; its reference is what it implements."}),
    ({"validate"}, "--policy", {"type": Path, "metavar": "POLICY.json", "help": "Tolerance, domain, budget, seed, "
                                "probes and seconds; default: what the regressions file pinned, else the defaults."}),
    ({"validate"}, "--regressions", {"type": Path, "metavar": "FILE", "help": "Where a failing case is kept; "
                                     "default: regressions/<reference>.json in the project."}),
    ({"validate"}, "--history", {"type": Path, "metavar": "DIR", "help": "Also keep the result in this candidate "
                                 "history, under the implementation's identity."}),
    ({"explain", "predict", "shot"}, "--symbol", {"action": "append", "help": "This function only (repeatable); "
                                                  "for shot, a function whose effect row is reported."}),
    ({"tune"}, "--symbol", {"action": "append", "required": True, "help": "The function whose plan is chosen."}),
    ({"predict", "tune", "export"}, "--at", {"action": "append", "default": [], "metavar": "NAME=SIZE[,NAME=SIZE]", "help":
                                   "Price at these sizes (repeatable); predict defaults a function of one extent to "
                                   "1e3, 1e5 and 1e7."}),
    ({"tune"}, "--measure", {"type": int, "default": 0, "metavar": "K", "help": "Time the K best-ranked plans and "
                             "the current one on this host, halving each round."}),
    ({"tune"}, "--device", {"action": "store_true", "help": "Time device plans on the device; only make "
                            "tune-device, the owner's target, allows it to run."}),
    ({"tune"}, "--write", {"action": "store_true", "help": "Write the chosen plan into the file that declares the "
                           "function."}),
    ({"tune"}, "--budget-compiles", {"type": int, "default": 4, "metavar": "N", "help": "Device compiles the search "
                                     "may start for resource inspection; kept inspections are free."}),
    ({"tune"}, "--budget-seconds", {"type": float, "default": 300.0, "metavar": "S", "help": "Wall time of the "
                                    "whole search."}),
    ({"tune"}, "--budget-runs", {"type": int, "metavar": "N", "help": "Timed runs --measure may start; kept "
                                 "measurements are free. Default: what --measure asks."}),
    ({"tune", "state"}, "--history", {"type": Path, "metavar": "DIR", "help": "The candidate history to record into "
                             "and answer from; default: .cairn/history beside the manifest."}),
    ({"tune"}, "--no-history", {"action": "store_true", "help": "Record nothing and answer from nothing kept."}),
    ({"tune"}, "--since", {"type": Path, "metavar": "TUNE.json", "help": "Print only what changed since this saved "
                           "answer: the candidates whose row changed, and what else differs."}),
    ({"tune"}, "--compare", {"action": "append", "default": [], "metavar": "PLAN", "help": "Give twice, as `none`, "
                             "items such as `grain 1; lanes 8`, or with `use g` for an implementation: report how the "
                             "second differs from the first, each line labelled by the kind of evidence it is, "
                             "instead of searching."}),
    ({"tune"}, "--artifacts", {"action": "store_true", "help": "With --compare, add the path of every file behind "
                               "the report."}),
    ({"predict", "shot"}, "--against", {"type": Path, "metavar": "BEFORE", "help": "What changing BEFORE into this "
                                         "program does: predicted costs, or for shot the rows of --symbol."}),
    ({"predict", "tune"}, "--profile", {"type": Path, "help": "A cairn.machine/1 profile; default: the packaged one."}),
    ({"build", "run", "export"}, "--out", {"type": Path}),
    ({"build", "run", "explain", "predict", "tune", "state", "export"}, "--arch", {"choices": sorted(ARCHS)}),
    ({"build", "run"}, "--target", {"choices": sorted(TARGETS), "help": "Freestanding profile; default hosted."}),
    ({"foreign"}, "--implementation", {"required": True, "metavar": "NAME", "help": "The implementation, whose "
                                       "body calls what a [foreign] source of the manifest defines."}),
    ({"build", "run", "predict", "tune", "state", "export", "foreign"}, "--device-target", {"metavar": "SM", "help": "The GPU's compilation "
                                                               "target, as sm_120, sm_120f or sm_120a; default: "
                                                               "[build] device_target, else the GPU nvidia-smi "
                                                               "reports."}),
    ({"build", "run", "test", "shot"}, "--timeout", {"type": int, "default": 60}),
    ({"build", "run"}, "--debug", {"action": "store_true", "help": "Debug symbols that point at the CAIRN source."}),
    ({"build", "run"}, "--incremental", {"action": "store_true", "help": "One object per module, reused by content "
                                         "hash; gives up inlining across modules."}),
    ({"emit", "build", "run", "export"}, "--keep-guards", {"action": "store_true", "help": "Write every guard, also those the "
                                                 "checker showed cannot fail: the conservative build."}),
    ({"run", "test"}, "--memory-mib", {"type": int, "default": 1024,
                                       "help": "Native address-space cap, 64..65536 MiB; not a sandbox."}),
    ({"build", "export"}, "--kind", {"choices": ["library", "exe"]}),
    ({"emit", "build", "export"}, "--header", {"action": "store_true", "help": "The C header of a library: emit prints it, "
                                     "build writes NAME.h beside the library and holds the library to its layouts."}),
    ({"emit"}, "--ctypes", {"action": "store_true", "help": "Print a Python module that loads the library through "
                            "ctypes with the header's layouts asserted at import."}),
    ({"test"}, "--contract", {"type": Path}),
    ({"export"}, "--tests", {"action": "store_true", "help": "Export the test blocks' program; cairn test runs it."}),
    ({"export"}, "--time", {"metavar": "SYMBOL", "help": "Export SYMBOL beside a timing driver at the --at sizes; "
                            "cairn run of the export measures it."}),
    ({"export"}, "--compare", {"type": Path, "metavar": "OTHER", "help": "With an export: whether OTHER is the same "
                               "code, function by function; exit 1 when it is not."}),
    ({"test"}, "--test", {"default": "", "metavar": "NAME", "help": "Run the one test block of exactly this name "
                           "(`sums`, or `store.sums` in module store), and no contract."}),
    ({"test"}, "--filter", {"default": "", "metavar": "TEXT", "help": "Run only the test blocks and contracts whose "
                            "name contains TEXT."}),
    ({"test"}, "--jobs", {"type": int, "default": 0, "help": "Test processes at once, 1..64; default: the cores, "
                          "at most 8."}),
    ({"check"}, "--watch", {"action": "store_true", "help": "Check again whenever a file the project reads "
                            "changes, until interrupted."}),
    ({"check"}, "--generics", {"action": "store_true", "help": "Also check each generic function once against its "
                               "bounds; fail if one needs more."}),
    ({"doc"}, "--module", {"action": "append",
                           "help": "Document this module (repeatable); default: the project's own."}),
    ({"doc"}, "--std", {"action": "store_true", "help": "Document the packaged standard library instead."}),
    ({"doc"}, "--pages", {"type": Path, "metavar": "DIR", "help": "With --std, write DIR/std_api.md and one page per "
                          "module under DIR/std/ instead of printing; a page no module has is removed."}),
    ({"inspect", "migrate"}, "--symbol", {"required": True}),
    ({"inspect"}, "--scope", {"choices": ["focused", "component"], "default": "focused", "help": "focused: the symbol "
                              "and the interfaces around it; component: its whole call graph."}),
    ({"inspect"}, "--expand", {"action": "append", "default": [], "metavar": "NAME", "help": "Disclose this "
                               "function's source or this type first, as an expand request would."}),
    ({"inspect"}, "--explain", {"action": "store_true", "help": "Attach cairn explain for the disclosed functions."}),
    ({"graph"}, "--interfaces", {"action": "store_true", "help": "Also check the program and give each module "
                                 "the hash of its public signatures and effect rows."}),
    ({"state"}, "--since", {"type": Path, "metavar": "STATE.json", "help": "Print only what changed since this "
                            "saved state."}),
    ({"state"}, "--symbol", {"help": "Print the investigation of this one function instead: what the candidate "
                             "history holds for it now, to resume from without rerunning what ran."}),
    ({"migrate"}, "--to", {"required": True, "metavar": "SIGNATURE", "help": "The new signature, from fn."}),
    ({"migrate"}, "--also", {"action": "append", "default": [], "metavar": "NAME=SIGNATURE", "help": "Another "
                             "function whose signature changes with it (repeatable)."}),
    ({"migrate"}, "--allow", {"action": "append", "default": [], "metavar": "EFFECT", "help": "An effect the "
                              "rows may gain (repeatable)."}),
    ({"migrate"}, "--reply", {"type": Path, "metavar": "REPLY.json", "help": "Check this reply and write every "
                              "file, or none; without it, print the packet."}),
]  # fmt: skip
REFUSED = {"counterexample", "rejected", "invalid-contract", "invalid-domain", "invalid-reference"}  # verify exits 1


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


def parser() -> argparse.ArgumentParser:
    """Every command and option: what `main` parses, and what `cairn completions` offers a shell."""
    p = argparse.ArgumentParser(prog="cairn", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--format", choices=["human", "json"], help="human: rendered for a person; json: the "
                        "record. Default: human on a terminal, json when piped, or CAIRN_FORMAT.")  # fmt: skip
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report local tools; never downloads them.", parents=[shared])
    new = sub.add_parser("new", help="Create a project from a template; it is data only.", parents=[shared])
    new.add_argument("directory", type=Path)
    new.add_argument("--template", choices=templates(), default="default", help="default: the average the guide "
                     "walks through; cli, lib, service and parallel: a starting point for each kind of program.")  # fmt: skip
    for name, help in COMMANDS.items():
        c = sub.add_parser(name, help=help, parents=[shared])
        c.add_argument(
            "path", nargs="?", default=".", help="A .cairn file, a project directory or a manifest; default: here."
        )
        for option, keywords in ((option, keywords) for names, option, keywords in OPTIONS if name in names):
            c.add_argument(option, **keywords)
    v = sub.add_parser("verify", help="SMT source equivalence, not native or Lean verification.", parents=[shared])
    v.add_argument("reference", type=Path)
    v.add_argument("candidate", type=Path)
    mode = v.add_mutually_exclusive_group(required=True)
    mode.add_argument("--symbol")
    mode.add_argument("--all", action="store_true", help="Require scalar equivalence for every declared function.")
    v.add_argument("--timeout-ms", type=int, default=3000)
    v.add_argument("--assume", action="append", default=[], metavar="SYMBOL=EXPRESSION", help="Compare one "
                   "function only where this holds (repeatable); the receipt records every text.")  # fmt: skip
    d = sub.add_parser("diff", help="What changed between two versions, function by function, and on what "
                       "evidence.", parents=[shared])  # fmt: skip
    d.add_argument("old", help="A path, or a revision of the repository around here.")
    d.add_argument("new", help="The version it became: a path or a revision.")
    d.add_argument("--in", dest="within", default=".", metavar="PATH", help="The project a revision holds, as a "
                   "path from here; default: here.")  # fmt: skip
    d.add_argument("--std", action="store_true", help="Compare the packaged library under src/cairn/std.")
    d.add_argument("--timeout-ms", type=int, default=3000, help="The solver's time for one function.")
    d.add_argument("--budget-s", type=float, default=60.0, help="The solver's time for the whole diff.")
    d.add_argument("--replays", type=int, default=8, help="Witnesses replayed natively, at most.")
    d.add_argument("--require", choices=["identical", "equivalent"], help="Exit 1 unless every function is "
                   "identical-code, or identical-code or smt-equivalent.")  # fmt: skip
    d.add_argument("--markdown", type=Path, metavar="FILE", help="Also write a pull request section to FILE.")
    d.add_argument("--no-predict", action="store_true", help="Leave out the predicted cost change.")
    sub.add_parser("certificates", help="Check collector arithmetic certificates; not a Lean/compiler proof.",
                   parents=[shared])  # fmt: skip
    f = sub.add_parser("fmt", help="Format CAIRN sources in place; refuses any change to the token stream.")
    f.add_argument("paths", nargs="+", type=Path, help="Files, or directories searched for *.cairn.")
    f.add_argument("--check", action="store_true", help="Write nothing; exit 1 if any file would change.")
    f.add_argument("--diff", action="store_true", help="Write nothing; print a unified diff of what would change.")
    sub.add_parser("lsp", help="Speak the Language Server Protocol over stdin/stdout.")
    sub.add_parser(
        "mcp",
        help="Serve check, state and the edit, plan and implementation hosts to an agent over the "
        "Model Context Protocol on stdin/stdout; an admitted change is written back to its files.",
    )
    s = sub.add_parser("completions", help="Print the completion script of a shell: bash or zsh.")
    s.add_argument("shell", choices=["bash", "zsh"])
    return p


def main(argv: list[str] | None = None) -> int:
    global FORMAT
    # A compile builds millions of tree objects that live until it ends, so the collector looks at old ones rarely.
    # Interleaved checks of a 77,000-line project ran 10 to 20 percent faster at the same peak memory; the loaded
    # scale run of evidence/v0_9/scale does not separate that from its noise.
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
                project.source, keep_guards=getattr(a, "keep_guards", False), sites=project.site
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
            from .perf.resources import device_identity, host_target

            where = a.history or project.root / ".cairn" / "history"
            device = resolve_device(a.device_target, project.device_target, required=False)
            targets = {"host": host_target(resolve_arch(a.arch or project.arch), a.cxx),
                       "device": device_identity(device)}  # fmt: skip
            packet = investigation.investigation(project.source, a.symbol, where, targets)
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
            device = resolve_device(a.device_target, project.device_target, required=False)
            if a.against:
                before = load_project(a.against).source
                answer = priced.delta(before, project.source, sizes, chosen, supplied, arch, device)
            else:
                answer = priced.report(project.source, sizes, chosen, supplied, arch, device)
            print(priced.lines(answer)) if terminal.human(FORMAT) else report(answer)
            return 0
        if a.command == "tune":
            from .perf import report as priced
            from .perf.plan_source import KEEP, write_plan
            from .perf.profile import Profile
            from .perf.tune import Budget, tune
            from .perf.tune import delta as tune_delta
            from .perf.tune import lines as tune_lines

            supplied = Profile.load(a.profile) if a.profile else None
            arch = resolve_arch(a.arch or project.arch)
            device = resolve_device(a.device_target, project.device_target, required=False)
            budget = Budget(a.budget_compiles, a.budget_seconds, a.budget_runs)
            kept = None if a.no_history else a.history or project.root / ".cairn" / "history"
            if a.compare:  # a difference report between two plans, in place of a search
                from .perf import feedback

                if len(a.compare) != 2:
                    raise ProjectError("--compare names two plans: the one to compare against, then the other.")
                first, second = (feedback.parse_candidate(x) for x in a.compare)
                answer = feedback.compare(project.source, a.symbol[0], first, second, priced.parse_sizes(a.at),
                                          supplied, arch, kept, device, a.budget_compiles, a.artifacts, a.cxx)  # fmt: skip
                print(feedback.lines_for_people(answer)) if terminal.human(FORMAT) else report(answer)
                return 0
            answer = tune(project.source, a.symbol[0], priced.parse_sizes(a.at), supplied, arch, a.measure, a.cxx,
                          a.device, device, budget, kept)  # fmt: skip
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
            record = validate_project(project, a.symbol, given, a.cxx, a.regressions, a.history)
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
                                   timeout=a.timeout, memory_mib=a.memory_mib)  # fmt: skip
            if not paths and not blocks["tests"] and blocks["status"] == "no-test-blocks":
                named = f" named {a.test!r}" if a.test else f" whose name contains {a.filter!r}" if a.filter else ""
                raise ProjectError(f"No tests{named}: write a test block, add project.tests or supply --contract.")
            results = [{"contract": path.name, **evaluate(project.source, load_json_strict(read_text(path, 2_000_000)),
                                                          a.cxx, project.libraries)} for path in paths]  # fmt: skip
            passed = all(x["status"] == "passed-finite-tests" for x in results)
            passed &= blocks["status"] in {"passed-test-blocks", "no-test-blocks"}
            status = "passed-finite-tests" if passed else "tests-not-passed"
            report({"status": status, "tests": results, "blocks": blocks, "formal_status": "not-verified"}, brief=True)
            return 0 if passed else 1
        from .projects.build import build

        result = build(project, output=a.out, cxx=a.cxx, arch=a.arch, kind="exe" if a.command == "run" else a.kind,
                       timeout=a.timeout, target=a.target, debug=a.debug, incremental=a.incremental,
                       keep_guards=a.keep_guards, header=getattr(a, "header", False),
                       device_target=a.device_target)  # fmt: skip
        if a.command == "build" or result["status"] != "native-built":
            report(result, brief=True)
            return 0 if result["status"] == "native-built" else 2
        # Execution is explicit. Process timeout is not an OS security sandbox.
        from .verify.testing import limited

        # A freestanding image is not a host process: it runs in the emulator its target names.
        machine = emulator(result["target"], result["artifact"])
        cuda = "cuda" in result["frontend"]["requires"]  # Unified addressing reserves far more than it uses.
        limits = functools.partial(limited, a.timeout, None if cuda else a.memory_mib)

        if machine and a.arguments:
            raise ProjectError("A freestanding image is started by its board, with no arguments.")
        started = machine or [result["artifact"], *a.arguments]
        run: dict = {"stdin": subprocess.DEVNULL} if machine else {"preexec_fn": limits}
        if terminal.human(FORMAT):  # A person sees the program itself: its streams are the terminal's.
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
                "memory_limit_mib": None if machine else a.memory_mib, "emulator": machine})  # fmt: skip
        return 0 if cp.returncode == 0 else 1
    except Diagnostic as error:
        located = project.locate(error) if project else error.data
        if terminal.human(FORMAT):
            shown = library_source(located["module"]) if located.get("module", "").startswith("std.") else None
            terminal.diagnostic(
                {**located, "source_line": error.data["line"]}, shown or (project.source if project else "")
            )
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

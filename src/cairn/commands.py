"""Every command and option of the command line: what `cli.main` parses, what `cairn completions` offers a shell,
and what the agent skill lists."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import __version__
from .projects.new import templates
from .projects.toolchain import ARCHS, TARGETS

DESCRIPTION = "One command for native compilation, projects, tests and scalar comparison."

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
    ({"tune"}, "--shapes", {"type": Path, "metavar": "SHAPES.json", "help": "More sizes to tune for, each with a "
                            "weight: a JSON list of {\"at\": \"n=1e6\", \"weight\": 2}."}),
    ({"tune"}, "--objective", {"choices": ["geomean", "mean"], "default": "geomean", "help": "How the times at "
                               "the sizes fold into the one that ranks candidates: the geometric mean (default), "
                               "as GPU MODE scores a list of shapes, or the arithmetic mean; weighted by --shapes."}),
    ({"tune"}, "--measure", {"type": int, "default": 0, "metavar": "K", "help": "Time the K best-ranked plans and "
                             "the current one on this host, halving each round."}),
    ({"tune"}, "--device", {"action": "store_true", "help": "Time device plans on the device; only make "
                            "tune-device, the owner's target, allows it to run."}),
    ({"tune"}, "--write", {"action": "store_true", "help": "Write the chosen plan into the file that declares the "
                           "function."}),
    ({"tune"}, "--accept-emulated", {"action": "store_true", "help": "Let an implementation whose validation ran "
                                     "only on a host emulation of the device be chosen and timed; its row names "
                                     "that evidence."}),
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
    ({"predict", "tune"}, "--card", {"metavar": "CARD", "help": "Price device work on this packaged device card, "
                                     "published figures that no run here measured (cairn cards lists them; h100 "
                                     "names h100-sxm5). Without --device-target or a manifest target, the target is "
                                     "the card's own. predict also takes all: a row per card."}),
    ({"predict"}, "--inspect", {"action": "store_true", "help": "Compile the device code for the device target and "
                                "read each cooperative region's registers from ptxas; nothing runs."}),
    ({"build", "run", "export"}, "--out", {"type": Path}),
    ({"build", "run", "explain", "predict", "tune", "state", "export"}, "--arch", {"choices": sorted(ARCHS)}),
    ({"build", "run"}, "--target", {"choices": sorted(TARGETS), "help": "Freestanding profile; default hosted."}),
    ({"foreign"}, "--implementation", {"required": True, "metavar": "NAME", "help": "The implementation, whose "
                                       "body calls what a [foreign] source of the manifest defines."}),
    ({"build", "run", "test", "validate", "predict", "tune", "state", "export", "foreign"}, "--device-target", {"metavar": "SM", "help": "The GPU's compilation "
                                                               "target, as sm_120, sm_120f or sm_120a; default: "
                                                               "[build] device_target, else the GPU nvidia-smi "
                                                               "reports."}),
    ({"build", "run", "test", "validate"}, "--emulate", {"action": "store_true", "help": "Run device work on host "
                                                        "threads, judged against the device target: a host run for "
                                                        "correctness, never a device run or a timing."}),
    ({"build", "run", "test", "shot"}, "--timeout", {"type": int, "default": 60}),
    ({"build", "run"}, "--debug", {"action": "store_true", "help": "Debug symbols that point at the CAIRN source."}),
    ({"build", "run"}, "--incremental", {"action": "store_true", "help": "One object per module, reused by content "
                                         "hash; gives up inlining across modules."}),
    ({"emit", "build", "run", "export"}, "--keep-guards", {"action": "store_true", "help": "Write every guard, also those the "
                                                 "checker showed cannot fail: the conservative build."}),
    ({"build", "run"}, "--sanitize", {"choices": ["address", "thread"], "help": "Build for the host at -O1 checked by "
                                     "this sanitizer (address also checks undefined behaviour and leaks) and run it "
                                     "without the memory cap, which its shadow memory would exceed."}),
    ({"run", "test"}, "--memory-mib", {"type": int, "default": 1024,
                                       "help": "Native memory cap on data (heap and thread stacks, 8 MiB each), 64..65536 MiB; not a sandbox."}),
    ({"build", "export"}, "--kind", {"choices": ["library", "exe"]}),
    ({"emit", "build", "export"}, "--header", {"action": "store_true", "help": "The C header of a library: emit prints it, "
                                     "build writes NAME.h beside the library and holds the library to its layouts."}),
    ({"emit"}, "--ctypes", {"action": "store_true", "help": "Print a Python module that loads the library through "
                            "ctypes with the header's layouts asserted at import."}),
    ({"test"}, "--contract", {"type": Path}),
    ({"export"}, "--tests", {"action": "store_true", "help": "Export the test blocks' program; cairn test runs it."}),
    ({"export"}, "--time", {"metavar": "SYMBOL", "help": "Export SYMBOL beside a timing driver at the --at sizes; "
                            "cairn run of the export measures it."}),
    ({"export"}, "--harness", {"choices": ["sol-execbench", "gpumode", "kernelbench"], "help": "Package --symbol "
                               "as this benchmark's submission beside its export and a record; runs and submits "
                               "nothing, and prints the commands that would."}),
    ({"export"}, "--symbol", {"metavar": "F", "help": "With --harness: the function the benchmark calls."}),
    ({"export"}, "--mapping", {"type": Path, "metavar": "HARNESS.toml", "help": "With --harness: which benchmark "
                               "argument feeds which parameter; default: harness.toml beside the manifest."}),
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
                           "help": "Document this module (repeatable); default: the project's own, or with --std "
                                   "every packaged module."}),
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


def parser() -> argparse.ArgumentParser:
    """Every command and option: what `main` parses, and what `cairn completions` offers a shell."""
    p = argparse.ArgumentParser(prog="cairn", description=DESCRIPTION)
    p.add_argument("--version", action="version", version=__version__)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--format", choices=["human", "json"], help="human: rendered for a person; json: the "
                        "record. Default: human on a terminal, json when piped, or CAIRN_FORMAT.")  # fmt: skip
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Report local tools; never downloads them.", parents=[shared])
    sub.add_parser("cards", help="List the device cards predict and tune price device work on: NVIDIA's published "
                   "figures and the assumptions each names, none measured.", parents=[shared])  # fmt: skip
    new = sub.add_parser("new", help="Create a project from a template; it is data only.", parents=[shared])
    new.add_argument("directory", type=Path)
    new.add_argument("--template", choices=templates(), default="default", help="default: the average the guide "
                     "walks through; cli, lib, service and parallel: a starting point for each kind of program.")  # fmt: skip
    new.add_argument("--from-sol-execbench", type=Path, metavar="DEFINITION.json", help="A library project for "
                     "this SOL-ExecBench problem: the reference's signature, the tolerance of its workload.jsonl as "
                     "a validation policy, and harness.toml.")  # fmt: skip
    new.add_argument("--device-target", metavar="SM", help="With --from-sol-execbench: the manifest's device "
                     "target; default: sm_100a, the B200 the benchmark runs on.")  # fmt: skip
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
    r = sub.add_parser("rules", help="Print a rule card, offline: the one that owns a diagnostic code, one by name, "
                       "or those a program selects.", parents=[shared])  # fmt: skip
    r.add_argument("asked", nargs="?", default="", metavar="CODE|CARD|PATH", help="E-LEASED, tasks, or a .cairn "
                   "file or project; default: every card with its codes.")  # fmt: skip
    r.add_argument("--list", action="store_true", help="Every card with its codes and the words that select it.")
    q = sub.add_parser("find", help="The functions to call, best first, among the builtins, std and the program's "
                       "own: those that take the values you have, or whose names and comments hold your words.",
                       parents=[shared])  # fmt: skip
    q.add_argument("words", nargs="*", help="parse integer, read stdin, map insert.")
    q.add_argument("--takes", action="append", default=[], metavar="TYPE", help="The type of a value you have "
                   "(repeatable, any order): ro<u8>[n], Vec[i64], u64.")  # fmt: skip
    q.add_argument("--returns", metavar="TYPE", help="The type you want back; a Result or Option of it fits next.")
    q.add_argument("--effects", metavar="CEILING", help="Only functions whose rows fit this ceiling: pure, or "
                   "effects such as 'trap, alloc'.")  # fmt: skip
    q.add_argument("--limit", type=int, default=10, help="Hits to list; the answer counts the rest.")
    q.add_argument("--in", dest="within", metavar="PATH", help="The program whose own functions are searched too; "
                   "default: the project here, when there is one.")  # fmt: skip
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

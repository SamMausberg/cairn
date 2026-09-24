#!/usr/bin/env python3
"""Equal-budget evaluations of CAIRN, C++ and Rust, each fixed by its preregistration before any subject runs.

Two studies share the harness. `v1_0` is the benchmark of PREREGISTRATION.md: ten tasks, three arms, one subject at a
time. `v1_1` is the evaluation of PREREGISTRATION_V1_1.md: thirteen tasks, four arms (CAIRN with its Claude Code
plugin, CAIRN with its documentation, C++ and Rust), up to three subjects at once.

verify   every reference passes its hidden check, every starter fails it, every SPEC.md example agrees with its
         oracle, in all three languages; no model runs
run      one fresh subject per task and arm, each judged and audited as it finishes
report   results and tables from the records, with the audit decisions applied
"""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analysis
from checking import EXTENSION, LANGUAGES, SOURCE, judge
from scoring import audit, breakdown, markdown, pairs, row, summary
from subjects import LANGUAGE, LIMITS, hidden, run_subject, toolchain, unlock
from tasks import BY_NAME, ORIGINAL, TASKS, example

REPO = HERE.parents[1]
BUDGET_STOPS = ("success", "error_max_turns", "error_max_budget_usd")
SONNET, OPUS = "claude-sonnet-5", "claude-opus-5-5"
# Each study: where its records go, its tasks in preregistered order, its arms, its phases and their models, how many
# subjects may run at once, the platform cost after which no counted subject starts, where its report goes, whether
# each subject runs isolated (its own /tmp, and nothing of the checkouts, the other subjects or other sessions), and
# the default root of its runs, which an isolated subject's own /tmp keeps out of /tmp.
STUDIES = {
    "v1_0": {"records": REPO / "results" / "ai_benchmark", "tasks": [t.name for t in ORIGINAL],
             "arms": ("cairn", "cpp", "rust"), "models": {"pilot": SONNET, "primary": SONNET, "secondary": OPUS},
             "jobs": 1, "ceiling_usd": None, "out": REPO / "evidence" / "v1_0" / "ai_benchmark",
             "isolated": False, "root": Path(tempfile.gettempdir()) / "cairn-aibench"},
    "v1_1": {"records": REPO / "results" / "ai_eval", "tasks": [t.name for t in TASKS],
             "arms": ("plugin", "cairn", "cpp", "rust"), "models": {"pilot": SONNET, "counted": SONNET},
             "jobs": 3, "ceiling_usd": {"counted": 200.0}, "out": REPO / "evidence" / "v1_1" / "ai_eval",
             "isolated": True, "root": Path.home() / "cairn-aieval"},
}  # fmt: skip
RECORDS = STUDIES["v1_0"]["records"]


def program(task: str, language: str, which: str) -> str:
    return (HERE / "tasks" / task / f"{which}.{EXTENSION[language]}").read_text()


def verify(names: list[str], languages: list[str], jobs: int, programs: list[str], records: Path = RECORDS) -> int:
    """References pass, starters fail, examples agree: the precondition for running any subject."""
    rows = []
    for name in names:
        task = BY_NAME[name]
        given, wanted = example(task)
        rows.append((name, "example", "oracle", task.oracle(given) == wanted, "SPEC.md example"))
    work = [(n, lang, which) for n in names for lang in languages for which in programs]

    def one(item):
        name, language, which = item
        verdict = judge(BY_NAME[name], language, program(name, language, which))
        ok = verdict.passed if which == "reference" else not verdict.passed
        return (name, language, which, ok, verdict.reason if which == "starter" or not ok else "passed", verdict)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(one, work))
    record = []
    for name, language, which, ok, reason, verdict in results:
        rows.append((name, language, which, ok, reason))
        record.append({"task": name, "language": language, "program": which, "as_required": ok, **verdict.record()})
    for r in rows:
        print("{:<13} {:<8} {:<10} {:<5} {}".format(r[0], r[1], r[2], "yes" if r[3] else "NO", r[4]))
    bad = [r for r in rows if not r[3]]
    print(f"{len(rows) - len(bad)} of {len(rows)} as required")
    records.mkdir(parents=True, exist_ok=True)
    (records / "verify.json").write_text(json.dumps(record, indent=1))
    return 1 if bad else 0


def order(names: list[str], arms: list[str], study: str = "v1_0") -> list[tuple[str, str]]:
    """Tasks in the study's order; within task i its arms rotate by i, so no arm always goes first."""
    every, cells = STUDIES[study]["arms"], []
    for i, task in enumerate(STUDIES[study]["tasks"]):
        if task in names:
            turn = list(every[i % len(every) :] + every[: i % len(every)])
            cells += [(task, arm) for arm in turn if arm in arms]
    return cells


def spent(records: Path, phase: str) -> float:
    """The platform cost of every subject of a phase recorded so far, infrastructure failures included."""
    total = 0.0
    for path in [*records.glob(f"{phase}/r*/*/*/record.json"), *records.glob(f"{phase}/r*/*/*/infrastructure-*.json")]:
        total += ((json.loads(path.read_text()).get("result") or {}).get("total_cost_usd")) or 0.0
    return total


def infrastructure(record: dict) -> bool:
    """A session the platform ended, not the subject's budget: no closing record, or one for another reason."""
    if record["killed_at_wall_limit"]:
        return False
    result = record.get("result")
    return result is None or result.get("subtype") not in BUDGET_STOPS or bool(result.get("api_error_status"))


def run(phase: str, replicate: int, names: list[str], arms: list[str], root: Path, limits: dict,
        study: str = "v1_0", jobs: int = 1) -> int:  # fmt: skip
    """Run every cell of one replicate in order, up to `jobs` subjects at once, each judged and audited as it ends.

    A cell with a record is skipped, so a stopped run continues where it stopped. No subject starts after a PAUSE
    file appears, after an infrastructure failure, once the phase's recorded cost reaches the study's ceiling, or once
    one cell has failed for infrastructure three times or the phase five times."""
    records, ceiling = STUDIES[study]["records"], (STUDIES[study]["ceiling_usd"] or {}).get(phase)
    tools = toolchain(root / "toolchain")
    harness = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    cairn = [str(tools / "cairn")]
    stop = threading.Event()
    codes: list[int] = []
    lock = threading.Lock()
    cells = iter(order(names, arms, study))

    def next_cell() -> tuple[str, str] | None:
        with lock:
            for task, arm in cells:
                done = records / phase / f"r{replicate}" / task / arm / "record.json"
                if done.exists() and not json.loads(done.read_text()).get("infrastructure"):
                    continue
                if stop.is_set():
                    return None
                if (records / "PAUSE").exists():  # a pause between subjects: none is running, and none starts
                    print(f"paused before {phase} r{replicate} {task} in {arm}", flush=True)
                    codes.append(4)
                    return None
                if ceiling is not None and spent(records, phase) >= ceiling:
                    print(f"the {phase} phase has spent its ceiling of {ceiling} USD; stopping", flush=True)
                    codes.append(5)
                    return None
                failed = len(list(done.parent.glob("infrastructure-*.json")))
                if failed >= 3 or len(list(records.glob(f"{phase}/r*/*/*/infrastructure-*.json"))) >= 5:
                    print(f"{phase} has failed for infrastructure too often ({failed} here); stopping", flush=True)
                    codes.append(6)
                    return None
                return task, arm
            return None

    def worker() -> None:
        while not stop.is_set() and (cell := next_cell()) is not None:
            try:
                finished = one(*cell)
            except Exception:  # the harness failed, not the subject: stop as for an infrastructure failure
                traceback.print_exc()
                finished = False
            if not finished:
                stop.set()
                codes.append(3)

    def one(task: str, arm: str) -> bool:
        cell = records / phase / f"r{replicate}" / task / arm
        attempt = len(list(cell.glob("infrastructure-*.json"))) if cell.exists() else 0
        print(f"{phase} r{replicate}: {task} in {arm} ...", flush=True)
        where = root / "runs" / phase / f"r{replicate}" / task / arm
        plugin = root / "plugins" / phase / f"r{replicate}" / task / "plugin"
        scratch = root / "tmp" / phase / f"r{replicate}" / task / arm if STUDIES[study]["isolated"] else None
        hide = hidden(root) if scratch is not None else None
        record = run_subject(BY_NAME[task], arm, where, cell, tools, limits, plugin, scratch, hide)
        record.update(phase=phase, replicate=replicate, study=study, harness_commit=harness)
        record["infrastructure"] = infrastructure(record)
        if record["infrastructure"]:
            (cell / f"infrastructure-{attempt}.json").write_text(json.dumps(record, indent=1))
            (cell / "record.json").unlink(missing_ok=True)
            print(f"  infrastructure failure: {(record.get('result') or {}).get('subtype')} {record['stderr'][-300:]}")
            print("  stopping; rerun `harness.py run` to continue from this cell")
            unlock(plugin)
            return False
        source = (cell / Path(SOURCE[LANGUAGE[arm]]).name).read_text()
        record["source_bytes"] = len(source.encode())
        record["verdict"] = judge(BY_NAME[task], LANGUAGE[arm], source, cairn=cairn).record()
        record["audit"] = audit(Path(record["transcript"]), record["sandbox"], str(root), record.get("plugin"))
        (cell / "record.json").write_text(json.dumps(record, indent=1))
        shutil.rmtree(record["sandbox"], ignore_errors=True)  # no later subject can reach this one's work
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        unlock(plugin)
        r = row(record)
        print(f"  {task} in {arm}: {'solved' if r['solved'] else 'not solved'} ({r['verdict']}), stop {r['stop']}, "
              f"turns {r['turns']}, tokens {r.get('tokens_total')}, ${r.get('tokens_cost_usd')}, {r['wall_seconds']} s, "
              f"flags {len(record['audit']['flags'])}", flush=True)  # fmt: skip
        return True

    threads = [threading.Thread(target=worker) for _ in range(max(1, jobs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return max(codes, default=0)


def export(record: dict, where: Path, transcripts: bool = False) -> None:
    """A subject's final program and its record, with the transcript named by path and sha256, and with
    `transcripts` also kept beside it compressed with xz."""
    where.mkdir(parents=True, exist_ok=True)
    source = Path(record["transcript"]).parent / Path(SOURCE[record["language"]]).name
    shutil.copy(source, where / source.name)
    transcript = Path(record["transcript"])
    kept = {k: v for k, v in record.items() if k not in ("given", "transcript")}
    kept["transcript"] = {
        "path": str(transcript.relative_to(REPO)) if transcript.is_relative_to(REPO) else str(transcript),
        "sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "bytes": transcript.stat().st_size,
    }
    kept["given_sha256"] = hashlib.sha256(json.dumps(record.get("given", {}), sort_keys=True).encode()).hexdigest()
    (where / "record.json").write_text(json.dumps(kept, indent=1))
    if transcripts:
        (where / "transcript.jsonl.xz").write_bytes(lzma.compress(transcript.read_bytes(), preset=9))


def gathered(study: str, phase: str, out: Path, root: Path | None) -> tuple[list[dict], dict]:
    """Every record of a phase, audited again under the current rule when `root` is given, with the person's audit
    decisions applied."""
    decisions_file = out / "audit_decisions.json"
    decisions = json.loads(decisions_file.read_text()) if decisions_file.exists() else {}
    records = []
    for path in sorted((STUDIES[study]["records"] / phase).glob("r*/*/*/record.json")):
        record = json.loads(path.read_text())
        if root is not None:  # the audit again, under the rule as it stands, for every subject alike
            record["audit"] = audit(Path(record["transcript"]), record["sandbox"], str(root), record.get("plugin"))
        arm = record.get("arm", record["language"])
        key = f"{phase}/r{record.get('replicate', 1)}/{record['task']}/{arm}"
        record["contaminated"] = decisions.get(key, {}).get("contaminated", False)
        records.append(record)
    return records, {k: v for k, v in decisions.items() if k.startswith(f"{phase}/")}


def report(phase: str, out: Path, root: Path | None = None, study: str = "v1_0") -> int:
    records, decisions = gathered(study, phase, out, root)
    rows = [
        {**row(r), "exploratory": breakdown(Path(r["transcript"]), r["language"], r.get("plugin"))} for r in records
    ]
    out.mkdir(parents=True, exist_ok=True)
    if study == "v1_0":
        table = {
            "phase": phase,
            "summary": summary(rows, LANGUAGES),
            "pairs": {
                f"{a}_vs_{b}": pairs(rows, a, b) for a, b in (("cairn", "cpp"), ("cairn", "rust"), ("cpp", "rust"))
            },
            "rows": rows,
            "audit_decisions": decisions,
        }
        (out / f"tables_{phase}.md").write_text(markdown(table))
        shown = {"summary": table["summary"], "pairs": table["pairs"]}
    else:
        arms = STUDIES[study]["arms"]
        table = {"phase": phase, **analysis.analyse(rows, arms), "rows": rows, "audit_decisions": decisions}
        (out / f"tables_{phase}.md").write_text(analysis.markdown(table, rows, arms) + exploratory(rows))
        shown = {"bootstrap": table["bootstrap"], "safety_failures": table["safety_failures"]}
    (out / f"results_{phase}.json").write_text(json.dumps(table, indent=1))
    for record in records:
        arm = record.get("arm", record["language"])
        export(record, out / "subjects" / phase / f"r{record.get('replicate', 1)}" / record["task"] / arm,
               transcripts=study != "v1_0")  # fmt: skip
    print(json.dumps(shown, indent=1))
    return 0


def exploratory(rows: list[dict]) -> str:
    """Not preregistered: where each subject's calls went."""
    lines = ["", "Not preregistered, a description of where the calls went: calls that read documentation (the "
             "`docs/` of the sandbox or the plugin, the skill and its cards) and the characters they returned, the "
             "other calls and theirs, and the compiler diagnostics a subject saw outside the documentation.", "",
             "| replicate | task | arm | docs calls | docs characters | other calls | other characters | diagnostics |",
             "|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in sorted(rows, key=lambda r: (r["replicate"], r["task"], r["arm"])):
        if e := r.get("exploratory"):
            seen = ", ".join(f"{code} {count}" for code, count in e["diagnostics"].items())
            lines.append(f"| {r['replicate']} | {r['task']} | {r['arm']} | {e['docs_calls']} | {e['docs_chars']} | "
                         f"{e['other_calls']} | {e['other_chars']} | {seen} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--study", choices=list(STUDIES), default="v1_1")
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="references pass, starters fail, examples agree")
    v.add_argument("--task", action="append", choices=[t.name for t in TASKS])
    v.add_argument("--language", action="append", choices=LANGUAGES)
    v.add_argument("--program", action="append", choices=["reference", "starter"])
    v.add_argument("--jobs", type=int, default=4)
    r = sub.add_parser("run", help="run subjects, each judged and audited as it finishes")
    r.add_argument("--phase", required=True)
    r.add_argument("--replicate", type=int, default=1)
    r.add_argument("--task", action="append", choices=[t.name for t in TASKS])
    r.add_argument("--arm", "--language", dest="arm", action="append", choices=list(LANGUAGE))
    r.add_argument("--jobs", type=int, help="subjects at once; default: the study's preregistered number")
    r.add_argument("--root", type=Path, help="outside the repository; default: the study's")
    r.add_argument("--model", help="default: the phase's preregistered model")
    t = sub.add_parser("report", help="summarize one phase")
    t.add_argument("--phase", required=True)
    t.add_argument("--out", type=Path, help="default: the study's evidence folder")
    t.add_argument("--root", type=Path, help="the run's root: audit every transcript again under the current rule")
    args = p.parse_args(argv)
    study = STUDIES[args.study]
    if args.command == "verify":
        programs = args.program or ["reference", "starter"]
        names = args.task or study["tasks"]
        return verify(names, args.language or list(LANGUAGES), args.jobs, programs, study["records"])
    if args.command in ("run", "report") and args.phase not in study["models"]:
        p.error(f"the {args.study} study's phases are {', '.join(study['models'])}")
    if args.command == "run":
        root = (args.root or study["root"]).resolve()
        if root == REPO or REPO in root.parents:
            p.error("--root must be outside the repository")
        if study["isolated"] and Path("/tmp") in [root, *root.parents]:
            p.error("--root must be outside /tmp, which each subject replaces with a /tmp of its own")
        names = args.task or study["tasks"]
        arms = args.arm or list(study["arms"])
        if not set(names) <= set(study["tasks"]) or not set(arms) <= set(study["arms"]):
            p.error(f"the {args.study} study has the tasks {study['tasks']} and the arms {study['arms']}")
        limits = {**LIMITS, "model": args.model or study["models"][args.phase]}
        return run(args.phase, args.replicate, names, arms, root, limits, args.study, args.jobs or study["jobs"])
    return report(args.phase, args.out or study["out"], args.root, args.study)


if __name__ == "__main__":
    raise SystemExit(main())

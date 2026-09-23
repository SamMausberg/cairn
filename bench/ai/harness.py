#!/usr/bin/env python3
"""The equal-budget benchmark of CAIRN, C++ and Rust: PREREGISTRATION.md fixes the design before any subject runs.

verify   every reference passes its hidden check, every starter fails it, every SPEC.md example agrees with its
         oracle, in all three languages; no model runs
run      one fresh subject per task and language, one at a time, each judged and audited as it finishes
report   results.json and RESULTS.md from the records, with the audit decisions applied
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from checking import EXTENSION, LANGUAGES, SOURCE, judge
from scoring import audit, breakdown, markdown, pairs, row, summary
from subjects import LIMITS, run_subject, toolchain
from tasks import BY_NAME, TASKS, example

RECORDS = HERE.parents[1] / "results" / "ai_benchmark"
BUDGET_STOPS = ("success", "error_max_turns", "error_max_budget_usd")
PHASES = ("pilot", "primary", "secondary")
MODELS = {"pilot": "claude-sonnet-5", "primary": "claude-sonnet-5", "secondary": "claude-opus-5-5"}


def program(task: str, language: str, which: str) -> str:
    return (HERE / "tasks" / task / f"{which}.{EXTENSION[language]}").read_text()


def verify(names: list[str], languages: list[str], jobs: int, programs: list[str]) -> int:
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
    RECORDS.mkdir(parents=True, exist_ok=True)
    (RECORDS / "verify.json").write_text(json.dumps(record, indent=1))
    return 1 if bad else 0


def order(names: list[str], languages: list[str]) -> list[tuple[str, str]]:
    """Tasks in the order of TASKS; within task i the languages rotate by i, so no language always goes first."""
    cells = []
    for i, task in enumerate(t.name for t in TASKS):
        if task in names:
            turn = list(LANGUAGES[i % 3 :] + LANGUAGES[: i % 3])
            cells += [(task, lang) for lang in turn if lang in languages]
    return cells


def infrastructure(record: dict) -> bool:
    """A session the platform ended, not the subject's budget: no closing record, or one for another reason."""
    if record["killed_at_wall_limit"]:
        return False
    result = record.get("result")
    return result is None or result.get("subtype") not in BUDGET_STOPS or bool(result.get("api_error_status"))


def run(phase: str, replicate: int, names: list[str], languages: list[str], root: Path, limits: dict) -> int:
    tools = toolchain(root / "toolchain")
    cairn = [str(tools / "cairn")]
    for task, language in order(names, languages):
        cell = RECORDS / phase / f"r{replicate}" / task / language
        done = cell / "record.json"
        if done.exists() and not json.loads(done.read_text()).get("infrastructure"):
            continue
        if (RECORDS / "PAUSE").exists():  # a pause between subjects: none is running, and none starts
            print(f"paused before {phase} r{replicate} {task} in {language}", flush=True)
            return 4
        attempt = len(list(cell.glob("infrastructure-*.json"))) if cell.exists() else 0
        print(f"{phase} r{replicate}: {task} in {language} ...", flush=True)
        where = root / "runs" / phase / f"r{replicate}" / task / language
        record = run_subject(BY_NAME[task], language, where, cell, tools, limits)
        record["phase"] = phase
        record["replicate"] = replicate
        record["infrastructure"] = infrastructure(record)
        if record["infrastructure"]:
            (cell / f"infrastructure-{attempt}.json").write_text(json.dumps(record, indent=1))
            done.unlink(missing_ok=True)
            print(f"  infrastructure failure: {(record.get('result') or {}).get('subtype')} {record['stderr'][-300:]}")
            print("  stopping; rerun `harness.py run` to continue from this cell")
            return 3
        source = (cell / Path(SOURCE[language]).name).read_text()
        record["source_bytes"] = len(source.encode())
        record["verdict"] = judge(BY_NAME[task], language, source, cairn=cairn).record()
        record["audit"] = audit(Path(record["transcript"]), record["sandbox"], str(root))
        done.write_text(json.dumps(record, indent=1))
        shutil.rmtree(record["sandbox"], ignore_errors=True)  # no later subject can reach this one's work
        r = row(record)
        print(f"  {'solved' if r['solved'] else 'not solved'} ({r['verdict']}), stop {r['stop']}, turns {r['turns']}, "
              f"tokens {r.get('tokens_total')}, ${r.get('tokens_cost_usd')}, {r['wall_seconds']} s, "
              f"flags {len(record['audit']['flags'])}", flush=True)  # fmt: skip
    return 0


def export(record: dict, where: Path) -> None:
    """A subject's final program and its record, with the transcript named by path and sha256 instead of copied."""
    where.mkdir(parents=True, exist_ok=True)
    source = Path(record["transcript"]).parent / Path(SOURCE[record["language"]]).name
    shutil.copy(source, where / source.name)
    transcript = Path(record["transcript"])
    kept = {k: v for k, v in record.items() if k not in ("given", "transcript")}
    kept["transcript"] = {
        "path": str(transcript.relative_to(HERE.parents[1]))
        if transcript.is_relative_to(HERE.parents[1])
        else str(transcript),
        "sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        "bytes": transcript.stat().st_size,
    }
    kept["given_sha256"] = hashlib.sha256(json.dumps(record.get("given", {}), sort_keys=True).encode()).hexdigest()
    (where / "record.json").write_text(json.dumps(kept, indent=1))


def report(phase: str, out: Path, root: Path | None = None) -> int:
    decisions_file = out / "audit_decisions.json"
    decisions = json.loads(decisions_file.read_text()) if decisions_file.exists() else {}
    records = []
    for path in sorted((RECORDS / phase).glob("r*/*/*/record.json")):
        record = json.loads(path.read_text())
        if root is not None:  # the audit again, under the rule as it stands, for every subject alike
            record["audit"] = audit(Path(record["transcript"]), record["sandbox"], str(root))
        key = f"{phase}/r{record.get('replicate', 1)}/{record['task']}/{record['language']}"
        record["contaminated"] = decisions.get(key, {}).get("contaminated", False)
        records.append(record)
    rows = [{**row(r), "exploratory": breakdown(Path(r["transcript"]), r["language"])} for r in records]
    table = {
        "phase": phase,
        "summary": summary(rows, LANGUAGES),
        "pairs": {f"{a}_vs_{b}": pairs(rows, a, b) for a, b in (("cairn", "cpp"), ("cairn", "rust"), ("cpp", "rust"))},
        "rows": rows,
        "audit_decisions": {k: v for k, v in decisions.items() if k.startswith(f"{phase}/")},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / f"results_{phase}.json").write_text(json.dumps(table, indent=1))
    (out / f"tables_{phase}.md").write_text(markdown(table))
    for record in records:
        export(
            record, out / "subjects" / phase / f"r{record.get('replicate', 1)}" / record["task"] / record["language"]
        )
    print(json.dumps({"summary": table["summary"], "pairs": table["pairs"]}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="references pass, starters fail, examples agree")
    v.add_argument("--task", action="append", choices=[t.name for t in TASKS])
    v.add_argument("--language", action="append", choices=LANGUAGES)
    v.add_argument("--program", action="append", choices=["reference", "starter"])
    v.add_argument("--jobs", type=int, default=4)
    r = sub.add_parser("run", help="run subjects one at a time")
    r.add_argument("--phase", choices=PHASES, required=True)
    r.add_argument("--replicate", type=int, default=1)
    r.add_argument("--task", action="append", choices=[t.name for t in TASKS])
    r.add_argument("--language", action="append", choices=LANGUAGES)
    r.add_argument(
        "--root", type=Path, default=Path(tempfile.gettempdir()) / "cairn-aibench", help="outside the repository"
    )
    r.add_argument("--model", help="default: the phase's preregistered model")
    t = sub.add_parser("report", help="summarize one phase")
    t.add_argument("--phase", choices=PHASES, required=True)
    t.add_argument("--out", type=Path, default=HERE.parents[1] / "evidence" / "v0_9" / "ai_benchmark")
    t.add_argument("--root", type=Path, help="the run's root: audit every transcript again under the current rule")
    args = p.parse_args(argv)
    if args.command == "verify":
        programs = args.program or ["reference", "starter"]
        return verify(args.task or [t.name for t in TASKS], args.language or list(LANGUAGES), args.jobs, programs)
    if args.command == "run":
        if HERE.parents[1] in args.root.resolve().parents:
            p.error("--root must be outside the repository")
        names = args.task or [t.name for t in TASKS]
        limits = {**LIMITS, "model": args.model or MODELS[args.phase]}
        return run(args.phase, args.replicate, names, args.language or list(LANGUAGES), args.root, limits)
    return report(args.phase, args.out, args.root)


if __name__ == "__main__":
    raise SystemExit(main())

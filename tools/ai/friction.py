#!/usr/bin/env python3
"""Where an AI evaluation's CAIRN subjects spent their tokens, read from their kept transcripts; no model runs.

A session's tokens are almost all context reads, and each request reads everything before it, so a tool result
costs its size at every later request. `costs` splits every subject's context that way: the context gained before
its first edit and carried to the end, each documentation file's share, and every refusal the compiler gave with the
requests and tokens of its round trip, up to the next check. `judge` rebuilds each version of a subject's program from
the task's starter and the subject's edits and judges them in order with the evaluation's own hidden check until one
passes, which splits a session into before the first edit, up to the first passing version, and after it. `replay`
runs one compiler's `cairn check` on every version a subject checked, which measures a diagnostic or rule change
without a model. evidence/v1_1/friction/README.md says what the numbers showed.

    python3 tools/ai/friction.py costs [--first-pass FILE]
    python3 tools/ai/friction.py judge --compiler DIR     # DIR holds bin/cairn, e.g. `git archive dd3f75e`
    python3 tools/ai/friction.py replay --compiler DIR
"""

from __future__ import annotations

import argparse
import json
import lzma
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COUNTED = ROOT / "evidence" / "v1_1" / "ai_eval" / "subjects" / "counted"
TASKS = ROOT / "bench" / "ai" / "tasks"
SOURCE = {"cairn": "src/main.cairn", "cpp": "main.cpp"}
# Tokens of context per character of a tool result: the least-squares fit of each request's context growth on the
# characters of the results before it and of the model's own writing, over the 65 counted transcripts of the 1.1 run.
TOKENS_PER_CHARACTER = 0.46
COMPILED = re.compile(r"(?<![\w/-])cairn\s+(check|build|run|test)\b")
LOOKUP = re.compile(r"(?<![\w/-])cairn\s+(rules|doc|explain|--help)\b")
SHELL_EDIT = re.compile(r"main\.cairn.*(sed -i|open\()|(sed -i|open\().*main\.cairn", re.S)  # a rewrite by shell
CODE = re.compile(r'"code":\s*"(E-[A-Z0-9-]+)"|\berror\[(E-[A-Z0-9-]+)\]')
# What a refusal was about, from its code and the line it named: the first row whose pattern matches wins.
CAUSES = [
    ("E-EFFECT-ORDER", r"(usize|u8|u16|u32|u64|i8|i16|i32|i64)\(\w[\w.]*\([^()]*\)\);", "a writing call as the one operand of a conversion"),
    ("E-EFFECT-ORDER", r"", "a writing call beside another operand"),
    ("E-LITERAL-RANGE", r"", "the minimum of a signed type as a literal or constant"),
    ("E-WRAP-TYPE", r"", "wrapping or checked arithmetic on a signed integer"),
    ("E-TYPE-MISMATCH", r"println", "an imported println hiding the builtin"),
    ("E-TYPE-MISMATCH", r"got \w+<\w+>\[len\(", "a Buf, whose extent is len(b), passed where [n] is expected"),
    ("E-TYPE-MISMATCH", r"Expected usize, got u64", "an unannotated literal's u64 used as a usize"),
    ("E-LEASED", r"\[\?\.\.\?\]", "disjoint parts whose bounds are constant expressions"),
    ("E-LEN", r"", "len of a Vec"),
    ("E-NAME", r"found 'if'", "if as an expression"),
    ("E-VIEW-ALIAS", r"", "a part bound to a local"),
    ("E-COOP-GLOBAL", r"", "one guarded thread writing an outside element"),
    ("E-LEASED", r"", "a place lent to a task touched before its wait"),
    ("E-LINEAR-LEAK", r"", "a linear value not consumed on every path"),
    ("E-PROJECT-OR-ENVIRONMENT", r"No tests", "cairn test of a project with no test blocks"),
]  # fmt: skip


@dataclass
class Call:
    name: str
    input: dict
    result: str = ""
    error: bool = False

    def target(self) -> str:
        given = self.input
        return str(given.get("file_path") or given.get("path") or given.get("command") or given.get("pattern") or "")


@dataclass
class Request:
    index: int
    context: int  # what the model read for this request: fresh input, cache writes and cache reads
    calls: list[Call] = field(default_factory=list)


@dataclass
class Subject:
    replicate: str
    task: str
    arm: str
    set_aside: bool
    folder: Path

    @property
    def name(self) -> str:
        return f"{self.replicate}_{self.task}_{self.arm}" + ("_set_aside" if self.set_aside else "")

    @property
    def language(self) -> str:
        return {"plugin": "cairn", "cairn": "cairn"}.get(self.arm, self.arm)

    def starter(self) -> str:
        return (TASKS / self.task / f"starter.{self.language}").read_text()


def subjects(root: Path = COUNTED) -> list[Subject]:
    """Every subject whose transcript is kept, set-aside ones included, in a stable order."""
    found = []
    for transcript in sorted(root.glob("r*/*/*/**/transcript.jsonl.xz")):
        folder = transcript.parent
        aside = folder.name == "set_aside"
        arm_folder = folder.parent if aside else folder
        found.append(Subject(arm_folder.parent.parent.name, arm_folder.parent.name, arm_folder.name, aside, folder))
    return found


def requests(subject: Subject) -> list[Request]:
    """Each model request of a session in order, with the tool calls it made and their results. A message streams
    in several records under one id; the first carries the request's usage."""
    out: list[Request] = []
    by_id: dict[str, Request] = {}
    calls: dict[str, Call] = {}
    with lzma.open(subject.folder / "transcript.jsonl.xz", "rt") as lines:
        for line in lines:
            m = json.loads(line)
            message = m.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if m.get("type") == "assistant":
                if message["id"] not in by_id:
                    u = message.get("usage", {})
                    context = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                        "cache_creation_input_tokens"))  # fmt: skip
                    by_id[message["id"]] = Request(len(out), context)
                    out.append(by_id[message["id"]])
                for block in content:
                    if block["type"] == "tool_use":
                        calls[block["id"]] = Call(block["name"], block.get("input", {}))
                        by_id[message["id"]].calls.append(calls[block["id"]])
            elif m.get("type") == "user" and isinstance(content, list):
                for block in content:
                    if block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                        body = block.get("content")
                        text = body if isinstance(body, str) else " ".join(b.get("text", "") for b in body or [])
                        calls[block["tool_use_id"]].result, calls[block["tool_use_id"]].error = (
                            text,
                            bool(block.get("is_error")),
                        )
    return out


def documentation(text: str) -> str | None:
    """The documentation a call reads: a skill card, the skill, a docs file, or a search of the docs folder."""
    if m := re.search(r"skills/cairn/cards/([\w-]+)\.md", text):
        return f"card {m.group(1)}"
    if "skills/cairn/SKILL.md" in text:
        return "skill"
    if m := re.search(r"docs/(std/[\w-]+\.md|[\w-]+\.md)", text):
        return m.group(1)
    if re.search(r"(?:^|[\s;&|(])(?:grep|rg|find|ls|cat|sed|head|tail)\b[^|;]*\bdocs\b", text, re.I):
        return "a search of docs/"
    return None


def kind(call: Call) -> tuple[str, str]:
    """(what a call bought, the file or command): docs, compile, test, experiment, edit, source, task or other."""
    if call.name == "Skill":
        return "docs", "skill"
    if call.name.startswith("mcp__"):
        return "compile", call.name.rsplit("__", 1)[-1]
    target = call.target()
    if call.name in ("Write", "Edit"):
        return ("edit", "program") if target.endswith(tuple(SOURCE.values())) else ("experiment", "a scratch file")
    if call.name == "Bash":
        if m := LOOKUP.search(target):
            return "docs", f"cairn {m.group(1)}"
        if runs := COMPILED.findall(target):
            if re.search(r"cairn\s+\w+\s+/tmp/\S+\.cairn", target):
                return "experiment", "a scratch program"
            if "run" in runs and re.search(r"<|printf|echo", target):
                return "test", "cairn run"
            return "compile", "cairn " + "/".join(sorted(set(runs)))
        if found := documentation(target):
            return "docs", found
        if re.search(r"clang\+\+|g\+\+|rustc|cargo|python3|ASAN|TSAN|(?:^|[\s;&|])\./", target):
            return "test", "a build or run by hand"
        return "other", target.split()[0] if target.split() else ""
    if found := documentation(target):
        return "docs", found
    if target.endswith(tuple(SOURCE.values())):
        return "source", ""
    return ("task", "") if target.endswith("TASK.md") else ("other", call.name)


def rewritten(source: str, command: str, sandbox: str) -> str:
    """A subject's own shell rewrite of its source (`sed -i`, a Python script), run on a copy."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "src").mkdir()
        (Path(tmp) / "src" / "main.cairn").write_text(source)
        subprocess.run(["bash", "-c", command.replace(sandbox, tmp)], cwd=tmp, capture_output=True, timeout=60)
        return (Path(tmp) / "src" / "main.cairn").read_text()


def versions(subject: Subject, session: list[Request]) -> list[tuple[int, str]]:
    """(request, source after it) for every edit of the program, from the task's starter."""
    tail, source, sandbox, out = SOURCE[subject.language], subject.starter(), "", []
    for r in session:
        for c in r.calls:
            target = str(c.input.get("file_path", ""))
            sandbox = sandbox or (target[: -len(tail) - 1] if target.endswith(tail) else "")
            if c.name == "Write" and target.endswith(tail):
                source = c.input.get("content", "")
            elif c.name == "Edit" and target.endswith(tail) and not c.error and c.input["old_string"] in source:
                old, new = c.input["old_string"], c.input["new_string"]
                source = source.replace(old, new) if c.input.get("replace_all") else source.replace(old, new, 1)
            elif c.name == "Bash" and subject.language == "cairn" and re.search(SHELL_EDIT, c.target()):
                source = rewritten(source, c.target(), sandbox)
            else:
                continue
            out.append((r.index, source))
    return out


def checked(subject: Subject, session: list[Request]) -> list[tuple[int, str]]:
    """The program as it stood at each check, build, run or test the subject made of it, once per distinct text."""
    edits, out = versions(subject, session), []
    for r in session:
        if any(kind(c)[0] in ("compile", "test") for c in r.calls):
            source = next((s for at, s in reversed(edits) if at <= r.index), None)
            if source is not None and (not out or out[-1][1] != source):
                out.append((r.index, source))
    return out


def refusals(subject: Subject, session: list[Request]) -> list[dict]:
    """Every diagnostic code in the compiler's own output, with the line it named, its cause and its round trip."""
    edits, out = versions(subject, session), []
    for r in session:
        for c in r.calls:
            if kind(c)[0] not in ("compile", "test", "experiment") or not (m := CODE.search(c.result)):
                continue
            code = next(g for g in m.groups() if g)
            message = re.search(r'"message":\s*"((?:[^"\\]|\\.)*)"', c.result)
            line = re.search(r'"line":\s*(\d+)', c.result)
            if kind(c)[0] == "experiment":
                written = re.search(r"<<\s*'?(\w+)'?\n(.*?)\n\1", c.input.get("command", ""), re.S)
                source = written.group(2) if written else ""
            else:
                source = next((s for at, s in reversed(edits) if at <= r.index), subject.starter())
            lines = source.splitlines()
            text = lines[int(line.group(1)) - 1].strip() if line and 0 < int(line.group(1)) <= len(lines) else ""
            said = f"{text} {message.group(1) if message else ''}"
            cause = next((n for c_, pattern, n in CAUSES if c_ == code and re.search(pattern, said)), code)
            after = next((s for s in session[r.index + 1 :] if any(kind(d)[0] in ("compile", "test",
                          "experiment") for d in s.calls)), None)  # fmt: skip
            spent = session[r.index + 1 : after.index + 1] if after else []
            out.append({"request": r.index, "code": code, "cause": cause, "line": text,
                        "message": message.group(1) if message else "", "round_trip_requests": len(spent),
                        "round_trip_tokens": sum(s.context for s in spent)})  # fmt: skip
    return out


def costs(subject: Subject, first_pass: int | None = None) -> dict:
    """One subject's context split by what bought it."""
    session = requests(subject)
    edits = versions(subject, session)
    first_edit = edits[0][0] if edits else None
    total, n = sum(r.context for r in session), len(session)
    documents: Counter = Counter()
    for i, r in enumerate(session[:-1]):
        growth, used = session[i + 1].context - r.context, 0.0
        for c in r.calls:
            share = max(0.0, min(growth - used, len(c.result) * TOKENS_PER_CHARACTER))
            used += share
            what, detail = kind(c)
            if what == "docs":
                documents[detail] += round(share * (n - i - 1))
        if any(c.name == "Skill" for c in r.calls):  # the skill's text arrives as a message of its own
            documents["skill"] += round(max(0.0, growth - used) * (n - i - 1))
    gained = session[first_edit].context - session[0].context if first_edit is not None else 0
    record = {
        "subject": subject.name, "task": subject.task, "arm": subject.arm, "replicate": subject.replicate,
        "set_aside": subject.set_aside, "requests": n, "tokens": total, "first_request_context": session[0].context,
        "first_edit": first_edit, "context_gained_before_first_edit": gained,
        "carried_after_first_edit": gained * (n - first_edit) if first_edit is not None else 0,
        "documentation_carried": dict(documents.most_common()),
        "refusals": refusals(subject, session) if subject.language == "cairn" else [],
    }  # fmt: skip
    if first_pass is not None and first_edit is not None:
        record["first_pass"] = first_pass
        record["phases"] = {
            "before_first_edit": sum(r.context for r in session[:first_edit]),
            "first_edit_to_first_pass": sum(r.context for r in session[first_edit : first_pass + 1]),
            "after_first_pass": sum(r.context for r in session[first_pass + 1 :]),
        }
    return record


def judged(subject: Subject, compiler: Path) -> dict:
    """The first version that passes the task's hidden check, judged with `compiler`'s cairn."""
    sys.path.insert(0, str(ROOT / "bench" / "ai"))
    from checking import judge
    from tasks import BY_NAME

    cairn = [sys.executable, str(compiler / "bin" / "cairn")]
    tried = []
    for at, source in versions(subject, requests(subject)):
        if subject.language == "cairn" and check(compiler, source)["status"] != "typed":
            tried.append([at, "not typed"])
            continue
        verdict = judge(BY_NAME[subject.task], subject.language, source, cairn=cairn)
        tried.append([at, verdict.reason])
        if verdict.passed:
            return {"subject": subject.name, "first_pass": at, "judged": tried}
    return {"subject": subject.name, "first_pass": None, "judged": tried}


def check(compiler: Path, source: str) -> dict:
    """What `compiler`'s `cairn check` says of one program: typed, or the refusal's code, message and hint."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "main.cairn").write_text(source)
        done = subprocess.run([sys.executable, str(compiler / "bin" / "cairn"), "check", str(Path(tmp) / "main.cairn"),
                               "--format", "json"], capture_output=True, text=True, timeout=600)  # fmt: skip
    try:
        said = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"status": "no record", "stderr": done.stderr[-500:]}
    keep = ("status",) if said.get("status") == "typed" else ("status", "code", "message", "repair_hint", "line")
    return {k: said.get(k) for k in keep}


def summary(records: list[dict]) -> dict:
    """Per arm, over the counted subjects: tokens, requests, the reading before the first edit and what carried it,
    the phases when known, and the refusals of every subject by cause."""
    arms: dict[str, Counter] = defaultdict(Counter)
    documents: dict[str, Counter] = defaultdict(Counter)
    causes: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        for x in r["refusals"]:
            causes[x["cause"]].update(refusals=1, requests=x["round_trip_requests"], tokens=x["round_trip_tokens"])
        if r["set_aside"]:
            continue
        a = arms[r["arm"]]
        a.update(subjects=1, tokens=r["tokens"], requests=r["requests"], gained=r["context_gained_before_first_edit"],
                 carried=r["carried_after_first_edit"], **r.get("phases", {}))  # fmt: skip
        documents[r["arm"]].update(r["documentation_carried"])
    return {
        "arms": {k: dict(v) for k, v in arms.items()},
        "documentation": {k: dict(v.most_common(12)) for k, v in documents.items()},
        "refusals": {k: dict(v) for k, v in sorted(causes.items(), key=lambda kv: -kv[1]["tokens"])},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["costs", "judge", "replay"])
    parser.add_argument("--compiler", type=Path, help="a tree holding bin/cairn: for judge and replay")
    parser.add_argument("--first-pass", type=Path, help="costs: a judge record, to split sessions into phases")
    parser.add_argument("--subject", action="append", default=[], help="only subjects whose name contains this")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--output", type=Path, help="also write the record here")
    a = parser.parse_args(argv)
    chosen = [s for s in subjects() if not a.subject or any(p in s.name for p in a.subject)]
    if a.command == "costs":
        passes = json.loads(a.first_pass.read_text()) if a.first_pass else {}
        records = [costs(s, (passes.get(s.name) or {}).get("first_pass")) for s in chosen if s.arm != "rust"]
        result = {"summary": summary(records), "subjects": records}
    elif a.compiler is None:
        parser.error(f"{a.command} needs --compiler")
    elif a.command == "judge":
        with ThreadPoolExecutor(a.jobs) as pool:
            ran = pool.map(lambda s: judged(s, a.compiler.resolve()), [s for s in chosen if s.arm != "rust"])
            result = {r["subject"]: r for r in ran}
    else:
        cairn = [s for s in chosen if s.language == "cairn"]
        with ThreadPoolExecutor(a.jobs) as pool:
            ran = pool.map(lambda s: [{"request": at, **check(a.compiler.resolve(), src)}
                                      for at, src in checked(s, requests(s))], cairn)  # fmt: skip
            result = dict(zip((s.name for s in cairn), ran, strict=True))
    text = json.dumps(result, indent=1) + "\n"
    if a.output:
        a.output.write_text(text)
    print(text if a.command != "costs" else json.dumps(result["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

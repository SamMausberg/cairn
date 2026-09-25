#!/usr/bin/env python3
"""The edit-protocol trial: the same repairs under the component packet and under the focused packet.

verify        every task: the planted bug fails the hidden check, and the program as shipped passes it
prepare DIR   one sandbox per task and arm (DIR/<arm>/<task>): TASK.md, PACKET.json and host.py
host BOX REQ  (run by the subject, through host.py) one cairn.edit/2 request, or {"kind": "submit"}
rehearse DIR  a scripted subject, no model: it reads one callee under the focused arm, then writes the fix
score DIR     the hidden check of every submitted candidate, and every byte and round, per arm

A task is a real function of a shipped example with one planted bug and a bug report that names the
symptom. The subject sees only its sandbox: the host keeps the program, and each call is replayed from
the transcript, so a sandbox holds no source. The design, the budgets and the analysis are fixed in
protocol_trial.md before any subject runs. Nothing here starts a model.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.agent.hosts.edits import HANDLES, EditHost, stable_json
from cairn.compiler.cairnc import Parser, compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from cairn.verify.testing import evaluate

ARMS = {  # What PACKET.json shows under each arm.
    "component": "the function's source and the source of every function connected to it by calls, every type of the "
    "program, the rule cards, and the effects the function may have",
    "focused": "the function's source, the signature, effect row and comment of each function it calls or that calls "
    "it, the types those name, the rule cards, and the effects the function may have",
}
CALLS = 8  # Host calls a subject may make before it must submit.
SUBMIT = {"kind": "submit"}
METRICS = ("bytes_read", "bytes_written", "tokens_read", "tokens_written", "calls")  # Summed per arm.
TASKS = {  # name -> (program, symbol, text as shipped, the planted bug, the bug report the subject gets)
    "above_loop": ("examples/apps/analytics", "analytics.query.above_loop",
                   "out[used] = price[i];", "out[used] = price[used];",
                   "`above_loop` keeps as many prices as `above` does, but not always the same ones."),
    "venue_tasks": ("examples/apps/analytics", "analytics.query.venue_sums_tasks",
                    "for p in 0..4 {", "for p in 0..3 {",
                    "`venue_sums_tasks` gives smaller bins than `venue_sums` on the same rows."),
    "notional": ("examples/apps/analytics", "analytics.query.notional_loop",
                 "out[i] = mul_wrap(price[i], u64(qty[i]));", "out[i] = add_wrap(price[i], u64(qty[i]));",
                 "`notional_loop` and `notional_par` disagree on every row with a quantity above one."),
    "replay_tail": ("examples/apps/kvstore", "replay",
                    "if u64(k) + u64(v) + u64(HEAD) > total - offset { break; }",
                    "if u64(k) + u64(v) + u64(HEAD) >= total - offset { break; }",
                    "After the store is reopened, the last record in the log is missing from the index, and the "
                    "log is cut back before it."),
    "apply_kind": ("examples/apps/kvstore", "apply", "if kind == ERASE {", "if kind == PUT {",
                   "A key that was put is not found, and a key that was erased is found."),
    "digest_order": ("examples/apps/kvstore", "digest", "h = mul_wrap(h ^ u64(data[i]), 1099511628211);",
                     "h = mul_wrap(h, 1099511628211) ^ u64(data[i]);",
                     "`digest` must be 64-bit FNV-1a chained from `seed`: each byte is xored in, then the hash is "
                     "multiplied by the FNV prime. Logs written by another FNV-1a implementation fail their checksums."),
    "checksum_check": ("examples/apps/kvstore", "replay",
                       "if checksum(h.kind, k, key.data[0..k], v, value.data[0..v]) != h.check { break; }",
                       "if h.check == 0 { break; }",
                       "When the store is reopened, a whole record whose checksum does not match is taken into the "
                       "index instead of ending the replay."),
    "overflow_digit": ("examples/systems", "decimal", "if value > (18446744073709551615 - digit) / 10 {",
                       "if value > 18446744073709551615 / 10 {",
                       "Some 20-digit inputs just above 2^64 - 1 trap instead of returning `Parsed.Overflow`."),
    "digit_range": ("examples/systems", "decimal", "if byte < 48 || byte > 57 {", "if byte < 48 || byte > 58 {",
                    "`decimal` accepts a byte that is not a decimal digit."),
    "sort_top": ("examples/systems", "sort_bytes", "for bucket in 0..256 {", "for bucket in 0..255 {",
                 "`sort_bytes` loses every byte of value 255, and leaves the end of `out` unwritten."),
    "even_bit": ("examples/systems", "sorted_even", "(scratch[i] & 1) == 0", "(scratch[i] & 1) == 1",
                 "`sorted_even` returns the odd bytes instead of the even ones."),
    "plan_first": ("examples/apps/analytics", "analytics.agg.run_plan", "for p in 0..plan.len {",
                   "for p in 1..plan.len {", "`run_plan` never reports the answer of the first aggregator in a plan."),
}  # fmt: skip


def fnv1a(seed: int, xs: list[int]) -> int:
    """The reference for `digest`."""
    for x in xs:
        seed = ((seed ^ x) * 1099511628211) % 2**64
    return seed


def decimal_kind(xs: list[int]) -> int:
    """The reference for `decimal_kind`: 3 empty, 1 a byte that is not a digit, 2 above 2^64 - 1, else 0."""
    if not xs:
        return 3
    if any(not 48 <= x <= 57 for x in xs):
        return 1
    return 2 if int(bytes(xs)) > 2**64 - 1 else 0


HIDDEN = {  # Finite cases the host adds to a task's hidden check, from a reference written here.
    "digit_range": {"schema": "cairn.task/1", "symbol": "decimal_kind", "cases": [
        {"args": {"n": len(xs), "input": xs}, "return": decimal_kind(xs)}
        for xs in ([58], [49, 58], [47], [48, 57], [57, 58, 48], [52, 50], [49] * 20, [57] * 20)]},
    "digest_order": {"schema": "cairn.task/1", "symbol": "digest", "cases": [
        {"args": {"seed": seed, "n": len(xs), "data": xs}, "return": fnv1a(seed, xs)}
        for seed in (0, 14695981039346656037, 2**64 - 1) for xs in ([], [0], [97], [1, 2, 3], list(range(250, 256)))]},
}  # fmt: skip


def shipped(task: str) -> tuple[str, str]:
    """The program as shipped, and the name of the function the task is about."""
    program, symbol, *_ = TASKS[task]
    return load_project(ROOT / program).source, symbol


def planted(task: str) -> str:
    """The shipped program with the task's bug planted in its function, and nowhere else."""
    source, symbol = shipped(task)
    _, _, good, bad, _ = TASKS[task]
    f = next(f for f in Parser(source).parse().functions if f.name == symbol)
    body = source[f.body_start : f.end + len(good)]  # A bug may reach just past the closing brace.
    if body.count(good) != 1:
        raise ValueError(f"{task}: the shipped text must occur once in {symbol}.")
    at = f.body_start + body.index(good)
    return source[:at] + bad + source[at + len(good) :]


def hidden(task: str, source: str, cxx: str = "clang++") -> dict:
    """The program's own self-check (`main` returns 0) and every finite contract its manifest lists."""
    project = load_project(ROOT / TASKS[task][0])
    with tempfile.TemporaryDirectory(prefix="cairn-trial-") as scratch:
        (Path(scratch) / "program.cairn").write_text(source, encoding="utf-8")
        record = build(load_project(Path(scratch) / "program.cairn"), output=Path(scratch) / "build", cxx=cxx,
                       kind="exe", timeout=120)  # fmt: skip
        if record["status"] != "native-built":
            return {"passed": False, "stage": "build", "detail": record.get("stderr", "")[:2000]}
        run = subprocess.run([record["artifact"]], capture_output=True, timeout=120, cwd=scratch)
    contracts = [json.loads((project.root / path).read_text()) for path in project.contracts]
    contracts += [HIDDEN[task]] if task in HIDDEN else []
    tests = [evaluate(source, contract, cxx)["status"] for contract in contracts]
    passed = run.returncode == 0 and all(t == "passed-finite-tests" for t in tests)
    return {"passed": passed, "main_exit": run.returncode, "contracts": tests}


def verify(tasks: list[str] | None = None) -> dict:
    """Before any subject: each bug fails the hidden check and the shipped program passes it."""
    out = {}
    for task in tasks or TASKS:
        source, _ = shipped(task)
        out[task] = {"shipped": hidden(task, source), "planted": hidden(task, planted(task))}
        out[task]["valid"] = out[task]["shipped"]["passed"] and not out[task]["planted"]["passed"]
    return {"tasks": out, "valid": all(t["valid"] for t in out.values())}


def brief(task: str, arm: str) -> str:
    grow = arm == "focused"
    expand = " Send an `expand` request to read the source of a function or a type the packet names." if grow else ""
    return f"""# Repair: {task}

A CAIRN program has a bug in one function, `{TASKS[task][1]}`. The bug report:

> {TASKS[task][4]}

Fix it by replacing that function's body. `PACKET.json` is everything the host shows you about the program: {ARMS[arm]}.

Talk to the host with `python host.py '<one JSON request>'`, or write the request to a `.json` file here and run
`python host.py request.json`. A `body` request, shaped like `draft_protocol` in the \
packet, proposes a new body.{expand} The host answers with a diagnostic, with an admission (`typed`, which says \
nothing about behaviour), or with what you asked to read.

You have {CALLS} calls. When the edit you want is admitted, run `python host.py '{{"kind": "submit"}}'`. Your last \
admitted edit is your answer, and tests you do not see decide it. Read no file outside this directory, and use no \
network.
"""


def prepare(out: Path, tasks: list[str] | None = None) -> None:
    for arm in ARMS:
        for task in tasks or TASKS:
            box = out / arm / task
            box.mkdir(parents=True, exist_ok=False)
            (box / "TASK.md").write_text(brief(task, arm), encoding="utf-8")
            (box / "state.json").write_text(json.dumps({"task": task, "arm": arm}) + "\n", encoding="utf-8")
            packet = host(box, None)
            (box / "PACKET.json").write_text(json.dumps(packet, indent=1) + "\n", encoding="utf-8")
            call = [sys.executable, str(Path(__file__)), "host", str(box)]
            (box / "host.py").write_text(f"import subprocess, sys\nsys.exit(subprocess.run([*{call!r}, "
                                         "*sys.argv[1:]]).returncode)\n", encoding="utf-8")  # fmt: skip


def session(box: Path) -> tuple[EditHost, dict, list[dict]]:
    """The subject's host as it stands: opened on the planted program, then every logged request replayed."""
    state = json.loads((box / "state.json").read_text())
    task, arm = state["task"], state["arm"]
    edits = EditHost()
    packet = edits.open(planted(task), TASKS[task][1], {"task": TASKS[task][4]}, scope=arm)
    kept = box / "transcript.jsonl"
    log = [json.loads(line) for line in kept.read_text().splitlines()] if kept.exists() else []
    for entry in log:
        if entry["response"].get("status") not in {"submitted", "budget-spent", "closed"}:
            edits.reply(entry["text"])  # Exactly what the subject sent, so a replay answers as the call did.
    return edits, packet, log


def host(box: Path, request: str | None) -> dict:
    """One call from the subject: answered, logged with its size, and refused once the budget is spent."""
    edits, packet, log = session(box)
    if request is None:
        return packet
    if request.endswith(".json") and (box / Path(request).name).is_file():  # A request written to a file here.
        request = (box / Path(request).name).read_text(encoding="utf-8")
    if submitted(log):
        return {"status": "closed", "message": "This repair was submitted."}
    try:
        parsed = json.loads(request)
    except json.JSONDecodeError:
        parsed = None
    if parsed == SUBMIT:
        answer = {"status": "submitted", "admitted_edits": len(edits.admitted.get("e1", []))}
    elif len(log) >= CALLS:
        answer = {"status": "budget-spent", "message": f"{CALLS} calls were made; submit now."}
    else:
        answer = edits.reply(request)
    entry = {"request": parsed if parsed is not None else request, "text": request, "response": answer,
             "request_bytes": len(request.encode()), "response_bytes": len(stable_json(answer).encode())}  # fmt: skip
    with (box / "transcript.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry) + "\n")
    return answer


def submitted(log: list[dict]) -> bool:
    return any(entry["request"] == SUBMIT for entry in log)


def rehearse(out: Path) -> None:
    """A scripted subject per sandbox. Under the focused arm it expands what the fix calls that the packet does not
    show, or else the first callee written in the program, as a reader would; then it sends the body as shipped and
    submits. It shows that every task can be solved through the host. It is not a model."""
    for box in sorted(path.parent for path in out.glob("*/*/state.json")):
        state = json.loads((box / "state.json").read_text())
        packet = json.loads((box / "PACKET.json").read_text())
        source, symbol = shipped(state["task"])
        f = next(f for f in Parser(source).parse().functions if f.name == symbol)
        needed = [n for n in compile_source(source)[1]["functions"][symbol]["calls"] if n not in packet["dependencies"]]
        written = [n for n in packet["dependencies"] if n not in packet.get("callers", []) and "." not in n]
        steps = [{"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": source[f.body_start : f.end]}]
        if state["arm"] == "focused" and (needed or written):
            steps.insert(0, {"protocol": HANDLES, "handle": "e1", "kind": "expand", "symbols": needed or written[:1]})
        for request in [*steps, SUBMIT]:
            host(box, stable_json(request))


def count_tokens():
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_base")
        return "o200k_base", lambda s: len(encoding.encode(s))
    except ImportError:
        return None, None


def score(out: Path, cxx: str = "clang++") -> dict:
    unit, tokens = count_tokens()
    rows = []
    for state_file in sorted(out.glob("*/*/state.json")):
        box = state_file.parent
        state = json.loads(state_file.read_text())
        edits, packet, log = session(box)
        final = edits.admitted["e1"][-1][0] if submitted(log) and edits.admitted.get("e1") else None
        read = [stable_json(packet), *(stable_json(e["response"]) for e in log)]
        written = [stable_json(e["request"]) for e in log]
        rows.append({
            **state,
            "solved": final is not None and hidden(state["task"], final, cxx)["passed"],
            "calls": len(log),
            "expansions": sum(isinstance(e["request"], dict) and e["request"].get("kind") == "expand" for e in log),
            "refusals": sum("code" in e["response"] for e in log),
            "bytes_read": sum(len(s.encode()) for s in read),
            "bytes_written": sum(len(s.encode()) for s in written),
            **({"tokens_read": sum(map(tokens, read)), "tokens_written": sum(map(tokens, written))} if tokens else {}),
        })  # fmt: skip
    arms = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        sums = {k: sum(r[k] for r in mine) for k in METRICS if mine and k in mine[0]}
        arms[arm] = {"subjects": len(mine), "solved": sum(r["solved"] for r in mine), **sums}
    ratio = {k: arms["focused"][k] / arms["component"][k] for k in METRICS if arms["component"].get(k)}
    result = {"schema": "cairn.protocol-trial/1", "tokenizer": unit, "arms": arms, "focused_over_component": ratio,
              "rows": rows, "note": "Host-visible traffic only; a subject's own reasoning is recorded by whoever ran it."}  # fmt: skip
    (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "verify":
        print(json.dumps(verify(), indent=2))
    elif command == "prepare":
        prepare(Path(rest[0]))
    elif command == "host":
        print(json.dumps(host(Path(rest[0]), rest[1] if len(rest) > 1 else None), indent=1))
    elif command == "rehearse":
        rehearse(Path(rest[0]))
    else:
        print(json.dumps(score(Path(rest[0]))["arms"], indent=2))

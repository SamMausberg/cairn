#!/usr/bin/env python3
"""An agent repairs demos/repair/latency.cairn through the edit host, and `cairn diff` reviews what it changed.

The agent is scripted by default: its replies come from scripted.json, so every run tells the same story. `--live`
asks a model through `claude -p` instead and writes its replies where `--replay` reads them back. Everything the
host and the compiler say is computed on every run; nothing they say is read from a file.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import textwrap
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "src"), str(HERE.parent)]
from transcript import cairn, rel  # noqa: E402

from cairn.agent.hosts.edits import EditHost, stable_json  # noqa: E402
from cairn.editor import changes  # noqa: E402
from cairn.verify.testing import evaluate  # noqa: E402

SYSTEM = (
    "You edit one function of a CAIRN program through a host. Reply with exactly one strict cairn.edit/2 JSON "
    "object and nothing else, no Markdown fence: a body edit of the function the packet names, or an expand request "
    "for a function or type you need to read. The host checks every reply against the whole program and answers "
    "with what it admitted or why it refused."
)
ATTEMPTS = 4


def show_run(path: Path, out: Path) -> dict:
    done = cairn("run", rel(path), "--format", "human", "--out", str(out))
    print(f"$ cairn run {rel(path)}")
    print(textwrap.indent(done.stdout + done.stderr, "  "), end="")
    return {"exit_code": done.returncode, "stdout": done.stdout}


def scripted(path: Path):
    replies = {k: list(v) for k, v in json.loads(path.read_text())["replies"].items()}
    return lambda symbol, messages: (replies[symbol].pop(0), None) if replies[symbol] else (None, None)


def live(model: str):
    def ask(symbol: str, messages: list[dict]) -> tuple[str | None, dict | None]:
        prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages[1:])
        command = ["claude", "-p", prompt, "--output-format", "json", "--tools", "", "--model", model,
                   "--system-prompt", messages[0]["content"], "--no-session-persistence"]  # fmt: skip
        with tempfile.TemporaryDirectory(prefix="cairn-live-") as tmp:
            done = subprocess.run(command, capture_output=True, text=True, timeout=900, cwd=tmp)
        if done.returncode:
            raise SystemExit(f"claude -p failed ({done.returncode}): {done.stderr[-2000:]}")
        record = json.loads(done.stdout)
        cost = {k: record.get(k) for k in ("usage", "total_cost_usd", "duration_ms")}
        return record["result"].strip(), {**cost, "models": sorted(record.get("modelUsage", {}))}

    return ask


def summary(response: dict) -> str:
    if response.get("status") == "typed":
        return f"typed, effect row [{', '.join(response['effects'])}] within the ceiling"
    head = f"refused {response.get('code')}: {response.get('message')}"
    extra = [f"{k}: {', '.join(response[k])}" for k in ("added_effects",) if k in response]
    extra += [f"repair_hint: {response['repair_hint']}"] if "repair_hint" in response else []
    return "\n".join([head, *extra])


def session(host: EditHost, ask, spec: dict, source: str, log: list) -> str:
    """One function's edit session: the packet, then replies until one is admitted and passes what the host holds."""
    name = spec["symbol"]
    contract = {"symbol": name, "task": spec["task"]}
    public = hidden = None
    if "cases" in spec:
        contract |= {"schema": "cairn.task/1", "cases": spec["cases"]}
        public = {**contract, "cases": spec["cases"][: spec["public"]]}
        hidden = {**contract, "cases": spec["cases"][spec["public"] :]}
    if "preserve" in spec:
        contract["preserve"] = spec["preserve"]
    packet = host.open(source, name, contract)
    if public:
        packet["public_examples"] = public["cases"]
    cards = ", ".join(packet["rule_cards"]) or "none new"
    print(f"\n[{name}] host -> agent: a packet of {len(stable_json(packet)):,} bytes")
    print(f"    the source of {name}; the signature and effect row of {', '.join(packet['dependencies'])}")
    print(f"    rule cards: {cards}; the task: {spec['task'].split('. ')[0]}.")
    print(f"    {len(public['cases'])} public cases" if public else f"    the contract preserve={spec['preserve']}")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": stable_json(packet)}]
    for attempt in range(ATTEMPTS):
        reply, cost = ask(name, messages)
        if reply is None:
            raise SystemExit(f"The agent has no reply left for {name}.")
        response = host.reply(reply)
        entry = {"symbol": name, "attempt": attempt, "reply": reply, "response": response}
        if cost:
            entry["cost"] = cost
        log.append(entry)
        try:
            body = json.loads(reply).get("replacement", reply)
        except (ValueError, AttributeError):
            body = reply
        print(f"[{name}] agent -> host:\n{textwrap.indent(body, '    ')}")
        print(f"[{name}] host: " + summary(response).replace("\n", "\n    "))
        if response.get("status") == "typed":
            candidate, receipt = host.admitted[json.loads(reply)["handle"]][-1]
            if "preserve" in spec:
                print(f"[{name}] host: the preserve contract holds: {receipt['equivalence']}")
                return candidate
            tests = evaluate(candidate, public)
            entry["public"] = tests
            if tests["status"] == "passed-finite-tests":
                entry["hidden"] = held = evaluate(candidate, hidden)
                ok = held["status"] == "passed-finite-tests"
                print(f"[{name}] host: {len(public['cases'])} public cases pass; "
                      f"{len(hidden['cases'])} hidden cases {'pass' if ok else 'fail'}")  # fmt: skip
                if ok:
                    return candidate
                feedback = {"status": "hidden-tests-failed"}  # which case failed never reaches the agent
            else:
                feedback = tests
                print(f"[{name}] host: public case failed: {stable_json(tests)[:300]}")
        else:
            feedback = response
        messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": stable_json(feedback)}]
    raise SystemExit(f"{name} was not repaired in {ATTEMPTS} attempts.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "results/demos/repair", help="Where the run is written.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--live", metavar="MODEL", help="Ask this model through `claude -p`, and record its replies.")
    group.add_argument("--replay", type=Path, help="Replay replies recorded by --live, or scripted.json by default.")
    a = p.parse_args()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads((HERE / "task.json").read_text())
    before = HERE / spec["program"]
    ask = live(a.live) if a.live else scripted(a.replay or HERE / "scripted.json")
    print("An agent repairs a latency report through the CAIRN edit host; cairn diff reviews the change.\n")
    agent = f"live: {a.live} through claude -p" if a.live else f"replayed: {rel(a.replay or HERE / 'scripted.json')}"
    record: dict = {"agent": agent}
    record["before"] = show_run(before, out / "build")
    source, log = before.read_text(), []
    host = EditHost()
    for s in spec["sessions"]:
        source = session(host, ask, s, source, log)
    after = out / "latency_after.cairn"
    after.write_text(source)
    print()
    record["after"] = show_run(after, out / "build")
    print(f"\n$ cairn diff {rel(before)} {rel(after)}")
    record["diff"] = json.loads(cairn("diff", rel(before), rel(after), "--format", "json").stdout)
    print(textwrap.indent(changes.lines(record["diff"], rel(before), rel(after)), "  "))  # as a terminal shows it
    record["exchanges"] = log
    (out / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    if a.live:
        replies: dict[str, list[str]] = {}
        for e in log:
            replies.setdefault(e["symbol"], []).append(e["reply"])
        models = sorted({m for e in log for m in e["cost"]["models"]})
        cost = round(sum(e["cost"]["total_cost_usd"] or 0 for e in log), 4)
        kept = {"agent": f"{', '.join(models)} through claude -p, asked with --live {a.live}",
                "date": date.today().isoformat(), "cost_usd": cost, "replies": replies}  # fmt: skip
        (out / "live.json").write_text(json.dumps(kept, indent=2) + "\n")
    print(f"\nThe whole exchange, every packet reply and host answer, is in {rel(out / 'record.json')}.")
    return 0 if record["before"]["exit_code"] == 1 and record["after"]["exit_code"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

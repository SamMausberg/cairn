#!/usr/bin/env python3
"""What an edit costs in context: whole scripted tasks, and the rule-card counterfactual against 0.5.

No model runs. `tasks` replays one authored transcript per function of five real programs: the packet, a
first reply with a type error and its diagnostic, a request to read a callee's body (focused scope only, where
the body is not already shown), and the correct reply with its admission. It counts every message once, and
also as a model reads it, re-reading the conversation so far on every turn. Five settings are compared: the
component packet over cairn.edit/1 (the 1.3 protocol), and component or focused packets over cairn.edit/2,
cold (a new host per task) or warm (one host per program, so cards and boundaries go once).

`cards` keeps the 0.6 measurement: the same component packet with only the card texts swapped for 0.5's.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.agent.agent_tools import HANDLES, PROTOCOL, EditHost, EditSession, explain, stable_json
from cairn.agent.teaching import CARDS
from cairn.compiler.cairnc import Diagnostic
from cairn.compiler.syntax import Parser
from cairn.projects.project import load_project

PROGRAMS = ["examples/apps/kvstore", "examples/apps/analytics", "examples/apps/service", "examples/apps/simulator",
            "examples/systems"]  # fmt: skip
PER_PROGRAM = 6
SETTINGS = [("component", "edit/1", "cold"), ("component", "edit/2", "cold"), ("focused", "edit/2", "cold"),
            ("component", "edit/2", "warm"), ("focused", "edit/2", "warm")]  # fmt: skip


def text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def tasks(source: str) -> list[tuple[str, str]]:
    """The first functions with a block body whose own text is an admitted edit: (symbol, body)."""
    chosen = []
    for f in Parser(source).parse().functions:
        body = source[f.body_start : f.end]
        if f.static or not body.startswith("{") or len(chosen) == PER_PROGRAM:
            continue
        try:
            session = EditSession(source, f.name)
            session.check({"protocol": PROTOCOL, "session": session.session, "kind": "body", "replacement": body})
        except Diagnostic:
            continue
        chosen.append((f.name, body))
    return chosen


def transcript(source: str, symbol: str, body: str, scope: str, wire: str, host: EditHost) -> list[tuple[str, str]]:
    """(direction, message) pairs: `in` is what the model reads, `out` what it writes."""
    wrong = "{ let cairn_probe:bool = 0;" + body[1:]  # The same type error in every setting.
    if wire == "edit/1":
        s = EditSession(source, symbol, scope=scope)
        packet = s.packet()

        def ask(replacement: str) -> dict:
            return {"protocol": PROTOCOL, "session": s.session, "kind": "body", "replacement": replacement}

        def answer(request: dict) -> dict:
            try:
                return s.check(request)[1]
            except Diagnostic as e:
                return explain(e, source)
    else:
        packet = host.open(source, symbol, scope=scope)
        handle = packet["handle"]

        def ask(replacement: str) -> dict:
            return {"protocol": HANDLES, "handle": handle, "kind": "body", "replacement": replacement}

        def answer(request: dict) -> dict:
            return host.reply(stable_json(request))

    messages = [("in", packet)]
    for request in [ask(wrong)]:
        messages += [("out", request), ("in", answer(request))]
    callees = [n for n in sorted(packet["dependencies"]) if n not in packet.get("callers", []) and "." not in n]
    if scope == "focused" and callees:  # Read the first callee written in this program before relying on it.
        grow = {"protocol": HANDLES, "handle": packet["handle"], "kind": "expand", "symbols": callees[:1]}
        messages += [("out", grow), ("in", host.respond(grow))]
    request = ask(body)
    receipt = answer(request)
    assert receipt["status"] == "typed", (symbol, receipt)
    return [*messages, ("out", request), ("in", receipt)]


def account(runs: list[list[tuple[str, str]]], count) -> dict:
    """Totals over conversations: each message once, and as read with the whole conversation before it."""
    sent = {"in": 0, "out": 0}
    read = turns = 0
    for conversation in runs:
        seen = 0
        for direction, message in conversation:
            size = count(stable_json(message))
            sent[direction] += size
            seen += size
            read += seen if direction == "in" else 0  # The model rereads everything up to each message it reads.
            turns += direction == "out"
    return {"model_reads_once": sent["in"], "model_writes": sent["out"], "total_once": sent["in"] + sent["out"],
            "reads_with_history": read, "model_turns": turns}  # fmt: skip


def measure_tasks(count) -> dict:
    rows = []
    totals = {f"{scope} {wire} {warmth}": [] for scope, wire, warmth in SETTINGS}
    for path in PROGRAMS:
        source = load_project(ROOT / path).source
        chosen = tasks(source)
        for scope, wire, warmth in SETTINGS:
            host = EditHost()
            runs = []
            for symbol, body in chosen:
                host = host if warmth == "warm" else EditHost()
                runs.append(transcript(source, symbol, body, scope, wire, host))
            # A warm host is one conversation per program; cold hosts are one per task.
            key = f"{scope} {wire} {warmth}"
            conversations = [[m for r in runs for m in r]] if warmth == "warm" else runs
            totals[key] += conversations
            rows.append({"program": path, "setting": key, "tasks": [s for s, _ in chosen],
                         **account(conversations, count)})  # fmt: skip
    baseline = account(totals["component edit/1 cold"], count)
    summary = {}
    for key, runs in totals.items():
        a = account(runs, count)
        summary[key] = {**a, "vs_1_3": {k: round(a[k] / baseline[k], 3) for k in a}}
    return {"rows": rows, "summary": summary, "task_count": sum(len(r["tasks"]) for r in rows) // len(SETTINGS)}


def measure_cards(count) -> dict:
    prior = json.loads((ROOT / "bench/cpu/fixtures/cards_05.json").read_text())
    source = (ROOT / "examples/basics/native.cairn").read_text()
    rows, current_only = [], []

    def unmatched(symbol: str, packet: dict, reason: str) -> None:
        current_only.append({"symbol": symbol, "complete_packet_tokens": count(text(packet)), "reason": reason,
                             "cards": list(packet["rule_cards"]), "legacy_comparison": None})  # fmt: skip

    for function in (f for f in Parser(source).parse().functions if not f.static):
        packet = EditSession(source, function.name, scope="component").packet()
        if new := sorted(set(packet["rule_cards"]) - set(prior["cards"])):  # No 0.5 text, so no counterfactual.
            unmatched(function.name, packet, "Cards absent from the 0.5 curriculum: " + ", ".join(new) + ".")
            continue
        old = copy.deepcopy(packet)
        old["rule_cards"] = {name: prior["cards"][name] for name in packet["rule_cards"]}
        before, after = count(text(old)), count(text(packet))
        rows.append({"symbol": function.name, "cards": list(packet["rule_cards"]), "legacy_card_packet_tokens": before,
                     "current_packet_tokens": after, "saving_tokens": before - after,
                     "saving_fraction": 1 - after / before})  # fmt: skip
    systems = load_project(ROOT / "examples/systems").source
    for symbol in ["decimal", "sort_bytes", "sorted_even"]:
        packet = EditSession(systems, symbol, scope="component").packet()
        unmatched(symbol, packet, "Scoped storage or tagged sums were not accepted in 0.5.")
    before = sum(r["legacy_card_packet_tokens"] for r in rows)
    after = sum(r["current_packet_tokens"] for r in rows)
    aggregate = {"packet_count": len(rows), "current_only_packet_count": len(current_only), "before": before,
                 "after": after, "saving_fraction": 1 - after / before}  # fmt: skip
    cards = {"baseline": count("\n\n".join(prior["cards"].values())), "current": count("\n\n".join(CARDS.values()))}
    return {
        "baseline_card_source_sha256": prior["source_sha256"],
        "current_card_source_sha256": hashlib.sha256((ROOT / "src/cairn/agent/teaching.py").read_bytes()).hexdigest(),
        "rows": rows,
        "current_only": current_only,
        "aggregate": aggregate,
        "full_card_text": cards,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiktoken", default=None, help="Also count with this offline tiktoken encoding.")
    ap.add_argument("--output", type=Path, default=ROOT / "results/context/context.json")
    args = ap.parse_args()
    counters = {"utf8_bytes": lambda s: len(s.encode("utf-8"))}
    if args.tiktoken:
        import tiktoken

        encoding = tiktoken.get_encoding(args.tiktoken)
        counters["tiktoken/" + args.tiktoken] = lambda s: len(encoding.encode(s))
    result = {
        "model_trials": 0,
        "units": list(counters),
        "method": __doc__.strip(),
        "tasks": {unit: measure_tasks(count) for unit, count in counters.items()},
        **measure_cards(counters["utf8_bytes"]),
        "limitations": [
            "Authored transcripts, not a model: one type error, at most one expansion, then the right body.",
            "A real agent may expand more, or less; the focused packet's saving shrinks with every expansion.",
            "UTF-8 bytes are not model tokens; tiktoken encodings, where given, are not Claude's tokenizer.",
            "Reading with history charges a whole reread per model turn; prompt caching changes that price.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text(result))
    summary = {unit: result["tasks"][unit]["summary"] for unit in counters}
    print(text({"aggregate": result["aggregate"], "tasks": summary,
                "current_only": [{k: r[k] for k in ("symbol", "reason")} for r in result["current_only"]]}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

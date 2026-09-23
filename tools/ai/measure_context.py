#!/usr/bin/env python3
"""What an edit costs in context: whole scripted tasks, and the rule-card counterfactual against 0.5.

No model runs. `tasks` replays one authored transcript per function of five real programs: the packet, a
first reply with a type error and its diagnostic, a request to read a callee's body (focused scope only, where
the body is not already shown), and the correct reply with its admission. It counts every message once, and
also as a model reads it, re-reading the conversation so far on every turn. Five settings are compared: the
component packet over cairn.edit/1 (the 0.8.3 protocol), and component or focused packets over cairn.edit/2,
cold (a new host per task) or warm (one host per program, so cards and boundaries go once).

`cards` keeps the 0.6 measurement: the same component packet with only the card texts swapped for 0.5's.
Every total is also broken down by kind of message (packet, diagnostic, expansion, admission, reply), and
`card_sizes` counts every rule card alone, and `resume` sets the whole warm focused conversation of a program
beside the `cairn.state/1` object that stands in for it. Tokens are a real BPE vocabulary (`o200k_base` by
default) when tiktoken and its cached vocabulary are present. Nothing is downloaded; without them only UTF-8
bytes count.
"""

from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from cairn.agent.agent_tools import HANDLES, PROTOCOL, EditHost, EditSession, explain, stable_json
from cairn.agent.state import state
from cairn.agent.teaching import CARDS
from cairn.compiler.cairnc import Diagnostic
from cairn.compiler.syntax import Parser
from cairn.projects.project import load_project

sys.path.insert(0, str(ROOT / "tools"))
from support import TOKENIZER, tokenizer

PROGRAMS = ["examples/apps/kvstore", "examples/apps/analytics", "examples/apps/service", "examples/apps/simulator",
            "examples/systems"]  # fmt: skip
PER_PROGRAM = 6
SETTINGS = [("component", "edit/1", "cold"), ("component", "edit/2", "cold"), ("focused", "edit/2", "cold"),
            ("component", "edit/2", "warm"), ("focused", "edit/2", "warm")]  # fmt: skip


def text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def tasks(source: str, limit: int = PER_PROGRAM) -> list[tuple[str, str]]:
    """The first functions with a block body whose own text is an admitted edit: (symbol, body)."""
    chosen: list[tuple[str, str]] = []
    for f in Parser(source).parse().functions:
        body = source[f.body_start : f.end]
        if f.static or not body.startswith("{") or len(chosen) == limit:
            continue
        try:
            session = EditSession(source, f.name)
            session.check({"protocol": PROTOCOL, "session": session.session, "kind": "body", "replacement": body})
        except Diagnostic:
            continue
        chosen.append((f.name, body))
    return chosen


def transcript(source: str, symbol: str, body: str, scope: str, wire: str, host: EditHost) -> list[tuple]:
    """(direction, kind, message): `in` is what the model reads, `out` what it writes."""
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

    messages = [("in", "packet", packet)]
    for request in [ask(wrong)]:
        messages += [("out", "reply", request), ("in", "diagnostic", answer(request))]
    callees = [n for n in sorted(packet["dependencies"]) if n not in packet.get("callers", []) and "." not in n]
    if scope == "focused" and callees:  # Read the first callee written in this program before relying on it.
        grow = {"protocol": HANDLES, "handle": packet["handle"], "kind": "expand", "symbols": callees[:1]}
        messages += [("out", "reply", grow), ("in", "expansion", host.respond(grow))]
    request = ask(body)
    receipt = answer(request)
    assert receipt["status"] == "typed", (symbol, receipt)
    return [*messages, ("out", "reply", request), ("in", "admission", receipt)]


def account(runs: list[list[tuple]], count) -> dict:
    """Totals over conversations: each message once, and as read with the whole conversation before it."""
    sent = {"in": 0, "out": 0}
    kinds: dict[str, int] = {}
    read = turns = 0
    for conversation in runs:
        seen = 0
        for direction, kind, message in conversation:
            size = count(stable_json(message))
            sent[direction] += size
            kinds[kind] = kinds.get(kind, 0) + size
            seen += size
            read += seen if direction == "in" else 0  # The model rereads everything up to each message it reads.
            turns += direction == "out"
    return {"model_reads_once": sent["in"], "model_writes": sent["out"], "total_once": sent["in"] + sent["out"],
            "reads_with_history": read, "model_turns": turns, "by_kind": dict(sorted(kinds.items()))}  # fmt: skip


def conversations(root: Path, programs: list[str], per_program: int) -> list[tuple[str, str, list[str], list]]:
    """(program, setting, tasks, conversations): every transcript, built once whatever it is counted in."""
    out = []
    for path in programs:
        source = load_project(root / path).source
        chosen = tasks(source, per_program)
        for scope, wire, warmth in SETTINGS:
            host = EditHost()
            runs = []
            for symbol, body in chosen:
                host = host if warmth == "warm" else EditHost()
                runs.append(transcript(source, symbol, body, scope, wire, host))
            # A warm host is one conversation per program; cold hosts are one per task.
            talks = [[m for r in runs for m in r]] if warmth == "warm" else runs
            out.append((path, f"{scope} {wire} {warmth}", [s for s, _ in chosen], talks))
    return out


def measure_tasks(runs: list[tuple[str, str, list[str], list]], count) -> dict:
    rows = []
    totals: dict[str, list] = {f"{scope} {wire} {warmth}": [] for scope, wire, warmth in SETTINGS}
    for path, key, chosen, talks in runs:
        totals[key] += talks
        rows.append({"program": path, "setting": key, "tasks": chosen, **account(talks, count)})
    baseline = account(totals["component edit/1 cold"], count)
    summary = {}
    for key, talks in totals.items():
        a = account(talks, count)
        summary[key] = {**a, "vs_1_3": {k: round(a[k] / baseline[k], 3) for k in a if k != "by_kind"}}
    return {"rows": rows, "summary": summary, "task_count": sum(len(r["tasks"]) for r in rows) // len(SETTINGS)}


def remember(compile):
    """`compile`, asked once per distinct call: a result is shared, a refusal is raised afresh each time."""
    seen: dict = {}

    def once(*args, **kwargs):
        key = (args, tuple(sorted(kwargs.items())))
        if key not in seen:
            try:
                seen[key] = (True, compile(*args, **kwargs))
            except Diagnostic as e:
                seen[key] = (False, e.data)
        ok, value = seen[key]
        if ok:
            return value
        error = Diagnostic(value["code"], value["message"])
        error.data = copy.deepcopy(value)  # A host writes where in the reply it is into its own copy.
        raise error

    return once


def compile_once() -> None:
    """Every transcript recompiles the same few programs, so the process asks the compiler once per source text.
    The answers, results and refusals alike, are the compiler's own."""
    import cairn.agent.agent_tools as host

    host.compile_source = remember(host.compile_source)
    host.compile_program = remember(host.compile_program)


def measure_cards(count) -> dict:
    prior = json.loads((ROOT / "tools/ai/cards_05.json").read_text())
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


DIAGNOSED = """struct Frame { head:u8; body:Buf[u8]; }
fn send(f:Frame) -> usize = len(f.body);
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}
fn relay(n:usize, bytes:ro<u8>[n]) -> u32 {
  let f = Frame(1, Buf[u8](n));
  let sent = send(f);
  return checksum(bytes) + u32(sent);
}
"""
WRONG = {  # A representative wrong body per diagnostic an edit commonly meets, for relay.
    "E-TYPE-MISMATCH": "{ let f = Frame(1, Buf[u8](n)); let sent:bool = send(f); return checksum(bytes); }",
    "E-UNBOUND": "{ return checksum(bytez); }",
    "E-MOVED": "{ let f = Frame(1, Buf[u8](n)); let a = send(f); let b = send(f); return checksum(bytes); }",
    "E-SHADOW": "{ let n = 2; return checksum(bytes); }",
    "E-RETURN": "{ if n > 0 { return checksum(bytes); } }",
    "E-PARSE": "{ return checksum(bytes) }",
    "E-EFFECT-EXPANSION": "{ stack pad:u8[4] = zeroed; return checksum(bytes) + checksum(pad); }",
    "E-CALLEE": "{ return crc32(bytes); }",
}


def measure_state(runs: list[tuple[str, str, list[str], list]], root: Path, count) -> dict:
    """What a model resuming a program's work reads: the whole warm focused conversation so far, or the state."""
    out = {}
    for path, key, _, talks in runs:
        if key == "focused edit/2 warm":
            history = sum(count(stable_json(message)) for talk in talks for _, _, message in talk)
            out[path] = {"history": history, "state": count(stable_json(state(load_project(root / path).source)))}
    return out


def measure_diagnostics(count) -> dict:
    """What a model reads back for one wrong reply of each kind, from a host that already sent the packet."""
    host = EditHost()
    host.open(DIAGNOSED, "relay", contract={"allowed_effects": ["alloc", "ffi_precondition", "free", "read:bytes",
                                                                "trap", "zero_init"]})  # fmt: skip
    sizes = {}
    for code, body in WRONG.items():
        reply = host.reply(stable_json({"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": body}))
        assert reply.get("code") == code, (code, reply)
        sizes[code] = count(stable_json(reply))
    return {"sizes": sizes, "total": sum(sizes.values())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tiktoken", default=TOKENIZER, help="The offline tiktoken encoding to count tokens with.")
    ap.add_argument("--programs", type=Path, default=ROOT, help="Read the example programs from this tree.")
    ap.add_argument("--quick", action="store_true", help="Two programs, two tasks each: the shape, not the record.")
    ap.add_argument("--output", type=Path, default=ROOT / "results/context/context.json")
    args = ap.parse_args()
    compile_once()
    counters = {"utf8_bytes": lambda s: len(s.encode("utf-8"))}
    found = tokenizer(args.tiktoken)
    if found:
        counters[found[0]] = found[1]
    counters = {name: functools.lru_cache(maxsize=None)(count) for name, count in counters.items()}
    unit = found[0] if found else "utf8_bytes"  # what the card breakdowns count in
    runs = conversations(args.programs, PROGRAMS[:2] if args.quick else PROGRAMS, 2 if args.quick else PER_PROGRAM)
    result = {
        "model_trials": 0,
        "units": list(counters),
        "tokenizer": unit if found else f"tiktoken/{args.tiktoken} is not available offline; bytes only",
        "method": __doc__.strip(),
        **({"quick": "two programs, two tasks each; not a record"} if args.quick else {}),
        "tasks": {name: measure_tasks(runs, count) for name, count in counters.items()},
        "card_sizes": {name: counters[unit](text) for name, text in CARDS.items()},
        "diagnostic_sizes": measure_diagnostics(counters[unit]),
        "resume": measure_state(runs, args.programs, counters[unit]),
        **measure_cards(counters[unit]),
        "limitations": [
            "Authored transcripts, not a model: one type error, at most one expansion, then the right body.",
            "A real agent may expand more, or less; the focused packet's saving shrinks with every expansion.",
            "UTF-8 bytes are not model tokens; a tiktoken encoding is one real BPE vocabulary, not every model's.",
            "Reading with history charges a whole reread per model turn; prompt caching changes that price.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text(result))
    summary = {name: result["tasks"][name]["summary"] for name in counters}
    print(text({"tokenizer": result["tokenizer"], "aggregate": result["aggregate"], "tasks": summary,
                "cards": sum(result["card_sizes"].values()), "diagnostics": result["diagnostic_sizes"]["total"],
                "current_only": [{k: r[k] for k in ("symbol", "reason")} for r in result["current_only"]]}))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure actual packets, patches and identity edits, not model performance."""

import json
import statistics
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(R / "src"))
from cairn.agent.agent_tools import EditSession, stable_json
from cairn.agent.projection import canonical_source
from cairn.agent.teaching import CARDS
from cairn.compiler.cairnc import Parser, compile_source


def tokens(text):
    return len(text.encode("utf-8"))


def main():
    rows = []
    durations = []
    native = (R / "examples/basics/native.cairn").read_text()
    generated, _ = compile_source(native)
    for f in (f for f in Parser(native).parse().functions if not f.static):
        s = EditSession(native, f.name)
        for site, info in s.sites.items():
            req = {"protocol": "cairn.edit/1", "session": s.session, "kind": "expr", "site": site,
                   "replacement": info["source"]}  # fmt: skip
            start = time.perf_counter()
            candidate, _ = s.check(req)
            durations.append(time.perf_counter() - start)
            assert compile_source(candidate)[0] == generated
    identity = len(durations)
    for name, source, symbol in [
        ("native_module", native, "compact_even"),
        ("small_demo", (R / "examples/agent/selection_before.cairn").read_text(), "select_gt"),
        (
            "constructed_100_independent_functions",
            "\n".join(f"fn unit_{i}(x:u64)->u64{{return add_wrap(x,{i});}}" for i in range(100)) + "\n",
            "unit_0",
        ),
    ]:
        session = EditSession(source, symbol)
        packet = session.packet()
        context = packet["types"] + "\n" + "\n".join(c["source"] for c in packet["context"])
        body = source[session.f.body_start : session.f.end]
        req = {**packet["draft_protocol"], "replacement": body}
        source_and_cards = source + "\n" + "\n".join(packet["rule_cards"].values())
        rows.append(
            {
                "case": name,
                "module_source_tokens": tokens(source),
                "projected_source_tokens": tokens(context),
                "complete_packet_tokens": tokens(stable_json(packet)),
                "source_plus_same_cards_tokens": tokens(source_and_cards),
                "body_tokens": tokens(body),
                "complete_body_edit_tokens": tokens(stable_json(req)),
                "visible_functions": len(session.visible),
                "total_functions": len(session.receipt["functions"]),
                "note": "Constructed lexical packet, not model-read cost or measured comprehension.",
            }
        )
    (R / "results/agent").mkdir(parents=True, exist_ok=True)
    (R / "results/agent/agent_metrics.json").write_text(
        json.dumps(
            {
                "tokenizer": "ByT5 plain UTF-8 byte-token mapping; no special tokens",
                "frontier_BPE_available": False,
                "model_runs": 0,
                "packet_comparisons": rows,
                "teaching_cards_tokens": {k: tokens(v) for k, v in CARDS.items()},
                "full_teaching_cards_tokens": tokens("\n\n".join(CARDS.values())),
                "canonical_projection_tokens": tokens(canonical_source(native)),
                "identity_expression_edits": identity,
                "all_identity_edits_preserve_generated_cpp": True,
                "check_seconds_median": statistics.median(durations),
                "check_seconds_max": max(durations),
                "timing_scope": "Python edit check on the provided module on a shared host; not model or native program speed.",
            },
            indent=2,
        )
        + "\n"
    )
    print("Identity expression edits:", identity)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()

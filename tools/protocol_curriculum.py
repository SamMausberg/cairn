#!/usr/bin/env python3
"""Generate executed, tool-format-aligned repair lessons from audited tasks.

The adapter actions here are authored fixtures, not model outputs. Every good
choice passes fresh semantic checking. The task and slot map do not change.
"""

import argparse
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(R / "src")]
from cairn.cairnc import Parser
from cairn.sketches import ScalarContract, Sketch, public_feedback


def expression(source):
    f = Parser(source).parse().functions[0]
    if len(f.body) != 1 or f.body[0].tag != "return":
        return None
    e = f.body[0].exprs[0]
    return source[e.start : e.end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=R / "training/semantic", help="Audited corpus in, lessons out.")
    root = p.parse_args().root
    rows = json.loads((root / "audit.json").read_text())
    lessons = []
    for r in rows:
        yes, no = expression(r["chosen"]), expression(r["rejected"])
        if yes is None or no is None:
            continue
        sk = Sketch(
            r["rejected"],
            "task",
            task={"symbol": "task", "task": r["task"]},
            semantic=ScalarContract(r["reference"], "task"),
        ).hole("value", no)
        packet = sk.packet()
        bad = json.dumps({"value": no})
        good = json.dumps({"value": yes})
        bad_candidate = sk.fill_json(bad)
        bad_receipt = sk.check_semantics(bad_candidate, timeout_ms=10000)
        good_candidate = sk.fill_json(good)
        good_receipt = sk.check_semantics(good_candidate, timeout_ms=10000)
        assert bad_receipt["status"] == "counterexample", (r["id"], bad_receipt)
        assert good_receipt["status"] == "smt-equivalent", (r["id"], good_receipt)
        lessons.append(
            {
                "id": r["id"],
                "family": r["family"],
                "split": r["split"],
                "provenance": "synthetic-executed-repair-fixture-not-model",
                "messages": [
                    {"role": "user", "content": json.dumps(packet, separators=(",", ":"))},
                    {"role": "assistant", "content": bad},
                    {"role": "tool", "content": json.dumps(public_feedback(bad_receipt), separators=(",", ":"))},
                    {"role": "assistant", "content": good},
                ],
                "immutable_contract_sha256": r["contract_sha256"],
                "chosen_semantic_receipt": good_receipt,
                "rejected_semantic_receipt": bad_receipt,
            }
        )
    (root / "protocol_audit.json").write_text(json.dumps(lessons, indent=2) + "\n")
    train = [x for x in lessons if x["split"] == "training"]
    evaluation = [x for x in lessons if x["split"] == "evaluation"]
    # Do not export the wrong assistant turn as an SFT target. It is feedback
    # context only. Two role messages carry that completed prior interaction.
    sft = []
    for r in train:
        context = (
            r["messages"][0]["content"]
            + "\nPrevious proposal: "
            + r["messages"][1]["content"]
            + "\nChecker feedback: "
            + r["messages"][2]["content"]
        )
        sft.append(
            {
                "id": r["id"],
                "family": r["family"],
                "messages": [{"role": "user", "content": context}, r["messages"][3]],
                "target_status": "smt-equivalent-scalar-only",
                "contract_sha256": r["immutable_contract_sha256"],
            }
        )
    (root / "train_repair_sft.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sft))
    (root / "protocol_evaluation_prompts.jsonl").write_text(
        "".join(
            json.dumps({"id": r["id"], "family": r["family"], "messages": [r["messages"][0]]}) + "\n"
            for r in evaluation
        )
    )
    summary = {
        "status": "passed",
        "executed_protocol_lessons": len(lessons),
        "training_repair_targets": len(train),
        "evaluation_prompts": len(evaluation),
        "algorithm_families": len({r["family"] for r in lessons}),
        "wrong_turns_never_sft_targets": True,
        "strict_production_choice_parser_used": True,
        "fresh_semantic_checks": 2 * len(lessons),
        "model_or_training_run": False,
    }
    (root / "protocol_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

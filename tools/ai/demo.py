#!/usr/bin/env python3
"""Reproduce a scripted edit/repair transcript. Does not call or train an LLM."""

import argparse
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R / "src"), str(R / "tools")]
from ai.agent_loop import run
from cairn.agent.agent_tools import EditSession
from cairn.compiler.cairnc import compile_source


def main():
    base = R / "examples/agent"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=base, help="Where the transcript files are written.")
    out = p.parse_args().out
    out.mkdir(parents=True, exist_ok=True)
    source = (base / "selection_before.cairn").read_text()
    _, receipt = compile_source(source)
    values = [[], [2], [0, 2, 3, 2, 9], [2, 2, 2], [0, 1], [9, 8, 7], [2**64 - 1, 0, 2], list(range(19))]
    picked = [[v for v in x if v > 2] for x in values]
    cases = [{"args": {"n": len(x), "out": [123] * len(x), "x": x, "threshold": 2}, "return": len(chosen),
              "after": {"out": chosen + [123] * (len(x) - len(chosen))}}
             for x, chosen in zip(values, picked, strict=True)]  # fmt: skip
    task = {
        "schema": "cairn.task/1",
        "symbol": "select_gt",
        "task": "Stably select values STRICTLY greater than threshold. Return selected count. Preserve the unwritten output tail. Do not mutate the input.",
        "allowed_effects": receipt["functions"]["select_gt"]["effects"],
        "cases": cases,
    }
    (out / "task.json").write_text(json.dumps(task, indent=2) + "\n")
    session = EditSession(source, "select_gt", task)
    (out / "packet.json").write_text(json.dumps(session.packet(), indent=2) + "\n")
    body = "{ let used=compact out for i in n where x[i]>threshold yield x[i]; return used; }"
    edit = {"protocol": "cairn.edit/1", "session": session.session, "kind": "body", "replacement": body}
    candidate, typed = session.check(edit)
    (out / "edit.json").write_text(json.dumps(edit, indent=2) + "\n")
    (out / "selection_after.cairn").write_text(candidate)
    (out / "typed_receipt.json").write_text(json.dumps(typed, indent=2) + "\n")
    actual, trace = run(source, task, [sys.executable, str(base / "scripted_adapter.py")], 3, 3, "scripted-fixture")
    assert actual == candidate
    assert trace["status"] == "passed-reserved-finite-tests" and trace["attempts"] == 3
    assert trace["trace"][0]["feedback"]["code"] == "E-TYPE-MISMATCH"
    assert trace["trace"][1]["feedback"]["tests"]["status"] == "failed-tests"
    (R / "results/agent").mkdir(parents=True, exist_ok=True)
    (R / "results/agent/scripted_demo.json").write_text(json.dumps(trace, indent=2) + "\n")
    print(json.dumps({k: v for k, v in trace.items() if k != "trace"}, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""The implementation loop on this project, with scripted agent replies.

The host opens an implementation session on `prefix` under the pinned policy in policy.json. The scripted agent
submits a blocked prefix sum that restarts each block at zero; validation refuses it with the smallest input it
could shrink the failure to, and keeps that input in regressions/prefix.json. The agent then asks for a looser
tolerance and for fewer permitted inputs, and the host refuses both. Last, it submits the repaired version, which
validates. The replies are fixed text, replayed; everything the host, the compiler and the native runs say is
computed on each run. Prints the transcript as JSON.

    python3 examples/implementations/loop.py > transcript.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from cairn.agent.implementations import PROTOCOL, ImplementationHost  # noqa: E402
from cairn.projects.project import load_project  # noqa: E402


def submission(source: str, **extra) -> str:
    return json.dumps({"protocol": PROTOCOL, "handle": "i1", "kind": "submit", "source": source, **extra})


def main() -> int:
    project = load_project(HERE)
    policy = json.loads((HERE / "policy.json").read_text())
    wrong = (HERE / "candidates/prefix_blocks_wrong.cairn").read_text()
    fixed = (HERE / "candidates/prefix_blocks.cairn").read_text()
    history: list[dict] = []
    host = ImplementationHost(regressions=HERE / "regressions/prefix.json", history=history.append)
    packet = host.open(project.source, "prefix", policy)
    steps = [
        ("a blocked prefix sum that restarts each block", submission(wrong)),
        ("the same, with a looser tolerance", submission(wrong, tolerance={"absolute": 64.0, "relative": 0.0})),
        ("the same, admitting only one block", submission(wrong, domain={"largest_extent": 8})),
        ("the repaired blocked prefix sum", submission(fixed)),
    ]
    transcript = {"packet": {k: packet[k] for k in ("handle", "reference", "pinned", "reply")}, "exchange": []}
    for what, text in steps:
        answer = host.reply(text)
        kept = {k: answer[k] for k in ("code", "message", "repair_hint", "finite", "status", "implementation",
                                       "identity", "smt", "select_with") if k in answer}  # fmt: skip
        transcript["exchange"].append({"agent": what, "host": kept})
    transcript["history"] = history
    print(json.dumps(transcript, indent=2).replace(str(HERE), "examples/implementations"))
    codes = [step["host"].get("code", step["host"].get("status")) for step in transcript["exchange"]]
    return 0 if codes == ["E-VALIDATION", "E-TOLERANCE", "E-DOMAIN", "validated"] else 1


if __name__ == "__main__":
    sys.exit(main())

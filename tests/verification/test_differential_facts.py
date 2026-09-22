"""`compiler/facts.py` and `proofs/Cairn/Facts.lean` must decide the same guard sites alike.

The Lean side is proved sound: a discharged index is in bounds, a discharged `+` or `-` cannot trap, a value
discharged below a constant is at most it. This comparison is what ties that proof to the Python that lowering runs.
A small run is part of `make test`; `CAIRN_DIFFERENTIAL_N` asks for a large one. The second test plants a slip in
`facts.py` and requires the comparison to report it, because a comparison that cannot fail is not evidence.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "checks" / "differential_facts.py"
UNAVAILABLE = 3


def test_the_rule_and_its_lean_model_decide_alike():
    count = int(os.environ.get("CAIRN_DIFFERENTIAL_N", 300))
    done = subprocess.run(
        [sys.executable, str(HARNESS), "--count", str(count)], cwd=ROOT, capture_output=True, text=True, timeout=3600
    )
    if done.returncode == UNAVAILABLE:
        pytest.skip("lake is not installed; see tests/verification/test_lean_proofs.py for how to install it")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    report = json.loads(done.stdout)
    assert report["status"] == "agreed" and report["inputs"] == count
    assert all(report["discharged"].values()), report["discharged"]  # every decision says yes somewhere


def test_a_slip_in_the_rule_would_be_reported(monkeypatch):
    from checks import differential_facts as harness

    lake = harness.find_lake()
    if lake is None:
        pytest.skip("lake is not installed")

    def off_by_one(c, e):  # an index at most its extent, where the rule needs below it
        n = harness.F.extent(c, e.args[0])
        return n is not None and any(harness.F.at_most(c, x, n, 0) for x in harness.F.bounds(c, e.args[1])[0])

    monkeypatch.setattr(harness.F, "index", off_by_one)
    with tempfile.TemporaryDirectory() as scratch:
        report = harness.compare(300, 1, lake, Path(scratch) / "Facts.lean", 600)
    assert report["status"] == "disagreed" and report["disagreements"]

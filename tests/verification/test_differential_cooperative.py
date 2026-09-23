"""`compiler/phases.py` and `proofs/Cairn/Cooperative.lean` must decide the same generated cooperative regions alike.

The Lean side proves that a phase its rule accepts has no clashing steps in any interleaving and one result whatever
order the threads take. This comparison is what ties that proof to the Python the checker runs. A small run is part
of `make test`; `CAIRN_DIFFERENTIAL_N` asks for a large one. The second test plants a slip in `phases.py`, a rule
that forgets reads, and requires the comparison to report it, because a comparison that cannot fail is not evidence.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "checks" / "differential_cooperative.py"
UNAVAILABLE = 3


def test_the_phase_rule_and_its_lean_model_decide_alike():
    count = int(os.environ.get("CAIRN_DIFFERENTIAL_N", 200))
    done = subprocess.run(
        [sys.executable, str(HARNESS), "--count", str(count)], cwd=ROOT, capture_output=True, text=True, timeout=3600
    )
    if done.returncode == UNAVAILABLE:
        pytest.skip("lake is not installed; see tests/verification/test_lean_proofs.py for how to install it")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    report = json.loads(done.stdout)
    assert report["status"] == "agreed" and report["inputs"] == count
    assert 0 < report["accepted"] < count, report  # both answers occur, so agreement says something


def test_a_slip_in_the_rule_would_be_reported(monkeypatch):
    from cairn.compiler import phases
    from checks import differential_cooperative as harness

    lake = harness.find_lake()
    if lake is None:
        pytest.skip("lake is not installed")
    original = phases.Phases.refuse

    def writes_only(self, array, element, x, y):  # a rule that lets a read beside another thread's write through
        if x[1].write and y[1].write:
            original(self, array, element, x, y)

    monkeypatch.setattr(phases.Phases, "refuse", writes_only)
    with tempfile.TemporaryDirectory() as scratch:
        report = harness.compare(200, 1, lake, Path(scratch) / "Cooperative.lean", 1800)
    assert report["status"] == "disagreed" and report["disagreements"]

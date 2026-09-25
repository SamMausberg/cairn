"""`compiler/device/layout_algebra.py` and `proofs/Cairn/Layout.lean` must judge the same layouts alike.

The Lean side proves that a spread that passes gives every element of its tile one holder, and that with a storage
layout that passes no two holders write one offset. This comparison is what ties that proof to the Python the
checker runs. A small run is part of `make test`; `CAIRN_DIFFERENTIAL_N` asks for a large one. The second test plants
a slip in `layout_algebra.py` and requires the comparison to report it, because a comparison that cannot fail is not
evidence.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "checks" / "differential_layouts.py"
UNAVAILABLE = 3


def test_the_rule_and_its_lean_model_judge_alike():
    count = int(os.environ.get("CAIRN_DIFFERENTIAL_N", 300))
    done = subprocess.run(
        [sys.executable, str(HARNESS), "--count", str(count)], cwd=ROOT, capture_output=True, text=True, timeout=3600
    )
    if done.returncode == UNAVAILABLE:
        pytest.skip("lake is not installed; see tests/verification/test_lean_proofs.py for how to install it")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    report = json.loads(done.stdout)
    assert report["status"] == "agreed" and report["inputs"] == count
    assert set(report["verdicts"]) == {"ok", "gap", "overlap", "outside", "clash", "distinct"}, report["verdicts"]


def test_a_slip_in_the_rule_would_be_reported(monkeypatch):
    from checks import differential_layouts as harness

    lake = harness.find_lake()
    if lake is None:
        pytest.skip("lake is not installed")
    real = harness.L.Cover.overlap

    def lenient(self):  # an element held twice by one participant's two values would pass
        e = real.fget(self)
        return None if e is not None and len({t for t, _ in self.holders[e]}) == 1 else e

    monkeypatch.setattr(harness.L.Cover, "overlap", property(lenient))
    with tempfile.TemporaryDirectory() as scratch:
        report = harness.compare(300, 1, lake, Path(scratch) / "Layouts.lean", 600)
    assert report["status"] == "disagreed" and report["disagreements"]

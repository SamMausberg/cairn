"""The Python checker and the Lean ownership calculus must classify the same programs alike.

`tools/checks/differential_ownership.py` generates programs in the fragment both of them
understand, renders each one as CAIRN source and as a Lean ``Program``, and compares the two
verdicts.  A small run is part of ``make test``; ``CAIRN_DIFFERENTIAL_N`` asks for a large one.

The harness is only evidence while a disagreement would actually be reported, so the second
test plants one and requires it to come back.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools" / "checks" / "differential_ownership.py"
DEFAULT_COUNT = 40
UNAVAILABLE = 3  # the harness says so rather than passing: unknown is never success


def test_the_two_checkers_agree_on_generated_programs():
    count = int(os.environ.get("CAIRN_DIFFERENTIAL_N", DEFAULT_COUNT))
    done = subprocess.run(
        [sys.executable, str(HARNESS), "--count", str(count)], cwd=ROOT, capture_output=True, text=True, timeout=3600
    )
    if done.returncode == UNAVAILABLE:
        pytest.skip(
            "lake is not installed: nothing named 'lake' on PATH and no ~/.elan/bin/lake. "
            "Install with: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y "
            "--default-toolchain none"
        )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-8000:]
    report = json.loads(done.stdout)
    assert report["status"] == "agreed", json.dumps(report, indent=2)
    assert report["programs"] == count
    # A generator that accepted everything, or refused everything, would agree for no reason.
    assert report["python_accepted"] > 0 and report["python_rejected"] > 0, json.dumps(report["codes"])
    assert set(report["codes"]) - {"accepted"}, "no program was refused for an ownership reason"


def test_a_disagreement_would_be_reported():
    from checks import differential_ownership as harness

    lake = harness.find_lake()
    if lake is None:
        pytest.skip("lake is not installed; see the previous test for how to install it")
    real = harness.python_verdict
    flipped = {"count": 0}

    def flip(source):
        ok, code = real(source)
        flipped["count"] += 1
        if flipped["count"] != 1:  # Exactly one program is answered wrongly.
            return ok, code
        return (False, "E-LEASED") if ok else (True, "")

    with tempfile.TemporaryDirectory() as scratch:
        target = Path(scratch) / "Differential.lean"
        honest = harness.compare(8, 5, lake, target, 600)
        assert honest["status"] == "agreed", json.dumps(honest, indent=2)
        harness.python_verdict = flip
        try:
            planted = harness.compare(8, 5, lake, target, 600)
        finally:
            harness.python_verdict = real
    assert planted["status"] == "disagreed"
    assert planted["disagreements"], "the flipped verdict was not reported"
    first = planted["disagreements"][0]
    assert "fn main() -> i32" in first["cairn"] and "Program" in first["lean_program"]

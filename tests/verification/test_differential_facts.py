"""`compiler/check/facts.py` and `proofs/Cairn/Facts.lean` must decide the same guard sites alike.

The Lean side is proved sound: a discharged index is in bounds, a discharged `+`, `x * C` or `-` cannot trap, a value
discharged below a constant is at most it, and the facts `let q = a / C` adds are true. This comparison is what ties
that proof to the Python that lowering runs. A small run is part of `make test`; `CAIRN_DIFFERENTIAL_N` asks for a
large one. The other tests plant a slip in `facts.py` and require the comparison to report it, because a comparison
that cannot fail is not evidence: among them, the two ways `times` differed from Facts.lean before they agreed, a
product atom times a constant named as an atom and two constants left unfolded.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.compiler.check import facts as F
from support import find_lake

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

    lake = find_lake()
    if lake is None:
        pytest.skip("lake is not installed")

    def off_by_one(c, e):  # an index at most its extent, where the rule needs below it
        n = harness.F.extent(c, e.args[0])
        return n is not None and any(harness.F.at_most(c, x, n, 0) for x in harness.F.bounds(c, e.args[1])[0])

    monkeypatch.setattr(harness.F, "index", off_by_one)
    with tempfile.TemporaryDirectory() as scratch:
        report = harness.compare(300, 1, lake, Path(scratch) / "Facts.lean", 600)
    assert report["status"] == "disagreed" and report["disagreements"]


def under_lower_bounds(real):
    """q*C at most each lower bound of the dividend too, where only its upper bounds hold."""

    def quotient(c, name, value):
        added = real(c, name, value)
        return [*added, *((added[0][0], x, j) for x, j in F.bounds(c, value.args[0])[1])] if added else []

    return quotient


def uncapped(real):
    """Any atom times a positive constant, whether or not the facts cap the product."""

    def product(c, e):
        t = F.times(c, *e.args)
        return t is not None and t[0].count("*") == 1

    return product


def one_past(real):
    """A sum's lower bounds each one higher than its sides give."""

    def bounds(c, e):
        high, low = real(c, e)
        return high, [(x, k + 1) if e.tag == "binary" and e.val == "+" and x else (x, k) for x, k in low]

    return bounds


def nested(real):
    """A product atom times a constant named as an atom of its own (`x0*256*2`), which Facts.lean names nothing."""

    def times(c, a, b):
        (x, j), (y, k) = (F.exact(c, e) or (None, 0) for e in (a, b))
        return (f"{x}*{k}", 0) if x and "*" in x and y == F.ZERO and not j and k > 0 else real(c, a, b)

    return times


def unfolded(real):
    """Two constants multiplied and left unfolded, where Facts.lean folds them."""

    def times(c, a, b):
        both = all((F.exact(c, e) or (None, 0))[0] == F.ZERO for e in (a, b))
        return None if both else real(c, a, b)

    return times


SLIPS = {  # a slip's name: the function of facts.py it replaces, and the slip
    "quotient": ("quotient", under_lower_bounds),
    "product": ("product", uncapped),
    "bounds": ("bounds", one_past),
    "nested": ("times", nested),
    "unfolded": ("times", unfolded),
}


@pytest.mark.parametrize("slip", SLIPS)
def test_a_slip_in_a_new_rule_would_be_reported(slip, monkeypatch):
    from checks import differential_facts as harness

    lake = find_lake()
    if lake is None:
        pytest.skip("lake is not installed")
    rule, planted = SLIPS[slip]
    monkeypatch.setattr(harness.F, rule, planted(getattr(harness.F, rule)))
    with tempfile.TemporaryDirectory() as scratch:
        report = harness.compare(300, 1, lake, Path(scratch) / "Facts.lean", 600)
    assert report["status"] == "disagreed" and report["disagreements"]

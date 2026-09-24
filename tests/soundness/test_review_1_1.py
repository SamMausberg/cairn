"""The adversarial review of what landed after 1.0.0: each defect it found pinned by the program or request that showed
it, and the attacks that were correctly refused or held, kept so that a later change cannot reopen them.

`evidence/v1_1/review/README.md` has the write-up.
"""

import pytest

from cairn.compiler.cairnc import Diagnostic, compile_program

# --- Fixed: a check that reports every refusal judged a row an implementation had not yet joined -------------------

SCALE = """fn scale(n:usize, xs:rw<u64>[n]) effects(pure, write:xs, par:host) {
  for i in 0..n { xs[i] = xs[i] * 2; }
}

fn scale_lanes(n:usize, xs:rw<u64>[n]) implements scale {
  parallel i in n { xs[i] = xs[i] * 2; }
}

plan scale use scale_lanes;

fn g(n:usize, xs:rw<u64>[n]) effects(pure, write:xs) {
  scale(n, xs);
}
"""
BROKEN = """
fn broken() -> u64 {
  return missing;
}
"""


def every(source: str) -> dict:
    with pytest.raises(Diagnostic) as error:
        compile_program(source, every=True)
    assert error.value.abandoned is None
    return error.value.data


def test_a_caller_of_a_reference_is_not_judged_on_a_row_its_implementations_have_not_joined():
    """A reference's row joins its implementations' only once the implementation rules have run, and a check with a
    refusal never runs them. `g` exceeds its ceiling through `scale_lanes`, which a clean check refuses; beside an
    unrelated refusal, `g` was reported neither as refused nor as not judged, so the record implied a verdict it did
    not reach."""
    with pytest.raises(Diagnostic) as alone:
        compile_program(SCALE)
    assert alone.value.data["code"] == "E-EFFECT-CEILING" and alone.value.data["line"] == 11
    record = every(SCALE + BROKEN)
    assert record["code"] == "E-UNBOUND"
    reported = [(d["code"], d["line"]) for d in record.get("further", [])]
    assert ("E-EFFECT-CEILING", 11) in reported or record.get("not_judged", 0) >= 2, record

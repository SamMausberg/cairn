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


# --- Fixed: the fix for a type mismatch said a float conversion traps, which it never does --------------------------


@pytest.mark.parametrize(
    ("want", "got"), [("f32", "u64"), ("f32", "f64"), ("f64", "u64"), ("u32", "u64"), ("u8", "f32")]
)
def test_the_conversion_a_type_mismatch_suggests_is_stated_as_it_behaves(want, got):
    """`f32(x)` rounds, to infinity past the range, and `f64(x)` of a u64 past 2^53 rounds too; only an integer
    target is range checked (docs/language.md). The fix said every conversion traps outside its target's range, which
    told an agent a rounding conversion was checked."""
    from cairn.agent.diagnostics import fix

    hint = fix({"code": "E-TYPE-MISMATCH", "message": "", "expected_type": want, "actual_type": got})
    assert hint is not None and f"{want}(x)" in hint
    if want in {"f32", "f64"}:
        assert "trap" not in hint and "round" in hint, hint
    else:
        assert "traps outside" in hint, hint


# --- Fixed: the parallel card left out what keeps a function from its one wait and its cq_NAME entry ---------------


def test_the_parallel_card_names_everything_that_keeps_a_function_waiting():
    """compiler/execution.py waits after each operation when a row holds an atomic, a lock, a call through a function
    value, a machine register or host assembly, and gives no cq_NAME entry. The card listed only transfers, device
    allocation, tickets, I/O, foreign calls and @unified views, so an agent could expect an enqueued entry for a
    function that takes a lock."""
    from cairn.agent.teaching import CARDS

    said = next(p for p in CARDS["parallel"].split("\n") if "cq_NAME" in p)
    for construct in ("atomic", "lock", "function value", "machine register", "host assembly", "@unified"):
        assert construct in said, construct

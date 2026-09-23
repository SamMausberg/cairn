"""Contract-driven validation: generated boundary inputs, each call in a process of its own against the reference,
a failing case shrunk and kept, and Z3's answer reported apart from the finite one.

The oracle is the reference, run on the same inputs: a right implementation passes, a wrong one fails at the
smallest input the shrinker reaches, a trap agrees only with a trap, a float result agrees only within the host's
tolerance, and a finite pass is labelled finite testing, never proof.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.compiler.cairnc import Parser, compile_program
from cairn.verify import boundaries
from cairn.verify.boundaries import Unsupported
from cairn.verify.isolated_calls import Param
from cairn.verify.validation import FINITE, REGRESSIONS, agree, replay, validate

ROOT = Path(__file__).resolve().parents[2]
TOTAL = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s += xs[i]; }
  return s;
}
"""
BY4 = """fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 {
  let mut a:u64 = 0;
  let mut b:u64 = 0;
  for k in 0..n / 4 { a += xs[4 * k] + xs[4 * k + 1]; b += xs[4 * k + 2] + xs[4 * k + 3]; }
  return a + b;
}
"""
# Wrong at every odd length.
PAIRS = """fn total_pairs(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for k in 0..n / 2 { s += xs[2 * k] + xs[2 * k + 1]; }
  return s;
}
"""
# Wrong only where the reference traps: it wraps instead.
WRAPS = """fn total_wraps(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for i in 0..n { s = add_wrap(s, xs[i]); }
  return s;
}
"""
DOT = """fn dot(n:usize, x:ro<f64>[n], y:ro<f64>[n]) -> f64 {
  let mut s:f64 = 0.0;
  for i in 0..n { s += x[i] * y[i]; }
  return s;
}
fn dot_pairs(n:usize, x:ro<f64>[n], y:ro<f64>[n]) -> f64 implements dot when n % 2 == 0 {
  let mut a:f64 = 0.0;
  let mut b:f64 = 0.0;
  for k in 0..n / 2 { a += x[2 * k] * y[2 * k]; b += x[2 * k + 1] * y[2 * k + 1]; }
  return a + b;
}
"""
SMALL = {"domain": {"largest_extent": 40}, "budget": 40}


def reference(source: str, name: str):
    return next(f for f in Parser(source).parse().functions if f.name == name)


def test_the_inputs_sit_at_the_tiles_the_contract_names_and_repeat_for_a_seed():
    source = TOTAL + BY4
    found = boundaries.tiles(None, reference(source, "total_by4"))
    assert found == {4: "the condition's % 4"}
    sizes = [n for n, _ in boundaries.sizes(found, 0, 40)]
    assert {0, 1, 3, 4, 5, 7, 8, 13, 40} <= set(sizes)
    cases = boundaries.generate(reference(source, "total"), found, {"largest_extent": 40}, budget=64, seed=3)
    again = boundaries.generate(reference(source, "total"), found, {"largest_extent": 40}, budget=64, seed=3)
    assert [c.key() for c in cases] == [c.key() for c in again] and len(cases) <= 64
    assert any(c.offsets for c in cases) and any(c.args["xs"] and max(c.args["xs"]) == 2**64 - 1 for c in cases)
    assert all(len(c.args["xs"]) == c.args["n"] for c in cases)


def test_the_plan_and_the_lane_pool_put_boundaries_where_they_split_the_work():
    source = """fn fill(n:usize, out:rw<u64>[n]) effects(pure, write:out, par:host) { for i in 0..n { out[i] = 1; } }
fn fill_lanes(n:usize, out:rw<u64>[n]) implements fill { parallel i in n { out[i] = 1; } }"""
    p = compile_program(source)[0]
    found = boundaries.tiles(p, next(f for f in p.functions if f.name == "fill_lanes"), {"grain": 64})
    assert found[64] == "plan grain 64" and found[boundaries.CUTOFF] == "the lane pool's cutoff"


def test_a_signature_the_validator_cannot_feed_is_unsupported():
    with pytest.raises(Unsupported):
        boundaries.signature(reference("fn f(b:Buf[u64]) -> usize = len(b);", "f"))
    record = validate("struct P { x:u64; }\nfn f(p:P) -> u64 = p.x;\nfn g(p:P) -> u64 implements f = p.x;\n", "f", "g")
    assert record["status"] == "unknown" and "scalars" in record["reason"]


def test_a_right_implementation_passes_as_finite_testing_and_z3_answers_apart(tmp_path):
    record = validate(TOTAL + BY4, "total", "total_by4", SMALL, regressions=tmp_path / "total.json")
    finite = record["finite"]
    assert record["status"] == finite["status"] == "passed" and finite["claim"] == FINITE
    assert 0 < finite["implementation_ran"] < finite["cases"] == finite["dispatch_ran"]
    assert record["smt"]["status"] in {"smt-equivalent", "unknown"} and "n <= 16" in record["smt"]["where"]
    assert record["smt"]["bounded"] and not (tmp_path / "total.json").exists()  # a pass keeps nothing


def test_a_wrong_implementation_fails_at_its_shrunk_input_which_is_kept_once(tmp_path):
    kept = tmp_path / "regressions" / "total.json"
    record = validate(TOTAL + PAIRS, "total", "total_pairs", SMALL, regressions=kept)
    failed = record["finite"]["failed"]
    assert record["status"] == "failed" and failed["inputs"] == {"n": 1, "xs": [1]}
    assert failed["reference"]["return"] == 1 and failed["implementation"]["return"] == 0
    text = kept.read_text()
    saved = json.loads(text)
    assert saved["schema"] == REGRESSIONS and saved["reference"] == "total" and len(saved["cases"]) == 1
    again = validate(TOTAL + PAIRS, "total", "total_pairs", SMALL, regressions=kept)
    assert again["finite"]["kept_cases"] == 1 and again["finite"]["cases"] == 1  # the kept case fails first
    assert kept.read_text() == text  # the same case is not kept twice, and the file does not move


def test_z3_s_answer_is_reported_apart_from_the_finite_one():
    source = "fn cap(x:u64, hi:u64) -> u64 { if x > hi { return hi; } return x; }\n"
    right = validate(
        source + "fn cap_min(x:u64, hi:u64) -> u64 implements cap = min(x, hi);\n", "cap", "cap_min", SMALL
    )
    wrong = validate(source + "fn cap_off(x:u64, hi:u64) -> u64 implements cap when hi > 0 { if x >= hi { return sub_wrap(hi, 1); } "
                     "return x; }\n", "cap", "cap_off", SMALL)  # fmt: skip
    assert right["status"] == "passed" and right["smt"] == {"status": "smt-equivalent", "where": "true"}
    assert wrong["status"] == "failed" and wrong["smt"]["status"] == "counterexample"
    assert wrong["smt"]["where"] == "((hi > 0))" and wrong["finite"]["failed"]["inputs"] == {"x": 1, "hi": 1}


def test_a_trap_agrees_only_with_a_trap():
    record = validate(TOTAL + WRAPS, "total", "total_wraps", SMALL)
    failed = record["finite"]["failed"]
    assert record["status"] == "failed" and failed["reference"]["outcome"] == "trap"
    assert failed["implementation"]["outcome"] == "return" and failed["inputs"]["n"] == 2


def test_a_float_result_agrees_within_the_host_s_tolerance_and_no_further():
    exact = validate(DOT, "dot", "dot_pairs", SMALL)
    loose = validate(DOT, "dot", "dot_pairs", {**SMALL, "tolerance": {"absolute": 0.0, "relative": 1e-9}})
    assert exact["status"] == "failed" and loose["status"] == "passed"
    failed = exact["finite"]["failed"]
    assert failed["reference"]["return"] != failed["implementation"]["return"]  # bits apart, as hexadecimal text


def test_an_implementation_no_case_reaches_is_unknown():
    never = BY4.replace("when n % 4 == 0", "when n == 123456789").replace("total_by4", "total_far")
    assert validate(TOTAL + never, "total", "total_far", SMALL)["status"] == "unknown"


def test_a_call_that_does_not_finish_decides_nothing():
    params = [Param("n", "scalar", "u64")]
    done = {"outcome": "return", "return": 1, "after": {}}
    assert agree(done, {"outcome": "timeout"}, "u64", params, {}) is None
    assert agree({"outcome": "trap"}, {"outcome": "trap"}, "u64", params, {}) is True
    assert agree({"outcome": "trap"}, {"outcome": "crash"}, "u64", params, {}) is False


def test_kept_cases_replay_against_every_implementation(tmp_path):
    kept = tmp_path / "total.json"
    validate(TOTAL + PAIRS, "total", "total_pairs", SMALL, regressions=kept)
    record = json.loads(kept.read_text())
    assert replay(TOTAL + BY4, record)["status"] == "passed-finite-tests"
    assert replay(TOTAL + BY4 + PAIRS, record)["status"] == "failed-tests"


def test_cairn_validate_and_cairn_test_on_the_example_project(tmp_path):
    from cairn.agent.history import History

    project = ROOT / "examples/implementations"
    done = subprocess.run([sys.executable, str(ROOT / "bin/cairn"), "validate", str(project), "--symbol", "prefix_by4",
                           "--history", str(tmp_path / "history"), "--format", "json"], capture_output=True, text=True,
                          timeout=600)  # fmt: skip
    record = json.loads(done.stdout)
    assert done.returncode == 0 and record["status"] == "passed", done.stderr
    assert record["regressions"] == {"file": "regressions/prefix.json", "exists": True, "replayed_by_cairn_test": True}
    kept = History(tmp_path / "history").records("prefix")
    assert [(r["id"], r["kind"], r["candidate"]) for r in kept] == [(record["history"], "validation", "prefix_by4")]
    assert kept[0]["variant"] == record["identity"] and kept[0]["detail"]["evidence"] == "finite-tested"
    done = subprocess.run([sys.executable, str(ROOT / "bin/cairn"), "test", str(project), "--format", "json"],
                          capture_output=True, text=True, timeout=600)  # fmt: skip
    tested = json.loads(done.stdout)
    assert done.returncode == 0 and tested["tests"][0]["status"] == "passed-finite-tests", done.stdout[-2000:]
    assert set(tested["tests"][0]["implementations"]) == {"prefix_by4", "prefix_lanes"}

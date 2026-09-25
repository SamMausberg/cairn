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
from cairn.verify.validation import boundaries
from cairn.verify.validation.boundaries import Unsupported
from cairn.verify.validation.isolated_calls import Param
from cairn.verify.validation.validation import FINITE, REGRESSIONS, Policy, agree, replay, validate

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


def test_the_relative_tolerance_scales_the_reference_on_the_host_as_on_the_device():
    source = "fn f(x:f64) -> f64 = 100.0;\nfn g(x:f64) -> f64 implements f = 111.0;\n"
    tenth = {"tolerance": {"absolute": 0.0, "relative": 0.1}}
    assert validate(source, "f", "g", tenth)["status"] == "failed"  # 11 > 0.1 * 100
    backwards = "fn f(x:f64) -> f64 = 111.0;\nfn g(x:f64) -> f64 implements f = 100.0;\n"
    assert validate(backwards, "f", "g", tenth)["status"] == "passed"  # 11 <= 0.1 * 111


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
    from cairn.agent.history import History, selectable
    from cairn.projects.project import load_project

    project = ROOT / "examples/implementations"
    done = subprocess.run([sys.executable, str(ROOT / "bin/cairn"), "validate", str(project), "--symbol", "prefix_by4",
                           "--history", str(tmp_path / "history"), "--format", "json"], capture_output=True, text=True,
                          timeout=600)  # fmt: skip
    record = json.loads(done.stdout)
    assert done.returncode == 0 and record["status"] == "passed", done.stderr
    assert record["regressions"] == {"file": "regressions/prefix.json", "exists": True, "replayed_by_cairn_test": True}
    kept = History(tmp_path / "history").records("prefix")
    assert [(r["id"], r["kind"], r["candidate"]) for r in kept] == [
        (record["history"], "validation", "plan prefix use prefix_by4;")
    ]  # the name cairn tune gives it
    source = load_project(project).source  # kept under the implementation with everything it calls
    assert kept[0]["variant"] == selectable(source, {"prefix_by4": record})["prefix_by4"]["identity"]
    assert kept[0]["variant"] != record["identity"] and kept[0]["detail"]["evidence"] == "finite-tested"
    done = subprocess.run([sys.executable, str(ROOT / "bin/cairn"), "test", str(project), "--format", "json"],
                          capture_output=True, text=True, timeout=600)  # fmt: skip
    tested = json.loads(done.stdout)
    assert done.returncode == 0 and tested["tests"][0]["status"] == "passed-finite-tests", done.stdout[-2000:]
    instances = {f"prefix_by[{k}]" for k in (4, 8, 16, 32)}  # each instance of prefix_by replays the kept cases
    assert set(tested["tests"][0]["implementations"]) == {"prefix_by4", "prefix_lanes", *instances}


# A reference and an implementation that differ at one input the boundaries never generate, which Z3 finds.
DOOR = """fn same(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s = add_wrap(s, xs[i]); }
  return s;
}

fn same_door(n:usize, xs:ro<u64>[n]) -> u64 implements same {
  if n == 1 && xs[0] == 12345 { return 0; }
  let mut s:u64 = 0;
  for i in 0..n { s = add_wrap(s, xs[i]); }
  return s;
}

fn main() -> i32 { return 0; }
"""


def test_a_counterexample_that_breaks_the_policy_fails_the_validation_and_is_kept(tmp_path):
    """The finite cases passed and Z3 named an input where the two differ; the record stood as `passed`."""
    kept = tmp_path / "same.json"
    record = validate(DOOR, "same", "same_door", SMALL, regressions=kept)
    replay = record["smt"]["replay"]
    assert record["finite"]["status"] == "passed" and record["smt"]["status"] == "counterexample"
    assert record["status"] == "failed" and replay["status"] == "failed"
    assert (
        replay["failed"]["inputs"] == {"n": 1, "xs": [12345]} and replay["failed"]["found_as"] == "Z3's counterexample"
    )
    assert [c["args"] for c in json.loads(kept.read_text())["cases"]] == [{"n": 1, "xs": [12345]}]
    assert record["coverage"]["counterexample"] == "failed" and record["coverage"]["ran"] == record["finite"]["cases"]


def test_a_counterexample_outside_the_admitted_domain_leaves_the_finite_result_standing():
    record = validate(DOOR, "same", "same_door", {**SMALL, "domain": {"largest_extent": 40, "values": {"xs": [0, 99]}}})
    replay = record["smt"]["replay"]
    assert record["status"] == "passed" and replay["status"] == "outside-domain" and "0..99" in replay["reason"]


def test_a_counterexample_within_the_tolerance_is_said_to_be_so():
    """Z3 compares exactly; -0.0 against 0.0 is a difference the policy forgives once a tolerance is given."""
    source = "fn f(x:f64) -> f64 = x;\nfn g(x:f64) -> f64 implements f = x + 0.0;\n"
    record = validate(source, "f", "g", {"tolerance": {"absolute": 1e-12, "relative": 0.0}})
    replay = record["smt"]["replay"]
    assert record["status"] == "passed" and replay["status"] == "within-policy" and replay["inputs"] == {"x": -0.0}
    assert "within the policy's tolerance" in replay["reason"]
    exact = validate(source, "f", "g")  # no tolerance: the finite cases fail first, and nothing is replayed
    assert exact["status"] == "failed" and exact["smt"]["replay"]["status"] == "not-run"


def test_the_record_states_what_it_rests_on():
    from cairn.verify.validation import agreement

    record = validate(TOTAL + BY4, "total", "total_by4", SMALL)
    assert record["agreement"]["sha256"] == agreement.DIGEST and record["agreement"]["relative_to"] == "the reference"
    assert record["target"] == {"kind": "host"} and record["compiler"]["cxx"] == "clang++"
    assert record["compiler"]["version"].startswith(("clang version", "Ubuntu clang", "Debian clang"))
    assert set(record["artifacts"]) == {"base", "selected"}
    assert all(len(v) == 64 for a in record["artifacts"].values() for v in a.values())
    assert record["coverage"] == {"generated": record["finite"]["cases"], "kept": 0, "ran": record["finite"]["cases"],
                                  "left_out": {}}  # fmt: skip


def test_no_case_run_is_unknown_even_where_the_implementation_need_not_run():
    from cairn.verify.validation.validation import Policy, Subject, finite

    nothing = Subject("f", "", "", "cf_f", "cf_g", None, [], "u64")  # nothing is called, so nothing is loaded
    result = finite(nothing, [], 0, Policy(), None, "g", vacuous=True)
    assert result["status"] == "unknown" and "No case ran" in result["reason"]


def door_project(tmp_path: Path) -> Path:
    root = tmp_path / "door"
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text(DOOR)
    (root / "cairn.toml").write_text('[project]\nname = "door"\nsources = ["src/main.cairn"]\n')
    (root / "policy.json").write_text(json.dumps(SMALL))
    return root


def test_cairn_tune_never_chooses_an_implementation_a_replayed_counterexample_refuted(tmp_path, capsys):
    from cairn.cli import main

    root, history = door_project(tmp_path), tmp_path / "history"
    validate_ = ["validate", str(root), "--symbol", "same_door", "--policy", str(root / "policy.json"), "--history",
                 str(history), "--format", "json"]  # fmt: skip
    assert main(validate_) == 1
    assert json.loads(capsys.readouterr().out)["smt"]["replay"]["status"] == "failed"
    assert main(["tune", str(root), "--symbol", "same", "--at", "n=1e4", "--history", str(history), "--format",
                 "json"]) == 0  # fmt: skip
    answer = json.loads(capsys.readouterr().out)
    [row] = [c for c in answer["candidates"] if c.get("use") == "same_door"]
    assert isinstance(row["validated"], str) and answer["chosen"].get("use") is None


def test_a_validation_holds_only_under_its_numerical_policy_and_its_compiler(tmp_path, capsys, monkeypatch):
    """A validation built by g++, or made under another numerical policy, says nothing of what clang++ builds."""
    from cairn.cli import main
    from cairn.verify.validation import agreement

    root, history = tmp_path / "total", tmp_path / "history"
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text(TOTAL + BY4 + "fn main() -> i32 { return 0; }\n")
    (root / "cairn.toml").write_text('[project]\nname = "total"\nsources = ["src/main.cairn"]\n')
    (root / "policy.json").write_text(json.dumps(SMALL))
    (root / "regressions").mkdir()  # the project pins SMALL, so a validation under it is one tune may choose on
    pinned = {"schema": REGRESSIONS, "reference": "total", "policy": Policy.of(SMALL).record(), "cases": []}
    (root / "regressions/total.json").write_text(json.dumps(pinned))
    tune = ["tune", str(root), "--symbol", "total", "--at", "n=1e4", "--history", str(history), "--format", "json"]

    def cited() -> object:
        assert main(tune) == 0
        return next(c for c in json.loads(capsys.readouterr().out)["candidates"] if c.get("use") == "total_by4")[
            "validated"
        ]

    validate_ = ["validate", str(root), "--symbol", "total_by4", "--policy", str(root / "policy.json"), "--history",
                 str(history), "--format", "json"]  # fmt: skip
    assert main([*validate_, "--cxx", "g++"]) == 0
    capsys.readouterr()
    assert isinstance(cited(), str)  # tune builds with clang++
    assert main(validate_) == 0
    capsys.readouterr()
    assert isinstance(cited(), dict)
    monkeypatch.setattr(agreement, "DIGEST", "0" * 64)
    assert isinstance(cited(), str)


def test_a_policy_is_weaker_than_the_reference_s_where_a_pass_under_it_covers_less():
    """What cairn tune asks of a validation before it chooses on it: at least as many cases, no looser tolerance, and
    a domain that admits every input the reference's does. A different seed takes nothing away."""
    pinned = Policy.of({"domain": {"extents": {"n": [0, 64]}, "values": {"x": [-8, 8]}}, "budget": 64,
                        "tolerance": {"absolute": 0.0, "relative": 1e-6}})  # fmt: skip
    assert pinned.weaker(pinned) == ""
    wider = {"domain": {"extents": {"n": [0, 128]}}, "tolerance": {"relative": 1e-9}, "budget": 256, "seed": 3}
    assert Policy.of(wider).weaker(pinned) == ""
    for change, said in [
        ({"budget": 32}, "it ran 32 generated cases, where the reference's policy runs 64"),
        ({"tolerance": {"absolute": 0.0, "relative": 1e-3}}, "its relative tolerance 0.001 is looser"),
        ({"domain": {"extents": {"n": [1, 64]}, "values": {"x": [-8, 8]}}}, "it admits n in 1..64"),
        ({"domain": {"largest_extent": 48, "values": {"x": [-8, 8]}}}, "it admits n in 0..48"),
        ({"domain": {"extents": {"n": [0, 64]}, "largest_extent": 48}}, "it admits extents up to 48"),
        ({"domain": {"extents": {"n": [0, 64]}, "values": {"x": [-4, 8]}}}, "it admits values of x in -4..8"),
    ]:
        assert Policy.of({**pinned.record(), **change}).weaker(pinned).startswith(said), change

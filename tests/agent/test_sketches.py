import pytest

from cairn.agent.sketches import ScalarContract, Sketch, solve_finite
from cairn.compiler.cairnc import Diagnostic, compile_source

BEFORE = (
    "// keep comment\nfn f(x:u64,y:u64)->u64 {return (x+y)/2;}\n// preserve this too\nfn other(x:u64)->u64{return x;}\n"
)
REF = BEFORE.replace("(x+y)/2", "(x/2)+(y/2)+((x&1)&(y&1))")


def sketch():
    return Sketch(
        BEFORE, "f", task={"task": "Floor average; no traps on valid u64 inputs."}, semantic=ScalarContract(REF, "f")
    ).hole("average", "(x+y)/2")


def test_fill_preserves_surroundings():
    s = sketch()
    r = s.fill(average="(x&y)+shr(x^y,1)")
    assert r.source == BEFORE.replace("(x+y)/2", "((x&y)+shr(x^y,1))")
    assert s.check_semantics(r)["status"] == "smt-equivalent"


def test_packet_no_required_hash_copying():
    s = sketch()
    p = s.packet()
    assert p["protocol"] == "cairn.choices/1" and p["reply"] == {"average": "(x+y)/2"}
    assert "session" not in p and "other" not in p["source"]
    assert "no traps" in p["task"]


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"extra": "x"},
        {"average": "x", "extra": "y"},
        {"average": 12},
        {"average": "x; return 7;"},
        {"average": "unknown"},
        {"average": "true"},
    ],
)
def test_reject_bad_choices(values):
    with pytest.raises(Diagnostic):
        sketch().fill(**values)


def test_nested_slots_rejected():
    s = sketch()
    with pytest.raises(Diagnostic):
        s.hole("input_x", "x", occurrence=0)


def test_repeated_expression_needs_explicit_occurrence():
    src = "fn f(x:u64)->u64{return add_wrap(x,x);}"
    s = Sketch(src, "f")
    with pytest.raises(Diagnostic):
        s.hole("one", "x")
    s.hole("one", "x", occurrence=0).hole("two", "x", occurrence=1)
    r = s.fill(one="1", two="2")
    compile_source(r.source)


def test_no_post_packet_hole_change():
    s = sketch()
    s.packet()
    with pytest.raises(Diagnostic):
        s.hole("xslot", "x")


def test_changed_candidate_rejected():
    s = sketch()
    r = s.fill(average="x")
    r.source = r.source.replace("(x)", "(y)")
    with pytest.raises(Diagnostic):
        s.check_semantics(r)


def test_no_implicit_semantic_acceptance():
    s = Sketch(BEFORE, "f").hole("average", "(x+y)/2")
    assert s.check_semantics(s.fill(average="x"))["status"] == "not-run"


def test_changed_host_task_rejected():
    s = sketch()
    s.packet()
    s._session.contract["task"] = "different"
    with pytest.raises(Diagnostic):
        s.fill(average="x")


def test_counterexample_cache_never_accepts_without_solver():
    choices = {"average": ["x+y", "(x+y)/2", "add_wrap(x,y)/2", "(x&y)+shr(x^y,1)"]}
    cached = solve_finite(sketch(), choices)
    plain = solve_finite(sketch(), choices, use_counterexample_cache=False)
    assert cached["status"] == plain["status"] == "smt-equivalent"
    assert cached["choices"] == plain["choices"]
    assert cached["cache_rejections"] > 0 and cached["solver_calls"] < plain["solver_calls"]
    assert cached["attempts"][-1]["stage"] == "solver"


@pytest.mark.parametrize("choices", [{}, {"average": []}, {"average": [1]}, {"extra": ["x"]}])
def test_search_dimension_errors(choices):
    with pytest.raises(Diagnostic):
        solve_finite(sketch(), choices)


def test_exhausted_search_not_success():
    r = solve_finite(sketch(), {"average": ["x+y", "x"]})
    assert r["status"] == "no-certified-candidate"


def test_unknown_solver_not_success(monkeypatch):
    import cairn.agent.sketches as sketches

    monkeypatch.setattr(sketches, "equivalent", lambda *a, **k: {"status": "unknown", "reason": "forced test timeout"})
    r = solve_finite(sketch(), {"average": ["(x&y)+shr(x^y,1)"]})
    assert r["status"] == "no-certified-candidate"


def test_two_slots_joint_transaction():
    src = "fn f(x:u8,y:u8)->u8{let first=x;let second=y;return add_wrap(first,second);}"
    s = Sketch(src, "f", semantic=ScalarContract(src, "f")).hole("first", "x").hole("second", "y")
    c = s.fill(first="y", second="x")
    assert s.check_semantics(c)["status"] == "smt-equivalent"


@pytest.mark.parametrize("bad", ['{"value":"x","value":"y"}', "[]", '{"value":NaN}', '{"value":4}'])
def test_strict_choice_transport_rejects(bad):
    sk = Sketch("fn f(x:u64)->u64{return x;}", "f").hole("value", "x")
    with pytest.raises(Diagnostic):
        sk.fill_json(bad)


def test_strict_choice_transport_accepts():
    sk = Sketch("fn f(x:u64)->u64{return x;}", "f").hole("value", "x")
    assert sk.fill_json('{"value":"x"}').receipt["status"] == "typed"


def test_packet_discloses_fixed_domain_and_reference():
    source = "fn f(x:u64)->u64{return x;}"
    ref = ScalarContract(source, "f", assume="x<8")
    sk = Sketch(source, "f", semantic=ref).hole("value", "x")
    visible = sk.packet()["semantic_contract"]
    assert visible["reference_source"] == source and visible["assume"] == "x<8"
    assert visible["runtime_precondition_guard"] == "not-inserted-by-builder"


def test_packet_mutation_does_not_change_host_reference():
    source = "fn f(x:u64)->u64{return x;}"
    sk = Sketch(source, "f", semantic=ScalarContract(source, "f")).hole("value", "x")
    sk.packet()["semantic_contract"]["reference_source"] = "arbitrary replacement"
    assert sk.semantic.reference == source
    assert sk.check_semantics(sk.fill(value="x"))["status"] == "smt-equivalent"

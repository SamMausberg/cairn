"""The bounded search: the checker decides which combinations are legal, each complete device candidate is compiled
for its own resources within the compile budget and kept by what was compiled, and a history answers what an earlier
search established without compiling or running it again, for the same target only."""

import json
import shutil

import pytest
from test_predict import MACHINE

from cairn.agent.history import History
from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf.profile import packaged
from cairn.perf.regions import identified
from cairn.perf.tuning.plan_source import written
from cairn.perf.tuning.resources import Inspector
from cairn.perf.tuning.search import Budget, Candidate, shaped, space
from cairn.perf.tuning.tune import tune
from cairn.projects.target import parse
from cairn.verify.validation.validation import REGRESSIONS, Policy
from emitted import code_of

BLUR = """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}
"""
TWO = """fn two(n:usize, out:rw<f32>[n]@device, y:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { y[i] = 2.0 * x[i]; }
  parallel j in n { out[j] = y[j] + 1.0; }
}
"""
MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
"""
NVCC = pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="compiling for the "
                          "device needs nvcc and cuobjdump; nothing runs on a GPU")  # fmt: skip


def test_stage_enters_the_space_and_the_checker_refuses_what_it_refuses():
    result = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=0))
    assert {row.get("stage", 0) for row in result["candidates"]} == {0, 1}  # the radius the staging rule reads
    refused = result["space"]["refused"]
    both = [r for r in refused if r["code"] == "E-PLAN" and "stage" in r["message"]]
    assert both and all("stage" in r["example"] and "vector" in r["example"] for r in both)
    assert result["space"]["configurations"] == result["space"]["legal"] + sum(r["configurations"] for r in refused)
    (region,) = [r["id"] for r in result["regions"]]
    staged = next(row for row in result["candidates"] if row.get("stage"))
    assert staged["applies_to"] == {"stage": {region: {"radius": 1, "arrays": ["x"]}}}
    chunked = next(row for row in result["candidates"] if row.get("vector"))
    assert chunked["applies_to"] == {"vector": {region: {"width": chunked["vector"], "arrays": ["out"]}}}
    assert sum(result["budget"]["undone"].values()) == result["space"]["legal"]  # nothing compiled, and it says so
    assert result["budget"]["compiles"]["started"] == 0


def test_vector_beside_fuse_is_tried_and_the_checker_refuses_it():
    plans = space({"device"}, 16, regions=2)
    assert any(dict(p).get("vector") and dict(p).get("fuse") for p in plans)  # the search no longer leaves it out
    result = tune(TWO, "two", [{"n": 1e7}], MACHINE, budget=Budget(compiles=0))
    assert not any(row.get("vector") and row.get("fuse") for row in result["candidates"])
    assert any(r["code"] == "E-PLAN" and "fuse" in r["message"] and r["example"].get("vector")
               for r in result["space"]["refused"])  # fmt: skip
    head, tail = [r["id"] for r in identified(TWO, "two")]
    fused = next(row for row in result["candidates"] if row.get("fuse"))
    assert fused["applies_to"]["fuse"] == [[head, tail]]


def test_a_spent_clock_leaves_the_rest_ungenerated_and_says_so(monkeypatch):
    from cairn.perf.tuning import search

    calls = iter(range(10_000))
    monkeypatch.setattr(search.Spent, "out_of_time", lambda self, share=1.0: next(calls) >= 5)
    result = tune(MIX, "spread", [{"n": 1e6}], MACHINE)
    assert result["space"]["checked"] == 5 and len(result["candidates"]) == 5
    assert result["budget"]["undone"] == {"not generated: out of time": result["space"]["configurations"] - 5}
    with pytest.raises(ValueError):
        Budget(compiles=-1)


def test_among_plans_priced_alike_a_new_kernel_is_compiled_before_a_launch_variant():
    def made(plan, ns):
        return Candidate(written(plan), predicted_ns=ns)

    ranked = [made({"block": 64}, 10), made({"block": 128}, 10), made({"unroll": 4}, 10),
              made({"block": 64, "unroll": 4}, 10), made({"vector": 2}, 20), made({"block": 64}, 30)]  # fmt: skip
    order = [dict(c.plan) for c in shaped(ranked)]
    assert order == [{"block": 64}, {"unroll": 4}, {"block": 128}, {"block": 64, "unroll": 4}, {"vector": 2},
                     {"block": 64}]  # fmt: skip


def test_registers_and_shared_memory_bound_how_many_blocks_stay_resident():
    card = packaged("rtx-5070-ti").device
    assert card.occupancy(24, 128) == card.occupancy(24, 128, 0) == 1.0  # twelve blocks of 128 fill 1536 threads
    assert card.occupancy(24, 128, 40_000) == 2 * 128 / 1536  # two tiles of 40 kB fit the SM's 100 kB
    assert card.occupancy(128, 256) == 2 * 256 / 1536  # 65536 registers hold two blocks of 256 at 128 each


@NVCC
def test_each_complete_candidate_is_compiled_for_its_own_resources():
    staged = BLUR + "plan blur { stage 1; block 128; }\n"
    p, checker, _ = compile_program(staged)
    inspector = Inspector(parse("sm_120"), None)
    read = inspector.inspect(staged, "blur", p, checker)
    assert read["status"] == "read" and read["registers"] > 0 and read["kernels"] == 1
    assert read["dynamic_shared_bytes"] == (130 * 4 + 15) // 16 * 16  # one tile of x: 128 + 2 elements
    assert read["memory"].get("shared_load", 0) > 0
    plain = inspector.inspect(BLUR, "blur", *compile_program(BLUR)[:2])
    assert plain["key"] != read["key"] and plain["dynamic_shared_bytes"] == 0
    assert inspector.kept(staged, "blur", p)["key"] == read["key"]  # the same program: the same inspection
    assert Inspector(parse("sm_120f"), None).key(staged) != read["key"]  # another target: another key


def test_a_kept_inspection_that_names_another_target_is_refused(tmp_path):
    inspector = Inspector(parse("sm_120"), History(tmp_path))
    History(tmp_path).keep(inspector.key(BLUR), {"status": "read", "device_target": "sm_90", "registers": 8})
    assert code_of(lambda: inspector.kept(BLUR, "blur", None)) == "E-TARGET-MISMATCH"


@NVCC
def test_a_kept_inspection_answers_the_next_search_for_its_target_only(tmp_path):
    first = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=2), history=tmp_path,
                 device_target=parse("sm_120"))  # fmt: skip
    assert first["budget"]["compiles"] == {"allowed": 2, "started": 2, "kept": 0}
    read = [row for row in first["candidates"] if "resources" in row]
    compiled = [row for row in read if "same_kernels_as" not in row["resources"]]
    assert len(compiled) == 2 and len({row["resources"]["key"] for row in compiled}) == 2
    assert {row["resources"]["key"] for row in read} == {row["resources"]["key"] for row in compiled}  # launch variants
    assert first["chosen"] in read  # the chosen candidate is one a compile read
    again = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=0), history=tmp_path,
                 device_target=parse("sm_120"))  # fmt: skip
    assert again["budget"]["compiles"] == {"allowed": 0, "started": 0, "kept": 2}
    assert all(row["resources"]["kept"] for row in again["candidates"] if "resources" in row)
    other = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=0), history=tmp_path,
                 device_target=parse("sm_120f"))  # fmt: skip
    assert other["budget"]["compiles"]["kept"] == 0 and not any("resources" in row for row in other["candidates"])
    kinds = {r["kind"] for r in History(tmp_path).records("blur")}
    assert kinds == {"attempt", "failure", "observation"}  # the search, the checker's refusals, each compile


@pytest.mark.skipif(not shutil.which("clang++"), reason="measuring times a native build")
def test_the_run_budget_bounds_measurement_and_a_kept_measurement_is_not_run_again(tmp_path):
    first = tune(MIX, "spread", [{"n": 20000}], MACHINE, measure=2, budget=Budget(runs=2), history=tmp_path)
    assert first["budget"]["runs"]["started"] == 2 and first["budget"]["undone"]["not measured: run budget spent"]
    records = [r for r in History(tmp_path).records("spread") if r["kind"] == "measurement"]
    assert len(records) == 2 and all("median of 3 blocks" in r["detail"]["procedure"] for r in records)
    again = tune(MIX, "spread", [{"n": 20000}], MACHINE, measure=2, budget=Budget(runs=1), history=tmp_path)
    assert again["budget"]["runs"]["kept"] == 2 and again["budget"]["runs"]["started"] == 1


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
}"""
PAIRS = """fn total_pairs(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for k in 0..n / 2 { s += xs[2 * k] + xs[2 * k + 1]; }
  return s;
}"""  # wrong at every odd length, and never submitted, so nothing validated it


@pytest.mark.skipif(not shutil.which("clang++"), reason="validating an implementation runs native builds")
def test_an_implementation_is_searched_and_chosen_only_while_its_validation_holds(tmp_path):
    from cairn.agent.hosts.implementations import PROTOCOL, ImplementationHost

    host = ImplementationHost(records=tmp_path)
    policy = {"tolerance": {"absolute": 0.0, "relative": 0.0}, "domain": {"largest_extent": 48}}
    host.open(TOTAL, "total", policy)
    answer = host.respond({"protocol": PROTOCOL, "handle": "i1", "kind": "submit", "source": BY4})
    assert answer["status"] == "validated"
    source = host.source("i1") + "\n" + PAIRS + "\n"
    result = tune(source, "total", [{"n": 1e6}], MACHINE, history=tmp_path, pinned=policy)
    rows = {row.get("use"): row for row in result["candidates"]}
    assert set(rows) == {None, "total_by4", "total_pairs"} and result["implementations"] == ["total_by4", "total_pairs"]
    assert rows["total_by4"]["validated"]["evidence"] == "finite-tested"
    assert rows["total_by4"]["plan"] == "plan total use total_by4;"
    assert rows["total_pairs"]["validated"].startswith("no validation holds")
    assert result["chosen"].get("use") != "total_pairs"  # selecting it could change a result
    unkept = tune(source, "total", [{"n": 1e6}], MACHINE)
    assert all(isinstance(row["validated"], str) for row in unkept["candidates"] if row.get("use"))
    assert "use" not in unkept["chosen"]  # without a history, only the reference is chosen
    edited = source.replace("a += xs[4 * k] + xs[4 * k + 1];", "a += xs[4 * k + 1] + xs[4 * k];")
    moved = tune(edited, "total", [{"n": 1e6}], MACHINE, history=tmp_path, pinned=policy)
    assert isinstance(next(r for r in moved["candidates"] if r.get("use") == "total_by4")["validated"], str)


BULK = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { out[i] = a * x[i]; } }
fn scale_bulk(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale needs(tma) {
  parallel i in n { out[i] = a * x[i]; }
}
"""


def test_a_candidate_whose_implementation_needs_what_the_target_lacks_is_refused_as_its_build_is():
    """`plan scale use scale_bulk;` needs tma. A build for sm_80, which lacks it, is E-IMPL-TARGET, so the search
    refuses that candidate for sm_80 with the same judgment and prices it for sm_90a; `--compare` refuses it too."""
    from cairn.compiler.cairnc import Diagnostic
    from cairn.perf.profile import card, carrying, default
    from cairn.perf.tuning.feedback import compare, parse_candidate

    a100, h100 = (carrying(default(), card(name)) for name in ("a100", "h100-sxm5"))
    sm_80 = tune(BULK, "scale", [{"n": 1e6}], a100, device_target=parse("sm_80"), budget=Budget(compiles=0))
    [refused] = [r for r in sm_80["space"]["refused"] if r["code"] == "E-IMPL-TARGET"]
    assert "needs tma, which sm_80 does not provide" in refused["message"] and refused["configurations"] == 1
    assert {r.get("use") for r in sm_80["candidates"]} == {None}
    sm_90a = tune(BULK, "scale", [{"n": 1e6}], h100, device_target=parse("sm_90a"), budget=Budget(compiles=0))
    assert {r.get("use") for r in sm_90a["candidates"]} == {None, "scale_bulk"}
    assert all(r["code"] != "E-IMPL-TARGET" for r in sm_90a["space"]["refused"])
    with pytest.raises(Diagnostic) as error:
        compare(BULK, "scale", parse_candidate("none"), parse_candidate("use scale_bulk"), [{"n": 1e6}], a100,
                target=parse("sm_80"), compiles=0)  # fmt: skip
    assert error.value.data["code"] == "E-IMPL-TARGET"


@pytest.mark.skipif(not shutil.which("clang++"), reason="validating an implementation runs native builds")
def test_the_command_writes_the_selection_of_the_validated_implementation_it_chose(tmp_path, capsys):
    from cairn.agent.hosts.implementations import PROTOCOL, ImplementationHost

    total = TOTAL.replace("-> u64 {", "-> u64 effects(pure, par:host) {", 1)
    lanes = ("fn total_lanes(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 {\n"
             "  let s = reduce + parallel i in n yield xs[i];\n  return s;\n}")  # fmt: skip
    host = ImplementationHost(records=tmp_path / "history")
    policy = {"tolerance": {"absolute": 0.0, "relative": 0.0}, "domain": {"largest_extent": 48}}
    host.open(total, "total", policy)
    assert host.respond({"protocol": PROTOCOL, "handle": "i1", "kind": "submit", "source": lanes})["status"] == (
        "validated")  # fmt: skip
    source = tmp_path / "total.cairn"
    source.write_text(host.source("i1"))
    ask = ["tune", str(source), "--symbol", "total", "--at", "n=1e7", "--history", str(tmp_path / "history")]
    assert main([*ask, "--format", "json"]) == 0  # the reference's policy is the defaults: 4096, not 48
    [row] = [r for r in json.loads(capsys.readouterr().out)["candidates"] if r.get("use") == "total_lanes"]
    assert "it admits extents up to 48, where the reference's policy admits 4096" in row["validated"]
    (tmp_path / "regressions").mkdir()  # the project pins the policy the session validated under
    pinned = {"schema": REGRESSIONS, "reference": "total", "policy": Policy.of(policy).record(), "cases": []}
    (tmp_path / "regressions/total.json").write_text(json.dumps(pinned))
    assert main([*ask, "--write", "--format", "json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["chosen"]["use"] == "total_lanes" and answer["chosen"]["validated"]["evidence"] == "finite-tested"
    assert "plan total use total_lanes;" in source.read_text()
    compile_program(source.read_text())


def test_a_person_reads_the_space_the_best_few_and_the_budget(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--no-history", "--format", "human"]) == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0].startswith("spread: ") and "plans," in shown[0] and "predicted" in shown[1]
    assert any(line.startswith("chosen: ") for line in shown) and shown[-1].startswith("budget: 0 of 4")


def test_a_search_asked_again_answers_with_what_changed(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    ask = ["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--format", "json"]
    assert main(ask) == 0
    (tmp_path / "first.json").write_text(capsys.readouterr().out)
    assert main([*ask, "--since", str(tmp_path / "first.json")]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["schema"] == "cairn.tune-delta/2" and again["candidates"] == [] and again["gone"] == []
    assert again["unchanged"] == len(json.loads((tmp_path / "first.json").read_text())["candidates"])
    source.write_text(MIX + "plan spread { lanes 2; }\n")
    assert main([*ask, "--since", str(tmp_path / "first.json")]) == 0
    moved = json.loads(capsys.readouterr().out)
    assert moved["current"] == "plan spread { lanes 2; }" and moved["unchanged"] == again["unchanged"]


def test_the_command_records_beside_the_manifest_unless_told_not_to(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--no-history", "--format", "json"]) == 0
    assert not (tmp_path / ".cairn").exists()
    capsys.readouterr()
    kept = tmp_path / "kept"
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--history", str(kept),
                 "--budget-seconds", "60", "--format", "json"]) == 0  # fmt: skip
    assert json.loads(capsys.readouterr().out)["schema"] == "cairn.tune/2"
    assert [r["kind"] for r in History(kept).records("spread")] == ["attempt"]
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--format", "json"]) == 0
    assert [r["kind"] for r in History(tmp_path / ".cairn" / "history").records("spread")] == ["attempt"]

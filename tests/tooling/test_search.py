"""The bounded search: the checker decides which combinations are legal, each complete device candidate is compiled
for its own resources within the compile budget and kept by what was compiled, and a history answers what an earlier
search established without compiling or running it again, for the same target only."""

import shutil

import pytest
from test_predict import MACHINE

from cairn.agent.history import History
from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf.profile import packaged
from cairn.perf.regions import identified
from cairn.perf.resources import Inspector
from cairn.perf.search import Budget, space
from cairn.perf.tune import tune
from cairn.projects.target import parse
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


def test_a_spent_clock_leaves_the_rest_unchecked_and_says_so(monkeypatch):
    from cairn.perf import search

    calls = iter(range(10_000))
    monkeypatch.setattr(search.Spent, "out_of_time", lambda self: next(calls) >= 5)
    result = tune(MIX, "spread", [{"n": 1e6}], MACHINE)
    assert result["space"]["checked"] == 5 and len(result["candidates"]) == 5
    assert result["budget"]["undone"] == {"not checked: out of time": result["space"]["configurations"] - 5}
    with pytest.raises(ValueError):
        Budget(compiles=-1)


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
    assert len(read) == 2 and len({row["resources"]["key"] for row in read}) == 2
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


def test_the_command_records_beside_the_manifest_unless_told_not_to(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--no-history", "--format", "json"]) == 0
    assert not (tmp_path / ".cairn").exists()
    capsys.readouterr()
    kept = tmp_path / "kept"
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--history", str(kept),
                 "--budget-seconds", "60", "--format", "json"]) == 0  # fmt: skip
    assert '"schema": "cairn.tune/2"' in capsys.readouterr().out
    assert [r["kind"] for r in History(kept).records("spread")] == ["attempt"]
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--format", "json"]) == 0
    assert [r["kind"] for r in History(tmp_path / ".cairn" / "history").records("spread")] == ["attempt"]

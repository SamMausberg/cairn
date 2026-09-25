"""`cairn tune`: every candidate is a plan the checker accepts, the ranking is the model's, and a measured round
times only what the model ranked best.
"""

import json
import shutil

import pytest
from test_predict import MACHINE

from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf.model import predict
from cairn.perf.tuning.plan_source import replanned, written
from cairn.perf.tuning.search import Candidate, priced
from cairn.perf.tuning.tune import distinct, space, tune
from cairn.perf.tuning.tune import row as shown_row
from cairn.perf.work import count

MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
"""


def test_every_candidate_is_a_plan_the_checker_accepts():
    result = tune(MIX, "spread", [{"n": 1e6}], MACHINE)
    assert len(result["candidates"]) == len(space({"host"}, 8)) == 30  # six grains by five lane caps up to eight
    for row in result["candidates"]:
        if row["plan"].startswith("plan"):
            compile_program(replanned(MIX, "spread", row["plan"]))  # raises if the checker refused it
    predicted = [row["predicted_ns"] for row in result["candidates"]]
    assert predicted == sorted(predicted) and result["chosen"] == result["candidates"][0]
    assert result["current"] == "(no plan for spread)" and "none needed checking" in result["predicted"]


DEVICE = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"


def test_a_candidate_that_spills_is_priced_as_without_the_spills_and_says_so():
    """What ptxas reports of spills enters no price, so two readings that differ only in their spills rank alike, a
    spilling candidate says so, and a prediction of its kernel says the time leaves the spills out."""
    p, checker, _ = compile_program(DEVICE)
    cost = count(p, checker, {"scale"})["scale"]
    read = {"status": "read", "registers": 40, "shared_bytes": 0, "dynamic_shared_bytes": 0, "key": "k"}
    sizes = [{"n": 1e7}]
    alike = [priced(cost, MACHINE, sizes, None, {**read, "spill_bytes": spilled}) for spilled in (0, 64)]
    assert alike[0] == alike[1]
    spilling = Candidate(written({}), predicted_ns=alike[1][0], resources={**read, "spill_bytes": 64})
    shown = shown_row("scale", spilling, [])["resources"]
    assert shown["spill_bytes"] == 64 and shown["spills"] == "not priced"
    (region,) = cost.regions
    region.registers, region.spilled = 40, 64
    found = predict(cost, MACHINE, sizes[0])
    assert [x["spill_bytes"] for x in found["parts"] if "spill_bytes" in x] == [64]
    assert any("64 bytes of spill stores and loads" in why and "not priced" in why for why in found["why"])


def test_a_function_without_a_host_region_has_nothing_to_tune():
    with pytest.raises(ValueError, match="no parallel region"):
        tune("fn f(n:usize, o:rw<u64>[n]) { for i in 0..n { o[i] = 1; } }", "f", [{"n": 1e6}], MACHINE)
    with pytest.raises(ValueError, match="sizes"):
        tune(MIX, "spread", [], MACHINE)


def test_a_plan_is_replaced_or_removed_and_nothing_else_moves():
    planned = replanned(MIX, "spread", "plan spread { lanes 4; }")
    assert planned.startswith(MIX.rstrip("\n")) and planned.endswith("plan spread { lanes 4; }\n")
    again = replanned(planned, "spread", "plan spread { grain 1; }")
    assert again.count("plan spread") == 1 and "grain 1" in again
    assert replanned(again, "spread", "").rstrip("\n") == MIX.rstrip("\n")


def test_the_timed_field_holds_one_plan_per_lane_cap():
    ranked = [written(p) for p in ({"lanes": 8}, {"grain": 1, "lanes": 8}, {"grain": 64, "lanes": 8}, {"lanes": 4}, {})]
    assert distinct(ranked, 3) == [written({"lanes": 8}), written({"lanes": 4}), written({})]


@pytest.mark.skipif(not shutil.which("clang++"), reason="measuring times a native build")
def test_a_measured_round_times_the_best_ranked_few_and_the_current_plan():
    result = tune(MIX, "spread", [{"n": 20000}], MACHINE, measure=2)
    first = result["rounds"][0]["measured_ns"]
    assert "(no plan for spread)" in first and len(first) == 3  # two candidates and the plan the function has now
    assert result["chosen"]["measured_ns"] > 0 and result["measured_best"] == result["chosen"]["plan"]
    assert result["pairs_ordered_as_predicted"].endswith("of 3")


def test_the_command_writes_the_chosen_plan_into_the_file(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    assert main(["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--write", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    written = source.read_text()
    assert out["written"] and (out["chosen"]["plan"] in written or not out["chosen"]["plan"].startswith("plan"))
    compile_program(written)

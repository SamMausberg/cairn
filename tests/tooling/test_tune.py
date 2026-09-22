"""`cairn tune`: every candidate is a plan the checker accepts, the ranking is the model's, and a measured round
times only what the model ranked best.
"""

import json
import shutil

import pytest
from test_predict import MACHINE

from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf.tune import distinct, replanned, space, tune

MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
"""


def test_every_candidate_is_a_plan_the_checker_accepts():
    result = tune(MIX, "spread", [{"n": 1e6}], MACHINE)
    assert len(result["candidates"]) == len(space(8)) == 30  # six grains by five lane caps up to eight lanes
    for row in result["candidates"]:
        if row["plan"].startswith("plan"):
            compile_program(replanned(MIX, "spread", row["plan"]))  # raises if the checker refused it
    predicted = [row["predicted_ns"] for row in result["candidates"]]
    assert predicted == sorted(predicted) and result["chosen"] == result["candidates"][0]
    assert result["current"] == "(no plan for spread)" and "none needed checking" in result["predicted"]


def test_a_function_without_a_host_region_has_nothing_to_tune():
    with pytest.raises(ValueError, match="no host parallel region"):
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
    ranked = [(0, 8), (1, 8), (64, 8), (0, 4), (0, 16)]
    assert distinct(ranked, 3) == [(0, 8), (0, 4), (0, 16)]


@pytest.mark.skipif(not shutil.which("clang++"), reason="measuring times a native build")
def test_a_measured_round_times_the_best_ranked_few_and_the_current_plan():
    result = tune(MIX, "spread", [{"n": 20000}], MACHINE, measure=2)
    first = result["rounds"][0]["measured_ns"]
    assert "(none)" in first and len(first) == 3  # two distinct candidates and the plan the function has now
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

"""`cairn tune` over several sizes: every candidate is priced at each size, and one objective over those times ranks
it, the geometric mean unless the arithmetic mean is asked for, weighted when weights are given. A candidate that is
fastest at one size and loses the objective is shown as such, the history records the objective that chose, and a
measurement the history holds outranks the model's price."""

import dataclasses
import json
import math

import pytest
from test_predict import MACHINE

from cairn.agent.history import History
from cairn.cli import main
from cairn.perf.profile import card, carrying
from cairn.perf.tuning.objective import Objective, shapes
from cairn.perf.tuning.search import Budget
from cairn.perf.tuning.tune import tune
from cairn.projects.target import parse

MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
"""
# Each lane the pool starts costs a fifth of a millisecond on this machine, so at thirty thousand elements four
# lanes beat eight, and at a hundred million eight win: the best plan depends on the size.
COSTLY = dataclasses.replace(MACHINE, host=dataclasses.replace(MACHINE.host, pool={
    "fork_ns": 1000.0, "per_lane_ns": 200_000.0, "cutoff": 16384, "grain": 8192}))  # fmt: skip
SIZES = [{"n": 3e4}, {"n": 1e8}]


def folded(row: dict, weights=(1.0, 1.0), kind: str = "geomean") -> float:
    """The objective of a row's times at each size, computed here from the row as the answer shows it."""
    times = row["predicted_ns_at"]
    if kind == "mean":
        return sum(w * t for w, t in zip(weights, times, strict=True)) / sum(weights)
    return math.exp(sum(w * math.log(t) for w, t in zip(weights, times, strict=True)) / sum(weights))


def least(result: dict, weights=(1.0, 1.0), kind: str = "geomean") -> float:
    return min(folded(row, weights, kind) for row in result["candidates"])


def test_the_geometric_mean_chooses_what_the_arithmetic_says_and_weights_change_it():
    result = tune(MIX, "spread", SIZES, COSTLY)
    assert result["objective"]["kind"] == "geomean" and result["objective"]["weights"] == [1.0, 1.0]
    assert all(len(row["predicted_ns_at"]) == 2 for row in result["candidates"])  # every row, at every size
    assert folded(result["chosen"]) == pytest.approx(least(result), rel=1e-3)
    small, large = result["fastest"]
    assert large["chosen"] and not small["chosen"]  # fastest at 3e4, and the objective did not choose it
    winner = next(row for row in result["candidates"] if row["plan"] == small["plan"])
    assert winner["fastest_at"] == ["n=30000"] and folded(winner) > folded(result["chosen"]) * 1.1
    assert winner["predicted_ns_at"][0] < result["chosen"]["predicted_ns_at"][0]
    heavy = tune(MIX, "spread", SIZES, COSTLY, weights=[100, 1])
    assert folded(heavy["chosen"], (100, 1)) == pytest.approx(least(heavy, (100, 1)), rel=1e-3)
    assert heavy["chosen"]["plan"] == small["plan"]  # weighing the small size makes its winner the choice
    assert heavy["objective"]["text"].endswith("weighted 100, 1")
    assert heavy["chosen_by"] == "the geometric mean of the predicted times at n=30000; n=1e+08, weighted 100, 1"
    mean = tune(MIX, "spread", SIZES, COSTLY, objective="mean")
    assert folded(mean["chosen"], kind="mean") == pytest.approx(least(mean, kind="mean"), rel=1e-3)


def test_one_size_is_its_own_objective():
    one = tune(MIX, "spread", [{"n": 1e6}], MACHINE)
    assert all(row["predicted_ns"] == row["predicted_ns_at"][0] for row in one["candidates"])
    assert one["objective"]["text"] == "the time at n=1e+06"


def test_an_objective_folds_times_as_stated_and_refuses_what_it_cannot_fold():
    goal = Objective.over([{"n": 1.0}, {"n": 2.0}], [1, 3])
    assert goal.value([2.0, 8.0]) == pytest.approx(2**2.5)  # (1 * log 2 + 3 * log 8) / 4 = 2.5 log 2
    assert Objective.over([{"n": 1.0}, {"n": 2.0}], [1, 3], "mean").value([2.0, 8.0]) == pytest.approx(6.5)
    for bad in (lambda: Objective.over([]), lambda: Objective.over([{"n": 1.0}], [1, 2]),
                lambda: Objective.over([{"n": 1.0}], [0]), lambda: Objective.over([{"n": 1.0}], None, "max")):  # fmt: skip
        with pytest.raises(ValueError):
            bad()
    assert shapes([{"at": "n=1e6,m=2", "weight": 2}, {"at": {"n": 1e8}}]) == ([{"n": 1e6, "m": 2.0}, {"n": 1e8}],
                                                                              [2.0, 1.0])  # fmt: skip
    for bad in ([], {"at": "n=1"}, [{"at": "n=1", "weight": -1}], [{"at": "n=1", "size": 2}], [{"at": {"n": "x"}}]):
        with pytest.raises(ValueError):
            shapes(bad)


def test_a_card_prices_each_size_and_the_objective_folds_that_card_s_times():
    two = """fn two(n:usize, out:rw<f32>[n]@device, y:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { y[i] = 2.0 * x[i]; }
  parallel j in n { out[j] = y[j] + 1.0; }
}
"""
    sizes = [{"n": 1e4}, {"n": 1e8}]
    answers = {name: tune(two, "two", sizes, carrying(MACHINE, card(name)), budget=Budget(compiles=0),
                          device_target=parse(target))
               for name, target in (("h100", "sm_90a"), ("rtx-5070-ti", "sm_120"))}  # fmt: skip
    for name, answer in answers.items():
        assert answer["device_card"]["card"] == card(name).source["card"]
        for row in answer["candidates"]:
            assert row["predicted_ns"] == pytest.approx(folded(row), rel=1e-3)
    h100, rtx = ({row["plan"]: row["predicted_ns_at"] for row in a["candidates"]} for a in answers.values())
    assert h100 != rtx  # each card's own times, folded by the same objective


def test_the_command_takes_weighted_shapes_and_the_history_records_the_objective(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    given = tmp_path / "shapes.json"
    given.write_text(json.dumps([{"at": "n=3e4", "weight": 100}, {"at": {"n": 1e8}}]))
    kept = tmp_path / "history"
    ask = ["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--shapes", str(given), "--objective", "mean",
           "--history", str(kept)]  # fmt: skip
    assert main([*ask, "--format", "json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["sizes"] == [{"n": 1e6}, {"n": 3e4}, {"n": 1e8}]
    assert answer["objective"]["kind"] == "mean" and answer["objective"]["weights"] == [1.0, 100.0, 1.0]
    (attempt,) = [r for r in History(kept).records("spread") if r["kind"] == "attempt"]
    assert attempt["detail"]["objective"] == answer["objective"]["text"]
    assert main([*ask, "--format", "human"]) == 0
    shown = capsys.readouterr().out
    assert "ranked by the arithmetic mean of the times at n=1e+06; n=30000; n=1e+08, weighted 1, 100, 1" in shown
    given.write_text(json.dumps([{"at": "n=3e4", "weight": "heavy"}]))
    assert main([*ask, "--format", "json"]) != 0


def test_a_measurement_the_history_holds_outranks_the_prediction(tmp_path):
    from cairn.perf.tuning.plan_source import contract, written
    from cairn.perf.tuning.resources import host_target
    from cairn.perf.tuning.tune import PROCEDURE, Recorder

    first = tune(MIX, "spread", SIZES, COSTLY, history=tmp_path)
    ahead, behind = first["candidates"][:2]
    recorder = Recorder(tmp_path, MIX, "spread", contract(MIX, "spread"), host_target(None, "clang++"), None)
    for row, ns in ((ahead, 9e6), (behind, 1e6)):  # measured the other way round from the prediction
        key = (written({k: v for k, v in row.items() if isinstance(v, int)}), None)
        for s in SIZES:
            recorder.put("measurement", key, recorder.host, {"procedure": PROCEDURE[False].format(blocks=3),
                                                             "sizes": s, "median_ns": ns})  # fmt: skip
    again = tune(MIX, "spread", SIZES, COSTLY, history=tmp_path)
    assert [row["plan"] for row in again["candidates"][:2]] == [behind["plan"], ahead["plan"]]
    assert again["candidates"][0]["measured_ns"] == 1e6 and again["candidates"][0]["measured_ns_at"] == [1e6, 1e6]
    assert again["chosen"]["plan"] == behind["plan"] and "predicted_ns" in again["chosen"]  # the model still prices
    assert again["measured_order"].startswith("0 of 1 pairs")  # the order the model did not predict, said so
    assert [row["plan"] for row in again["candidates"][2:]] == [row["plan"] for row in first["candidates"][2:]]

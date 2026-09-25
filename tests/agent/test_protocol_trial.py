"""The protocol trial's harness: sandboxes that hold no source, a host that replays and counts, a budget, a score."""

import json
import shutil

import pytest

from ai import protocol_trial as trial
from cairn.agent.hosts.edits import HANDLES

pytestmark = pytest.mark.skipif(shutil.which("clang++") is None, reason="the hidden check builds with clang++")


def call(box, request):
    return trial.host(box, json.dumps(request))


def test_every_planted_bug_is_in_its_function_and_fails_the_hidden_check():
    for task, (_, symbol, good, bad, report) in trial.TASKS.items():
        shipped, _ = trial.shipped(task)
        planted = trial.planted(task)
        assert planted != shipped and planted.replace(bad, good, 1) == shipped, task
        assert report and symbol.rsplit(".", 1)[-1] in shipped
    result = trial.verify(["even_bit", "digit_range"])
    assert result["valid"], result
    assert result["tasks"]["digit_range"]["planted"]["contracts"][-1] == "failed-tests"  # Only the host's cases see it.


def test_a_sandbox_shows_the_packet_and_holds_no_source(tmp_path):
    trial.prepare(tmp_path, ["even_bit"])
    for arm in trial.ARMS:
        box = tmp_path / arm / "even_bit"
        assert sorted(p.name for p in box.iterdir()) == ["PACKET.json", "TASK.md", "host.py", "state.json"]
        packet = json.loads((box / "PACKET.json").read_text())
        target = next(c["source"] for c in packet["context"] if c.get("symbol") == "sorted_even")
        assert packet["handle"] == "e1" and "(scratch[i] & 1) == 1" in target  # The planted bug, as seen.
        report = trial.TASKS["even_bit"][4]
        assert report in (box / "TASK.md").read_text() and packet["task"] == report
    focused = json.loads((tmp_path / "focused/even_bit/PACKET.json").read_text())
    component = json.loads((tmp_path / "component/even_bit/PACKET.json").read_text())
    assert "expand_protocol" in focused and "expand_protocol" not in component
    assert len(json.dumps(focused)) < len(json.dumps(component))


def test_the_host_replays_counts_and_closes(tmp_path):
    trial.prepare(tmp_path, ["sort_top"])
    box = tmp_path / "component/sort_top"
    refused = trial.host(box, "{not json")
    assert refused["code"] == "E-REQUEST"
    expand = {"protocol": HANDLES, "handle": "e1", "kind": "expand", "symbols": ["sorted_even"]}
    assert call(box, expand)["code"] == "E-REQUEST"  # The component arm has nothing more to show.
    (box / "request.json").write_text(
        json.dumps({**expand, "kind": "body", "replacement": "{ }"}).replace(', "symbols": ["sorted_even"]', "")
    )
    assert trial.host(box, "request.json")["status"] == "typed"  # A request may come from a file in the sandbox.
    for _ in range(trial.CALLS - 3):
        call(box, {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ }"})
    assert (
        call(box, {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ }"})["status"]
        == "budget-spent"
    )
    assert call(box, {"kind": "submit"}) == {"status": "submitted", "admitted_edits": trial.CALLS - 2}
    assert call(box, {"kind": "submit"})["status"] == "closed"
    log = [json.loads(line) for line in (box / "transcript.jsonl").read_text().splitlines()]
    assert len(log) == trial.CALLS + 2 and all(e["request_bytes"] > 0 and e["response_bytes"] > 0 for e in log)


def test_a_rehearsal_solves_through_both_arms_and_the_score_counts_every_byte(tmp_path):
    trial.prepare(tmp_path, ["even_bit"])
    trial.rehearse(tmp_path)
    result = trial.score(tmp_path)
    assert {arm: result["arms"][arm]["solved"] for arm in trial.ARMS} == {"component": 1, "focused": 1}
    focused = next(r for r in result["rows"] if r["arm"] == "focused")
    assert focused["expansions"] == 1 and focused["refusals"] == 0 and focused["calls"] == 3
    assert result["focused_over_component"]["bytes_read"] < 1


def test_an_unsubmitted_or_wrong_repair_is_not_solved(tmp_path):
    trial.prepare(tmp_path, ["even_bit"])
    component, focused = tmp_path / "component/even_bit", tmp_path / "focused/even_bit"
    call(component, {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ return 0; }"})
    call(component, {"kind": "submit"})
    call(focused, {"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": "{ return 0; }"})
    rows = {r["arm"]: r for r in trial.score(tmp_path)["rows"]}
    assert not rows["component"]["solved"] and not rows["focused"]["solved"]

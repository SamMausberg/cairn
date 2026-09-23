"""The investigation packet: what the candidate history holds for one function now, compact, stale records counted
and never shown, experiments marked done once their runs are kept, and a delta that rebuilds the next packet. A fresh
agent resumes from it and runs nothing that already ran."""

import json
import shutil

import pytest

from cairn.agent.history import as_written, digest, identity, record
from cairn.agent.investigation import apply, delta, investigation
from cairn.cli import main
from cairn.perf.feedback import compare, parse_plan
from cairn.perf.plan_source import contract
from cairn.perf.profile import packaged
from cairn.perf.resources import device_identity, host_target
from cairn.perf.tune import tune
from cairn.projects.target import parse

MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
fn other(x:u64) -> u64 { return x + 1; }
"""
FAST = parse_plan("grain 1; lanes 8")
HOST = host_target(None, "clang++")
TARGETS = {"host": HOST, "device": device_identity(parse("sm_120"))}
MACHINE = packaged("zen4-7800x3d")  # 16 lanes: six lane caps by six grains


def timed(where, source, plan, ns):
    """A measurement as `cairn tune --measure` keeps one."""
    variant = {"plan": dict(plan)}
    made = identity(as_written(source, "spread"), variant, contract(source, "spread"), digest(HOST))
    label = f"plan spread {{ {' '.join(f'{k} {v};' for k, v in plan)} }}" if plan else "(no plan for spread)"
    detail = {"procedure": "cairn.perf.measure: the median of 3 blocks, on this host", "sizes": {"n": 1e6},
              "median_ns": ns}  # fmt: skip
    return record(where, "measurement", "spread", label, made, detail, variant)


def test_the_packet_holds_what_the_history_holds_now_and_marks_experiments_done(tmp_path):
    tune(MIX, "spread", [{"n": 1e6}], MACHINE, history=tmp_path)
    compare(MIX, "spread", (), FAST, [{"n": 1e6}], MACHINE, history=tmp_path)  # suggests timing both
    packet = investigation(MIX, "spread", tmp_path, TARGETS)
    assert packet["signature"].startswith("fn spread(") and packet["plan"] == "(no plan for spread)"
    assert [r["id"] for r in packet["regions"]] and packet["stale"] == {"records": 0, "by_part": {}}
    (search,) = packet["searches"]
    assert search["configurations"] == 36 and len(search["ranked"]) == 8 and search["chosen"].startswith("plan")
    (experiment,) = packet["experiments"]
    assert "time a and b" in experiment["run"] and experiment["done"] is False
    timed(tmp_path, MIX, (), 900.0)
    timed(tmp_path, MIX, FAST, 1300.0)
    after = investigation(MIX, "spread", tmp_path, TARGETS)
    assert after["experiments"][0]["done"] is True
    measured = after["candidates"]["plan spread { grain 1; lanes 8; }"]["measured"]
    assert measured[0]["median_ns"] == 1300.0 and "median of 3 blocks" in after["procedures"][measured[0]["procedure"]]
    change = delta(packet, after)
    assert set(change["candidates"]) == {"(no plan for spread)", "plan spread { grain 1; lanes 8; }"}
    assert apply(packet, change) == after
    with pytest.raises(ValueError):
        apply(after, change)  # taken from another packet


def test_a_record_that_no_longer_holds_is_counted_and_never_shown(tmp_path):
    timed(tmp_path, MIX, FAST, 1300.0)
    unrelated = MIX.replace("return x + 1;", "return x + 2;")
    assert investigation(unrelated, "spread", tmp_path, TARGETS)["candidates"]  # another function's edit
    edited = MIX.replace("0..64", "0..32")
    packet = investigation(edited, "spread", tmp_path, TARGETS)
    assert packet["candidates"] == {} and packet["stale"] == {"records": 1, "by_part": {"source": 1}}
    elsewhere = investigation(MIX, "spread", tmp_path, {"host": {"kind": "host", "cpu": "another machine"}})
    assert elsewhere["candidates"] == {} and elsewhere["stale"]["by_part"] == {"target": 1}


@pytest.mark.skipif(not shutil.which("clang++"), reason="measuring times a native build")
def test_a_fresh_agent_resumes_from_the_packet_and_reruns_nothing(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    ask = ["tune", str(source), "--symbol", "spread", "--at", "n=20000", "--measure", "2", "--format", "json"]
    assert main(ask) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["budget"]["runs"]["started"] > 0
    assert main(["state", str(source), "--symbol", "spread", "--format", "json"]) == 0
    packet = json.loads(capsys.readouterr().out)
    measured = {c for c, entry in packet["candidates"].items() if "measured" in entry}
    assert first["measured_best"] in measured and packet["searches"][0]["measured_best"] == first["measured_best"]
    assert main(ask) == 0  # the same question from an agent that has only the packet and the files
    again = json.loads(capsys.readouterr().out)
    assert (
        again["budget"]["runs"]["started"] == 0
        and again["budget"]["runs"]["kept"] == first["budget"]["runs"]["started"]
    )
    assert again["measured_best"] == first["measured_best"]
    assert main(["state", str(source), "--symbol", "spread", "--since", str(save(tmp_path, packet))]) == 0
    assert json.loads(capsys.readouterr().out)["protocol"] == "cairn.investigation-delta/1"


def save(where, packet):
    path = where / "packet.json"
    path.write_text(json.dumps(packet))
    return path

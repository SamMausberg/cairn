"""The candidate history: records carry what makes them evidence, equal records are kept once, a record holds only
while its identity matches the program, contract, target and compiler of now, and an analysis is kept by its key."""

import json
from pathlib import Path

import pytest

from cairn.agent import history
from cairn.agent.history import History, as_written, identity, record

S = """fn mix(v:u64) -> u64 { return mul_wrap(v ^ shr(v, 29), 0xbf58476d1ce4e5b9); }
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
fn other(x:u64) -> u64 { return x + 1; }
"""
HOST = {"kind": "host", "arch": "x86-64-v3"}
CONTRACT = {"signature": "fn spread(n:usize, out:rw<u64>[n]@host)"}


def measured(where, source=S, variant=None, target=HOST):
    ident = identity(as_written(source, "spread"), variant, CONTRACT, target)
    detail = {"procedure": "cairn.perf.measure: median of 9 blocks", "median_ns": 1200.0}
    return record(where, "measurement", "spread", "plan spread { lanes 4; }", ident, detail, variant)


def test_a_record_carries_what_makes_its_kind_evidence(tmp_path):
    ident = identity(as_written(S, "spread"), None, CONTRACT, HOST)
    refused = [
        ("timing", {"procedure": "x"}),  # no such kind
        ("measurement", {"median_ns": 5.0}),  # a time without the procedure that produced it
        ("profile", {"tool": "ncu"}),  # a profiler reading without the run it came from
        ("hypothesis", {}),
        ("failure", {"stage": "check"}),  # a failure says why
    ]
    for kind, detail in refused:
        with pytest.raises(ValueError):
            record(tmp_path, kind, "spread", "c", ident, detail)
    with pytest.raises(ValueError, match="identity"):
        record(tmp_path, "attempt", "spread", "c", {k: v for k, v in ident.items() if k != "target"}, {})
    with pytest.raises(ValueError, match="bytes"):
        record(tmp_path, "attempt", "spread", "c", ident, {"log": "x" * 70_000})
    assert History(tmp_path).records() == []


def test_an_equal_record_is_kept_once(tmp_path):
    first, second = measured(tmp_path), measured(tmp_path)
    assert first == second and len(History(tmp_path).records("spread")) == 1
    assert len((tmp_path / "records.jsonl").read_text().splitlines()) == 1
    measured(tmp_path, variant={"lanes": 4})
    assert len(History(tmp_path).records()) == 2 and History(tmp_path).records("other") == []


def judged(where, source, targets=None):
    held = set(targets or [history.digest(HOST)])
    return History(where).judged("spread", as_written(source, "spread"), {history.digest(CONTRACT)}, held)


def test_a_record_holds_until_its_function_contract_target_or_compiler_moves(tmp_path, monkeypatch):
    measured(tmp_path, variant={"lanes": 4})
    assert len(judged(tmp_path, S)["current"]) == 1
    elsewhere = S.replace("return x + 1;", "return x + 2;") + "// a comment\n"
    assert len(judged(tmp_path, elsewhere)["current"]) == 1  # an edit to another function is not this one's
    renamed = S.replace("mix(v:u64)", "mix(value:u64)").replace("(v ^ shr(v, 29)", "(value ^ shr(value, 29)")
    assert len(judged(tmp_path, renamed)["current"]) == 1  # the same code up to a renamed parameter
    planned = S + "plan spread { lanes 4; }\n"  # the variant written in: the function as written is the same
    assert len(judged(tmp_path, planned)["current"]) == 1
    callee = S.replace("0xbf58476d1ce4e5b9", "0x9e3779b97f4a7c15")  # what spread calls computes something else
    assert judged(tmp_path, callee)["stale"][0]["stale"] == ["source"]
    assert judged(tmp_path, S, targets=["sm_120"])["stale"][0]["stale"] == ["target"]
    monkeypatch.setattr(history, "compiler", lambda: "another compiler")
    assert judged(tmp_path, S)["stale"][0]["stale"] == ["compiler"]


def test_a_stale_record_is_never_returned_as_current(tmp_path):
    measured(tmp_path, variant={"lanes": 4})
    edited = S.replace("out[i] = mix(u64(i));", "out[i] = mix(u64(i)) ^ 1;")
    split = judged(tmp_path, edited)
    assert split["current"] == [] and split["stale"][0]["detail"]["median_ns"] == 1200.0


def test_an_analysis_is_kept_under_its_key_with_its_artifacts(tmp_path):
    kept = History(tmp_path)
    key = history.digest(["ptxas", "code", "sm_120"])
    assert kept.analysis(key) is None
    stored = kept.keep(key, {"registers": 12}, {"program.cubin": b"\x7fELF", "ptxas.log": b"Used 12 registers"})
    assert stored["registers"] == 12 and stored["key"] == key
    assert Path(stored["artifacts"]["ptxas.log"]).read_text() == "Used 12 registers"
    assert History(tmp_path).analysis(key)["artifacts"] == stored["artifacts"]
    (tmp_path / "analysis" / key[:2] / key / "result.json").write_text(json.dumps({"key": "0" * 64}))
    assert kept.analysis(key) is None  # a result that names another key is not this analysis
    with pytest.raises(ValueError):
        kept.analysis("../../etc")
    with pytest.raises(ValueError):
        kept.keep(history.digest("x"), {}, {"../escape": b""})


def test_a_line_that_does_not_read_is_skipped(tmp_path):
    measured(tmp_path)
    with (tmp_path / "records.jsonl").open("a") as out:
        out.write("not json\n" + json.dumps({"protocol": "other"}) + "\n")
    assert len(History(tmp_path).records()) == 1

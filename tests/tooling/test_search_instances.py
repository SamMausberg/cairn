"""The search over a parameterized implementation: every instance its `tune` clause lists is a candidate of its own,
chosen or timed only while the candidate history holds a validation of that instance as it is now, compiled for its
own kernels when it runs device code, and written back as `plan f use g[K];`. One implementation has one name in the
history, whoever recorded what about it."""

import json
import shutil

import pytest
from test_predict import MACHINE

from cairn.agent.history import History
from cairn.agent.investigation import investigation
from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf.feedback import compare, parse_candidate
from cairn.perf.resources import host_target
from cairn.perf.search import Budget
from cairn.perf.tune import tune
from cairn.projects.target import parse

TOTAL = """fn total(n:usize, xs:ro<u64>[n]) -> u64 effects(pure, par:host) {
  let mut s:u64 = 0;
  for i in 0..n { s += xs[i]; }
  return s;
}

// The lane pool's sum, from M elements on: the search tries each M.
fn total_from[M:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n >= M tune M in [16, 65536] {
  let s = reduce + parallel i in n yield xs[i];
  return s;
}
"""
SCALE = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) effects(pure, write:out, par:device, zero_init) {
  parallel i in n { out[i] = 2.0 * x[i]; }
}

// Blocks of K threads, one element each, for a length K divides.
fn scale_blocks[K:nat](n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) implements scale when n % K == 0
  tune K in [64, 256] {
  blocks b in n / K threads t in K { out[b * K + t] = 2.0 * x[b * K + t]; }
}
"""
NATIVE = pytest.mark.skipif(not shutil.which("clang++"), reason="validating an instance runs native builds")
NVCC = pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="compiling for the "
                          "device needs nvcc and cuobjdump; nothing runs on a GPU")  # fmt: skip


def validated(path, instance, history, capsys) -> dict:
    assert main(["validate", str(path), "--symbol", instance, "--history", str(history), "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


@NATIVE
def test_each_instance_is_a_candidate_and_only_a_validated_one_is_chosen_and_written(tmp_path, capsys):
    source = tmp_path / "total.cairn"
    source.write_text(TOTAL)
    history = tmp_path / "history"
    record = validated(source, "total_from[16]", history, capsys)
    assert record["tiles"]["16"] == "the condition's >= 16" and record["finite"]["implementation_ran"] > 0
    ask = ["tune", str(source), "--symbol", "total", "--at", "n=1e7", "--history", str(history), "--format", "json"]
    assert main([*ask, "--write"]) == 0
    answer = json.loads(capsys.readouterr().out)
    rows = {row.get("use"): row for row in answer["candidates"]}
    assert set(rows) == {None, "total_from[16]", "total_from[65536]"}
    assert rows["total_from[16]"]["parameters"] == {"M": 16} and rows["total_from[16]"]["plan"] == (
        "plan total use total_from[16];")  # fmt: skip
    assert rows["total_from[16]"]["validated"]["evidence"] == "finite-tested"
    assert rows["total_from[65536]"]["validated"].startswith("no validation holds")  # validated per value
    assert answer["chosen"]["use"] == "total_from[16]"
    assert "plan total use total_from[16];" in source.read_text()
    compile_program(source.read_text())
    widened = source.read_text().replace("tune M in [16, 65536]", "tune M in [16, 1024, 65536]")
    again = tune(widened, "total", [{"n": 1e7}], MACHINE, history=history)  # the list is not in an identity
    assert {r.get("use"): r["validated"] for r in again["candidates"] if r.get("use")}["total_from[16]"]["record"] == (
        rows["total_from[16]"]["validated"]["record"])  # fmt: skip
    edited = widened.replace("yield xs[i];", "yield xs[i] + 0;")
    moved = tune(edited, "total", [{"n": 1e7}], MACHINE, history=history)
    assert "use" not in moved["chosen"]  # an edited body is a new identity for every instance


@NATIVE
def test_the_packet_lists_an_instance_under_one_name_whoever_recorded_it(tmp_path, capsys):
    source = tmp_path / "total.cairn"
    source.write_text(TOTAL)
    history = tmp_path / "history"
    validated(source, "total_from[16]", history, capsys)
    tune(TOTAL, "total", [{"n": 20000}], MACHINE, measure=2, budget=Budget(runs=6), history=history)
    names = {r["candidate"] for r in History(history).records("total") if r["kind"] in {"validation", "measurement"}}
    assert "plan total use total_from[16];" in names and not any(n.startswith("total_from") for n in names)
    packet = investigation(TOTAL, "total", history, {"host": host_target(None, "clang++")})
    entry = packet["candidates"]["plan total use total_from[16];"]
    assert entry["validated"] and entry["measured"]  # one implementation, one name


def test_compare_names_an_instance():
    assert parse_candidate("grain 1; use total_from[ 16 ]") == ((("grain", 1),), "total_from[16]")
    report = compare(TOTAL, "total", ((), None), ((), "total_from[16]"), [{"n": 1e7}], MACHINE)
    assert report["b"] == "plan total use total_from[16];" and report["a"] == "(no plan for total)"


@NVCC
def test_each_device_instance_is_compiled_for_its_own_kernels(tmp_path):
    result = tune(SCALE, "scale", [{"n": 1e7}], MACHINE, budget=Budget(compiles=3), history=tmp_path,
                  device_target=parse("sm_120"))  # fmt: skip
    read = {row["use"]: row["resources"] for row in result["candidates"] if row.get("use") and "resources" in row}
    # the model prices the reference and both instances alike, since each moves the same bytes: the three compiles
    # read those three, and every plan variant of an instance takes its instance's reading, since a plan of the
    # reference changes nothing in the instance's kernel
    assert set(read) == {"scale_blocks[64]", "scale_blocks[256]"}
    assert all(r["registers"] > 0 and r["instructions"] > 0 for r in read.values())
    assert read["scale_blocks[64]"]["key"] != read["scale_blocks[256]"]["key"]
    assert result["budget"]["compiles"] == {"allowed": 3, "started": 3, "kept": 0}

"""`cairn explain` reads costs from the emitted C++ and the vectorizer's record, at the `.cairn` line of each."""

import json
import shutil
from pathlib import Path

import pytest

from cairn.agent.explain import PACKAGE, explain
from cairn.agent.hosts.edits import HANDLES, EditHost, EditSession
from cairn.cli import main

clang = pytest.mark.skipif(shutil.which("clang++") is None, reason="clang++ reports the vectorizer's verdicts")

SOURCE = """import std.vec (Vec);

fn total(n:usize, x:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n {
    sum = add_wrap(sum, x[i]);
  }
  return sum;
}

fn first_big(n:usize, x:ro<u64>[n], limit:u64) -> usize {
  for i in 0..n {
    if x[i] > limit { return i; }
  }
  return n;
}

fn grow(v:rw<Vec[u64]>, k:u64) { vec.push(v, k + 1); }

fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = u64(i); } }

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(mid, data[0..mid]);
  wait(left);
}

fn main() -> i32 {
  let mut data = Buf[u64](8);
  halves(len(data), data);
  let mut v = vec.new[u64]();
  grow(v, 1);
  return i32(total(len(data), data) + u64(first_big(len(data), data, 3)));
}
"""


def line(text: str) -> int:
    return SOURCE.splitlines().index(text) + 1


@pytest.fixture(scope="module")
def report():
    return explain(SOURCE, "p.cairn")


def test_guards_sit_on_the_line_they_guard(report):
    total = report["functions"]["total"]
    summed = f"p.cairn:{line('    sum = add_wrap(sum, x[i]);')}"
    assert total["at"] == f"p.cairn:{line('fn total(n:usize, x:ro<u64>[n]) -> u64 {')}"
    assert total["guards"]["sites"] == {"bounds": 1, "view_entry": 1}
    assert total["guards"]["by_line"] == {total["at"]: {"view_entry": 1}}  # Entry guards belong to the declaration.
    assert total["guards"]["discharged"] == {"bounds": 1}  # The loop's bound shows x[i] is in range.
    assert total["guards"]["discharged_by_line"] == {summed: {"bounds": 1}}
    first = report["functions"]["first_big"]["guards"]
    assert first["discharged_by_line"] == {f"p.cairn:{line('    if x[i] > limit { return i; }')}": {"bounds": 1}}
    grow = report["functions"]["grow"]["guards"]["by_line"]
    assert grow[f"p.cairn:{line('fn grow(v:rw<Vec[u64]>, k:u64) { vec.push(v, k + 1); }')}"] == {"overflow": 1}


def test_a_library_function_is_placed_in_its_own_file(report):
    push = report["functions"]["std.vec.push[u64]"]
    assert push["at"].startswith("cairn/std/vec.cairn:")
    text = (PACKAGE / "std/vec.cairn").read_text().splitlines()
    for where, kinds in push["guards"]["by_line"].items():
        if "overflow" in kinds:
            assert "+" in text[int(where.rsplit(":", 1)[1]) - 1], where  # The line really holds an addition.


def test_allocations_waits_and_costly_calls_are_listed(report):
    main_ = report["functions"]["main"]
    assert [a["owner"] for a in main_["allocations"]] == ["cr::Buf<std::uint64_t>"]
    assert {c["calls"] for c in main_["costly_calls"]} >= {"grow", "halves"}
    assert all("alloc" in c["effects"] for c in main_["costly_calls"] if c["calls"] == "grow")
    halves = report["functions"]["halves"]["synchronization"]
    assert [(s["kind"], s["at"]) for s in halves] == [
        ("spawn", f"p.cairn:{line('  let left = spawn fill(mid, data[0..mid]);')}"),
        ("wait", f"p.cairn:{line('  wait(left);')}"),
    ]


@clang
def test_loop_verdicts_are_the_vectorizers_at_the_for(report):
    assert report["vectorization"]["status"] == "observed"
    assert "-gline-tables-only" in report["vectorization"]["flags"]
    [summed] = [x for x in report["functions"]["total"]["loops"] if x["at"].startswith("p.cairn")]
    assert summed["at"].startswith(f"p.cairn:{line('  for i in 0..n {')}:") and summed["verdict"] == "vectorized"
    early = [x for x in report["functions"]["first_big"]["loops"] if x["verdict"] == "not vectorized"]
    assert early and all(x["reasons"] for x in early)


def test_only_clang_is_read_and_a_device_program_is_not_compiled():
    assert explain(SOURCE, "p.cairn", {"total"}, cxx="g++")["vectorization"]["status"] == "not-run"
    device = "fn scale(n:usize, x:rw<f32>[n]@device) { parallel i in n { x[i] = x[i] * 2.0; } }"
    result = explain(device, "d.cairn")
    assert result["vectorization"]["status"] == "not-run" and "nvcc" in result["vectorization"]["reason"]
    assert result["functions"]["scale"]["synchronization"][0]["kind"] == "device region completes"


def test_the_command_names_what_it_explains(tmp_path, capsys):
    (tmp_path / "p.cairn").write_text(SOURCE)
    assert main(["explain", str(tmp_path / "p.cairn"), "--symbol", "total", "--cxx", "g++"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["functions"]) == {"total"} and out["schema"] == "cairn.explain/1"
    assert main(["explain", str(tmp_path / "p.cairn"), "--symbol", "ghost", "--cxx", "g++"]) == 2
    assert "ghost" in json.loads(capsys.readouterr().out)["message"]


@clang
def test_an_agent_asks_for_the_explanation_of_its_admitted_candidate():
    host = EditHost()
    host.open(SOURCE, "total")
    before = host.respond({"protocol": HANDLES, "handle": "e1", "kind": "explain"})
    assert set(before["functions"]) == {"total", "main"}  # The target and what the focused packet disclosed.
    assert "overflow" not in before["functions"]["total"]["guards"]["emitted"]
    body = "{\n  let mut sum:u64 = 0;\n  for i in 0..n {\n    sum = sum + x[i];\n  }\n  return sum;\n}"
    assert host.respond({"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": body})["status"] == "typed"
    after = host.respond({"protocol": HANDLES, "handle": "e1", "kind": "explain"})["functions"]["total"]
    summed = f"program.cairn:{line('    sum = add_wrap(sum, x[i]);')}"  # The candidate keeps the line numbering.
    assert after["guards"]["by_line"][summed] == {"overflow": 1}  # Its checked sum, where it wrote it.
    assert after["guards"]["discharged_by_line"] == {summed: {"bounds": 1}}
    session = EditSession(SOURCE, "total")
    assert session.explain()["functions"]["total"]["guards"] == before["functions"]["total"]["guards"]


def test_inspect_attaches_the_explanation_on_request(tmp_path, capsys):
    (tmp_path / "p.cairn").write_text(SOURCE)
    assert main(["inspect", str(tmp_path / "p.cairn"), "--symbol", "grow", "--explain"]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert {"grow", "std.vec.push[u64]"} <= set(packet["performance"]["functions"])
    assert Path(packet["performance"]["functions"]["grow"]["at"]).name.startswith("program.cairn:")


def test_a_compiler_symbol_is_read_back_to_the_function_it_belongs_to():
    """explain, predict's loop reader and the device reader share one rule: a checked entry, a lean body, a lambda
    nested in one, and a function whose own name starts as a lean body's symbol does."""
    from cairn.compiler.lower.codegen import demangled

    names = {"sum", "m_dot", "ci_x"}
    assert demangled("cf_sum", names) == demangled("ci_sum", names) == "sum"
    assert demangled("_ZZ8ci_m_dotPKmmENKUlmE_clEm", names) == "m_dot"  # a lane's lambda inside ci_m_dot
    assert demangled("cf_ci_x", names) == "ci_x" and demangled("cr_gpu_launch", names) is None

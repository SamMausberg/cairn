"""The difference report: every line carries one of five labels, a static count is never given as a cause, a timing
comes from the history with its procedure and only while it holds, and the files behind a line are given on request."""

import json
import shutil
from pathlib import Path

import pytest
from test_predict import MACHINE

from cairn.agent.history import History, as_written, digest, identity, record
from cairn.cli import main
from cairn.perf.feedback import EXPERIMENT, HYPOTHESIS, compare, lines_for_people, parse_candidate, parse_plan
from cairn.perf.plan_source import contract
from cairn.perf.resources import device_identity, host_target
from cairn.projects.target import parse

MIX = """fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..64 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }
"""
BLUR = """fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}
"""
KINDS = {"compiler observation", "runtime measurement", "profiler observation", "hypothesis", "suggested experiment"}
FAST = parse_plan("grain 1; lanes 8")


def kept(where, source, plan, kind, target, detail, name="spread"):
    """A record as a search or the owner's make target would have kept it."""
    variant = {"plan": dict(plan)}
    ident = identity(as_written(source, name), variant, contract(source, name), target)
    return record(where, kind, name, "c", ident, detail, variant)


def test_a_plan_is_read_as_the_language_writes_it():
    assert parse_plan("plan f { grain 1; lanes 8; }") == parse_plan("grain 1; lanes 8") == FAST
    assert parse_plan("none") == parse_plan("") == ()
    for bad in ("grain one", "grain 1; grain 2", "speed 3"):
        with pytest.raises(ValueError):
            parse_plan(bad)


def test_every_line_is_labelled_and_no_count_is_given_as_a_cause():
    report = compare(MIX, "spread", (), FAST, [{"n": 1e6}], MACHINE)
    assert report["a"] == "(no plan for spread)" and report["b"] == "plan spread { grain 1; lanes 8; }"
    assert {x["kind"] for x in report["lines"]} <= KINDS
    predicted = report["lines"][0]
    assert predicted["kind"] == "compiler observation" and "the model, not a run" in predicted["by"]
    assert [x["kind"] for x in report["lines"]][-1] == EXPERIMENT  # nothing was timed, so timing is suggested
    assert "bottleneck" not in lines_for_people(report) and "artifacts" not in report
    assert all(" may " in x["text"] or "model" in x["by"] for x in report["lines"] if x["kind"] == HYPOTHESIS)


def test_a_timing_comes_from_the_history_with_its_procedure_and_only_while_it_holds(tmp_path):
    here = digest(host_target(None, "clang++"))
    procedure = "cairn.perf.measure: the median of 3 blocks, on this host"
    for plan, ns in (((), 900.0), (FAST, 1300.0)):
        kept(tmp_path, MIX, plan, "measurement", here, {"procedure": procedure, "sizes": {"n": 1e6}, "median_ns": ns})
    kept(tmp_path, MIX, FAST, "measurement", "another machine", {"procedure": procedure, "sizes": {"n": 1e6},
                                                                  "median_ns": 5.0})  # fmt: skip
    kept(tmp_path, MIX, FAST, "profile", here, {"tool": "perf stat", "run": "an explicit profiling run",
                                                 "reading": "12% of cycles stalled on memory"})  # fmt: skip
    report = compare(MIX, "spread", (), FAST, [{"n": 1e6}], MACHINE, history=tmp_path, artifacts=True)
    measured = [x for x in report["lines"] if x["kind"] == "runtime measurement"]
    assert sorted(x["median_ns"] for x in measured) == [900.0, 1300.0]  # the other machine's timing is left out
    assert all(x["by"] == procedure for x in measured)
    (profiled,) = [x for x in report["lines"] if x["kind"] == "profiler observation"]
    assert profiled["by"] == "perf stat, an explicit profiling run" and "stalled" in profiled["text"]
    predicted = report["lines"][0]
    if predicted["b"] < predicted["a"]:  # the model expected b faster, and the runs measured it slower
        assert any(x["kind"] == HYPOTHESIS and "the model predicted" in x["text"] for x in report["lines"])
    assert len(report["artifacts"]["records"]) == 3
    edited = MIX.replace("0..64", "0..65")  # the function changed: nothing measured before holds for it
    again = compare(edited, "spread", (), FAST, [{"n": 1e6}], MACHINE, history=tmp_path)
    assert not [x for x in again["lines"] if x["kind"] == "runtime measurement"]
    assert any("no longer hold" in x["text"] for x in again["lines"])


def test_an_implementation_is_compared_with_the_reference_as_its_own_code():
    total = ("fn total(n:usize, xs:ro<u64>[n]) -> u64 effects(pure, par:host) {\n  let mut s:u64 = 0;\n"
             "  for i in 0..n { s += xs[i]; }\n  return s;\n}\n")  # fmt: skip
    lanes = ("fn total_lanes(n:usize, xs:ro<u64>[n]) -> u64 implements total when n >= 65536 {\n"
             "  let s = reduce + parallel i in n yield xs[i];\n  return s;\n}\nplan total use total_lanes;\n")  # fmt: skip
    assert parse_candidate("use total_lanes") == parse_candidate("plan total use total_lanes;") == ((), "total_lanes")
    assert parse_candidate("grain 1; lanes 8") == (FAST, None)
    report = compare(total + lanes, "total", parse_candidate("none"), parse_candidate("use total_lanes"),
                     [{"n": 1e7}], MACHINE)  # fmt: skip
    assert report["a"] == "(no plan for total)" and report["b"] == "plan total use total_lanes;"
    predicted = report["lines"][0]
    assert predicted["b"] < predicted["a"]  # b is priced as total_lanes, the code that runs where n >= 65536


def test_the_command_compares_two_plans_instead_of_searching(tmp_path, capsys):
    source = tmp_path / "spread.cairn"
    source.write_text(MIX)
    asked = ["tune", str(source), "--symbol", "spread", "--at", "n=1e6", "--no-history", "--compare", "none"]
    assert main([*asked, "--compare", "grain 1; lanes 8", "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "cairn.compare/1" and report["b"] == "plan spread { grain 1; lanes 8; }"
    assert main([*asked, "--compare", "grain 1; lanes 8", "--format", "human"]) == 0
    assert "[compiler observation] cairn predict (the model, not a run)" in capsys.readouterr().out
    assert main([*asked, "--format", "json"]) == 2  # one plan is not a comparison
    assert main([*asked, "--compare", "lanes 5000", "--format", "json"]) == 1  # the checker refuses it: E-PLAN
    assert "E-PLAN" in capsys.readouterr().out


@pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="compiling for the device "
                    "needs nvcc and cuobjdump; nothing runs on a GPU")  # fmt: skip
def test_a_device_comparison_reads_both_compiles_and_hands_over_their_files(tmp_path):
    staged = parse_plan("stage 1; block 128")
    report = compare(BLUR, "blur", (), staged, [{"n": 1e7}], MACHINE, history=tmp_path, artifacts=True,
                     target=parse("sm_120"))  # fmt: skip
    by = {x["text"].split(":")[0]: x for x in report["lines"] if x["kind"] == "compiler observation"}
    assert by["staged tile bytes per block, computed from the plan"]["b"] == 528
    assert by["staged tile bytes per block, computed from the plan"]["by"] == "the plan"
    assert by["shared load instructions in the code"]["a"] == 0
    assert any(x["kind"] == HYPOTHESIS and "b reads through shared memory" in x["text"] for x in report["lines"])
    experiments = [x["text"] for x in report["lines"] if x["kind"] == EXPERIMENT]
    assert any("make tune-device" in t for t in experiments) and any("profiling run" in t for t in experiments)
    files = report["artifacts"]["b"]
    assert set(files) == {"program.cu", "program.cubin", "ptxas.log", "sass.txt"}
    assert all(Path(p).is_file() for p in files.values()) and "Used" in Path(files["ptxas.log"]).read_text()
    kept_now = History(tmp_path).records("blur")
    (claim,) = [r for r in kept_now if r["kind"] == "hypothesis"]  # kept as a hypothesis, never as a finding
    assert "may move fewer bytes" in claim["detail"]["claim"]
    assert all(r["detail"]["tests"] == [claim["id"]] for r in kept_now if r["kind"] == "experiment")
    launch = compare(BLUR, "blur", (), parse_plan("block 128"), [{"n": 1e7}], MACHINE, history=tmp_path,
                     target=parse("sm_120"))  # fmt: skip
    assert any(x.get("same_code") for x in launch["lines"])  # a launch item leaves the kernel's code as it was
    assert any(x["kind"] == HYPOTHESIS and "same device code" in x["text"] for x in launch["lines"])
    owners = {"procedure": "make tune-device, 9 blocks", "sizes": {"n": 1e7}, "median_ns": 2e5}
    kept(tmp_path, BLUR, staged, "measurement", device_identity(parse("sm_120")), owners, name="blur")
    again = compare(BLUR, "blur", (), staged, [{"n": 1e7}], MACHINE, history=tmp_path, compiles=0,
                    target=parse("sm_120"))  # fmt: skip
    assert any(x["kind"] == "runtime measurement" and x["by"] == "make tune-device, 9 blocks" for x in again["lines"])
    assert any(x["text"].startswith("registers per thread") or x["text"].startswith("SASS") for x in again["lines"])

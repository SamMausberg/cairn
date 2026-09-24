"""What the search spends: it generates candidates lazily with the model's likely winners first and stops when its
time is spent, counting what it never generated; candidates that lower to the same code share one price, one
compile and one row; and its wall time holds, a compile or run that would outlast it stopped at the budget."""

import itertools
import os
import shutil
import time

import pytest
from test_predict import MACHINE

from cairn.compiler.cairnc import compile_program, compile_source
from cairn.perf import device, search
from cairn.perf import tune as tuning
from cairn.perf.plan_source import Placement, written
from cairn.perf.resources import Inspector
from cairn.perf.search import Budget, Order, axes, lowered
from cairn.perf.tune import tune
from cairn.projects import target
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
# A host region and a device region: grain and lanes times every device item, 3600 plans.
BOTH = """fn both(n:usize, h:rw<u64>[n], out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n { h[i] = u64(i) * 3; }
  parallel j in n { out[j] = 2.0 * x[j]; }
}
"""
NVCC = pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="compiling for the "
                          "device needs nvcc and cuobjdump; nothing runs on a GPU")  # fmt: skip


def test_a_large_space_is_generated_only_as_far_as_the_time_goes(monkeypatch):
    def whole(*_):
        raise AssertionError("the search listed the space whole")

    monkeypatch.setattr(search, "space", whole)
    monkeypatch.setattr(tuning, "space", whole)
    clock = iter(range(100_000))
    monkeypatch.setattr(search.Spent, "out_of_time", lambda self, share=1.0: next(clock) >= 40)
    result = tune(BOTH, "both", [{"n": 1e6}], MACHINE, budget=Budget(compiles=0), device_target=parse("sm_120"))
    space = result["space"]
    assert space["configurations"] == 6 * 5 * 5 * 4 * 2 * 3 * 2  # fuse too: two regions over one extent
    assert space["checked"] == 40  # the one check the clock allowed each time it was read
    assert result["budget"]["undone"]["not generated: out of time"] == space["configurations"] - 40


def test_the_plan_with_no_items_and_each_item_alone_come_first_then_the_best_combination():
    order = Order(axes({"host"}, 8), ())
    walk = iter(order)
    first = [plan for plan, _ in itertools.islice(walk, 10)]
    assert first[0] == () and all(len(plan) == 1 for plan in first[1:])  # five grains and four lane caps alone
    order.priced |= dict.fromkeys(first, 100.0)
    order.priced[written({"grain": 64})] = 50.0  # alone, grain 64 halves the time
    order.priced[written({"lanes": 4})] = 80.0
    assert next(walk) == (written({"grain": 64, "lanes": 4}), search.KEEP)  # the two best items together
    assert order.generated == 11 and order.left() == order.configurations - 11 == 30 - 11


SEQ = """plan spread { lanes 2; }
fn spread_seq(n:usize, out:rw<u64>[n]) implements spread { for i in 0..n { out[i] = mix(u64(i)); } }
"""


def test_an_implementation_is_tried_with_the_reference_s_current_plan_alone():
    result = tune(MIX + SEQ, "spread", [{"n": 1e6}], MACHINE)
    plans = 6 * 5  # grain by lane caps up to the test machine's eight
    assert result["space"]["configurations"] == 2 * plans and result["space"]["checked"] == plans + 1
    assert result["budget"]["undone"] == {
        "not generated: an implementation is tried with the reference's current plan alone": plans - 1}  # fmt: skip
    (selecting,) = [row for row in result["candidates"] if row.get("use")]
    assert selecting["plan"] == "plan spread { lanes 2; } plan spread use spread_seq;"


def fake_read(calls: list):
    def kernels(source, target=None, timeout=600.0, keep=None):
        calls.append(source)
        return {"status": "read", "arch": "sm_120", "kernels": {"blur": [{"registers": 16, "spill_bytes": 0,
                "stack_bytes": 0, "shared_bytes": 0, "instructions": 40, "memory": {}, "sass_sha256": "ab"}]}}  # fmt: skip

    return kernels


def test_plans_that_lower_to_the_same_code_are_one_candidate_one_compile_and_one_row(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(device, "available", lambda: True)
    monkeypatch.setattr(device, "kernels", fake_read(calls))
    for item, values in {"block": (0,), "per_lane": (0,), "vector": (0,), "unroll": (0, 1)}.items():
        monkeypatch.setitem(search.SPACE, item, values)  # unroll 1 is one pass: the launch it lowers to is plain
    result = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=8), device_target=parse("sm_120"))
    assert result["space"] | {"refused": []} == {"configurations": 4, "checked": 4, "legal": 4, "same_code": 2,
                                                 "refused": []}  # fmt: skip
    rows = {row["plan"]: row for row in result["candidates"]}
    assert rows["(no plan for blur)"]["same_code"] == ["plan blur { unroll 1; }"]
    assert rows["plan blur { stage 1; }"]["same_code"] == ["plan blur { unroll 1; stage 1; }"]
    assert len(rows) == 2 and len(calls) == 2 and result["budget"]["compiles"]["started"] == 2


def test_a_launch_variant_takes_the_reading_of_its_kernel(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(device, "available", lambda: True)
    monkeypatch.setattr(device, "kernels", fake_read(calls))
    result = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=64), device_target=parse("sm_120"))
    kernels = {tuple((k, row.get(k, 0)) for k in ("unroll", "vector", "stage")) for row in result["candidates"]}
    assert len(calls) == len(kernels) == result["budget"]["compiles"]["started"]  # one compile a kernel
    staged = [row for row in result["candidates"] if row.get("stage")]
    tile = {row["plan"]: (((row.get("block") or 256) + 2) * 4 + 15) // 16 * 16 for row in staged}  # x, 16-byte rows
    assert staged and all(row["resources"]["dynamic_shared_bytes"] == tile[row["plan"]] for row in staged)
    shared = [row for row in result["candidates"] if "same_kernels_as" in row["resources"]]
    assert shared and all(row["resources"]["kept"] for row in shared)


def test_the_program_a_candidate_lowers_to_is_the_one_a_build_compiles():
    for source, name, plan in ((BLUR, "blur", "plan blur { stage 1; block 128; }\n"), (MIX, "spread", "")):
        p, checker, _ = compile_program(source + plan)
        cpp, code = lowered(p, checker, name)
        assert cpp == compile_source(source + plan)[0] and "cf_@" in code
    inspector = Inspector(parse("sm_120"))
    p, checker, _ = compile_program(BLUR)
    assert inspector.key(BLUR, lowered(p, checker, "blur")[0]) == inspector.key(BLUR)  # kept readings still answer


@NVCC
def test_launch_items_leave_the_kernel_as_it_was():
    inspector = Inspector(parse("sm_120"))
    read = {}
    for plan in ("", "block 64; per_lane 16;", "stage 1;", "stage 1; block 512;"):
        source = BLUR + f"plan blur {{ {plan} }}\n" * bool(plan)
        p, checker, _ = compile_program(source)
        read[plan] = inspector.inspect(source, "blur", p, checker)
    same = ("registers", "spill_bytes", "stack_bytes", "shared_bytes", "instructions", "sass_sha256")
    first, launched, staged, wider = read.values()
    assert {k: first[k] for k in same} == {k: launched[k] for k in same}
    assert {k: staged[k] for k in same} == {k: wider[k] for k in same}
    assert staged["dynamic_shared_bytes"] != wider["dynamic_shared_bytes"]  # a tile's width is the plan's


def after(source: str, name: str) -> float:
    """What a search may take past its budget here: the one step under way when the time ran out, and naming the
    regions and writing the rows after it. A few checks' worth, bounded by ten checks of `source` as this machine,
    loaded or not, runs them, and never under two seconds."""
    began = time.monotonic()
    lowered(*compile_program(source)[:2], name)
    return max(2.0, 10 * (time.monotonic() - began))


def stub(directory, name: str, answers: str):
    """A tool on PATH that answers what `answers` says and otherwise sleeps: a compile that never ends in time."""
    path = directory / name
    path.write_text(f'#!/bin/sh\ncase "$*" in\n{answers}\n*) sleep 60 ;;\nesac\n')
    path.chmod(0o755)


@pytest.fixture
def fresh_toolkit():
    """The toolkit asked again inside a test that puts a stub nvcc on PATH, and again after it: `target.toolkit`
    keeps its first answer for the process, which without a real nvcc is that there is none."""
    target.toolkit.cache_clear()
    yield
    target.toolkit.cache_clear()


def test_a_compile_that_would_outlast_the_budget_is_stopped_there(tmp_path, monkeypatch, fresh_toolkit):
    stub(tmp_path, "nvcc", '*--version*) echo "Cuda compilation tools, release 13.2, V13.2.51" ;;\n'
         '*--list-gpu-code*) echo sm_120 ;;')  # fmt: skip
    stub(tmp_path, "cuobjdump", "")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    for item in ("block", "per_lane", "vector"):
        monkeypatch.setitem(search.SPACE, item, (0,))
    began = time.monotonic()
    result = tune(BLUR, "blur", [{"n": 1e7}], MACHINE, budget=Budget(compiles=8, seconds=4),
                  device_target=parse("sm_120"))  # fmt: skip
    took = time.monotonic() - began
    assert took < 4 + after(BLUR, "blur"), took  # the stub would have slept a minute
    undone = result["budget"]["undone"]
    assert undone["not inspected: stopped at the time budget"] == 1 and result["budget"]["compiles"]["started"] == 1
    assert "not inspected: out of time" in undone
    assert not any("resources" in row for row in result["candidates"])


def test_a_timed_run_that_would_outlast_the_budget_is_stopped_there(tmp_path):
    stub(tmp_path, "sleepy++", '*--version*) echo "clang version 21.1.8" ;;')
    began = time.monotonic()
    result = tune(MIX, "spread", [{"n": 20000}], MACHINE, measure=2, cxx=str(tmp_path / "sleepy++"),
                  budget=Budget(seconds=3))  # fmt: skip
    took = time.monotonic() - began
    assert took < 3 + after(MIX, "spread"), took
    undone = result["budget"]["undone"]
    assert undone["not measured: stopped at the time budget"] == 1 and result["budget"]["runs"]["started"] == 1
    assert result["measured_best"] is None


def test_a_placement_reuses_the_program_it_is_given():
    p, checker, _ = compile_program(MIX)
    assert Placement(MIX, "spread", (p, checker)).apply(written({"lanes": 4})) == Placement(MIX, "spread").apply(
        written({"lanes": 4}))  # fmt: skip

"""`cairn predict`: what the counter counts, how the model prices it, and that nothing it answers is a measurement.

The counts are pinned on the preregistered suite's kernels and on small programs written here. The model is priced
against a machine made up for the test, so each assertion reads as arithmetic on its numbers, not as a claim about
the machine the suite runs on.
"""

import json
import shutil
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_program
from cairn.perf import model
from cairn.perf.calibrate import nnls
from cairn.perf.profile import Host, Profile, packaged
from cairn.perf.report import delta, parse_sizes, report
from cairn.perf.work import Poly, count

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "bench/suite/kernels"


def costs(source: str):
    p, checker, _ = compile_program(source)
    return count(p, checker)


def suite(kernel: str):
    return costs((SUITE / kernel / "kernel.cairn").read_text())


def rendered(table) -> dict:
    return {k: n.render() for k, n in table.items()}


MACHINE = Profile(
    "a machine made up for the test",
    "measured",
    "",
    Host(
        lanes=8,
        cache={"l1": 32768, "l2": 1 << 20, "l3": 32 << 20},
        read={level: {"1": 100.0, "all": 400.0} for level in ("l1", "l2", "l3", "dram")},
        write={level: {"1": 50.0, "all": 200.0} for level in ("l1", "l2", "l3", "dram")},
        ops={
            "x86-64-v4": {
                "vector": {"f32": 0.01, "load": 0.01, "store": 0.01, "mul": 1.0, "int": 0.5},
                "scalar": {"f32": 1.0, "load": 1.0, "store": 1.0, "mul": 2.0, "int": 1.0, "f64_fold": 3.0, "div": 20.0},
                "keeps_scalar": ["div"],
            }
        },
        pool={"fork_ns": 1000.0, "per_lane_ns": 100.0, "cutoff": 16384, "grain": 8192},
        spawn_ns=50_000.0,
        irregular_ns={"l1": 1.0, "l2": 2.0, "l3": 5.0, "dram": 50.0},
        atomic_ns={"shared": 10.0},
    ),
    None,
)


def test_a_region_s_work_is_counted_per_index_in_its_own_sizes():
    region = suite("saxpy_f32")["saxpy_f32"].regions[0]
    assert (region.kind, region.count.render(), region.runs.render()) == ("host", "n", "1")
    assert rendered(region.body.ops) == {"f32": "2", "load": "2", "store": "1"}
    assert rendered(region.body.reads) == {"x": "4", "y": "4"} and rendered(region.body.writes) == {"out": "4"}
    assert rendered(region.body.footprint) == {"x": "4*n", "y": "4*n", "out": "4*n"}


def test_a_callee_is_counted_in_the_caller_s_sizes():
    body = suite("mixed_u64")["mixed_u64"].regions[0].body
    assert body.ops["int"].render() == "16" and body.ops["mul"].render() == "8"  # eight rounds of shr, xor, mul_wrap


def test_neighbours_on_one_pass_are_one_stream():
    body = suite("stencil_1d")["stencil_1d"].regions[0].body
    assert rendered(body.reads) == {"x": "4"}  # x[i-1], x[i] and x[i+1]: one new element a pass
    assert "int_checked" not in body.ops  # the facts discharged i + 1 and i - 1, so they are plain arithmetic
    shifted = costs(
        "fn f(n:usize, o:rw<u64>[n], m:usize, x:ro<u64>[m], s:usize) { for i in 0..n { o[i] = x[i + s]; } }"
    )
    assert rendered(shifted["f"].seq.ops)["index_checked"] == "n"  # checked, but on a binder: it vectorizes


def test_an_address_from_data_is_irregular_and_priced_by_its_own_view():
    cost = suite("histogram_u32")["histogram_u32"]
    assert rendered(cost.seq.irregular) == {"out": "n"}  # reading a bin and writing it back: one trip to its line
    assert cost.seq.footprint["out"].render() == "2048"
    blocks = suite("histogram_u32")["histogram_u32_blocks"]
    assert blocks.regions[0].count.render() == "1.53e-05*n + 1"  # (n + BLOCK - 1) / BLOCK blocks
    assert blocks.regions[0].body.reads["x"].render() == "262144"  # each block its 65536 elements, min() at its bound


def test_folds_that_wait_on_the_last_step_are_their_own_kind():
    assert suite("dot_f64")["dot_f64"].seq.ops["f64_fold"].render() == "n"
    assert suite("sum_u64_wrap")["sum_u64_wrap"].seq.ops["int"].render() == "n"  # wrapping sums reassociate
    loop = costs(
        "fn acc(n:usize, x:ro<f64>[n]) -> f64 { let mut t:f64 = 0.0; for i in 0..n { t = t + x[i]; } return t; }"
    )
    assert loop["acc"].seq.ops["f64_fold"].render() == "n"
    assert suite("compact_even")["compact_even"].seq.ops["collect"].render() == "n"


def test_tasks_carry_their_share():
    cost = suite("tasks_split")["tasks_split"]
    assert len(cost.tasks) == 4
    assert {t.seq.writes["out"].render() for _, t in cost.tasks} == {"2*n"}  # a quarter of n u64 each


def test_what_nothing_bounds_is_named_and_lowers_confidence():
    cost = costs("fn spin(n:usize) -> u64 { let mut k:u64 = 0; while k < 10 { k = k + 1; } return k; }")["spin"]
    assert any("while" in u for u in cost.unknown)
    predicted = model.predict(cost, MACHINE, {"n": 10})
    assert predicted["confidence"] == "low" and predicted["measure"]
    recursive = costs("fn down(n:u64) -> u64 { if n == 0 { return 0; } return down(n - 1); }")["down"]
    assert any("recursive" in u for u in recursive.unknown)


def test_polynomials():
    n, m = Poly.var("n"), Poly.var("m")
    p = n * m * 2.0 + n + Poly.of(3)
    assert p.render() == "2*m*n + n + 3" and p.value({"n": 10, "m": 5}) == 113.0
    assert p.subst({"n": m * 4.0}).render() == "8*m*m + 4*m + 3"
    assert p.value({"n": 1}) is None and (n * 2.0).join(n * 3.0 + Poly.of(1)).render() == "3*n + 1"


def test_a_small_region_is_the_loop_it_replaces_and_a_wide_one_pays_the_pool():
    cost = suite("saxpy_f32")["saxpy_f32"]
    small = model.predict(cost, MACHINE, {"n": 1000})
    assert small["parts"][0]["lanes"] == 1 and small["parts"][0]["start_ns"] == 0
    wide = model.predict(cost, MACHINE, {"n": 1 << 20})
    assert wide["parts"][0]["lanes"] == 8 and wide["parts"][0]["start_ns"] == 1000.0 + 7 * 100.0
    assert wide["bound"].startswith("memory") and 0 < wide["speed_of_light"] <= 1


def test_a_plan_that_keeps_one_lane_keeps_the_loop():
    source = (SUITE / "saxpy_f32/kernel.cairn").read_text() + "\nplan saxpy_f32 { lanes 1; }\n"
    predicted = model.predict(costs(source)["saxpy_f32"], MACHINE, {"n": 1 << 20})
    assert predicted["parts"][0]["lanes"] == 1


def test_a_division_keeps_the_loop_scalar():
    fast = costs("fn f(n:usize, o:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { o[i] = mul_wrap(x[i], 3); } }")["f"]
    slow = costs("fn f(n:usize, o:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { o[i] = x[i] / 3; } }")["f"]
    assert model.mode(fast.seq, MACHINE.host) == "vector" and model.mode(slow.seq, MACHINE.host) == "scalar"
    assert model.predict(slow, MACHINE, {"n": 1000})["ns"] > model.predict(fast, MACHINE, {"n": 1000})["ns"]


def test_a_missing_size_is_said_and_is_never_zero_success():
    predicted = model.predict(suite("saxpy_f32")["saxpy_f32"], MACHINE, {})
    assert predicted["confidence"] == "low" and any("no size given for n" in w for w in predicted["why"])


def test_a_region_is_predicted_faster_than_the_loop_it_replaces_at_scale():
    loop = "fn f(n:usize, o:rw<u64>[n], x:ro<u64>[n]) { for i in 0..n { o[i] = mul_wrap(x[i], 3); } }"
    lanes = "fn f(n:usize, o:rw<u64>[n], x:ro<u64>[n]) { parallel i in n { o[i] = mul_wrap(x[i], 3); } }"
    change = delta(loop, lanes, [{"n": 1e3}, {"n": 1e8}], profile=MACHINE)["functions"]["f"]
    assert change[0]["change_ns"] == 0.0  # below the cutoff the region is the loop
    assert change[1]["ratio"] < 0.5 and change[1]["confidence"] == "medium"  # a wide region is an approximation


def test_the_report_names_its_profile_and_says_nothing_ran():
    out = report((SUITE / "saxpy_f32/kernel.cairn").read_text(), profile=MACHINE)
    assert out["schema"] == "cairn.predict/1" and "nothing was built or run" in out["predicted"]
    entry = out["functions"]["saxpy_f32"]
    assert [p["sizes"]["n"] for p in entry["predictions"]] == [1e3, 1e5, 1e7] and "ns*n" in entry["formula"]
    assert parse_sizes(["n=1e6,m=64"]) == [{"n": 1e6, "m": 64.0}]
    with pytest.raises(ValueError):
        parse_sizes(["n"])


def test_the_command_line_prices_and_compares(tmp_path, capsys):
    before, after = tmp_path / "before.cairn", tmp_path / "after.cairn"
    before.write_text("fn f(n:usize, o:rw<u64>[n]) { for i in 0..n { o[i] = u64(i); } }\n")
    after.write_text("fn f(n:usize, o:rw<u64>[n]) { parallel i in n { o[i] = u64(i); } }\n")
    assert main(["predict", str(after), "--at", "n=1e7", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["functions"]["f"]["predictions"][0]["sizes"] == {"n": 1e7}
    assert main(["predict", str(after), "--against", str(before), "--at", "n=1e8", "--format", "json"]) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["schema"] == "cairn.predict.delta/1" and compared["functions"]["f"][0]["ratio"] < 1


def test_the_packaged_profiles_say_where_their_numbers_come_from():
    host = packaged("zen4-7800x3d")
    assert host.origin == "measured" and host.host and "x86-64-v4" in host.host.ops
    card = packaged("rtx-5070-ti")
    assert card.origin == "specification" and card.device and card.device.sms == 70
    assert set(card.source["source"]["assumed"]) >= {"launch_ns", "link_ns"}
    assert card.device.occupancy(32) == 1.0 and card.device.occupancy(255) < 0.2


def test_an_allocation_the_allocator_maps_afresh_pays_a_fault_a_page():
    fresh = Profile(MACHINE.name, "measured", "", Host(**{**MACHINE.host.__dict__, "alloc_ns": 20.0, "page_ns": 1000.0}),
                    None)  # fmt: skip
    source = (
        "fn scratch(n:usize) -> u64 { let b = Buf[u64](n); let t = reduce add_wrap for i in n yield b[i]; return t; }"
    )
    c = costs(source)["scratch"]
    parts = {n: {p["what"]: p["ns"] for p in model.predict(c, fresh, {"n": n})["parts"]} for n in (1 << 20, 1 << 23)}
    assert parts[1 << 20]["allocation"] == pytest.approx(20.0 + (8 << 20) / 50.0, rel=1e-3)  # 8 MiB: memory kept
    mapped = parts[1 << 23]["allocation"]  # 64 MiB: mapped afresh, 16384 pages faulted on the first touch
    assert mapped == pytest.approx(20.0 + (64 << 20) / 50.0 + 16384 * 1000.0, rel=1e-3)  # four significant digits
    held = packaged("zen4-7800x3d").host  # measured on a kernel that reads its allocation, so none of it was elided
    assert held.alloc_ns > 5 and held.page_ns > 100 and held.mapped_bytes == 32 << 20


def test_the_fit_finds_non_negative_costs():
    rows, targets = [[1, 1], [1, 0], [0, 1]], [3.0, 1.0, 2.0]
    assert [round(v, 3) for v in nnls(rows, targets)] == [1.0, 2.0]
    assert min(nnls([[1, 1], [1, 2]], [1.0, 0.5])) >= 0


@pytest.mark.skipif(not shutil.which("clang++"), reason="the loop reader reads clang++ assembly")
def test_llvm_mca_reads_a_dependent_chain_at_its_latency():
    from cairn.perf.native import loop_cycles, mca

    if not mca():
        pytest.skip("llvm-mca is absent")
    chain = "fn chain(n:usize, seed:u64) -> u64 { let mut w = seed; for i in 0..n { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); } return w; }"
    cycles = loop_cycles(chain, "chain", "imulq")
    assert cycles is not None and 3 <= cycles <= 8  # shr, xor and a 64-bit multiply, one after another


@pytest.mark.skipif(not shutil.which("nvcc"), reason="reading a kernel needs nvcc and cuobjdump")
def test_a_kernel_is_read_without_touching_a_device():
    from cairn.perf.device import kernels

    source = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"
    read = kernels(source)
    assert read["status"] == "read" and read["arch"] == "sm_120"
    kernel = read["kernels"]["scale"][0]
    assert 0 < kernel["registers"] <= 64 and kernel["spill_bytes"] == 0
    assert kernel["memory"].get("global_load", 0) >= 1 and kernel["memory"].get("global_store", 0) >= 1


@pytest.mark.skipif(not shutil.which("clang++"), reason="timing needs a host compiler")
def test_the_host_timer_times_and_refuses_device_programs():
    from cairn.perf.measure import time

    timed = time("fn fill(n:usize, o:rw<u64>[n]) { for i in 0..n { o[i] = u64(i); } }", "fill", {"n": 1024},
                 block_ns=2e5, blocks=3)  # fmt: skip
    assert timed["status"] == "measured" and 0 < timed["min_ns"] <= timed["median_ns"] <= timed["max_ns"]
    with pytest.raises(ValueError, match="never timed"):
        time("fn scale(n:usize, x:rw<f32>[n]@device) { parallel i in n { x[i] = 2.0 * x[i]; } }", "scale", {"n": 8})


def test_an_agent_asks_what_its_admitted_candidate_is_predicted_to_change():
    from cairn.agent.agent_tools import HANDLES, EditHost
    from cairn.compiler.cairnc import Diagnostic

    source = "fn fill(n:usize, o:rw<u64>[n]) { for i in 0..n { o[i] = u64(i); } }\n"
    row = compile_program(source)[2]["fill"]["effects"]
    host = EditHost()
    host.open(source, "fill", {"allowed_effects": [*row, "par:host"]})  # the host allows lanes; the agent cannot
    ask = {"protocol": HANDLES, "handle": "e1", "kind": "predict", "sizes": [{"n": 1e7}]}
    before = host.respond(ask)
    assert before["schema"] == "cairn.predict/1" and set(before["functions"]) == {"fill"}
    body = "{\n  parallel i in n { o[i] = u64(i); }\n}"
    assert host.respond({"protocol": HANDLES, "handle": "e1", "kind": "body", "replacement": body})["status"] == "typed"
    after = host.respond(ask)
    assert after["schema"] == "cairn.predict.delta/1" and after["functions"]["fill"][0]["ratio"] < 1
    with pytest.raises(Diagnostic, match="sizes"):
        host.respond({**ask, "sizes": {"n": 1}})


def test_a_parameter_no_count_depends_on_is_not_an_extent():
    source = (ROOT / "examples/apps/simulator/src/stencil.cairn").read_text()
    interior = costs(source)["interior"]  # interior(i, n) does the same work for every i and n
    assert interior.extents == [] and model.formula(interior, MACHINE).endswith(" ns")
    assert suite("saxpy_f32")["saxpy_f32"].extents == ["n"]


def test_a_small_cost_per_element_is_never_printed_as_free():
    cheap = costs("fn f(n:usize, o:rw<u8>[n]) { parallel i in n { o[i] = 1; } }")["f"]
    shown = model.formula(cheap, MACHINE)
    assert "0 ns*n" not in shown and ("ps*n" in shown or "ns*n" in shown)
    assert all(piece["per_element_ns"] > 0 for piece in model.regimes(cheap, MACHINE))
    blank = costs("fn g(a:f32, b:f32) -> f32 = a * b + a;")["g"]
    assert model.predict(blank, MACHINE, {})["ns"] > 0  # a kind the fit left at zero still costs a fraction of a cycle


def test_device_work_is_priced_from_the_specification_and_says_so():
    source = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"
    small, large = (model.predict(costs(source)["scale"], MACHINE, {"n": n}) for n in (1e3, 1e8))
    assert small["bound"] == "launch" and large["bound"] == "device memory" and large["ns"] > small["ns"]
    assert large["confidence"] == "low" and any("specification" in w for w in large["why"])

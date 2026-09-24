"""`cairn predict`, `cairn explain` and `cairn tune` on cooperative regions: a region is priced by its blocks, the
threads each holds, its shared memory and what every thread does between barriers, as a warp runs it; a pipeline's
depth changes its shared memory, the blocks an SM holds and the copies it keeps in flight, and nothing else; explain
shows each barrier, wait and warp collective at its line; and tune searches a block shape and a depth as the natural
parameters of an implementation. Nothing here runs device code; the ptxas tests compile for sm_120 only."""

import json
import shutil
from pathlib import Path

import pytest

from cairn.agent.explain import explain
from cairn.cli import main
from cairn.compiler.cairnc import compile_program, compile_source
from cairn.perf import report
from cairn.perf.device import available, kernels
from cairn.perf.feedback import compare, parse_candidate
from cairn.perf.profile import packaged
from cairn.perf.search import Budget
from cairn.perf.tune import tune
from cairn.perf.work import count
from cairn.projects.project import load_project
from cairn.projects.target import LIMITS, parse
from emitted import device_build, refused

ROOT = Path(__file__).resolve().parents[2]
COOPERATIVE = ROOT / "examples" / "cooperative"
TENSOR = ROOT / "examples" / "tensor"
NVCC = pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="reading a kernel needs "
                          "nvcc and cuobjdump")  # fmt: skip

# A row a block, through a pipeline of D stages of 1024 u64: a stage is 8 KiB, so the depth decides how many blocks
# an SM holds.
PIPELINED = """fn sums[D:nat](rows:usize, cols:usize, n:usize, x:ro<u64>[n]@device, m:usize, out:rw<u64>[m]@device) {
  let steps = (cols + 1023) / 1024;
  blocks r in rows threads t in 256 {
    pipeline tiles:u64[1024] depth D;
    for k in 0..D - 1 {
      let start = min(k * 1024, cols);
      tiles.fill(x, r * cols + start, min(1024, cols - start));
    }
    let mut sum:u64 = 0;
    for k in 0..steps {
      let ahead = min((k + D - 1) * 1024, cols);
      tiles.fill(x, r * cols + ahead, min(1024, cols - ahead));
      tiles.wait();
      sum += tiles[t] + tiles[t + 256] + tiles[t + 512] + tiles[t + 768];
      tiles.release();
      barrier;
    }
    out[r * 256 + t] = sum;
  }
}
fn two(rows:usize, cols:usize, n:usize, x:ro<u64>[n]@device, m:usize, out:rw<u64>[m]@device) { sums[2](rows, cols, n, x, m, out); }
fn three(rows:usize, cols:usize, n:usize, x:ro<u64>[n]@device, m:usize, out:rw<u64>[m]@device) { sums[3](rows, cols, n, x, m, out); }
"""
# The same 32 x 32 tile read down its columns, P elements a row: 32 rows of 32 put a column in one bank.
COLUMNS = """fn columns[P:nat](g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  blocks b in g threads tx, ty in 32, 8 {
    shared tile:f32[1056] = zeroed;
    for k in 0..4 { tile[(ty + 8 * k) * P + tx] = x[(b * 32 + ty + 8 * k) * 32 + tx]; }
    barrier;
    for k in 0..4 { out[(b * 32 + tx) * 32 + ty + 8 * k] = tile[tx * P + ty + 8 * k]; }
  }
}
fn rows(g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { columns[32](g, n, out, x); }
fn padded(g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { columns[33](g, n, out, x); }
"""


SPARSE = """fn every8(g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  blocks b in g threads t in 256 { out[b * 256 + t] = x[(b * 256 + t) * 8]; }
}
"""


def costs(source: str, *names: str):
    p, checker, _ = compile_program(source)
    return count(p, checker, set(names))


def region(cost):
    (found,) = [r for r in cost.regions if r.kind == "cooperative"]
    return found


def part(predicted: dict) -> dict:
    (found,) = [p for p in predicted["parts"] if "cooperative region" in p["what"]]
    return found


def test_a_region_is_priced_by_its_blocks_and_threads_not_as_one_thread():
    project = load_project(COOPERATIVE / "gpu.toml")
    answer = report.report(project.source, [{"gx": 100, "gy": 100, "g": 1000, "n": 256_000}],
                           {"transpose", "block_sums"}, site=project.site)  # fmt: skip
    transpose = answer["functions"]["transpose"]
    assert transpose["extents"] == ["gx", "gy", "n"]  # the grid's extents are sizes the cost depends on
    (described,) = transpose["regions"]
    d = described["cooperative"]
    assert (described["kind"], described["count"], d["threads_per_block"]) == ("cooperative", "gx*gy", 256)
    assert d["shared_bytes_per_block"] == 4224 and d["barriers"] == ["src/device_kernels.cairn:15"]
    assert d["resident"]["blocks_per_sm"] == 6 and d["resident"]["limited_by"] == ["threads"]
    predicted = transpose["predictions"][0]
    found = part(predicted)
    assert found["blocks"] == 10_000 and found["device_bytes"] == 10_000 * 256 * 32  # 16 bytes read, 16 written
    assert predicted["ns"] > 100_000 and predicted["confidence"] == "low"  # 82 MB, never one thread's 32 ns
    assert any("published specification" in why for why in predicted["why"])
    sums = answer["functions"]["block_sums"]["regions"][0]["cooperative"]
    assert sums["warp_collectives"] == [{"at": "src/device_kernels.cairn:37", "operation": "reduce + warp"}]


def test_a_branch_costs_what_the_warps_that_enter_it_issue():
    cost = costs(load_project(COOPERATIVE / "gpu.toml").source, "block_sums")["block_sums"]
    ops = region(cost).body.ops
    assert ops["shuffle"].render() == "0.625"  # five shuffles, in the one warp of eight that runs `if t < 32`
    assert ops["barrier"].render() == "4"


def test_the_census_prices_bank_conflicts_and_the_sectors_a_warp_touches():
    found = costs(COLUMNS, "columns[32]", "columns[33]")
    conflicted, padded = (region(found[f"columns[{p}]"]).body for p in (32, 33))
    zeroed, written = 4224 / 128 / 256, 4 / 32
    assert conflicted.ops["shared_wavefront"].value({}) == pytest.approx(zeroed + written + 4 * 32 / 32)
    assert padded.ops["shared_wavefront"].value({}) == pytest.approx(zeroed + written + 4 / 32)
    assert conflicted.reads["x"].value({}) == 16  # a warp reads 32 adjacent f32 a pass: four sectors
    assert conflicted.writes["out"].value({}) == 16  # a column a warp, but the block's phase writes whole sectors
    sparse = region(costs(SPARSE, "every8")["every8"]).body
    assert sparse.reads["x"].value({}) == 32 and sparse.writes["out"].value({}) == 4  # a sector for each f32 read


def test_raising_a_pipeline_s_depth_changes_its_shared_memory_occupancy_and_copies_in_flight():
    answer = report.report(PIPELINED, [{"rows": 8, "cols": 1e6}, {"rows": 20_000, "cols": 1e6}], {"sums[2]", "sums[3]"})
    two, three = (answer["functions"][f"sums[{d}]"] for d in (2, 3))
    (a,), (b,) = (x["regions"] for x in (two, three))
    assert [x["cooperative"]["shared_bytes_per_block"] for x in (a, b)] == [16384, 24576]
    assert [x["cooperative"]["pipelines"][0]["waits"][0]["wait_group"] for x in (a, b)] == [1, 2]
    held = [x["cooperative"]["resident"] for x in (a, b)]
    assert [h["blocks_per_sm"] for h in held] == [5, 4] and held[1]["limited_by"] == ["shared memory"]
    ops = [x["per_index"]["ops"] for x in (a, b)]  # the same barriers and waits a thread: only the prologue grew
    assert ops[0]["barrier"] == ops[1]["barrier"] and ops[0]["stage_wait"] == ops[1]["stage_wait"]
    small = [part(x["predictions"][0]) for x in (two, three)]
    assert [f["pipelines"][0]["stages_in_flight_per_block"] for f in small] == [2, 3]
    assert small[1]["copy_gbps"] == pytest.approx(small[0]["copy_gbps"] * 1.5, rel=1e-3)  # eight blocks: latency
    assert three["predictions"][0]["ns"] < two["predictions"][0]["ns"]
    large = [part(x["predictions"][1]) for x in (two, three)]
    assert large[0]["copy_gbps"] == large[1]["copy_gbps"] == pytest.approx(896 * 0.85)  # the bandwidth caps both


def test_predict_against_names_what_the_depth_changed(tmp_path, capsys):
    before, after = tmp_path / "two.cairn", tmp_path / "three.cairn"
    for path, depth in ((before, 2), (after, 3)):
        path.write_text(PIPELINED.split("fn two(")[0].replace("fn sums[D:nat]", "fn sums").replace("depth D", f"depth {depth}")
                        .replace("D - 1", f"{depth} - 1"))  # fmt: skip
    assert main(["predict", str(after), "--against", str(before), "--at", "rows=8,cols=1e6", "--format", "json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)["functions"]["sums"]
    (changed,) = row["cooperative"]
    assert changed["tiles.depth"] == [2, 3] and changed["tiles.wait_group"] == [1, 2]
    assert changed["shared_bytes_per_block"] == [16384, 24576] and changed["blocks_per_sm"] == [5, 4]
    assert row["ratio"] < 1
    assert main(["predict", str(after), "--against", str(before), "--at", "rows=8,cols=1e6", "--format", "human"]) == 0
    assert "tiles.depth 2 -> 3" in capsys.readouterr().out


def test_predict_shows_a_person_what_each_figure_rests_on(capsys):
    assert main(["predict", str(COOPERATIVE / "gpu.toml"), "--symbol", "row_sums[2]", "--at", "rows=3,cols=1e5",
                 "--format", "human"]) == 0  # fmt: skip
    shown = capsys.readouterr().out
    assert "cooperative region at src/device_kernels.cairn:48: rows blocks of 256 threads" in shown
    assert "[checked] tiles, line 49: depth 2; the wait at line 59 leaves 1 in flight" in shown
    assert "[not read] registers a thread: --inspect reads them from ptxas" in shown
    assert "[specification limits] an SM holds 6 blocks" in shown and "[assumed latency]" in shown


def test_a_host_region_is_priced_on_its_threads_and_its_barriers_are_named_a_guess():
    predicted = report.report(load_project(COOPERATIVE).source, [{"g": 1000, "n": 256_000}], {"block_sums"})
    (found,) = predicted["functions"]["block_sums"]["predictions"]
    assert part(found)["what"] == "host cooperative region at line 27" and part(found)["threads"] == 512
    assert found["confidence"] == "low" and any("std::barrier" in why for why in found["why"])


def test_the_device_card_holds_the_target_s_published_limits():
    card, limits = packaged("rtx-5070-ti").device, LIMITS[120]
    assert card.registers_per_sm == limits.registers_per_sm and card.threads_per_sm == limits.warps_per_sm * 32
    assert card.shared_per_sm == limits.shared_per_sm
    assert card.shared_reserved == limits.shared_per_sm - limits.shared_per_block_optin == 1024
    assert card.resident(44, 256, 6144) == {"threads": 6, "registers": 5, "shared memory": 14}  # 44 is 48 a warp
    assert card.resident(0, 128, 36864) == {"threads": 12, "shared memory": 2}


def test_explain_shows_each_region_s_barriers_waits_and_collectives_at_their_lines():
    project = load_project(COOPERATIVE / "gpu.toml")
    found = explain(project.source, project.origin, {"row_sums[2]"}, root=project.root)["functions"]["row_sums[2]"]
    (coop,) = found["cooperative"]
    at = "src/device_kernels.cairn:"
    assert coop["at"] == at + "48" and coop["shared_bytes"] == 6144
    assert coop["pipelines"] == [{"at": at + "49", "name": "tiles", "depth": 2, "stage_bytes": 2048, "bytes": 4096}]
    assert coop["waits"] == [{"at": at + "59", "pipeline": "tiles", "wait_group": 1}]
    assert coop["barriers"] == [at + "62", at + "65", at + "69"]
    assert coop["warp_collectives"] == [{"at": at + "72", "operation": "reduce + warp"}]
    kinds = {(s["at"], s["kind"]) for s in found["synchronization"]}
    assert {(at + "59", "pipeline wait"), (at + "62", "barrier"), (at + "72", "warp reduction")} <= kinds
    assert at + "58" in found["guards"]["by_line"]  # a guard inside the region, at its own line


def test_a_natural_parameter_is_a_number_to_the_phase_rule():
    compile_source(COLUMNS)  # T of tile[tx * P + ...] is 32 or 33, so the rule decides every phase
    refused(
        "E-COOP-CONFLICT",
        """fn f[K:nat](g:usize) {
  blocks b in g threads t in 64 { shared s:u64[64] = zeroed; s[t % K] = 1; }
}
fn g(n:usize) { f[32](n); }
""",
    )


def test_tune_searches_block_shape_and_depth_as_an_implementation_s_parameters():
    source = load_project(COOPERATIVE / "tuned.toml").source
    answer = tune(source, "row_totals", [{"rows": 64, "cols": 1e5}], None, None, 0, "clang++", False, parse("sm_120"),
                  Budget(compiles=0), None)  # fmt: skip
    rows = {r["plan"]: r for r in answer["candidates"]}
    priced = {tuple(r["parameters"].values()): r["predicted_ns"] for r in rows.values() if "parameters" in r}
    assert set(priced) == {(128, 2), (128, 3), (256, 2), (256, 3)} and answer["space"]["configurations"] == 5
    assert priced[(128, 3)] < priced[(128, 2)] and priced[(256, 3)] < priced[(256, 2)]  # 64 rows: latency bound
    assert answer["chosen"]["plan"] == "(no plan for row_totals)"  # none validated: none chosen
    why = "compile budget spent" if available() else "nvcc and cuobjdump are needed"  # CI has no nvcc
    assert answer["budget"]["undone"] == {f"not inspected: {why}": 5}


def test_compare_reports_what_the_checker_laid_out_for_two_instances():
    source = load_project(COOPERATIVE / "tuned.toml").source
    a, b = (parse_candidate(f"use row_totals_tiled[256, {d}]") for d in (2, 3))
    found = compare(source, "row_totals", a, b, [{"rows": 64, "cols": 1e5}], compiles=0)
    said = {x["text"] for x in found["lines"] if x["by"] == "the checker"}
    assert said == {"shared memory bytes a block: 6144 -> 8192", "tiles: stages: 2 -> 3",
                    "tiles: copies a wait leaves in flight: 1 -> 2"}  # fmt: skip


@NVCC
@pytest.mark.parametrize("path, names", [(COOPERATIVE / "gpu.toml", {"transpose", "block_sums", "row_sums[2]",
                                                                     "row_sums[3]"}),
                                         (TENSOR / "tile32.cairn", {"tile32"}), (TENSOR / "tile64.cairn", {"tile64"})],
                         ids=["cooperative", "tile32", "tile64"])  # fmt: skip
def test_ptxas_reports_the_shared_memory_the_checker_laid_out(path, names):
    source = load_project(path).source
    answer = report.report(source, [], names, device=parse("sm_120"), inspect=True)
    read = answer["inspection"]
    assert read["status"] == "read" and read["device_target"] == "sm_120" and "nothing ran" in read["by"]
    direct = kernels(source, parse("sm_120"))["kernels"]  # a second, independent read of the same compile
    for entry in read["regions"]:
        assert entry["status"] == "read" and entry["shared_bytes"] == entry["checked_shared_bytes"]
        (kernel,) = direct[entry["function"]]
        assert (entry["registers"], entry["shared_bytes"]) == (kernel["registers"], kernel["shared_bytes"])
    assert {e["function"] for e in read["regions"]} == names
    for name in names:
        d = answer["functions"][name]["regions"][0]["cooperative"]
        assert d["registers_per_thread"] and d["evidence"]["registers_per_thread"] == "ptxas"
        assert "registers" in d["resident"]["by_limit"]


@NVCC
def test_tune_compiles_each_instance_for_its_own_kernel():
    source = load_project(COOPERATIVE / "tuned.toml").source
    answer = tune(source, "row_totals", [{"rows": 64, "cols": 1e5}], None, None, 0, "clang++", False, parse("sm_120"),
                  Budget(compiles=5), None)  # fmt: skip
    read = {tuple(r["parameters"].values()): r["resources"] for r in answer["candidates"] if "parameters" in r}
    assert {k: r["shared_bytes"] for k, r in read.items()} == {(128, 2): 3072, (128, 3): 4096, (256, 2): 6144,
                                                               (256, 3): 8192}  # fmt: skip
    assert len({r["sass"] for r in read.values()}) == 4  # each instance's own kernel, not the whole program's


@NVCC
def test_the_tuned_configuration_compiles_every_instance_for_sm_120(tmp_path):
    device_build(tmp_path, compile_source(load_project(COOPERATIVE / "tuned.toml").source)[0], entry="main")

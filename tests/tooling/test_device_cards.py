"""The packaged device cards and pricing on them. Each card holds NVIDIA's published figures for one GPU and names the
document behind each, and the figures it derives agree with the published ones they follow from. `--card` prices
device work on one card and gives the device target when nothing names one; `--card all` prices every card, a row
each. Every answer is a prediction from a specification: nothing here runs on a device, and the ptxas tests compile
for sm_90a without launching anything."""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.perf import report
from cairn.perf.profile import DEFAULT_CARD, Device, card, cards, carrying, default
from cairn.perf.tuning import feedback
from cairn.projects import target
from cairn.projects.project import load_project
from cairn.projects.target import resolve
from emitted import code_of

ROOT = Path(__file__).resolve().parents[2]
COOPERATIVE = ROOT / "examples" / "cooperative"
TILE32 = ROOT / "examples" / "tensor" / "tile32.cairn"
SQUARE = "m=4096,n=4096,k=4096,cn=16777216,an=16777216,bn=16777216"  # tile32 on 4096 x 4096 matrices
NVCC = pytest.mark.skipif(not shutil.which("nvcc") or not shutil.which("cuobjdump"), reason="reading a kernel needs "
                          "nvcc and cuobjdump")  # fmt: skip
SCALE = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"

# Per SM a clock, against 32-bit floating-point results: 32-bit integer adds (CUDA C++ Programming Guide 12.9,
# Table 7) and FP64 results (CUDA Programming Guide 13.4.2, Table 30), by compute capability.
INT_PER_F32 = {"8.0": 1.0, "8.9": 0.5, "9.0": 0.5, "10.0": 0.5, "12.0": 1.0}
F64_PER_F32 = {"8.0": 1 / 2, "8.9": 1 / 64, "9.0": 1 / 2, "10.0": 1 / 2, "12.0": 1 / 64}
ASSUMED = {"launch_ns", "link_ns", "memory_efficiency", "occupancy_to_saturate", "memory_latency_ns"}


def answer(capsys, *argv: str) -> dict:
    assert main([*argv, "--format", "json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_cards_are_the_gpus_leaderboards_score_on():
    assert set(cards()) == {"a100-sxm4-80gb", "h100-sxm5", "h200-sxm", "b200-hgx", "l40s", "rtx-4090", "rtx-5090",
                            "rtx-5070-ti"}  # fmt: skip
    assert "zen4-7800x3d" not in cards()  # a host profile is not a card


@pytest.mark.parametrize("key", list(cards()))
def test_each_card_is_a_specification_that_names_its_sources(key):
    spec = cards()[key]
    said, fields = spec.source["source"], {f.name for f in dataclasses.fields(Device)}
    assert spec.origin == "specification" and spec.host is None and "not a measurement" in spec.notes
    named = said["published"] + said.get("derived", []) + said["assumed"]
    assert {n.split(".")[0] for n in named} <= fields | {"tensor"}
    assert set(said["assumed"]) == ASSUMED and not set(said["published"]) & ASSUMED
    assert all(n.split(".")[0] in said for n in said.get("derived", [])), "each derived figure says how"
    assert said["documents"] and all("https://" in text for text in said["documents"].values())


@pytest.mark.parametrize("key", list(cards()))
def test_each_card_s_derived_figures_agree_with_its_published_ones(key):
    """Within the rounding its sources use: whitepapers give three or four digits, datasheets whole TFLOPS."""
    d = cards()[key].device
    rate, capability = d.flops, d.compute_capability
    assert rate["f32"] == pytest.approx(d.sms * d.cores_per_sm * 2 * d.ghz, rel=0.002)
    assert rate["i32"] == pytest.approx(rate["f32"] * INT_PER_F32[capability], rel=0.002)
    assert rate["f64"] == pytest.approx(rate["f32"] * F64_PER_F32[capability], rel=0.02)  # 37 of 75 on the B200
    assert rate["tensor_bf16"] == rate["tensor_f16"] > rate["f32"]
    assert rate["tensor_tf32"] == pytest.approx(rate["tensor_f16"] / 2, rel=0.02)
    if "tensor_f8" in rate:  # compute capability 8.0 has no FP8 tensor cores
        assert rate["tensor_f8"] == pytest.approx(rate["tensor_f16"] * 2, rel=0.02)
    if "tensor_f4" in rate:  # four times FP8 where FP8 with an f32 accumulator runs at half rate
        assert any(rate["tensor_f4"] == pytest.approx(rate["tensor_f8"] * times, rel=0.002) for times in (2, 4))


@pytest.mark.parametrize("key", list(cards()))
def test_each_card_names_where_its_resident_block_limit_comes_from(key):
    """The count itself is held to the table of its compute capability in tests/tooling/test_target.py."""
    spec = cards()[key]
    d, said = spec.device, spec.source["source"]["occupancy"]
    assert f"{d.blocks_per_sm} blocks" in said and "blocks_per_sm" in spec.source["source"]["published"]
    assert "No limit on resident blocks" not in said
    if d.compute_capability == "12.0":  # NVIDIA's documents disagree for 12.0, and the card says which it takes
        assert "Tuning Guide" in said and "32 blocks" in said


def test_an_sm_holds_blocks_as_cuda_counts_them():
    """The review's case: 32 threads, 16 registers and no shared memory on the 5070 Ti were 48 blocks by threads
    alone; the SM holds 24. A block of 48 threads takes two whole warps, and a block that asks for more than a block
    may have is held by no SM."""
    d = card("rtx-5070-ti").device
    assert d.resident(16, 32) == {"threads": 48, "registers": 128, "shared memory": 100, "blocks": 24}
    assert d.held(16, 32)["limited_by"] == ["blocks"] and d.held(16, 32)["occupancy"] == 0.5
    assert d.resident(0, 48)["threads"] == 24 and d.occupancy(0, 48) == 1.0  # 24 blocks of two warps: all 48
    assert d.resident(80, 32)["registers"] == 24  # each of 4 parts holds 6 warps of 2560 registers, not 25 in all
    assert d.resident(0, 1056)["threads"] == 0 and d.resident(72, 1024)["registers"] == 0
    assert d.resident(0, 256, 99 * 1024)["shared memory"] == 1  # the most one block may have
    assert d.resident(0, 256, 99 * 1024 + 1)["shared memory"] == 0
    assert d.resident(0, 256, 1)["shared memory"] == 102400 // 1152  # a byte takes a 128-byte unit beside the 1 KB


@NVCC
def test_the_resident_blocks_agree_with_cuda_s_occupancy_calculator():
    """Every card over a grid of block sizes, registers and shared bytes against cuda_occupancy.h, the calculator
    CUDA ships as a host header, built with g++: the same blocks by every limit, and the same limits binding.
    Nothing runs on a device."""
    from checks import occupancy

    found = occupancy.compare()
    if found["status"] == "skipped":
        pytest.skip(found["reason"])
    assert found["differ"] == [] and found["status"] == "agree"
    assert set(found["cards"]) == set(cards()) and found["questions"] == 8 * 7728


def test_a_card_is_named_by_its_key_or_the_start_of_one():
    assert card("h100").source["card"] == "h100-sxm5" and card("a100").source["card"] == "a100-sxm4-80gb"
    assert card().source["card"] == DEFAULT_CARD == "rtx-5070-ti"
    assert code_of(lambda: card("h99")) == "E-DEVICE-CARD"
    assert code_of(lambda: card("rtx")) == "E-DEVICE-CARD"  # three cards start rtx-


def test_a_card_gives_the_target_when_nothing_names_one(monkeypatch):
    monkeypatch.setattr(target, "detect", lambda: ("12.0", "NVIDIA GeForce RTX 5070 Ti"))
    h100 = resolve(card=("9.0", "h100-sxm5"))
    assert h100.name == "sm_90a" and h100.origin == "card: h100-sxm5 is compute capability 9.0"  # not the GPU here
    assert resolve(card=("8.0", "a100-sxm4-80gb")).name == "sm_80"  # no arch-specific target before sm_90
    assert resolve(card=("10.0", "b200-hgx")).provides("tcgen05")
    assert resolve("sm_90", "sm_120", card=("9.0", "h100-sxm5")).name == "sm_90"  # the flag wins
    assert resolve(None, "sm_120", card=("9.0", "h100-sxm5")).name == "sm_120"  # then the manifest
    assert resolve().name == "sm_120"  # without a card, the GPU reported here


def test_the_5070_ti_card_prices_as_the_default_did():
    project = load_project(COOPERATIVE / "gpu.toml")
    for source, sizes in ((SCALE, [{"n": 1e7}]), (project.source, [{"rows": 3, "cols": 1e5}])):
        plain = report.report(source, sizes)
        chosen = report.report(source, sizes, profile=carrying(default(), card("rtx-5070-ti")))
        assert plain["functions"] == chosen["functions"]
        assert plain["device_card"] == chosen["device_card"] == {"card": "rtx-5070-ti", "name": "RTX 5070 Ti",
                                                                 "compute_capability": "12.0", "origin": "specification"}  # fmt: skip


def test_a_card_prices_device_work_and_the_answer_names_it(tmp_path, capsys):
    path = tmp_path / "scale.cairn"
    path.write_text(SCALE)
    found = answer(capsys, "predict", str(path), "--at", "n=1e7", "--card", "h100")
    assert found["device_card"] == {"card": "h100-sxm5", "name": "H100 SXM5 80GB", "compute_capability": "9.0",
                                    "origin": "specification"}  # fmt: skip
    assert found["device_target"]["name"] == "sm_90a" and found["device_target"]["origin"].startswith("card:")
    assert found["profile"]["origin"] == "measured"  # host work is still priced on the host measured here
    part = next(p for p in found["functions"]["scale"]["predictions"][0]["parts"] if p["what"].startswith("device"))
    assert part["device"] == "H100 SXM5 80GB" and part["bound"] == "device memory"
    slower = answer(capsys, "predict", str(path), "--at", "n=1e7", "--card", "rtx-4090")
    assert slower["functions"]["scale"]["predictions"][0]["ns"] > found["functions"]["scale"]["predictions"][0]["ns"]
    assert main(["predict", str(path), "--at", "n=1e7", "--card", "h100", "--format", "human"]) == 0
    assert (
        "device work priced on h100-sxm5 (specification), compute capability 9.0, for sm_90a" in capsys.readouterr().out
    )


def test_every_card_prices_one_kernel_and_the_bound_moves_with_the_card(capsys):
    found = answer(capsys, "predict", str(TILE32), "--at", SQUARE, "--card", "all")
    assert found["schema"] == "cairn.predict.cards/1" and found["predicted"].startswith("Predictions from published")
    rows = {row["card"]: row for row in found["functions"]["tile32"]["predictions"][0]["cards"]}
    assert set(rows) == set(cards()) and not any("refused" in c for c in found["cards"])
    assert {rows[k]["device_target"] for k in rows} == {"sm_80", "sm_89", "sm_90a", "sm_100a", "sm_120a"}
    assert rows["h100-sxm5"]["bound"] == "shared memory" and rows["rtx-4090"]["bound"] == "device memory"
    assert rows["b200-hgx"]["ns"] < rows["h100-sxm5"]["ns"] < rows["a100-sxm4-80gb"]["ns"]
    assert all(0 < row["speed_of_light"] <= 1 and row["confidence"] == "low" for row in rows.values())
    shown = report.lines_across(found)
    assert shown.startswith("predicted from published specifications, not measured: 8 cards")
    assert all(f"  {key} " in shown for key in cards())


def test_a_named_target_prices_only_the_cards_it_runs_on(capsys):
    found = answer(capsys, "predict", str(COOPERATIVE / "gpu.toml"), "--symbol", "row_sums[3]", "--at",
                   "rows=1000,cols=1e5", "--card", "all", "--device-target", "sm_90a")  # fmt: skip
    priced = {row["card"] for row in found["functions"]["row_sums[3]"]["predictions"][0]["cards"]}
    assert priced == {"h100-sxm5", "h200-sxm"}
    refused = {c["card"]: c["refused"]["code"] for c in found["cards"] if "refused" in c}
    assert refused == dict.fromkeys(set(cards()) - priced, "E-TARGET-MISMATCH")
    host = answer(capsys, "predict", str(COOPERATIVE / "cairn.toml"), "--card", "all")
    assert host["functions"] == {} and "block_sums" in host["host_only"]  # host work: no card changes it


def test_an_unknown_card_and_a_card_the_target_does_not_run_on_are_refused(tmp_path, capsys):
    path = tmp_path / "scale.cairn"
    path.write_text(SCALE)
    for argv, code in ((["--card", "h99"], "E-DEVICE-CARD"), (["--card", "h100", "--device-target", "sm_120"],
                       "E-TARGET-MISMATCH"), (["--card", "rtx-5090", "--device-target", "sm_120a"], None)):  # fmt: skip
        status = main(["predict", str(path), "--at", "n=1e6", *argv, "--format", "json"])
        assert (status, json.loads(capsys.readouterr().out).get("code")) == ((1, code) if code else (0, None))
    assert main(["tune", str(path), "--symbol", "scale", "--at", "n=1e6", "--card", "all", "--format", "json"]) == 2
    assert "--card all is for cairn predict" in json.loads(capsys.readouterr().out)["message"]


def test_cards_lists_every_card_with_its_headline_figures(capsys):
    listed = {c["card"]: c for c in answer(capsys, "cards")["cards"]}
    assert set(listed) == set(cards())
    h100 = listed["h100-sxm5"]
    assert (h100["compute_capability"], h100["sms"], h100["dram_gbps"], h100["flops"]["tensor_f16"]) == (
        "9.0", 132, 3352.0, 989400.0)  # fmt: skip
    assert listed["b200-hgx"]["derived"] == ["ghz", "flops.i32"] and set(h100["assumed"]) == ASSUMED
    assert (h100["blocks_per_sm"], h100["threads_per_block"], h100["shared_per_block"]) == (32, 1024, 227 * 1024)
    assert main(["cards", "--format", "human"]) == 0
    shown = capsys.readouterr().out
    assert "h100-sxm5" in shown and "no card was measured" in shown


@NVCC
def test_a_kernel_is_read_for_the_card_s_own_target(capsys):
    found = answer(capsys, "predict", str(COOPERATIVE / "gpu.toml"), "--symbol", "row_sums[3]", "--at",
                   "rows=1000,cols=1e5", "--card", "h100", "--inspect")  # fmt: skip
    inspection = found["inspection"]
    assert found["device_target"]["name"] == inspection["device_target"] == "sm_90a"
    assert inspection["status"] == "read" and inspection["regions"][0]["registers"] > 0
    held = found["functions"]["row_sums[3]"]["regions"][0]["cooperative"]["resident"]
    assert held["registers_per_thread"] == inspection["regions"][0]["registers"]


@NVCC
def test_two_instances_are_compared_for_sm_90a_and_priced_on_the_h100(capsys):
    """The compile-only inspection an agent without the GPU runs: ptxas for sm_90a, the model on the H100 card. The
    reference and an instance differ in their registers, which ptxas reads; the four instances no longer do."""
    found = answer(capsys, "tune", str(COOPERATIVE / "tuned.toml"), "--symbol", "row_totals", "--at",
                   "rows=64,cols=1e5", "--device-target", "sm_90a", "--card", "h100", "--no-history", "--compare",
                   "", "--compare", "use row_totals_tiled[256, 3]")  # fmt: skip
    assert found["device_target"]["name"] == "sm_90a" and found["device_card"]["card"] == "h100-sxm5"
    said = [line["text"] for line in found["lines"]]
    assert any(text.startswith("registers per thread: ") for text in said)
    assert "static shared memory bytes per block: 0 -> 8192" in said


def test_residency_that_registers_change_is_priced_on_the_card():
    """Two readings whose registers let a different share of an SM's warps stay resident on the H100: the hypothesis
    cites the card's published limits and names the limits that bind on each side. Blocks of 256 threads at 32
    registers are held 8 an SM by their threads and their registers alike, and at 128 registers 2 by their registers."""
    read = {k: {"status": "read", "registers": regs, "shared_bytes": 0, "dynamic_shared_bytes": 0, "spill_bytes": 0,
                "memory": {}} for k, regs in (("a", 32), ("b", 128))}  # fmt: skip
    said = feedback.reasoning([], read, True, {"a": [[]], "b": [[]]}, "f", priced=card("h100").device)
    (hypothesis,) = [line for line in said if line["kind"] == "hypothesis"]
    assert "published limits of the H100 SXM5 80GB" in hypothesis["by"]
    assert hypothesis["text"].startswith("at most 100% -> 25% of an SM's warps can be resident, limited by threads "
                                         "and registers -> registers;")  # fmt: skip

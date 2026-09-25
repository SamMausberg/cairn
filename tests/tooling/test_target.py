"""One device target through the build, the kernel reader, tuning, prediction and device timing.

A target is spelled as nvcc spells it, resolved once (flag, then manifest, then the GPU nvidia-smi reports), and
recorded wherever a result is made for it. A result made for another target is refused, and so is a feature the
target lacks. The feature table is held to the toolkit: ptxas assembles each probe for exactly the targets the table
says provide it. Nothing here runs on a device; nvidia-smi is replaced by a script where a test needs a GPU.
"""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.perf import report as priced
from cairn.perf.device import resources
from cairn.perf.profile import Profile, cards, packaged
from cairn.projects import target
from cairn.projects.build import build
from cairn.projects.project import load_project
from cairn.projects.target import FEATURES, LIMITS, DeviceTarget, parse, resolve
from cairn.projects.toolchain import command
from emitted import NVCC_HOST, code_of

SCALE = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"
MMA = "fn mm(c:rw<f32>[4]@device, a:ro<T>[4]@device, b:ro<T>[4]@device) { mma_unordered(2, 2, 2, c, a, b); }\n"
NVCC = pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")


@pytest.fixture
def gpus(tmp_path, monkeypatch):
    """A fake nvidia-smi that reports `rows`, first on PATH, and a fresh detection around the test."""

    def install(*rows: str) -> None:
        tool = tmp_path / "bin" / "nvidia-smi"
        tool.parent.mkdir(exist_ok=True)
        tool.write_text("#!/bin/sh\n" + "".join(f"echo '{row}'\n" for row in rows))
        tool.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tool.parent}:/usr/bin:/bin")
        target.detect.cache_clear()

    yield install
    target.detect.cache_clear()


@pytest.mark.parametrize("spelling", ["sm_75", "sm_120", "sm_120f", "sm_120a", "sm_90a", "sm_100f", "sm_103a"])
def test_a_target_is_spelled_as_nvcc_spells_it(spelling):
    chosen = parse(spelling)
    assert chosen.name == spelling and chosen.flags() == [f"-arch={spelling}"]
    suffix = spelling[-1] if spelling[-1] in "af" else ""
    assert chosen.variant == {"": "portable", "f": "family", "a": "arch-specific"}[suffix]


@pytest.mark.parametrize(
    "spelling",
    ["native", "x86-64-v3", "sm120", "sm_1", "sm_1200", "sm_075", "sm_80a", "sm_90f", "SM_120", "sm_120x", 120],
)
def test_anything_else_is_not_a_device_target(spelling):
    assert code_of(lambda: parse(spelling)) == "E-TARGET"


def test_the_flag_wins_then_the_manifest_then_the_gpu_reported_here(gpus):
    gpus("12.0, NVIDIA GeForce RTX 5070 Ti")
    assert resolve("sm_90a", "sm_120f").name == "sm_90a" and resolve("sm_90a").origin == "flag"
    assert resolve(None, "sm_120f").name == "sm_120f" and resolve(None, "sm_120f").origin == "manifest"
    found = resolve()
    assert found.name == "sm_120" and "nvidia-smi reports compute capability 12.0" in found.origin


def test_no_target_and_no_gpu_is_refused_not_guessed(gpus, monkeypatch):
    gpus()  # nvidia-smi answers, and names no GPU
    assert code_of(resolve) == "E-TARGET"
    assert resolve(required=False) is None
    monkeypatch.setenv("PATH", "/nonexistent")
    target.detect.cache_clear()
    assert code_of(resolve) == "E-TARGET"


def test_gpus_of_two_capabilities_need_a_named_target(gpus):
    gpus("12.0, NVIDIA GeForce RTX 5070 Ti", "8.9, NVIDIA GeForce RTX 4090")
    assert code_of(resolve) == "E-TARGET"
    assert resolve("sm_89").name == "sm_89"  # a named target needs no detection
    gpus("12.0, NVIDIA GeForce RTX 5070 Ti", "12.0, NVIDIA GeForce RTX 5080")
    assert resolve().name == "sm_120"


def test_code_runs_where_its_variant_says():
    assert parse("sm_120").runs_on("12.0") and parse("sm_120").runs_on("12.1")
    assert not parse("sm_121").runs_on("12.0") and not parse("sm_120").runs_on("10.0")
    assert parse("sm_120f").runs_on("12.1") and not parse("sm_120f").runs_on("13.0")
    assert parse("sm_120a").runs_on("12.0") and not parse("sm_120a").runs_on("12.1")
    assert not parse("sm_120").runs_on("") and not parse("sm_120").runs_on("twelve")


def test_a_feature_the_target_lacks_is_refused():
    assert code_of(lambda: parse("sm_120").require(["tcgen05"])) == "E-TARGET-FEATURE"  # not on RTX Blackwell
    assert code_of(lambda: parse("sm_120").require(["mma_f8f6f4"])) == "E-TARGET-FEATURE"  # needs sm_120f or a
    assert parse("sm_120f").require(["mma_f8f6f4"]).required == ("mma_f8f6f4",)
    assert parse("sm_120a").require(["mma_f8f6f4", "bf16"]).required == ("mma_f8f6f4", "bf16")
    assert code_of(lambda: parse("sm_100a").require(["wgmma"])) == "E-TARGET-FEATURE"  # sm_90a only
    assert parse("sm_100f").provides("tcgen05") and not parse("sm_100").provides("tcgen05")
    assert code_of(lambda: parse("sm_120").require(["warp_drive"])) == "E-TARGET-FEATURE"
    with pytest.raises(Diagnostic) as refused:
        parse("sm_120").require(["tcgen05"], "the tensor kernel")
    assert "sm_100f" in refused.value.data["message"] and refused.value.data["feature"] == "tcgen05"


def test_a_result_recorded_for_another_target_is_refused():
    here = parse("sm_120")
    here.accept({"device_target": {"name": "sm_120"}}, "a timing")
    here.accept(parse("sm_120").record(), "an inspection")
    for other in ({"device_target": {"name": "sm_120a"}}, "sm_120f", {"name": "sm_100a"}, {"status": "measured"}, None):
        assert code_of(lambda other=other: here.accept(other, "a record")) == "E-TARGET-MISMATCH"
    assert parse("sm_120") == DeviceTarget(120, "", "detected") and parse("sm_120") != parse("sm_120a")


@pytest.mark.parametrize("key", list(cards()))
def test_the_limits_agree_with_each_packaged_device_card(key):
    card = cards()[key].device
    limits = parse(f"sm_{card.compute_capability.replace('.', '')}").limits
    assert limits is not None, f"{key}: no LIMITS row for compute capability {card.compute_capability}"
    assert (limits.registers_per_sm, limits.warps_per_sm * 32, limits.blocks_per_sm, limits.shared_per_sm) == (
        card.registers_per_sm, card.threads_per_sm, card.blocks_per_sm, card.shared_per_sm)  # fmt: skip
    assert (limits.registers_per_thread, limits.threads_per_block, limits.shared_per_block_optin) == (
        card.registers_per_thread, card.threads_per_block, card.shared_per_block)  # fmt: skip
    assert card.shared_reserved == limits.shared_per_sm - limits.shared_per_block_optin == 1024


def test_a_capability_without_a_published_row_has_unknown_limits():
    assert LIMITS[120] == LIMITS[121]  # the programming guide's 12.x column
    assert parse("sm_88").limits is None and parse("sm_88").record()["limits_origin"].startswith("unknown")


def test_ptxas_reports_for_suffixed_targets_are_read():
    log = "\n".join([
        "ptxas info    : Compiling entry function '_Z1kPf' for 'sm_120a'",
        "ptxas info    : Used 8 registers, used 0 barriers, 360 bytes cmem[0]",
        "ptxas info    : Compiling entry function '_Z1gPf' for 'sm_100f'",
        "ptxas info    : Used 12 registers, 16 bytes stack frame",
    ])  # fmt: skip
    read = resources(log)
    assert read["_Z1kPf"]["arch"] == "sm_120a" and read["_Z1kPf"]["registers"] == 8
    assert read["_Z1gPf"]["arch"] == "sm_100f" and read["_Z1gPf"]["stack_bytes"] == 16


@NVCC
def test_the_device_command_names_the_target_and_never_native(tmp_path):
    line = command("g++", "p.cu", str(tmp_path / "p"), kind="exe", cuda=True, device=parse("sm_120f"))
    assert "-arch=sm_120f" in line and not any("native" in part for part in line)
    unbuilt = "sm_130a" if 101 in target.toolkit()["compiles"] else "sm_101a"  # nvcc 13 dropped sm_101; 12.9 has it
    assert code_of(lambda: command("g++", "p.cu", "p", cuda=True, device=parse(unbuilt))) == "E-TARGET-TOOLKIT"


@NVCC
def test_a_target_the_toolkit_does_not_build_is_refused(monkeypatch):
    assert code_of(lambda: target.supported(parse("sm_130"))) == "E-TARGET-TOOLKIT"
    assert code_of(lambda: target.supported(parse("sm_12"))) == "E-TARGET-TOOLKIT"  # a spelling nvcc 13 dropped
    monkeypatch.setattr(target, "toolkit", lambda: None)
    assert code_of(lambda: target.supported(parse("sm_120"))) == "E-TARGET-TOOLKIT"


F8F6F4 = """__global__ void p(float* out) {
  float d0, d1, d2, d3;
  unsigned a0 = 0, a1 = 0, a2 = 0, a3 = 0, b0 = 0, b1 = 0;
  float c0 = 0, c1 = 0, c2 = 0, c3 = 0;
  asm volatile("mma.sync.aligned.m16n8k32.row.col.kind::f8f6f4.f32.e2m1.e2m1.f32 {%0,%1,%2,%3}, {%4,%5,%6,%7}, "
               "{%8,%9}, {%10,%11,%12,%13};" : "=f"(d0), "=f"(d1), "=f"(d2), "=f"(d3)
               : "r"(a0), "r"(a1), "r"(a2), "r"(a3), "r"(b0), "r"(b1), "f"(c0), "f"(c1), "f"(c2), "f"(c3));
  out[0] = d0 + d1 + d2 + d3;
}
"""
PROBED = ["sm_75", "sm_80", "sm_89", "sm_90", "sm_90a", "sm_100", "sm_100a", "sm_100f", "sm_103", "sm_103a", "sm_110f",
          "sm_120", "sm_120f", "sm_120a", "sm_121", "sm_121a"]  # fmt: skip


@NVCC
def test_the_feature_table_is_what_ptxas_assembles(tmp_path):
    """Each probe is one instruction; ptxas assembles it for a target exactly when the table says it provides the
    feature, on every target the installed nvcc compiles (CUDA 12.9 has no sm_110). Compiled to a cubin, never run."""
    probes = {
        name: f'__global__ void p() {{ asm volatile("{f.probe}"); }}\n' for name, f in FEATURES.items() if f.probe
    }
    probes["mma_f8f6f4"] = F8F6F4
    jobs = []
    for name, text in probes.items():
        (tmp_path / f"{name}.cu").write_text(text)
        jobs += [(name, spelling) for spelling in PROBED if parse(spelling).sm in target.toolkit()["compiles"]]

    def assembles(job: tuple[str, str]) -> bool:
        name, spelling = job
        out = tmp_path / f"{name}_{spelling}.cubin"
        line = ["nvcc", f"-arch={spelling}", "-cubin", str(tmp_path / f"{name}.cu"), "-o", str(out)]
        return subprocess.run(line, capture_output=True, timeout=300).returncode == 0

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(jobs, pool.map(assembles, jobs), strict=True))
    table = {job: parse(job[1]).provides(job[0]) for job in jobs}
    assert found == table, sorted(job for job in jobs if found[job] != table[job])


def test_the_program_s_features_are_in_its_receipt():
    assert compile_source(SCALE)[1]["device_features"] == ["device_lanes"]
    assert sorted(compile_source(MMA.replace("T", "bf16"))[1]["device_features"]) == ["bf16", "device_lanes", "wmma"]
    assert sorted(compile_source(MMA.replace("T", "f16"))[1]["device_features"]) == ["device_lanes", "wmma"]
    assert compile_source(SCALE.replace("@device", ""))[1]["device_features"] == []


def test_a_build_for_a_target_without_the_program_s_features_is_refused_before_nvcc(tmp_path):
    path = tmp_path / "mm.cairn"
    path.write_text(MMA.replace("T", "bf16"))
    with pytest.raises(Diagnostic) as refused:
        build(load_project(path), output=tmp_path / "build", cxx="g++", device_target="sm_75")
    assert refused.value.data["code"] == "E-TARGET-FEATURE" and refused.value.data["feature"] == "bf16"
    assert not (tmp_path / "build").exists() or not any((tmp_path / "build").rglob("receipt.json"))


def project(tmp_path: Path, device_target: str | None, source: str = SCALE) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/scale.cairn").write_text(source)
    line = f'device_target = "{device_target}"\n' if device_target else ""
    (tmp_path / "cairn.toml").write_text(f'[project]\nname = "scale"\nsources = ["src/scale.cairn"]\n\n[build]\n{line}')
    return tmp_path


def test_the_manifest_names_a_target_or_is_refused(tmp_path, capsys):
    assert load_project(project(tmp_path, "sm_120a")).device_target == "sm_120a"
    project(tmp_path, "x86-64-v3")
    assert main(["check", str(tmp_path), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-TARGET"


def test_a_bad_flag_is_refused_with_its_code(tmp_path, capsys):
    assert main(["build", str(project(tmp_path, None)), "--device-target", "sm_80a", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-TARGET"


@NVCC
def test_the_build_receipt_records_the_target_its_command_used(tmp_path):
    record = build(load_project(project(tmp_path, "sm_120a")), output=tmp_path / "build", cxx=NVCC_HOST, timeout=300)
    assert record["status"] == "native-built", record.get("stderr", "")[-3000:]
    assert "-arch=sm_120a" in record["command"]
    held = record["device_target"]
    assert held["name"] == "sm_120a" and held["origin"] == "manifest" and held["required_features"] == ["device_lanes"]
    assert held["toolkit"]["release"] and "mma_f8f6f4" in held["features"]
    flagged = build(load_project(tmp_path), output=tmp_path / "b2", cxx=NVCC_HOST, device_target="sm_120", timeout=300)
    assert "-arch=sm_120" in flagged["command"] and flagged["device_target"]["origin"] == "flag"


def test_prediction_is_priced_on_a_card_the_target_runs_on():
    answer = priced.report(SCALE, [{"n": 1e6}], device=parse("sm_120f"))
    assert answer["device_target"]["name"] == "sm_120f"
    assert code_of(lambda: priced.report(SCALE, [{"n": 1e6}], device=parse("sm_100a"))) == "E-TARGET-MISMATCH"
    assert code_of(lambda: priced.report(SCALE, [{"n": 1e6}], device=parse("sm_121"))) == "E-TARGET-MISMATCH"
    unnamed = priced.report(SCALE, [{"n": 1e6}])
    assert unnamed["device_target"] is None and unnamed["device_card"]["compute_capability"] == "12.0"
    assert "device_target" not in priced.report(SCALE.replace("@device", ""), [{"n": 1e6}])


def test_a_card_measured_for_one_target_prices_no_other(tmp_path):
    base, host = packaged("rtx-5070-ti").source, packaged("zen4-7800x3d").source
    measured = {**host, "origin": "measured", "device": {**base["device"], "target": "sm_120"}}
    path = tmp_path / "measured.json"
    path.write_text(json.dumps(measured))
    card = Profile.load(path)
    assert card.device.target == "sm_120"
    assert priced.report(SCALE, [{"n": 1e6}], profile=card, device=parse("sm_120"))["device_target"]["name"] == "sm_120"
    assert (
        code_of(lambda: priced.report(SCALE, [{"n": 1e6}], profile=card, device=parse("sm_120a")))
        == "E-TARGET-MISMATCH"
    )


@NVCC
def test_tuning_reads_registers_for_the_target_it_records():
    from cairn.perf.tuning.tune import tune

    result = tune(SCALE, "scale", [{"n": 1e7}], device_target=parse("sm_120f"))
    read = [row["resources"] for row in result["candidates"] if row.get("resources", {}).get("registers")]
    assert result["device_target"]["name"] == "sm_120f" and read  # each compiled for sm_120f, and read
    assert code_of(lambda: tune(SCALE, "scale", [{"n": 1e7}], device_target=parse("sm_90"))) == "E-TARGET-MISMATCH"

"""The device timer and device calibration run only under the owner's make targets; here they are compiled, never run.

Device runs have crashed the reference machine, so every test in this file checks what would run without running it:
the gate that refuses outside `make tune-device` and `make calibrate-device`, the lock they share with `make gpu`,
the budget and cooldown, and that the timed program and the calibration kernels compile for the device.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.perf import on_device
from cairn.perf.tune import tune
from emitted import emit
from support import DEVICE_LOCK

ROOT = Path(__file__).resolve().parents[2]
SCALE = "fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }\n"


def test_nothing_runs_on_the_device_outside_the_owner_s_targets(monkeypatch):
    monkeypatch.delenv("CAIRN_GPU_TESTS", raising=False)
    assert "make tune-device" in on_device.allowed()
    with pytest.raises(ValueError, match="owner's make targets"):
        on_device.time_device(SCALE, "scale", {"n": 1024})
    assert on_device.main(["--out", "/nonexistent/never-written.json"]) == 2
    assert on_device.DEVICE_LOCK == DEVICE_LOCK  # one lock for make gpu, tune-device and calibrate-device


def test_a_process_stops_at_its_device_budget(monkeypatch):
    monkeypatch.setenv("CAIRN_GPU_TESTS", "1")
    monkeypatch.setattr(on_device, "ran", on_device.BUDGET)
    with pytest.raises(ValueError, match="device runs"):
        on_device.time_device(SCALE, "scale", {"n": 1024})  # refused before anything is built or run


def test_only_the_owner_s_make_targets_set_the_gate():
    makefile = (ROOT / "Makefile").read_text()
    targets = {m.group(1) for m in re.finditer(r"^([\w-]+):\n(?:\t.*\n)*?\t[^\n]*CAIRN_GPU_TESTS=1", makefile, re.M)}
    assert targets == {"gpu", "tune-device", "calibrate-device"}


def test_device_plans_are_priced_but_not_timed_without_the_target(monkeypatch):
    monkeypatch.delenv("CAIRN_GPU_TESTS", raising=False)
    result = tune(SCALE, "scale", [{"n": 1e8}], measure=3)
    assert "make tune-device" in result["measured"] and "rounds" not in result
    assert {row.get("block", 0) for row in result["candidates"]} == {0, 64, 128, 512, 1024}
    underfilled = [r for r in result["candidates"] if r.get("per_lane") == 64 and r.get("block", 0) in {0, 64}]
    assert underfilled and all(r["predicted_ns"] >= result["chosen"]["predicted_ns"] for r in underfilled)


@pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")
@pytest.mark.parametrize(("source", "symbol", "sizes"), [
    (SCALE + "plan scale { block 128; unroll 4; }\n", "scale", {"n": 1 << 20, "a": 2}),
    (on_device.KERNELS, "stream", {"n": 1 << 20}),
    (on_device.KERNELS, "cross", {"n": 1 << 16}),
])  # fmt: skip
def test_the_timed_program_compiles_for_the_device_without_touching_it(tmp_path, source, symbol, sizes):
    text = on_device.program(source, symbol, sizes)
    unit, _ = emit(tmp_path, text, entry=None)
    command = ["nvcc", "-std=c++20", "-O3", "--fmad=false", "-arch=sm_120", "--extended-lambda",
               "--expt-relaxed-constexpr", "-x", "cu", unit, "-o", str(tmp_path / "timed")]  # fmt: skip
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]  # linked, and never run


def test_a_host_program_is_not_the_device_timer_s():
    with pytest.raises(ValueError, match="no device code"):
        on_device.program("fn f(n:usize, o:rw<u64>[n]) { parallel i in n { o[i] = 1; } }", "f", {"n": 8})

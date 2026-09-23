"""examples/apps/matmul: an f16 layer multiplied by mma_unordered and held to its contract.

The host configuration runs everywhere, under both compilers and the sanitizers, with its two test blocks. The device
configuration, gpu.toml, typechecks and compiles for sm_120 here; building and running it on the tensor cores, where
it checks every output against the same bound, happens only under `make gpu`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import SANITIZED, device_build, on_device, run

APP = Path(__file__).resolve().parents[2] / "examples/apps/matmul"


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_the_host_layer_keeps_the_contract_under_both_compilers(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    record = build(load_project(APP), output=tmp_path / "build", cxx=cxx, timeout=240)
    assert record["status"] == "native-built", record.get("stderr", "")[:4000]
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == "host layer: 200 x 136 x 72, 0 outputs outside the contract\n"


def test_the_host_layer_is_sanitizer_clean(tmp_path):
    done = run(tmp_path, compile_source(load_project(APP).source)[0], *SANITIZED)
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stdout + done.stderr[-3000:]


def test_the_test_blocks_pass(capsys):
    assert main(["test", str(APP), "--format", "json"]) == 0


def test_the_receipt_says_what_each_configuration_costs():
    host = compile_source(load_project(APP).source)[1]["functions"]
    assert host["host_layer"]["numerics"][0]["rounding"] == "unordered-f32"
    assert "par:device" not in host["host_layer"]["effects"]
    device = compile_source(load_project(APP / "gpu.toml").source)[1]["functions"]
    assert "par:device" in device["device_layer"]["effects"]
    assert {"transfer:h2d", "transfer:d2h", "gpu_alloc", "gpu_free"} <= set(device["main"]["effects"])


def test_the_device_configuration_compiles_for_sm_120_without_touching_it(tmp_path):
    device_build(tmp_path, compile_source(load_project(APP / "gpu.toml").source)[0], entry="main", timeout=900)


def test_the_tensor_cores_keep_the_contract_on_the_device(tmp_path):
    with on_device():  # runs only under `make gpu`
        record = build(load_project(APP / "gpu.toml"), output=tmp_path / "build", cxx="g++", timeout=290)
        assert record["status"] == "native-built", record.get("stderr", "")[:4000]
        done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    assert done.stdout == "device layer: 256 x 512 x 1024, 0 outputs outside the contract\n"

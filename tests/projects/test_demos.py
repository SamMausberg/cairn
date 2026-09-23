"""The three demos under demos/: each runs here as a reader would run it, and tells the story its README tells.

The repair and visual demos replay a scripted agent through the real edit host, so what the host and the compiler say
is computed fresh and checked here. The plate's host half runs under two lane counts and beside its C++ loop; its
device half compiles for sm_120 here and runs only under `make gpu`, which writes results/demos/numeric/device.json.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_std_image import decode_png

from cairn.compiler.cairnc import compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import artifact, device_build, on_device

ROOT = Path(__file__).resolve().parents[2]
DEMOS = ROOT / "demos"


def script(name: str, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(DEMOS / name / "run.py"), *args], capture_output=True, text=True,
                          timeout=timeout, cwd=ROOT)  # fmt: skip


def test_the_agent_is_refused_twice_then_fixes_bucket_and_diff_names_what_changed(tmp_path):
    done = script("repair", "--out", str(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["before"]["exit_code"] == 1 and "3 by bucket but 2 by count" in record["before"]["stdout"]
    assert record["after"]["exit_code"] == 0 and "under 100 us  ###### 2" in record["after"]["stdout"]
    first, fixed, slip, tidy = record["exchanges"]
    assert first["response"]["code"] == "E-EFFECT-EXPANSION" and {"io"} <= set(first["response"]["added_effects"])
    assert fixed["response"]["status"] == "typed" and fixed["hidden"]["status"] == "passed-finite-tests"
    witness = slip["response"]["witness"]["inputs"]
    assert slip["response"]["code"] == "E-PRESERVE" and witness["x"] < witness["lo"] and witness["hi"] < witness["lo"]
    assert max(witness.values()) <= 16  # a witness small enough to read
    assert tidy["response"]["status"] == "typed"
    diff = record["diff"]
    assert diff["functions"]["bucket"]["class"] == "behavior-changed"
    assert diff["functions"]["bucket"]["witness"]["inputs"] == {"us": 100}
    assert diff["functions"]["clamp"]["class"] == "smt-equivalent"
    assert {diff["functions"][f]["class"] for f in ("count_below", "percentile")} == {"identical-code"}
    assert diff["semver"]["level"] == "major"
    assert "semver: major: bucket behaves differently at us = 100" in done.stdout


def test_the_plate_keeps_its_contract_and_its_bits_under_any_lane_count():
    executable = artifact(DEMOS / "numeric")
    prints = set()
    for lanes in ("1", "4"):
        done = subprocess.run([executable], capture_output=True, text=True, timeout=300,
                              env={**os.environ, "CAIRN_LANES": lanes})  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        err, limit = map(float, re.search(r"reference (\S+), contract (\S+)", done.stdout).groups())
        assert 0 < err <= limit / 100  # the contract holds with a hundredfold margin
        prints.add(re.search(r"bits (\d+)", done.stdout).group(1))
    assert len(prints) == 1


def test_the_plate_and_its_cpp_loop_compute_the_same_bits(tmp_path):
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")  # g++ ships its OpenMP runtime; a clang may have none installed
    done = script("numeric", "--out", str(tmp_path), "--cxx", "g++", "--rounds", "1", "--lanes", "4")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Every program printed the same fingerprint: yes." in done.stdout
    record = json.loads((tmp_path / "host.json").read_text())
    assert [r["program"] for r in record["programs"]] == [
        "cairn lanes", "cairn one lane", "c++ openmp, guards", "c++ one thread, guards", "c++ openmp, no guards",
    ]  # fmt: skip


def test_the_receipt_says_where_the_plate_sweeps():
    host = compile_source(load_project(DEMOS / "numeric").source)[1]["functions"]
    device = compile_source(load_project(DEMOS / "numeric/gpu.toml").source)[1]["functions"]
    assert "par:host" in host["sweep"]["effects"] and "par:device" in device["sweep_device"]["effects"]
    assert {"transfer:h2d", "transfer:d2h", "gpu_alloc"} <= set(device["main"]["effects"])
    same = [line.strip() for line in (DEMOS / "numeric/src/plate.cairn").read_text().splitlines()]
    body = (DEMOS / "numeric/src/device_main.cairn").read_text().split("fn sweep_device")[1].split("\n}\n")[0]
    assert all(line.strip() in same for line in body.splitlines()[1:]), "the device sweep is the host sweep's body"


def test_the_device_plate_compiles_for_sm_120_without_touching_it(tmp_path):
    device_build(
        tmp_path, compile_source(load_project(DEMOS / "numeric/gpu.toml").source)[0], entry="main", timeout=900
    )


def test_the_device_plate_has_the_host_bits(tmp_path):
    with on_device():  # runs only under `make gpu`
        record = build(load_project(DEMOS / "numeric/gpu.toml"), output=tmp_path / "build", timeout=600)
        assert record["status"] == "native-built", record.get("stderr", "")[:4000]
        done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=600)
    out = ROOT / "results/demos/numeric"
    out.mkdir(parents=True, exist_ok=True)
    (out / "device.json").write_text(json.dumps({"exit_code": done.returncode, "stdout": done.stdout,
                                                 "stderr": done.stderr[-4000:]}, indent=2) + "\n")  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    assert "device bits equal host bits: true" in done.stdout


def test_the_agent_sees_the_bar_cover_the_plate_and_moves_it(tmp_path):
    done = script("visual", "--out", str(tmp_path / "run"), "--frames", str(tmp_path / "frames"))
    assert done.returncode == 0, done.stdout + done.stderr
    record = json.loads((tmp_path / "run/record.json").read_text())
    before, edit, after = (e["answer"] for e in record["exchanges"])
    marks = [{e["name"]: e for e in s["frames"][-1]["layout"]["elements"]} for s in (before, after)]
    assert marks[0]["bar"]["x"] < marks[0]["map"]["x"] + marks[0]["map"]["w"]  # the bar covers the plate's edge
    assert marks[1]["bar"]["x"] >= marks[1]["map"]["x"] + marks[1]["map"]["w"]
    assert edit["status"] == "typed" and after["changed"] == {}  # moving the bar changed no effect row
    assert record["test_before"] != 0 and record["test_after"] == 0
    for fresh in sorted((tmp_path / "frames").glob("*.png")):
        committed = DEMOS / "visual/frames" / fresh.name
        assert decode_png(fresh.read_bytes()) == decode_png(committed.read_bytes()), f"{fresh.name} is stale"

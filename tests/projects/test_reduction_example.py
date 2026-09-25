"""examples/reduction: an f32 sum in one launch with wide streaming loads, warp and block reductions through shared
memory nobody zeroes, and a finish; beside it the same sum through an atomic add. No `unsafe` anywhere.

The host project runs under the thread sanitizer with both compilers and holds both sums to a plain loop. The device
configuration holds the same kernels with their views @device: it runs emulated on host threads, and it compiles for
sm_120 to a kernel whose hot loop is one 128-bit streaming load, with nothing in local memory. It runs on a GPU only
under `make gpu`.
"""

import re
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.project import load_project
from emitted import assembled, contract, ran_on_device, watched
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "reduction"
AGREES = "the one-pass and the atomic sums agree with a plain loop"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_both_sums_agree_with_a_plain_loop_under_the_thread_sanitizer(tmp_path, cxx):
    cpp = compile_source(load_project(EXAMPLE).source)[0]
    ran = watched(tmp_path, cpp, cxx, "thread")
    assert ran.returncode == 0, ran.stdout + ran.stderr[-4000:]
    assert AGREES in ran.stdout and "ThreadSanitizer" not in ran.stderr


def test_the_device_kernels_are_the_host_kernels_on_device_views():
    host = (EXAMPLE / "src" / "sum.cairn").read_text().splitlines()[2:]
    device = (EXAMPLE / "src" / "device_sum.cairn").read_text().splitlines()[4:]
    assert device == [re.sub(r"((?:ro|rw)<\w+>\[\w+\])", r"\1@device", line) for line in host]


def test_nothing_in_the_example_is_unsafe():
    for path in cairn_sources(EXAMPLE / "src"):
        code = re.sub(r"//[^\n]*", "", path.read_text())  # its comments may say so
        assert not re.search(r"\b(unsafe|asm|extern)\b", code), path.name


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_device_configuration_runs_emulated_and_agrees(tmp_path, cxx):
    cpp = compile_source(load_project(EXAMPLE / "gpu.toml").source)[0]
    done = contract(tmp_path, cpp, cxx, cuda=True, emulate=True, timeout=600)
    assert done.returncode == 0 and AGREES in done.stdout, (done.returncode, done.stderr[-2000:])


def test_the_one_pass_sum_compiles_for_sm_120_to_one_wide_streaming_load_a_step(tmp_path):
    """Compiled for sm_120 and read back with cuobjdump, nothing run: the sum's kernel loads x with LDG.E.EF.128, the
    evict-first 128-bit load `Cache.streaming` asks for, adds its warps' sums with shuffles, counts itself between two
    device-wide fences, and keeps nothing in local memory."""
    sass, report = assembled(tmp_path, compile_source((EXAMPLE / "src" / "device_sum.cairn").read_text())[0])
    assert "0 bytes stack frame, 0 bytes spill stores, 0 bytes spill loads" in report
    one_pass = next(kernel for kernel in sass.split("Function :") if "blocks_then" in kernel.split("\n")[0])
    assert "LDG.E.EF.128" in one_pass and "SHFL.BFLY" in one_pass
    assert one_pass.count("MEMBAR.SC.GPU") == 2 and "ATOMG.E.ADD" in one_pass
    assert "REDG.E.ADD.F32" in sass  # the unordered sum's one atomic add a block
    assert not re.search(r"\b(LDL|STL)\b", sass)


def test_the_device_configuration_agrees_with_its_plain_loop(tmp_path):
    ran_on_device(tmp_path, compile_source(load_project(EXAMPLE / "gpu.toml").source)[0])  # only under `make gpu`

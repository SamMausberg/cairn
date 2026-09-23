"""examples/cooperative: a tiled transpose, a block reduction and a pipelined row sum at two depths, each held to a
plain loop by its own driver, and the refused programs beside them.

The host project runs under ThreadSanitizer with both compilers. The device configuration holds the same kernels with
their views @device and compiles for sm_120; it runs only under `make gpu`.
"""

import re
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.projects.project import load_project
from emitted import contract, device_build, on_device, refused, watched

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "cooperative"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_kernel_agrees_with_its_plain_loop_under_the_thread_sanitizer(tmp_path, cxx):
    cpp = compile_source(load_project(EXAMPLE).source)[0]
    ran = watched(tmp_path, cpp, cxx, "thread")
    assert ran.returncode == 0, ran.stdout + ran.stderr[-4000:]
    assert "agree with plain loops" in ran.stdout and "ThreadSanitizer" not in ran.stderr


def test_the_device_kernels_are_the_host_kernels_on_device_views():
    host = (EXAMPLE / "src" / "kernels.cairn").read_text().splitlines()[1:]
    device = (EXAMPLE / "src" / "device_kernels.cairn").read_text().splitlines()[1:]
    assert device == [re.sub(r"((?:ro|rw)<\w+>\[\w+\])", r"\1@device", line) for line in host]


def test_the_device_configuration_compiles_for_sm_120(tmp_path):
    project = load_project(EXAMPLE / "gpu.toml")
    device_build(tmp_path, compile_source(project.source)[0], entry="main")


def test_the_device_configuration_agrees_with_its_plain_loops(tmp_path):
    with on_device():  # runs only under `make gpu`
        done = contract(tmp_path, compile_source(load_project(EXAMPLE / "gpu.toml").source)[0], "g++", cuda=True)
        assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


@pytest.mark.parametrize("path", sorted((EXAMPLE / "refused").glob("*.cairn")), ids=lambda p: p.stem)
def test_each_refused_program_is_refused_with_the_code_it_names(path):
    source = path.read_text()
    code = re.match(r"// Refused with (E-[A-Z-]+)", source).group(1)
    refused(code, source)

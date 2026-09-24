"""The harness submissions built and run with torch, on the CPU only.

A host-view CAIRN kernel goes through each format's own path, SOL-ExecBench's torch.utils.cpp_extension.load and
load_inline for GPU MODE and KernelBench, and its answers are compared with the benchmark's reference on CPU tensors;
a wrong tensor is refused with a Python exception. A device kernel's program compiles with nvcc against torch's
headers, as load_inline hands it over, and nothing launches. Every test skips where torch is absent, as the suite's
own environment has none; evidence/v1_1/harness says where they ran.
"""

import json
import os
import shutil
import subprocess
import sysconfig
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
extension = pytest.importorskip("torch.utils.cpp_extension")

from test_harness import AXPY, DEVICE, RELU, STAGED, project  # noqa: E402

from cairn.projects import harness  # noqa: E402
from cairn.projects.project import load_project  # noqa: E402

NVCC = pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")
# The staged function as SOL-ExecBench passes it: x on the host, out a device destination.
STAGED_SOL = """
[benchmark]
definition = "doubled_f32"

[extents]
n = "size"

[[argument]]
name = "x"
parameter = "x"
dtype = "float32"
shape = ["size"]

[[argument]]
name = "out"
parameter = "out"
dtype = "float32"
shape = ["size"]
output = true
"""


@pytest.fixture(autouse=True)
def builds(tmp_path, monkeypatch):
    monkeypatch.setenv("TORCH_EXTENSIONS_DIR", str(tmp_path / "torch_extensions"))
    monkeypatch.setenv("MAX_JOBS", "4")


def imported(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_kernelbench_model_new_matches_the_problems_model_on_cpu_tensors(tmp_path):
    root = project(tmp_path, mapping=RELU)
    harness.write(load_project(root), "kernelbench", "relu", tmp_path / "kb", cxx="g++")
    model = imported(tmp_path / "kb" / "model_new.py").ModelNew()

    class Model(torch.nn.Module):  # KernelBench level1/19_ReLU.py's Model, at smaller sizes
        def forward(self, x):
            return torch.relu(x)

    x = torch.randn(64, 1000)
    x[0, :3] = torch.tensor([float("nan"), -0.0, float("-inf")])
    ours, theirs = model(x), Model()(x)
    assert ours.shape == theirs.shape and ours.dtype == theirs.dtype
    assert torch.equal(ours[1:], theirs[1:]) and torch.isnan(ours[0, 0]) and ours[0, 2] == 0
    for bad, said in ((x.double(), "has dtype"), (x.t(), "not contiguous"), (x.reshape(-1), "dimensions")):
        with pytest.raises(RuntimeError, match=said):
            model(bad)


def test_a_gpumode_submission_returns_the_reference_output(tmp_path):
    root = project(tmp_path)
    harness.write(load_project(root), "gpumode", "axpy", tmp_path / "gm", cxx="g++")
    submission = imported(tmp_path / "gm" / "submission.py")
    x, y = torch.randn(33, 65), torch.randn(33, 65)
    out = torch.empty_like(x)
    got = submission.custom_kernel((x, y, 1.5, out))
    assert got is out and torch.equal(got, 1.5 * x + y)  # two roundings each side, no fused multiply-add
    with pytest.raises(RuntimeError, match="overlaps"):
        submission.custom_kernel((x, y, 1.5, x))
    with pytest.raises(RuntimeError, match="dimension 1"):
        submission.custom_kernel((x, torch.randn(33, 64), 1.5, out))


def test_a_sol_execbench_solution_builds_as_its_driver_builds_it_and_matches_the_reference(tmp_path):
    root = project(tmp_path)
    harness.write(load_project(root), "sol-execbench", "axpy", tmp_path / "sol", cxx="g++")
    solution = json.loads((tmp_path / "sol" / "solution.json").read_text())
    stage = tmp_path / "stage"  # as driver/templates/build_ext.py stages and builds it
    stage.mkdir()
    for source in solution["sources"]:
        (stage / source["path"]).write_text(source["content"])
    options = solution["spec"]["compile_options"]
    built = extension.load(name="benchmark_kernel", sources=[str(p) for p in stage.iterdir() if p.suffix in (".cu", ".cpp")],
                           extra_cflags=options["cflags"], extra_cuda_cflags=options["cuda_cflags"],
                           extra_ldflags=options["ld_flags"], extra_include_paths=[str(stage)],
                           build_directory=str(stage))  # fmt: skip
    x, y, out = torch.randn(7, 129), torch.randn(7, 129), torch.empty(7, 129)
    assert built.run(x, y, 0.25, out) is None  # destination-passing style: outputs last, nothing returned
    reference = {}
    exec("import torch\ndef run(x, y, a):\n    return a * x + y\n", reference)
    assert torch.equal(out, reference["run"](x, y, 0.25))


def test_the_solution_validates_against_sol_execbench_s_own_models(tmp_path):
    core = pytest.importorskip("sol_execbench.core")
    root = project(tmp_path)
    harness.write(load_project(root), "sol-execbench", "axpy", tmp_path / "sol", cxx="g++")
    solution = core.Solution(**json.loads((tmp_path / "sol" / "solution.json").read_text()))
    assert solution.get_entry_symbol() == "run" and solution.spec.compile_options.cuda_cflags == []
    assert solution.spec.destination_passing_style and solution.get_entry_source().path == "main.cpp"


@NVCC
def test_a_device_program_compiles_with_nvcc_against_torch_headers_as_load_inline_passes_it(tmp_path):
    root = project(tmp_path, DEVICE, AXPY)
    harness.write(load_project(root), "gpumode", "axpy", tmp_path / "gm", cxx="g++", device_target="sm_100a")
    record = json.loads((tmp_path / "gm" / "harness.json").read_text())
    export = tmp_path / "gm" / "export"
    for name in record["export"]["files"]:
        shutil.copy(export / name, tmp_path / name)
    # load_inline puts torch/types.h, cuda.h and cuda_runtime.h before the program in cuda.cu
    program = (export / "program.cu").read_text()
    (tmp_path / "cuda.cu").write_text(
        "#include <torch/types.h>\n#include <cuda.h>\n#include <cuda_runtime.h>\n" + program
    )
    includes = [f"-I{tmp_path}", *(flag for p in extension.include_paths() for flag in ("-isystem", p))]
    line = ["nvcc", *includes, *extension.COMMON_NVCC_FLAGS, "--compiler-options", "-fPIC",
            *record["flags"]["cuda_cflags"], "-c", str(tmp_path / "cuda.cu"), "-o", str(tmp_path / "cuda.o")]  # fmt: skip
    done = subprocess.run(line, capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-4000:]


@NVCC
@pytest.mark.parametrize("source, mapping, symbol, called", [
    (DEVICE, AXPY, "axpy", "cq_axpy("),  # queued on torch's stream, with no wait
    (STAGED, STAGED_SOL, "doubled", "cf_doubled("),  # E-ENQUEUE: it transfers from the host, so it waits
])  # fmt: skip
def test_the_device_binding_compiles_against_a_cuda_torch_s_headers(tmp_path, source, mapping, symbol, called):
    torch_root = Path(os.environ.get("CAIRN_TORCH_CUDA_ROOT", Path(torch.__file__).parent))
    if not (torch_root / "include/c10/cuda/impl/cuda_cmake_macros.h").is_file():
        pytest.skip("a CPU wheel has no c10/cuda/impl/cuda_cmake_macros.h, which a CUDA build of torch generates; "
                    "set CAIRN_TORCH_CUDA_ROOT to the torch directory of a CUDA wheel")  # fmt: skip
    root = project(tmp_path, source, mapping)
    harness.write(load_project(root), "sol-execbench", symbol, tmp_path / "sol", cxx="g++", device_target="sm_100a")
    solution = json.loads((tmp_path / "sol" / "solution.json").read_text())
    for source_file in solution["sources"]:
        (tmp_path / source_file["path"]).write_text(source_file["content"])
    assert called in (tmp_path / "main.cpp").read_text()
    cuda = Path(shutil.which("nvcc")).parents[1] / "include"
    includes = [f"-I{tmp_path}", f"-isystem{torch_root / 'include'}",
                f"-isystem{torch_root / 'include/torch/csrc/api/include'}", f"-isystem{cuda}"]  # fmt: skip
    python = sysconfig.get_path("include", scheme="posix_prefix")
    line = ["c++", "-DTORCH_EXTENSION_NAME=benchmark_kernel", "-DTORCH_API_INCLUDE_EXTENSION_H", *includes,
            f"-isystem{python}", "-fPIC", "-std=c++17", *solution["spec"]["compile_options"]["cflags"], "-c",
            str(tmp_path / "main.cpp"), "-o", str(tmp_path / "main.o")]  # fmt: skip
    done = subprocess.run(line, capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-4000:]

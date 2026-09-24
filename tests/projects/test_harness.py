"""`cairn export --harness` and `cairn new --from-sol-execbench`, without torch.

A mapping is checked against the CAIRN signature before anything is written, and each refusal names its code. Each
format's file is written beside the export it embeds; the Python ones compile, the SOL-ExecBench one is the JSON its
schema asks for, and the binding refuses a wrong tensor before a pointer crosses. Nothing here runs an evaluator,
submits or reaches the network; tests/projects/test_harness_torch.py builds and runs the submissions with torch.
"""

import json
import py_compile
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.tree import Diagnostic
from cairn.projects import harness
from cairn.projects.harness_import import create
from cairn.projects.harness_sources import Library, binding
from cairn.projects.project import ProjectError, load_project

NVCC = pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")

HOST = """
// out = x where x is not below zero, else zero; a NaN stays NaN.
pub fn relu(n:usize, x:ro<f32>[n], out:rw<f32>[n]) {
  parallel i in n {
    let v = x[i];
    if v < 0.0 { out[i] = 0.0; } else { out[i] = v; }
  }
}

// out = a * x + y.
pub fn axpy(n:usize, x:ro<f32>[n], y:ro<f32>[n], a:f32, out:rw<f32>[n]) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}

pub fn halves(n:usize, x:ro<bf16>[n], k:i32, out:rw<bf16>[n]) {
  for i in 0..n { out[i] = bf16(f32(x[i]) * 0.5); }
}

pub fn consume(b:Buf[u8]) { }
"""

DEVICE = """
pub fn axpy(n:usize, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32, out:rw<f32>[n]@device) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
"""

AXPY = """
[benchmark]
definition = "axpy_f32"
leaderboard = "axpy"
gpu = "B200"

[extents]
n = "rows * cols"

[[argument]]
name = "x"
parameter = "x"
dtype = "float32"
shape = ["rows", "cols"]

[[argument]]
name = "y"
parameter = "y"
dtype = "torch.float32"
shape = ["rows", "cols"]

[[argument]]
name = "a"
parameter = "a"
dtype = "float32"

[[argument]]
name = "out"
parameter = "out"
dtype = "f32"
shape = ["rows", "cols"]
output = true
"""

RELU = """
[extents]
n = "batch * dim"

[[argument]]
name = "x"
parameter = "x"
dtype = "float32"
shape = ["batch", "dim"]

[[result]]
name = "out"
parameter = "out"
dtype = "float32"
shape = ["batch", "dim"]
"""


def project(tmp_path: Path, source: str = HOST, mapping: str = AXPY, name: str = "kernels") -> Path:
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text(source)
    (root / "cairn.toml").write_text(
        f'[project]\nname = "{name}"\nsources = ["src/main.cairn"]\n\n[build]\nkind = "library"\n'
    )
    (root / "harness.toml").write_text(mapping)
    return root


def written(tmp_path: Path, fmt: str, symbol: str = "axpy", mapping: str = AXPY, source: str = HOST, **more) -> Path:
    root = project(tmp_path, source, mapping)
    harness.write(load_project(root), fmt, symbol, tmp_path / fmt, cxx="g++", **more)
    return tmp_path / fmt


def refused(tmp_path: Path, mapping: str, fmt: str = "sol-execbench", symbol: str = "axpy", source: str = HOST) -> str:
    root = project(tmp_path, source, mapping)
    with pytest.raises(Diagnostic) as caught:
        harness.write(load_project(root), fmt, symbol, tmp_path / "out", cxx="g++")
    assert not (tmp_path / "out").exists() and not [p for p in tmp_path.iterdir() if p.name.startswith(".out-")]
    return caught.value.data["code"]


def test_a_solution_holds_the_export_the_binding_and_flags_that_keep_cairn_numerics(tmp_path):
    out = written(tmp_path, "sol-execbench")
    record = json.loads((out / "harness.json").read_text())
    solution = json.loads((out / "solution.json").read_text())
    spec = solution["spec"]
    assert spec["languages"] == ["cuda_cpp"] and spec["entry_point"] == "main.cpp::run" and spec["binding"] == "torch"
    assert spec["destination_passing_style"] is True and solution["definition"] == "axpy_f32"
    options = spec["compile_options"]
    assert "--use_fast_math" not in options["cuda_cflags"] and "-ffp-contract=off" in options["cflags"]
    assert "-std=c++20" in options["cflags"] and "--use_fast_math" in record["flags"]["why"]
    exported = json.loads((out / "export" / "export.json").read_text())
    sources = {s["path"]: s["content"] for s in solution["sources"]}
    assert set(sources) == {*exported["files"], "main.cpp"}
    for name in exported["files"]:  # every source but the binding is the export's, byte for byte
        assert sources[name] == (out / "export" / name).read_text()
    assert record["schema"] == "cairn.harness/1" and record["format"] == "sol-execbench"
    assert record["upstream"]["commit"] == "a9fa0804c793d438e70850c33fe34426e66d53dd"
    assert (
        record["function"]["signature"]
        == "axpy(n:usize, x:ro<f32>[n]@host, y:ro<f32>[n]@host, a:f32, out:rw<f32>[n]@host)"
    )
    assert record["export"]["identity"] == exported["identity"] and record["entry"]["symbol"] == "cf_axpy"
    assert [a["name"] for a in record["mapping"]["arguments"]] == ["x", "y", "a", "out"]
    assert "0.5 at the baseline" in record["metric"] and "not a utilization" in record["metric"]
    assert record["commands"][0]["command"].startswith("sol-execbench PROBLEM_DIR --solution ")
    assert "nothing was submitted" in record["not_run"]


def test_the_binding_refuses_a_wrong_tensor_before_any_pointer_crosses(tmp_path):
    out = written(tmp_path, "sol-execbench")
    glue = next(
        s["content"] for s in json.loads((out / "solution.json").read_text())["sources"] if s["path"] == "main.cpp"
    )
    assert "void run(torch::Tensor t_x, torch::Tensor t_y, double a_a, torch::Tensor t_out)" in glue
    for check in ("t_x.scalar_type() == at::kFloat", "t_x.device().is_cpu()", "t_x.is_contiguous()", "t_x.dim() == 2",
                  "cairn_harness::size(t_y.size(0)) == x_rows", "cairn_harness::mul(x_rows, x_cols",
                  "cairn_harness::size(t_out.numel()) == p_n", "!cairn_harness::overlap(t_out, t_x)"):  # fmt: skip
        assert check in glue, check
    call = glue.index("cf_axpy(p_n")
    assert all(glue.index(c) < call for c in ("t_out.is_contiguous()", "overlap(t_out, t_y)", "t_out.numel()"))
    assert '#include "kernels.h"' in glue and "PYBIND11_MODULE(TORCH_EXTENSION_NAME, m)" in glue


def test_gpumode_and_kernelbench_files_compile_and_embed_the_export(tmp_path):
    gm = written(tmp_path, "gpumode")
    text = (gm / "submission.py").read_text()
    assert text.startswith("#!POPCORN leaderboard axpy\n#!POPCORN gpu B200\n")
    assert "def custom_kernel(data):" in text and "x, y, a, out = data" in text and "return out" in text
    py_compile.compile(str(gm / "submission.py"), doraise=True)
    record = json.loads((gm / "harness.json").read_text())
    modes = {c["command"].split("--mode ")[1].split()[0]: c["does"] for c in record["commands"]}
    assert set(modes) == {"test", "benchmark", "profile", "leaderboard"} and "public ranked" in modes["leaderboard"]
    assert "geometric mean" in record["metric"]
    root = project(tmp_path / "kb", mapping=RELU)
    harness.write(load_project(root), "kernelbench", "relu", tmp_path / "kbout", cxx="g++")
    model = (tmp_path / "kbout" / "model_new.py").read_text()
    py_compile.compile(str(tmp_path / "kbout" / "model_new.py"), doraise=True)
    assert "class ModelNew(nn.Module):" in model and "def forward(self, x):" in model
    assert "cpp_sources=[_PROGRAM, _BINDING]" in model  # a host library is one translation unit with its binding
    exported = json.loads((tmp_path / "kbout" / "export" / "export.json").read_text())
    for name, digest in exported["files"].items():
        if name.endswith((".hpp", ".cpp")):
            assert digest in model, name
    assert "fast_p" in json.loads((tmp_path / "kbout" / "harness.json").read_text())["metric"]


def test_kernelbench_strict_checks_see_no_fallback_in_the_python(tmp_path):
    """KernelBench's static checker refuses try, except and pass anywhere outside a comment
    (src/kernelbench/kernel_static_checker.py at the pinned commit), so the generated Python uses none of them."""
    import re

    root = project(tmp_path, mapping=RELU)
    harness.write(load_project(root), "kernelbench", "relu", tmp_path / "kb", cxx="g++")
    code = "\n".join(
        line.split("#")[0].split("//")[0] for line in (tmp_path / "kb/model_new.py").read_text().splitlines()
    )
    assert not any(re.search(p, code) for p in (r"\btry\s*:", r"\bexcept\s*:", r"\bexcept\s+\w+", r"\bpass\b"))
    assert not re.search(r"torch\.cuda\.Stream\s*\(|import\s+threading|concurrent\.futures", code)


@pytest.mark.parametrize("fmt, mapping, code", [
    ("sol-execbench", AXPY.replace('parameter = "y"', 'parameter = "x"'), "E-HARNESS-MAPPING"),  # fed twice
    ("sol-execbench", AXPY.replace('[extents]\nn = "rows * cols"\n', ""), "E-HARNESS-MAPPING"),  # n unfed
    ("sol-execbench", AXPY.replace('"rows * cols"', '"rows ** cols"'), "E-HARNESS-MAPPING"),
    ("sol-execbench", AXPY.replace('"rows * cols"', '"rows * depth"'), "E-HARNESS-MAPPING"),  # an unbound axis
    ("sol-execbench", AXPY + "\n[weights]\nw = 1\n", "E-HARNESS-MAPPING"),
    ("sol-execbench", AXPY.replace('parameter = "a"', 'parameter = "alpha"'), "E-HARNESS-MAPPING"),
    ("sol-execbench", AXPY.replace('output = true\n', ""), "E-HARNESS-MAPPING"),  # CAIRN writes an input
    ("sol-execbench", AXPY.replace('dtype = "float32"\nshape = ["rows", "cols"]', 'dtype = "float16"\nshape = ["rows", "cols"]', 1), "E-HARNESS-DTYPE"),
    ("sol-execbench", AXPY.replace('dtype = "float32"\nshape = ["rows", "cols"]', 'dtype = "float4_e2m1fn_x2"\nshape = ["rows", "cols"]', 1), "E-HARNESS-DTYPE"),
    ("sol-execbench", AXPY.replace('name = "a"\nparameter = "a"\ndtype = "float32"', 'name = "a"\nparameter = "a"\ndtype = "int64"'), "E-HARNESS-DTYPE"),
    ("sol-execbench", AXPY.replace('definition = "axpy_f32"\n', ""), "E-HARNESS-FORMAT"),
    ("sol-execbench", RELU, "E-HARNESS-MAPPING"),  # relu has no y and no a: nothing in it names axpy's
    ("kernelbench", AXPY, "E-HARNESS-FORMAT"),  # KernelBench passes no destination
])  # fmt: skip
def test_a_mapping_the_signature_does_not_bear_is_refused_and_nothing_is_written(tmp_path, fmt, mapping, code):
    assert refused(tmp_path, mapping, fmt) == code


def test_a_result_is_refused_where_the_benchmark_passes_destinations(tmp_path):
    assert refused(tmp_path, RELU, "sol-execbench", "relu") == "E-HARNESS-FORMAT"


def test_nvfp4_is_refused_by_name(tmp_path):
    root = project(tmp_path, mapping=RELU.replace('dtype = "float32"', 'dtype = "float4_e2m1"', 1))
    with pytest.raises(Diagnostic) as caught:
        harness.write(load_project(root), "kernelbench", "relu", tmp_path / "out", cxx="g++")
    assert caught.value.data["code"] == "E-HARNESS-DTYPE" and "NVFP4" in caught.value.data["message"]


def test_a_storage_float_scalar_and_a_function_without_a_c_entry_are_refused(tmp_path):
    halves = RELU.replace('parameter = "out"', 'parameter = "out"').replace('"float32"', '"bfloat16"')
    halves = halves.replace("[[result]]", '[[argument]]\nname = "k"\nparameter = "k"\ndtype = "float32"\n\n[[result]]')
    assert refused(tmp_path / "a", halves, "kernelbench", "halves") == "E-HARNESS-DTYPE"  # a float feeding i32
    assert refused(tmp_path / "b", RELU, "kernelbench", "consume") == "E-HARNESS-SYMBOL"  # an owner: no C entry
    assert refused(tmp_path / "c", RELU, "kernelbench", "nowhere") == "E-HARNESS-SYMBOL"


def test_a_scalar_feeds_a_usize_extent_with_its_range_checked(tmp_path):
    mapping = (
        RELU.replace('[extents]\nn = "batch * dim"\n', "")
        + '\n[[argument]]\nname = "n"\nparameter = "n"\ndtype = "int64"\n'
    )
    mapping = mapping.replace('shape = ["batch", "dim"]', 'shape = ["size"]')
    root = project(tmp_path, mapping=mapping)
    harness.write(load_project(root), "kernelbench", "relu", tmp_path / "out", cxx="g++")
    model = (tmp_path / "out/model_new.py").read_text()
    assert "def forward(self, x, n):" in model
    assert 'cairn_harness::integer<std::size_t>(a_n, "n")' in model and "== s_n" in model


def test_the_no_wait_entry_is_called_on_the_stream_once_the_header_declares_it(tmp_path):
    root = project(tmp_path, DEVICE)
    function = next(f for f in harness.Header(DEVICE, "kernels").p.functions if f.name == "axpy")
    mapping = harness.load(root / "harness.toml", function, "sol-execbench")
    ctypes = {n: "size_t" if n == "n" else "float" for n, _ in function.params}
    waiting = binding(mapping, Library("kernels", "cf_axpy", "cairn_kernels_device_stream", None, True, ctypes), "x")
    assert "cairn_kernels_device_stream(static_cast<void*>(at::cuda::getCurrentCUDAStream().stream()));" in waiting
    assert "cf_axpy(p_n" in waiting and "cairn_kernels_device_stream(nullptr);" in waiting and "cq_axpy" in waiting
    queued = binding(
        mapping, Library("kernels", "cf_axpy", "cairn_kernels_device_stream", "cq_axpy", True, ctypes), "x"
    )
    assert "cq_axpy(static_cast<void*>(at::cuda::getCurrentCUDAStream().stream()), p_n" in queued
    assert "cf_axpy(" not in queued and "device_stream(" not in queued
    assert "c10::cuda::CUDAGuard guard(device);" in queued and "t_x.is_cuda()" in queued


@NVCC
def test_a_device_solution_compiles_for_its_target_and_binds_the_torch_stream(tmp_path):
    out = written(tmp_path, "sol-execbench", source=DEVICE, device_target="sm_100a")
    solution = json.loads((out / "solution.json").read_text())
    options = solution["spec"]["compile_options"]
    assert "-arch=sm_100a" in options["cuda_cflags"] and "--fmad=false" in options["cuda_cflags"]
    assert options["cuda_cflags"][-2:] == ["-Xcompiler", "-ffp-contract=off,-fno-fast-math"]
    assert solution["spec"]["target_hardware"] == ["B200", "LOCAL"]
    glue = next(s["content"] for s in solution["sources"] if s["path"] == "main.cpp")
    assert "cairn_kernels_device_stream(static_cast<void*>(at::cuda::getCurrentCUDAStream().stream()));" in glue
    record = json.loads((out / "harness.json").read_text())
    assert record["entry"]["symbol"] == "cf_axpy" and "cq_axpy is not declared" in record["entry"]["no_wait_entry"]
    assert record["export"]["device_target"] == "sm_100a"


@NVCC
def test_a_gpu_the_target_does_not_load_on_is_refused(tmp_path):
    root = project(tmp_path, DEVICE)
    with pytest.raises(Diagnostic) as caught:
        harness.write(load_project(root), "gpumode", "axpy", tmp_path / "out", cxx="g++", device_target="sm_120")
    assert caught.value.data["code"] == "E-TARGET-MISMATCH" and not (tmp_path / "out").exists()


def test_the_command_line_writes_checks_and_refuses_a_changed_submission(tmp_path, capsys):
    root = project(tmp_path, mapping=RELU)
    out = tmp_path / "kb"
    assert main(["export", str(root), "--harness", "kernelbench", "--symbol", "relu", "--out", str(out),
                 "--cxx", "g++", "--format", "json"]) == 0  # fmt: skip
    made = json.loads(capsys.readouterr().out)
    assert made["status"] == "exported-harness" and made["file"] == "model_new.py"
    assert main(["export", str(out), "--format", "json"]) == 0
    intact = json.loads(capsys.readouterr().out)
    assert intact["status"] == "harness-intact" and intact["identity"] == made["identity"]
    (out / "model_new.py").write_text((out / "model_new.py").read_text() + "\n# edited\n")
    assert main(["export", str(out), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-EXPORT-TAMPERED"
    assert (
        main(["export", str(root), "--harness", "kernelbench", "--out", str(tmp_path / "x"), "--format", "json"]) == 2
    )
    capsys.readouterr()


def test_writing_a_submission_runs_no_evaluator_and_opens_no_connection(tmp_path, monkeypatch):
    ran: list[list[str]] = []

    class Watched(subprocess.Popen):
        def __init__(self, args, *a, **k):
            ran.append([str(x) for x in (args if isinstance(args, list | tuple) else [args])])
            super().__init__(args, *a, **k)

    def offline(*a, **k):
        raise AssertionError("a harness export opened a network connection")

    monkeypatch.setattr(subprocess, "Popen", Watched)
    monkeypatch.setattr(socket.socket, "connect", offline)
    monkeypatch.setattr(socket, "create_connection", offline)
    for fmt in ("gpumode", "sol-execbench"):
        written(tmp_path / fmt, fmt)
    programs = {Path(argv[0]).name for argv in ran}
    assert programs <= {"g++", "clang++", "nvcc"}, programs  # the compilers' --version, nothing else
    assert all("--version" in argv for argv in ran)


def test_a_sol_execbench_definition_becomes_a_project_whose_mapping_holds(tmp_path):
    problem = tmp_path / "problem"
    problem.mkdir()
    definition = {
        "name": "scale_rows_h256", "op_type": "scale_rows", "description": "Each row times its weight.",
        "axes": {"rows": {"type": "var"}, "hidden": {"type": "const", "value": 256},
                 "half": {"type": "expr", "expression": "hidden // 2"}},
        "inputs": {"x": {"shape": ["rows", "hidden"], "dtype": "bfloat16"}, "w": {"shape": ["rows"], "dtype": "float32"},
                   "halfs": {"shape": ["half"], "dtype": "float32"}, "eps": {"shape": None, "dtype": "float32"}},
        "outputs": {"out": {"shape": ["rows", "hidden"], "dtype": "bfloat16"}, "norm": {"shape": [], "dtype": "float32"}},
        "reference": "import torch\n\ndef run(x, w, halfs, eps):\n    return (x * w[:, None]).to(x.dtype), x.float().norm()\n",
    }  # fmt: skip
    (problem / "definition.json").write_text(json.dumps(definition))
    workloads = [{"uuid": "a", "axes": {"rows": 3}, "inputs": {}, "tolerance": {"max_atol": 0.002, "max_rtol": 0.05}},
                 {"uuid": "b", "axes": {"rows": 9}, "inputs": {}, "tolerance": {"max_atol": 0.01, "max_rtol": 0.004,
                                                                                "max_error_cap": 0.5}}]  # fmt: skip
    (problem / "workload.jsonl").write_text("\n".join(json.dumps(w) for w in workloads) + "\n")
    made = create(tmp_path / "scaled", problem / "definition.json")
    assert made["function"] == "scale_rows" and made["device_target"] == "sm_100a"
    assert made["tolerance"] == {"absolute": 0.002, "relative": 0.004}  # the tightest each workload states
    assert any("max_error_cap 0.5" in n for n in made["not_expressed"])
    assert any("required_matched_ratio" in n for n in made["not_expressed"])
    root = tmp_path / "scaled"
    assert json.loads((root / "policy.json").read_text()) == {"tolerance": {"absolute": 0.002, "relative": 0.004}}
    source = (root / "src/scale_rows.cairn").read_text()
    assert "const HIDDEN:usize = 256;" in source and "// def run(x, w, halfs, eps):" in source
    assert (
        "pub fn scale_rows(rows:usize, half:usize, x_len:usize, x:ro<bf16>[x_len]@device, w:ro<f32>[rows]@device, "
        "halfs:ro<f32>[half]@device, eps:f32, out:rw<bf16>[x_len]@device, norm:rw<f32>[1]@device)"
    ) in source
    assert (root / "problem/definition.json").is_file() and (root / "problem/workload.jsonl").is_file()
    project_ = load_project(root)
    function = next(f for f in harness.Header(project_.source, project_.name).p.functions if f.name == "scale_rows")
    mapping = harness.load(root / "harness.toml", function, "sol-execbench")
    assert [t.name for t in mapping.arguments] == ["x", "w", "halfs", "eps", "out", "norm"]
    assert [t.role for t in mapping.arguments][-2:] == ["output", "output"]
    assert mapping.record()["axes"] == {"hidden": "256", "half": "(hidden / 2)"}


def test_a_definition_cairn_has_no_type_for_is_refused_and_nothing_is_written(tmp_path):
    definition = {"name": "fp4", "axes": {"n": {"type": "var"}}, "inputs": {"x": {"shape": ["n"], "dtype": "float4_e2m1fn_x2"}},
                  "outputs": {"y": {"shape": ["n"], "dtype": "float32"}}, "reference": ""}  # fmt: skip
    (tmp_path / "definition.json").write_text(json.dumps(definition))
    with pytest.raises(ProjectError, match="NVFP4"):
        create(tmp_path / "fp4", tmp_path / "definition.json")
    assert not (tmp_path / "fp4").exists()

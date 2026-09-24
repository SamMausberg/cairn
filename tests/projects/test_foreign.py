"""Foreign implementations: vendored C++ and CUDA built by the project's own command line, held to the types their
externs declare, inspected, and run against the CAIRN reference they stand for, with a record that claims only what
ran. The C++ runs natively under both compilers; the CUDA compiles for sm_120 and runs only under `make gpu`."""

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic, compile_program, compile_source
from cairn.compiler.codegen import mangle
from cairn.projects.build import build
from cairn.projects.foreign import inspect
from cairn.projects.project import ProjectError, load_project
from cairn.projects.target import parse
from cairn.verify import boundaries
from cairn.verify.device_validation import cases_as_tests
from cairn.verify.foreign import passed, report
from cairn.verify.runner import run_tests
from emitted import NVCC_HOST, SANITIZED, WARNINGS, code_of, on_device, refused

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "examples/foreign/host"
DEVICE = ROOT / "examples/foreign/device"
KERNEL = 'extern "k" fn k(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) launch(n, 256) effects();\n'
READS = 'extern "r" fn r(n:usize, x:ro<f32>[n]@device) launch(n, 256) effects();\n'  # lends nothing lanes write
needs_nvcc = pytest.mark.skipif(
    not shutil.which("nvcc") or not shutil.which(NVCC_HOST), reason=f"needs nvcc and {NVCC_HOST}"
)

REJECTIONS = {
    "a launched kernel returns nothing": (
        "E-LAUNCH", 'extern "k" fn k(n:usize, x:ro<f32>[n]@device) -> f32 launch(n, 256) effects();'),
    "a block is whole warps": (
        "E-LAUNCH", 'extern "k" fn k(n:usize, x:ro<f32>[n]@device) launch(n, 100) effects();'),
    "a block is at most 1024 threads": (
        "E-LAUNCH", 'extern "k" fn k(n:usize, x:ro<f32>[n]@device) launch(n, 2048) effects();'),
    "threads are counted by a usize parameter": (
        "E-LAUNCH", 'extern "k" fn k(n:usize, a:f32, x:ro<f32>[n]@device) launch(a, 256) effects();'),
    "a launched kernel takes scalars and views": (
        "E-LAUNCH", 'struct P { a:u64; }\nextern "k" fn k(n:usize, p:P, x:ro<f32>[n]@device) launch(n, 256) effects();'),
    "a launched kernel reaches no host memory": (
        "E-PLACEMENT", 'extern "k" fn k(n:usize, x:ro<f32>[n]) launch(n, 256) effects();'),
    "a launch is a foreign call, inside unsafe": (
        "E-UNSAFE", KERNEL + "fn f(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { k(n, out, x); }"),
    "a device lane launches nothing": (
        "E-PARALLEL-CALL", READS + "fn f(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device) "
        "{ parallel i in n { unsafe { r(n, x); } y[i] = x[i]; } }"),
    "a host lane launches nothing": (
        "E-PARALLEL-CALL", READS + "fn g(n:usize, x:ro<f32>[n]@device) { unsafe { r(n, x); } }\n"
        "fn f(n:usize, h:rw<u64>[n], x:ro<f32>[n]@device) { parallel i in n { g(n, x); h[i] = 0; } }"),
}  # fmt: skip


@pytest.mark.parametrize("rule", REJECTIONS)
def test_a_launched_kernel_refuses(rule):
    code, source = REJECTIONS[rule]
    refused(code, source)


def test_a_launch_waits_for_the_device_and_its_row_says_so():
    source = KERNEL + "fn f(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { unsafe { k(n, out, x); } }"
    cpp, receipt = compile_source(source)
    assert "__global__ void k(std::size_t, float*, const float*);" in cpp
    assert "k<<<static_cast<unsigned>(cr_blocks), 256, 0, cr_stream>>>(v_n, v_out, v_x);" in cpp
    assert "cr::reuse::synchronous(cr::gpu::here()" in cpp and "cuda" in receipt["requires"]
    assert {"ffi:k", "par:device", "trap", "write:out", "read:x"} <= set(receipt["functions"]["f"]["effects"])
    canonical = canonical_source(source)
    assert "launch(n, 256)" in canonical and compile_source(canonical)[0] == cpp


def copied(tmp_path: Path, project: Path) -> Path:
    shutil.copytree(project, tmp_path / project.name, ignore=shutil.ignore_patterns("build"))
    return tmp_path / project.name


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.replace('"vendor/histogram.cpp"', '"vendor/histogram.c"'), "C\\+\\+ or CUDA"),
        (lambda t: t.replace('"vendor/histogram.cpp"', '"../histogram.cpp"'), "Noncanonical"),
        (lambda t: t.replace('["histogram_u32_interleaved"]', '["histogram-u32"]'), "C identifiers"),
        (lambda t: t.replace('["histogram_u32_interleaved"]', '"histogram_u32_interleaved"'), "in a list"),
    ],
)
def test_a_foreign_table_is_data_inside_the_project(tmp_path, change, message):
    root = copied(tmp_path, HOST)
    (root / "cairn.toml").write_text(change((root / "cairn.toml").read_text()))
    with pytest.raises(ProjectError, match=message):
        load_project(root)


def test_the_receipt_pins_every_vendored_source(tmp_path):
    record = load_project(copied(tmp_path, HOST)).receipt()
    assert record["foreign"][0]["path"] == "vendor/histogram.cpp" and len(record["foreign"][0]["sha256"]) == 64


def test_a_symbol_no_extern_declares_is_refused(tmp_path):
    root = copied(tmp_path, HOST)
    manifest = (root / "cairn.toml").read_text().replace('interleaved"]', 'interleaved", "histogram_other"]')
    (root / "cairn.toml").write_text(manifest)
    with pytest.raises(ProjectError, match="histogram_other, which no extern"):
        build(load_project(root), kind="exe")


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_definition_of_other_types_does_not_build(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    root = copied(tmp_path, HOST)
    vendored = root / "vendor/histogram.cpp"
    vendored.write_text(vendored.read_text().replace("const std::uint32_t* x", "const std::int32_t* x"))
    record = build(load_project(root), cxx=cxx, kind="exe")
    assert record["status"] == "native-build-failed" and record["foreign"][0]["status"] == "native-build-failed"
    assert "with other types than histogram_cpp(n:usize" in record["stderr"]


def test_a_cpp_symbol_is_found_by_its_c_name(tmp_path):
    root = copied(tmp_path, HOST)
    vendored = root / "vendor/histogram.cpp"
    vendored.write_text(vendored.read_text().replace('extern "C" void', "void"))
    record = build(load_project(root), kind="exe")
    assert record["status"] == "native-build-failed" and "histogram_u32_interleaved" in record["stderr"]


def test_the_host_implementation_matches_its_reference_under_both_compilers():
    record = report(load_project(HOST), "histogram_interleaved")
    assert record["implements"] == "histogram_u32" and len(record["identity"]) == 64
    assert record["contract"]["trust"] == "declared-not-checked"
    assert "ffi:histogram_u32_interleaved" in record["contract"]["externs"]["histogram_cpp"]["effects"]
    assert record["native_built"]["status"] == "native-built"
    assert record["device_inspected"]["status"] == "not-applicable"
    for cxx in ("clang++", "g++"):
        finite = record["finite_tested"][cxx]["finite"]
        assert finite["status"] == "passed" and finite["implementation_ran"] == finite["cases"] > 100
    assert passed(record)


def test_a_wrong_implementation_fails_on_a_case_with_a_tail(tmp_path):
    root = copied(tmp_path, HOST)
    vendored = root / "vendor/histogram.cpp"  # the samples after the last whole four are dropped
    vendored.write_text(vendored.read_text().replace("for (; i < n; ++i) ++table[0][x[i] & 255u];", ""))
    record = report(load_project(root), "histogram_interleaved", compilers=("clang++",))
    finite = record["finite_tested"]["clang++"]["finite"]
    assert finite["status"] == "failed" and finite["failed"]["inputs"]["n"] % 4 != 0
    assert not passed(record)


SANITIZED_TEST = """
test interleaved_counts_what_the_reference_counts {
  for n in 0..70 {
    buffer x:u32[n] = zeroed;
    for i in 0..n { x[i] = u32(i * 2654435761 % 4099); }
    stack want:u64[256] = zeroed;
    stack got:u64[256] = zeroed;
    histogram_u32(n, want, x);
    histogram_interleaved(n, got, x);
    for b in 0..256 { assert_eq(want[b], got[b]); }
  }
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_implementation_runs_clean_under_the_address_and_undefined_sanitizers(tmp_path, cxx):
    """A test block that runs both on every n below 70, and the vendored source, built with the sanitizers."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    test = "test$interleaved_counts_what_the_reference_counts"
    cpp, _ = compile_source(load_project(HOST).source + SANITIZED_TEST, roots=(test,))
    (tmp_path / "p.cpp").write_text(cpp + f"\nint main() {{ ctest_{mangle(test)}(); return 0; }}\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags, objects = [*SANITIZED, *WARNINGS], []
    for unit in (tmp_path / "p.cpp", HOST / "vendor/histogram.cpp"):
        objects.append(str(tmp_path / (unit.stem + ".o")))
        subprocess.run([cxx, *flags, "-c", str(unit), "-o", objects[-1]], check=True, timeout=300)
    subprocess.run([cxx, *flags, *objects, "-o", str(tmp_path / "p")], check=True, timeout=300)
    done = subprocess.run([str(tmp_path / "p")], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stderr[-3000:]


def test_the_vendored_benchmark_is_the_repository_s_own_byte_for_byte():
    assert (DEVICE / "vendor/parallel_gpu.cu").read_bytes() == (ROOT / "bench/gpu/parallel_gpu.cu").read_bytes()


@needs_nvcc
def test_the_cuda_sources_build_for_sm_120_and_ptxas_reports_their_kernels():
    record = report(load_project(DEVICE), "stencil_tiled", compilers=(NVCC_HOST,))
    assert record["native_built"]["status"] == "native-built"
    kernels = record["device_inspected"]["sources"]["vendor/stencil.cu"]["kernels"]
    tiled = next(v for k, v in kernels.items() if k.startswith("stencil_1d_tiled"))
    assert tiled["shared_bytes"] == 4 * (256 + 2) and tiled["registers"] > 0 and tiled["spill_bytes"] == 0
    tested = record["finite_tested"]
    assert tested["status"] == "not-run" and "make gpu" in tested["reason"]
    assert tested["built"] == "native-built" and tested["cases"] == tested["coverage"]["written"] > 0
    assert tested["coverage"]["left_out"] == {}  # every generated case was written
    assert passed(record)  # not running is not failing, and not passing either: finite_tested says not-run


@needs_nvcc
def test_an_unlinked_cuda_source_is_built_with_the_program_s_flags_and_inspected(tmp_path):
    record = build(load_project(DEVICE), cxx=NVCC_HOST, output=tmp_path, timeout=300)
    assert record["status"] == "native-built", record.get("stderr", "")[-3000:]
    by_path = {f["path"]: f for f in record["foreign"]}
    benchmark, tiled = by_path["vendor/parallel_gpu.cu"], by_path["vendor/stencil.cu"]
    assert benchmark["status"] == "native-built" and not benchmark["linked"] and tiled["linked"]
    assert "-arch=sm_120" in benchmark["command"] and "--fmad=false" in benchmark["command"]
    assert "all-warnings" in benchmark["command"]  # warnings are errors for vendored code too
    kernels = inspect(load_project(DEVICE), parse("sm_120"), Path(record["directory"]))["sources"]
    assert len(kernels["vendor/parallel_gpu.cu"]["kernels"]) >= 3  # saxpy's lanes, the reductions, the compaction
    assert json.dumps(kernels)  # every figure is plain data


@needs_nvcc
def test_the_device_implementation_runs_against_its_reference_on_the_gpu(tmp_path):
    """Only `make gpu` runs this: validation's device tests transfer the inputs, launch the kernel and compare."""
    with on_device():
        project = load_project(DEVICE)
        reference = next(f for f in compile_program(project.source)[0].functions if f.name == "stencil_1d")
        cases = boundaries.generate(reference, {}, {"largest_extent": 256}, budget=12, device=True)
        tests, names, _ = cases_as_tests(project.source, "stencil_1d", "stencil_tiled", cases)
        done = run_tests(replace(project, source=project.source + "\n" + tests), cxx=NVCC_HOST, chosen="device_",
                         output=tmp_path)  # fmt: skip
        assert done["status"] == "passed-test-blocks" and done["passed"] == len(names), json.dumps(done)[-3000:]


@pytest.mark.parametrize(("name", "why"), [("histogram_u32", "no implementation"), ("main", "no implementation")])
def test_only_an_implementation_is_reported(name, why):
    with pytest.raises(Diagnostic, match=why) as refusal:
        report(load_project(HOST), name)
    assert refusal.value.data["code"] == "E-FOREIGN"


def test_an_implementation_that_calls_nothing_vendored_is_not_foreign(tmp_path):
    root = copied(tmp_path, HOST)
    source = root / "src/histogram.cairn"
    source.write_text(source.read_text().replace("unsafe { histogram_cpp(n, out, x); }",
                                                 "each b in 256 { out[b] = 0; }\n  each i in n { let b = usize(x[i] & 255); out[b] = add_wrap(out[b], 1); }"))  # fmt: skip
    assert code_of(lambda: report(load_project(root), "histogram_interleaved")) == "E-FOREIGN"

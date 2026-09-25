"""`cairn export`: the directory a build compiles, and validation and measurement that run against it.

End to end on the host: export a project, build and run the export, run its test blocks and its timing harness, and
see every record carry the export's identity. Then change a byte of it, add a file, remove one, or edit the recorded
command, and see each refused with E-EXPORT-TAMPERED before anything is built. A device export compiles for sm_120
here and is never run.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic
from cairn.projects import export as exported
from cairn.projects.project import digest, load_project
from emitted import NVCC_HOST, code_of

NVCC = pytest.mark.skipif(not shutil.which("nvcc"), reason="needs nvcc")

SOURCE = """
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum += xs[i]; }
  return sum;
}

fn main() -> i32 {
  buffer xs:u64[100] = zeroed;
  for i in 0..100 { xs[i] = u64(i); }
  println("total = ", total(xs));
  return 0;
}

test sums { buffer xs:u64[4] = zeroed; xs[3] = 7; assert_eq(total(xs), 7); }
test ones { buffer xs:u64[3] = zeroed; for i in 0..3 { xs[i] = 1; } assert_eq(total(xs), 3); }
"""

DEVICE = """
fn scale(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { y[i] = a * x[i]; } }

fn main() -> i32 {
  buffer x:f32[256]@device = zeroed;
  buffer y:f32[256]@device = zeroed;
  scale(256, y, x, 2.0);
  return 0;
}
"""


SELECTED = (
    DEVICE
    + """
fn scale_tc(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale needs(tcgen05) {
  parallel i in n { y[i] = a * x[i]; }
}
plan scale use scale_tc;
"""
)


def project(tmp_path: Path, source: str = SOURCE, name: str = "summed") -> Path:
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / "src/main.cairn").write_text(source)
    (root / "cairn.toml").write_text(
        f'[project]\nname = "{name}"\nsources = ["src/main.cairn"]\n\n[build]\nkind = "exe"\n'
    )
    return root


@pytest.fixture(scope="module")
def made(tmp_path_factory) -> Path:
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")
    base = tmp_path_factory.mktemp("export")
    exported.export(load_project(project(base)), base / "out", cxx="g++")
    return base / "out"


def test_an_export_holds_the_program_exactly_the_headers_it_includes_and_its_record(made):
    record = json.loads((made / exported.RECORD).read_text())
    program = (made / "program.cpp").read_text()
    headers = exported.closure(program)
    assert set(record["files"]) == {"program.cpp", *headers} and set(headers) < set(RUNTIME_FILES)
    assert "cairn_gpu.hpp" not in headers and "cairn_runtime.hpp" in headers  # a host program: no device runtime
    assert set(record["roles"].values()) == {"program", "runtime"}
    assert record["command"][0] == shutil.which("g++") and record["command"][-3:] == ["program.cpp", "-o", "summed"]
    assert record["compilers"]["cxx"]["version"].startswith("g++") and record["device_target"] is None
    assert record["identity"] == exported.identity(record) and set(record["functions"]) >= {"total", "main"}
    assert {n: digest(made / n) for n in record["files"]} == record["files"]


def test_a_build_and_a_run_of_the_export_carry_its_identity(made, tmp_path):
    identity = json.loads((made / exported.RECORD).read_text())["identity"]
    built = exported.build(made, tmp_path / "builds")
    assert built["status"] == "native-built" and built["export"] == identity and len(built["artifact_sha256"]) == 64
    assert json.loads((Path(built["directory"]) / "build.json").read_text())["export"] == identity
    ran = exported.run(made, output=tmp_path / "runs")
    assert ran["exit_code"] == 0 and ran["stdout"] == "total = 4950\n" and ran["export"] == identity


def test_the_test_blocks_of_an_export_run_from_it(tmp_path):
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")
    exported.export(load_project(project(tmp_path)), tmp_path / "tests", cxx="g++", tests=True)
    tested = exported.test(tmp_path / "tests")
    assert tested["status"] == "passed-test-blocks" and [t["name"] for t in tested["tests"]] == ["sums", "ones"]
    wrong = SOURCE.replace("assert_eq(total(xs), 3)", "assert_eq(total(xs), 4)")
    exported.export(load_project(project(tmp_path, wrong, "wrong")), tmp_path / "failing", cxx="g++", tests=True)
    failed = exported.test(tmp_path / "failing")
    assert failed["status"] == "test-blocks-failed" and failed["failed"] == 1
    assert "assertion failed" in next(t["reason"] for t in failed["tests"] if t["name"] == "ones")


def test_the_timing_harness_is_part_of_the_export(tmp_path):
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")
    exported.export(load_project(project(tmp_path)), tmp_path / "timed", cxx="g++", timed=("total", {"n": 4096}))
    record = json.loads((tmp_path / "timed" / exported.RECORD).read_text())
    assert record["harness"] == {"symbol": "total", "sizes": {"n": 4096}, "timer": "host", "blocks": 9}
    assert "median_ns" in (tmp_path / "timed/program.cpp").read_text()  # the driver is in the hashed program
    ran = exported.run(tmp_path / "timed")
    assert ran["measured"]["status"] == "measured" and ran["measured"]["median_ns"] > 0
    assert ran["export"] == record["identity"] and "same harness" in ran["measured"]["note"]


@pytest.mark.parametrize("change", ["byte", "added", "removed", "command", "record"])
def test_a_changed_export_is_refused_before_anything_is_built(made, tmp_path, change):
    copy = tmp_path / "copy"
    shutil.copytree(made, copy, ignore=shutil.ignore_patterns(exported.BUILT))
    record = json.loads((copy / exported.RECORD).read_text())
    if change == "byte":
        (copy / "program.cpp").write_text((copy / "program.cpp").read_text().replace("4950", "4951") + " ")
    elif change == "added":
        (copy / "cairn_extra.hpp").write_text("#pragma once\n")
    elif change == "removed":
        (copy / "cairn_runtime.hpp").unlink()
    elif change == "command":  # the file list still matches; the identity no longer does
        record["command"].insert(1, "-DCAIRN_ALTERED=1")
        (copy / exported.RECORD).write_text(json.dumps(record))
    else:
        (copy / exported.RECORD).write_text("{not json")
    assert code_of(lambda: exported.check(copy)) == "E-EXPORT-TAMPERED"
    assert code_of(lambda: exported.build(copy, tmp_path / "builds")) == "E-EXPORT-TAMPERED"
    assert not (tmp_path / "builds").exists()


def test_a_build_with_another_compiler_is_refused(made, monkeypatch, tmp_path):
    monkeypatch.setattr(exported, "compiler_version", lambda path: "g++ (some other build) 99.0\n")
    assert code_of(lambda: exported.build(made, tmp_path / "builds")) == "E-EXPORT-TOOLCHAIN"


def test_two_exports_are_compared_as_code_and_never_as_speed(tmp_path):
    if not shutil.which("g++") or not shutil.which("clang++"):
        pytest.skip("needs g++ and clang++")
    exported.export(load_project(project(tmp_path)), tmp_path / "a", cxx="g++")
    exported.export(load_project(project(tmp_path, SOURCE, "again")), tmp_path / "b", cxx="g++")
    same = exported.compare(tmp_path / "a", tmp_path / "b")
    assert same["verdict"] == "differs" and same["built_alike"]["command"] is False  # another name, another artifact
    assert all(v == "identical-code" for v in same["functions"].values())
    changed = SOURCE.replace("sum += xs[i];", "sum = sum + xs[i] * 1;")
    exported.export(load_project(project(tmp_path, changed, "changed")), tmp_path / "c", cxx="g++")
    other = exported.compare(tmp_path / "a", tmp_path / "c")
    assert other["functions"]["total"] == "changed" and other["functions"]["main"] == "identical-code"
    exported.export(load_project(project(tmp_path, SOURCE, "summed2")), tmp_path / "d", cxx="clang++")
    assert exported.compare(tmp_path / "a", tmp_path / "d")["built_alike"]["compilers"] is False
    assert "same harness" in same["timing"]
    twin = tmp_path / "twin"
    shutil.copytree(tmp_path / "a", twin)
    assert exported.compare(tmp_path / "a", twin)["verdict"] == "same-code"


def test_the_command_line_exports_checks_builds_and_refuses(tmp_path, capsys):
    if not shutil.which("g++"):
        pytest.skip("g++ unavailable")
    root, out = project(tmp_path), tmp_path / "cli"
    assert main(["export", str(root), "--out", str(out), "--cxx", "g++", "--format", "json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(["export", str(out), "--format", "json"]) == 0
    intact = {"status": "export-intact", "identity": first["identity"], "files": len(first["files"])}
    assert json.loads(capsys.readouterr().out) == intact
    identity = first["identity"]
    assert main(["run", str(out), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["export"] == identity
    (out / "program.cpp").write_text((out / "program.cpp").read_text() + "\n")
    assert main(["build", str(out), "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "E-EXPORT-TAMPERED"
    assert main(["export", str(root), "--out", str(out), "--format", "json"]) == 2  # never over another export
    capsys.readouterr()


def test_an_export_judges_its_device_target_as_a_build_does(tmp_path):
    """A plan that selects an implementation sm_120 cannot run is E-IMPL-TARGET naming the plan, as `cairn build`
    refuses it (tests/language/test_implementations.py), before anything is written."""
    root = project(tmp_path, SELECTED, "selected")
    with pytest.raises(Diagnostic) as refused:
        exported.export(load_project(root), tmp_path / "out", device_target="sm_120")
    assert refused.value.data["code"] == "E-IMPL-TARGET" and "plan scale use scale_tc;" in refused.value.data["message"]
    assert not (tmp_path / "out").exists()


@NVCC
def test_a_device_export_separates_the_kernels_from_the_launch_wrappers_and_never_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("CAIRN_GPU_TESTS", raising=False)  # never run, under `make gpu` too: its run holds no lock
    root = project(tmp_path, DEVICE, "scaled")
    exported.export(load_project(root), tmp_path / "dev", cxx=NVCC_HOST, device_target="sm_120")
    record = json.loads((tmp_path / "dev" / exported.RECORD).read_text())
    assert (
        "program.cu" in record["files"] and record["device_target"] == "sm_120" and "-arch=sm_120" in record["command"]
    )
    roles = record["roles"]
    assert roles["cairn_kernels.hpp"] == "device" and roles["cairn_runtime.hpp"] == "device"
    assert {roles[n] for n in ("cairn_gpu.hpp", "cairn_exec.hpp", "cairn_reuse.hpp")} == {"launch"}
    assert record["compilers"]["nvcc"]["release"]
    built = exported.build(tmp_path / "dev", tmp_path / "builds", timeout=600)
    assert built["status"] == "native-built", built.get("stderr", "")[-3000:]
    ran = exported.run(tmp_path / "dev", output=tmp_path / "runs")
    assert ran["status"] == "not-run" and "make" in ran["reason"] and not (tmp_path / "runs").exists()


@NVCC
def test_the_kernels_header_stands_alone_on_a_stream_of_the_caller(tmp_path):
    """cairn_kernels.hpp, without the launch wrappers, launches a lane body on a stream the application made:
    compiled for sm_120 and never run."""
    (tmp_path / "cairn_kernels.hpp").write_text(RUNTIME_FILES["cairn_kernels.hpp"])
    (tmp_path / "cairn_runtime.hpp").write_text(RUNTIME_FILES["cairn_runtime.hpp"])
    (tmp_path / "app.cu").write_text(
        '#include "cairn_kernels.hpp"\n'
        "void scale(float* y, const float* x, std::size_t n, cudaStream_t mine) {\n"
        "  cr::gpu::fire(n, [=] CR_DEVICE(std::size_t i) { y[i] = 2.0f * x[i]; }, mine);\n"
        "}\n"
    )
    line = ["nvcc", "-std=c++20", "--extended-lambda", "--expt-relaxed-constexpr", "-arch=sm_120", "-c",
            str(tmp_path / "app.cu"), "-o", str(tmp_path / "app.o")]  # fmt: skip
    done = subprocess.run(line, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stderr[-3000:]
    assert '#include "cairn_reuse.hpp"' not in RUNTIME_FILES["cairn_kernels.hpp"]

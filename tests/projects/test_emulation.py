"""Device programs built for the host with `--emulate` (projects/emulation.py, runtime/cairn_emulate.hpp): judged
against a device target, their device work run on host threads.

Every device example runs emulated under clang++ and g++ and is held to its host counterpart or its reference loops,
which the language says it agrees with bit for bit. The address and leak sanitizers watch emulated device memory, and
the thread sanitizer emulated cooperative regions and queued tickets; with a guard or a barrier taken out of the
emitted C++ they report the bug, so each is shown to bite. What the host cannot run as the device would is refused
with E-EMULATE, what the target refuses is refused as it is for a device build, and the C++ is the device build's,
byte for byte. Nothing here runs on a GPU.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects import emulation, target
from cairn.projects.build import build, emitted
from cairn.projects.project import load_project
from cairn.verify.runner import run_tests
from emitted import code_of, contract, watched

ROOT = Path(__file__).resolve().parents[2]
# Each device example, and what it prints once it has held its device results to the host's own loops.
EXAMPLES = {
    "examples/cooperative/gpu.toml": "transpose, block sums and row sums agree with plain loops",
    "examples/apps/gpu_pipeline": "device result matches the host",
    "examples/apps/matmul/gpu.toml": "0 outputs outside the contract",
    "examples/apps/analytics/gpu.toml": "the device agrees with the host bit for bit",
    "examples/apps/simulator": "all three back ends agree bit for bit",
    "demos/numeric/gpu.toml": "device bits equal host bits: true",
}


def emulated(tmp_path: Path, path: Path | str, cxx: str = "clang++", **options) -> dict:
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    options = {"output": tmp_path / "build", "device_target": "sm_120", "timeout": 300, **options}
    record = build(load_project(ROOT / path if isinstance(path, str) else path), cxx=cxx, emulate=True, **options)
    assert record["status"] == "native-built", record.get("stderr", "")[-4000:]
    return record


def program(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "program.cairn"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("path", EXAMPLES)
def test_each_device_example_runs_emulated_and_agrees_with_the_host(tmp_path, path, cxx):
    record = emulated(tmp_path, path, cxx, kind="exe")
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    assert EXAMPLES[path] in done.stdout, done.stdout[-2000:]


@pytest.mark.parametrize("path", EXAMPLES)
def test_the_emulated_build_compiles_the_device_program_byte_for_byte(tmp_path, path):
    """The same C++ the device build hands nvcc, built by the host compiler with CAIRN_EMULATE, for the target the
    record names: no nvcc runs, and nothing in the emitted program depends on the emulation."""
    project = load_project(ROOT / path)
    record = emulated(tmp_path, path, kind="exe")
    device = emitted(project, kind="exe").generated
    assert (Path(record["directory"]) / "program.cpp").read_text() == device
    assert "-DCAIRN_EMULATE=1" in record["command"] and not any("nvcc" in part for part in record["command"])
    assert record["device_target"]["name"] == "sm_120" and record["emulation"]["judged_against"] == "sm_120"
    assert "never a device run" in record["emulation"]["claim"]
    receipt = json.loads((Path(record["directory"]) / "receipt.json").read_text())
    assert receipt["emulation"] == record["emulation"]


def test_the_device_command_is_unchanged_when_emulation_is_off():
    from cairn.projects.toolchain import command

    if not shutil.which("nvcc") or not shutil.which("g++"):
        pytest.skip("needs nvcc and g++")
    device = command("g++", "p.cpp", "p", kind="exe", cuda=True, device=target.parse("sm_120"))
    assert Path(device[0]).name == "nvcc" and "-arch=sm_120" in device and "-DCAIRN_EMULATE=1" not in device
    host = command("g++", "p.cpp", "p", kind="exe", cuda=True, device=target.parse("sm_120"), emulate=True)
    assert Path(host[0]).name == "g++" and "nvcc" not in " ".join(host)
    assert host[-6:] == ["-DCAIRN_EMULATE=1", "-include", "cairn_emulate.hpp", "p.cpp", "-o", "p"]


def test_cairn_run_says_the_device_work_was_emulated(tmp_path, capsys):
    path = ROOT / "examples/cooperative/gpu.toml"
    code = main(
        ["run", str(path), "--emulate", "--device-target", "sm_120", "--out", str(tmp_path), "--format", "json"]
    )
    record = json.loads(capsys.readouterr().out)
    assert code == 0 and record["exit_code"] == 0, record
    assert record["emulation"]["judged_against"] == "sm_120" and record["emulation"]["runs_on"] == "host"
    assert record["memory_limit_mib"] == 1024  # device memory is host memory, under the host's limit
    main(["run", str(path), "--emulate", "--device-target", "sm_120", "--out", str(tmp_path), "--format", "human"])
    assert "device work emulated on host threads for sm_120" in capsys.readouterr().err
    main(["build", str(path), "--emulate", "--device-target", "sm_120", "--out", str(tmp_path), "--format", "human"])
    assert "never a device run" in capsys.readouterr().out


SHIFT = """
fn shift(n:usize, out:rw<u32>[n]@device, x:ro<u32>[n]@device) { parallel i in n { out[i] = x[i + 1]; } }
fn main() -> i32 {
  let n:usize = 60;
  buffer x:u32[n]@device = zeroed;
  buffer out:u32[n]@device = zeroed;
  shift(n, out, x);
  return 0;
}
"""
GUARD = "cr::at(v_x, (v_i + static_cast<std::size_t>(1ULL)), v_n)"


def test_a_lane_that_reads_past_its_array_stops_at_the_guard(tmp_path):
    record = emulated(tmp_path, program(tmp_path, SHIFT), kind="exe")
    done = subprocess.run([record["artifact"]], capture_output=True, text=True, timeout=120)
    assert done.returncode == -6, (done.returncode, done.stderr[-2000:])  # SIGABRT, from the lane's guard


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_address_and_leak_sanitizers_watch_emulated_device_memory(tmp_path, cxx):
    """Clean on the pipeline and on the cooperative example; with the lane's guard taken out of the emitted C++, the
    read one past the device array is a heap overflow the sanitizer reports."""
    pipeline = compile_source(load_project(ROOT / "examples/apps/gpu_pipeline").source, roots=("main",))[0]
    for part in ("pipeline", "shift"):
        (tmp_path / part).mkdir()
    done = watched(tmp_path / "pipeline", pipeline, cxx, "address,undefined", emulate=True)
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, done.stderr[-3000:]
    cpp = compile_source(SHIFT)[0]
    assert GUARD in cpp
    bare = cpp.replace(GUARD, "v_x[v_i + 1]")
    done = watched(tmp_path / "shift", bare, cxx, "address", emulate=True)
    assert done.returncode != 0 and "heap-buffer-overflow" in done.stderr, done.stderr[-3000:]


def device_main(path: str) -> str:
    return compile_source(load_project(ROOT / path).source, roots=("main",))[0]


def test_the_thread_sanitizer_watches_emulated_cooperative_regions(tmp_path):
    """Each block's threads are host threads: clean as written, and a data race once the emitted C++ loses a
    barrier the checker required."""
    cpp = device_main("examples/cooperative/gpu.toml")
    for part in ("clean", "racy"):
        (tmp_path / part).mkdir()
    done = watched(tmp_path / "clean", cpp, "clang++", "thread", emulate=True)
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stderr[-3000:]
    racy = watched(tmp_path / "racy", cpp.replace("cr_blk.sync();", "", 1), "clang++", "thread", emulate=True)
    assert racy.returncode != 0 and "ThreadSanitizer: data race" in racy.stderr, racy.stderr[-3000:]


QUEUED = """
fn work(n:usize, x:rw<u32>[n]@device, round:u32) -> u64 {
  parallel i in n { x[i] = u32(i % 1000) + round; }
  let total = reduce + parallel i in n yield u64(x[i]);
  return total;
}

fn fan(n:usize, a:rw<u32>[n]@device, b:rw<u32>[n]@device, round:u32) -> u64 {
  let ta = spawn work(n, a, round);
  let tb = spawn work(n, b, round + 1);
  let one = wait(ta);
  let two = wait(tb);
  return one + two;
}

fn shuttle(n:usize, host:rw<f32>[n], x:rw<f32>[n]@device, y:rw<f32>[n]@device) {
  let up = spawn transfer(x, host);
  let work = spawn parallel i in n after up { y[i] = x[i] + 1.0; };
  wait(up);
  wait(work);
  let back = spawn transfer(host, y);
  wait(back);
}

fn main() -> i32 {
  let n:usize = 20011;
  buffer a:u32[n]@device = zeroed;
  buffer b:u32[n]@device = zeroed;
  buffer host:f32[n] = zeroed;
  buffer x:f32[n]@device = zeroed;
  buffer y:f32[n]@device = zeroed;
  for k in 0..8 {
    let round = u32(k);
    let mut want:u64 = 0;
    for i in 0..n { want += 2 * (u64(i % 1000) + u64(round)) + 1; }
    let got = fan(n, a, b, round);
    if got != want { return 1; }
    for i in 0..n { host[i] = f32(i % 97) * 0.25; }
    shuttle(n, host, x, y);
    for i in 0..n { if host[i] != f32(i % 97) * 0.25 + 1.0 { return 2; } }
  }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_queued_tickets_keep_their_order_and_race_nowhere_emulated(tmp_path, cxx):
    """Two tasks run device work at once, each on its own thread's context, and a queued transfer and region run in
    the order `after` gives them: right under both compilers, and silent under the thread sanitizer."""
    cpp = compile_source(QUEUED)[0]
    for part in ("plain", "thread"):
        (tmp_path / part).mkdir()
    done = contract(tmp_path / "plain", cpp, cxx, emulate=True)
    assert done.returncode == 0, done.stderr[-2000:]
    if cxx == "clang++":
        watched_run = watched(tmp_path / "thread", cpp, cxx, "thread", emulate=True)
        assert watched_run.returncode == 0 and "ThreadSanitizer" not in watched_run.stderr, watched_run.stderr[-3000:]


PLANNED = """
fn scale(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { y[i] = 2.0 * x[i]; } }
plan scale { vector 4; }

fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] + x[i + 1]; } else { out[i] = x[i]; }
  }
}
plan blur { stage 1; block 64; }

fn collect(n:usize, x:rw<u32>[n]@device, sums:rw<u32>[n]@device, kept:rw<u32>[n]@device) -> u64 {
  parallel i in n { x[i] = u32((u64(i) * 2654435761) % 1000); }
  let total = reduce + parallel i in n yield u64(x[i]);
  let last = scan + exclusive sums parallel i in n yield x[i] % 16;
  let used = compact kept for i in n where x[i] % 3 == 0 yield x[i];
  return total + u64(last) * 1000000 + u64(used) * 1000000000000;
}

fn main() -> i32 {
  let n:usize = 20011;
  buffer h:f32[n] = zeroed;
  for i in 0..n { h[i] = f32(i % 97) * 0.25; }
  buffer x:f32[n]@device = zeroed;
  buffer y:f32[n]@device = zeroed;
  buffer z:f32[n]@device = zeroed;
  transfer(x, h);
  scale(n, y, x);
  blur(n, z, y);
  buffer got:f32[n] = zeroed;
  transfer(got, z);
  for i in 0..n {
    let mut want = 2.0 * h[i];
    if i >= 1 && i + 1 < n { want = 2.0 * h[i - 1] + 2.0 * h[i] + 2.0 * h[i + 1]; }
    if to_bits(got[i]) != to_bits(want) { return 1; }
  }
  buffer dx:u32[n]@device = zeroed;
  buffer sums:u32[n]@device = zeroed;
  buffer kept:u32[n]@device = zeroed;
  let answer = collect(n, dx, sums, kept);
  let mut total:u64 = 0;
  let mut run:u32 = 0;
  let mut used:u64 = 0;
  buffer hs:u32[n] = zeroed;
  transfer(hs, sums);
  for i in 0..n {
    let v = u32((u64(i) * 2654435761) % 1000);
    total += u64(v);
    if hs[i] != run { return 2; }
    run += v % 16;
    if v % 3 == 0 { used += 1; }
  }
  if answer != total + u64(run) * 1000000 + used * 1000000000000 { return 3; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_vector_and_staged_plans_and_the_collectors_run_emulated_as_the_host_loops_do(tmp_path, cxx):
    """A `vector 4` region and a `stage 1` region, whose chunks and tiles the emulation runs block by block, and a
    device reduction, scan and compaction, each held to the host's own loop, clean under the address sanitizer."""
    cpp = compile_source(PLANNED)[0]
    assert "cr::gpu::run_vector<" in cpp and "cr::gpu::run_staged<" in cpp
    done = watched(tmp_path, cpp, cxx, "address,undefined", emulate=True)
    assert done.returncode == 0 and "Sanitizer" not in done.stderr, (done.returncode, done.stderr[-3000:])


TESTED = """
fn double(n:usize, x:rw<u32>[n]@device) { parallel i in n { x[i] = x[i] * 2; } }

test doubles {
  let n:usize = 1000;
  buffer h:u32[n] = zeroed;
  for i in 0..n { h[i] = u32(i); }
  buffer d:u32[n]@device = zeroed;
  transfer(d, h);
  double(n, d);
  transfer(h, d);
  for i in 0..n { assert(h[i] == u32(2 * i), "each element doubled"); }
}

test off_by_one {
  let n:usize = 8;
  buffer d:u32[n]@device = zeroed;
  double(n, d);
  buffer h:u32[n] = zeroed;
  transfer(h, d);
  assert(h[0] == 1, "zero doubled is not one");
}
"""


def test_cairn_test_runs_device_test_blocks_emulated(tmp_path):
    record = run_tests(load_project(program(tmp_path, TESTED)), output=tmp_path / "build", device_target="sm_120",
                       emulate=True)  # fmt: skip
    assert record["emulation"]["judged_against"] == "sm_120"
    outcome = {t["name"]: t["status"] for t in record["tests"]}
    assert outcome == {"doubles": "passed", "off_by_one": "failed"}, record["tests"]
    assert "zero doubled is not one" in next(t for t in record["tests"] if t["name"] == "off_by_one")["reason"]


PTX = """fn brev(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {
  parallel i in n { unsafe { asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, xs[i]); out[i] = r; } }
}
"""
LAUNCHED = """extern "blend" fn blend(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) launch(n, 256) effects();
fn go(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) { unsafe { blend(n, out, x); } }
"""
NEEDS = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { out[i] = a * x[i]; } }
fn scale_bulk(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale needs(tma) {
  parallel i in n { out[i] = a * x[i]; }
}
plan scale use scale_bulk;
"""


def test_what_the_host_cannot_run_as_the_device_would_is_refused_by_name(tmp_path):
    def refused(path: Path, device: str = "sm_120") -> dict:
        with pytest.raises(Diagnostic) as error:
            build(load_project(path), output=tmp_path / "build", kind="library", device_target=device, emulate=True)
        return error.value.data

    ptx = refused(program(tmp_path, PTX))
    assert ptx["code"] == "E-EMULATE" and "typed PTX" in ptx["message"] and ptx["line"] == 2
    launched = refused(program(tmp_path, LAUNCHED))
    assert launched["code"] == "E-EMULATE" and "launch(...)" in launched["message"] and launched["symbol"] == "blend"
    foreign = refused(ROOT / "examples/foreign/device")
    assert foreign["code"] == "E-EMULATE", foreign
    needs = refused(program(tmp_path, NEEDS), "sm_90")
    assert needs["code"] == "E-EMULATE" and needs["feature"] == "tma", needs
    with pytest.raises(Diagnostic) as error:  # vendored CUDA alone, whatever the program calls
        emulation.check("fn f() {}", {"functions": {}, "device_features": []}, [("vendor/k.cu", ())])
    assert error.value.data["code"] == "E-EMULATE" and error.value.data["source"] == "vendor/k.cu"


def test_emulating_a_target_refuses_what_that_target_refuses(tmp_path, monkeypatch):
    tile32 = ROOT / "examples/tensor/tile32.cairn"
    code = code_of(lambda: build(load_project(tile32), output=tmp_path, kind="library", device_target="sm_75",
                                 emulate=True))  # fmt: skip
    assert code == "E-TARGET-FEATURE"  # mma.sync starts at sm_80, emulated or not
    needs = program(tmp_path, NEEDS.replace("needs(tma)", "needs(tcgen05)"))  # sm_120 has no tcgen05
    assert code_of(lambda: build(load_project(needs), output=tmp_path, kind="library", device_target="sm_120",
                                 emulate=True)) == "E-IMPL-TARGET"  # fmt: skip
    ptx = program(tmp_path, PTX.replace("sm_75", "sm_90a"))
    assert code_of(lambda: build(load_project(ptx), output=tmp_path, kind="library", device_target="sm_120",
                                 emulate=True)) == "E-ASM-TARGET"  # fmt: skip
    monkeypatch.setattr(target, "detect", lambda: None)  # no GPU here, and none named
    assert code_of(lambda: build(load_project(tile32), output=tmp_path, kind="library", emulate=True)) == "E-TARGET"


def test_a_host_program_builds_as_it_always_does_under_emulate(tmp_path):
    record = build(load_project(ROOT / "examples/hello"), output=tmp_path, emulate=True)
    assert record["status"] == "native-built" and "emulation" not in record and "device_target" not in record
    assert "-DCAIRN_EMULATE=1" not in record["command"]


def test_an_export_is_not_emulated(tmp_path, capsys):
    (tmp_path / "export.json").write_text("{}")
    assert main(["run", str(tmp_path), "--emulate", "--format", "json"]) == 2
    assert "--emulate builds a project" in json.loads(capsys.readouterr().out)["message"]


def test_the_runtime_switch_leaves_the_cuda_header_whole_and_device_exports_as_they_were():
    """cairn_gpu.hpp names no emulation header: an emulated build reads it before the program, so a device export
    still holds exactly the headers nvcc reads."""
    from cairn.compiler.cairnc import RUNTIME_FILES
    from cairn.projects.export import closure

    gpu = RUNTIME_FILES["cairn_gpu.hpp"]
    assert gpu.index("#if !defined(CAIRN_EMULATE)") < gpu.index("#include <cuda_runtime.h>")
    assert gpu.rstrip().endswith("#endif") and '#include "cairn_emulate.hpp"' not in gpu
    assert "cudaMalloc" not in RUNTIME_FILES["cairn_emulate.hpp"]
    assert "cairn_emulate.hpp" not in closure(compile_source(SHIFT)[0])

"""Device work on the calling thread's execution context, run on the host.

The generated program is compiled against tests/runtime/gpu_host.hpp, a host machine for runtime/cairn_exec.hpp that
counts every stream, event, allocation, launch, copy and wait, so the suite runs it natively under both compilers and
the sanitizers. A pipeline of regions, a vector and a staged region, a reduction, a scan, a compaction, transfers and
queued work, run again and again, makes its streams and grows its scratch on the first pass and on no later one, and
waits only for its own stream. Its results are checked against a plain C++ computation of the same pipeline. The CUDA
machine (runtime/cairn_gpu.hpp) compiles for sm_120 here and runs only under `make gpu`.
"""

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.compiler.header import header
from cairn.projects.build import build
from cairn.projects.project import load_project
from emitted import contract, device_build, on_device, sanitized

ROOT = Path(__file__).resolve().parents[2]
RUNTIME, HOST = ROOT / "src/cairn/runtime", ROOT / "tests/runtime"

PIPELINE = """
fn pass(n:usize, x:rw<u32>[n]@device, sums:rw<u32>[n]@device, kept:rw<u32>[n]@device, round:u32) -> u64 {
  parallel i in n { x[i] = u32((u64(i) * 2654435761 + u64(round)) % 1000); }
  let total = reduce + parallel i in n yield u64(x[i]);
  let last = scan + exclusive sums parallel i in n yield x[i] % 16;
  let used = compact kept for i in n where x[i] % 3 == 0 yield x[i];
  return total + u64(last) * 1000000 + u64(used) * 1000000000000;
}

fn scale(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device) { parallel i in n { y[i] = 2.0 * x[i]; } }
plan scale { vector 4; }

fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] + x[i + 1]; } else { out[i] = x[i]; }
  }
}
plan blur { stage 1; block 64; }

fn shuttle(n:usize, host:rw<f32>[n], x:rw<f32>[n]@device, y:rw<f32>[n]@device, out:rw<f32>[n]@device) {
  let up = spawn transfer(x, host);
  let work = spawn parallel i in n after up { y[i] = x[i] + 1.0; };
  wait(up);
  wait(work);
  scale(n, out, y);
  blur(n, y, out);
  transfer(host, y);
}
"""

HARNESS = r"""
#include <cstdio>
#include <cstdint>
#include <vector>
extern "C" std::uint64_t cf_pass(std::size_t, std::uint32_t*, std::uint32_t*, std::uint32_t*, std::uint32_t) noexcept;
extern "C" void cf_shuttle(std::size_t, float*, float*, float*, float*) noexcept;

// The pipeline as plain C++: the oracle the device results are held to.
static std::uint64_t expected(std::size_t n, std::uint32_t round, std::vector<std::uint32_t>& x,
                              std::vector<std::uint32_t>& sums, std::vector<std::uint32_t>& kept) {
  std::uint64_t total = 0;
  for(std::size_t i = 0; i < n; ++i) x[i] = std::uint32_t((std::uint64_t(i) * 2654435761u + round) % 1000), total += x[i];
  std::uint32_t run = 0;
  for(std::size_t i = 0; i < n; ++i) sums[i] = run, run += x[i] % 16;
  std::size_t used = 0;
  for(std::size_t i = 0; i < n; ++i) if(x[i] % 3 == 0) kept[used++] = x[i];
  return total + std::uint64_t(run) * 1000000 + std::uint64_t(used) * 1000000000000ull;
}
static void shuttled(std::size_t n, std::vector<float>& host) {
  std::vector<float> y(n), out(n);
  for(std::size_t i = 0; i < n; ++i) y[i] = host[i] + 1.0f;
  for(std::size_t i = 0; i < n; ++i) out[i] = 2.0f * y[i];
  for(std::size_t i = 0; i < n; ++i) host[i] = (i >= 1 && i + 1 < n) ? out[i - 1] + out[i] + out[i + 1] : out[i];
}

static void row(const char* what, int pass) {
  const auto& c = cr::gpu::counted;
  std::printf("{\"what\": \"%s\", \"pass\": %d, \"streams\": %zu, \"events\": %zu, \"allocations\": %zu, "
              "\"scratch_allocations\": %zu, \"frees\": %zu, \"launches\": %zu, \"copies\": %zu, "
              "\"library_calls\": %zu, \"stream_waits\": %zu, \"event_waits\": %zu}\n", what, pass,
              c.streams.load(), c.events.load(), c.allocations.load(), c.scratch_allocations.load(), c.frees.load(),
              c.launches.load(), c.copies.load(), c.library_calls.load(), c.stream_waits.load(), c.event_waits.load());
}

int main() {
  const std::size_t n = 20011;
  int wrong = 0;
  {
    cr::gpu::Buffer<std::uint32_t> x(n), sums(n), kept(n);
    cr::gpu::Buffer<float> dx(n), dy(n), dout(n);
    std::vector<std::uint32_t> hx(n), hsums(n), hkept(n), back(n);
    std::vector<float> host(n), want(n);
    for(std::size_t i = 0; i < n; ++i) host[i] = want[i] = float(i % 97) * 0.25f;
    row("owners", 0);
    for(int k = 1; k <= PASSES; ++k) {
      const std::uint64_t got = cf_pass(n, x.data(), sums.data(), kept.data(), std::uint32_t(k));
      const std::uint64_t right = expected(n, std::uint32_t(k), hx, hsums, hkept);
      wrong += got != right;
      const std::size_t used = std::size_t(right / 1000000000000ull);
      for(std::size_t i = 0; i < n; ++i) wrong += x.data()[i] != hx[i] || sums.data()[i] != hsums[i];
      for(std::size_t i = 0; i < used; ++i) wrong += kept.data()[i] != hkept[i];
      cf_shuttle(n, host.data(), dx.data(), dy.data(), dout.data());
      shuttled(n, want);
      for(std::size_t i = 0; i < n; ++i) wrong += host[i] != want[i];
      row("pass", k);
    }
    // A stream the caller owns: synchronous work runs on it, and the context makes none for it.
    cr::gpu::HostStream mine{0};
    cr::gpu::use_stream(&mine);
    const std::size_t made = cr::gpu::counted.streams.load();
    wrong += cf_pass(n, x.data(), sums.data(), kept.data(), 1) != expected(n, 1, hx, hsums, hkept);
    wrong += cr::gpu::counted.last_stream.load() != &mine || cr::gpu::counted.streams.load() != made;
    cr::gpu::use_stream(nullptr);
    wrong += cf_pass(n, x.data(), sums.data(), kept.data(), 2) != expected(n, 2, hx, hsums, hkept);
    wrong += cr::gpu::counted.last_stream.load() == &mine;
    row("bound", PASSES + 1);
  }
  std::printf("{\"wrong\": %d}\n", wrong);
  return wrong ? 1 : 0;
}
"""


def host_build(tmp_path: Path, cxx: str, *flags: str, passes: int = 20) -> Path:
    """The pipeline's generated C++ against the host machine, with the harness; the executable."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    cpp = compile_source(PIPELINE)[0].replace('#include "cairn_gpu.hpp"', '#include "gpu_host.hpp"')
    (tmp_path / "pipeline.cpp").write_text(cpp)
    (tmp_path / "harness.cpp").write_text('#include "gpu_host.hpp"\n' + HARNESS.replace("PASSES", str(passes)))
    exe = tmp_path / "pipeline"
    line = [cxx, *flags, "-ffp-contract=off", f"-I{RUNTIME}", f"-I{HOST}", str(tmp_path / "pipeline.cpp"),
            str(tmp_path / "harness.cpp"), "-o", str(exe)]  # fmt: skip
    done = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-4000:]
    return exe


def rows(exe: Path) -> list[dict]:
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-4000:]
    return [json.loads(line) for line in done.stdout.splitlines()]


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_repeated_pipeline_makes_and_allocates_nothing_after_its_first_pass(tmp_path, cxx):
    found = rows(host_build(tmp_path, cxx, *sanitized(cxx)))
    assert found[-1] == {"wrong": 0}
    passes = [r for r in found if r.get("what") == "pass"]
    first, later = passes[0], passes[1:]
    fixed = ("streams", "events", "allocations", "scratch_allocations", "frees")
    assert all({k: r[k] for k in fixed} == {k: first[k] for k in fixed} for r in later)
    assert first["streams"] == 2  # the synchronous lane, and a second lane while two tickets are live
    assert first["scratch_allocations"] == 3  # the arena grew for the reduction, the scan and the compaction
    for kind in ("stream_waits", "launches", "copies", "library_calls"):  # the same work every pass
        steps = {b[kind] - a[kind] for a, b in itertools.pairwise(passes)}
        assert len(steps) == 1, (kind, steps)
    assert passes[1]["stream_waits"] - first["stream_waits"] == 9  # one per synchronous operation or ticket
    assert all(r["event_waits"] == first["event_waits"] for r in later)  # growth, on the first pass only
    bound = found[-2]
    assert bound["what"] == "bound" and bound["streams"] == first["streams"]


def test_the_pipeline_lowers_to_the_execution_context_only():
    cpp = compile_source(PIPELINE)[0]
    for entry in ("run(", "run_vector<", "run_staged<", "reduce_on<", "scan_on<", "compact_on(", "copy_on(", "queue(",
                  "queue_copy("):  # fmt: skip
        assert f"cr::gpu::{entry}" in cpp, entry
    assert "cr::gpu::here()" in cpp and "cr::gpu::Lent" in cpp
    for legacy in ("cr::gpu::launch", "cr::gpu::copy(", "cr::gpu::Ticket", "cr::gpu::reduce<", "cr::gpu::compact("):
        assert legacy not in cpp, legacy


MAIN = """
fn main() -> i32 {
  let n:usize = 4099;
  buffer x:u32[n]@device = zeroed;
  buffer sums:u32[n]@device = zeroed;
  buffer kept:u32[n]@device = zeroed;
  buffer host:f32[n]@pinned = zeroed;
  buffer shared:f32[n]@unified = zeroed;
  buffer dx:f32[n]@device = zeroed;
  buffer dy:f32[n]@device = zeroed;
  let mut total:u64 = 0;
  for k in 0..10 {
    let got = pass(n, x, sums, kept, u32(k));
    total = add_wrap(total, got);
    shuttle(n, host, dx, dy, shared);
  }
  if total == 0 { return 1; }
  return 0;
}
"""


def test_the_pipeline_compiles_for_sm_120_and_never_waits_for_the_whole_device(tmp_path):
    """Compiled by nvcc for sm_120 and never run. The object's undefined CUDA symbols are what the program can call:
    stream waits, and no whole-device wait, no synchronous copy and no stream made per ticket."""
    built = device_build(tmp_path, compile_source(PIPELINE + MAIN)[0], entry="main", timeout=900)
    if not shutil.which("nm"):
        pytest.skip("needs nm")
    listed = subprocess.run(["nm", "-u", str(built)], capture_output=True, text=True, check=True).stdout
    called = {line.split()[-1] for line in listed.splitlines() if line.split()[-1].startswith("cuda")}
    assert {"cudaStreamSynchronize", "cudaStreamCreate", "cudaMemcpyAsync", "cudaMemsetAsync"} <= called, called
    assert not called & {"cudaDeviceSynchronize", "cudaMemcpy", "cudaMemset"}, called


CHECKED = """
fn expected(n:usize, round:u32, hx:rw<u32>[n], hs:rw<u32>[n]) -> u64 {
  let mut total:u64 = 0;
  for i in 0..n { hx[i] = u32((u64(i) * 2654435761 + u64(round)) % 1000); total += u64(hx[i]); }
  let mut run:u32 = 0;
  for i in 0..n { hs[i] = run; run += hx[i] % 16; }
  let mut used:u64 = 0;
  for i in 0..n { if hx[i] % 3 == 0 { used += 1; } }
  return total + u64(run) * 1000000 + used * 1000000000000;
}

fn main() -> i32 {
  let n:usize = 20011;
  buffer x:u32[n]@device = zeroed;
  buffer sums:u32[n]@device = zeroed;
  buffer kept:u32[n]@device = zeroed;
  buffer hx:u32[n] = zeroed;
  buffer hs:u32[n] = zeroed;
  buffer back:u32[n] = zeroed;
  for k in 0..20 {
    let got = pass(n, x, sums, kept, u32(k));
    let want = expected(n, u32(k), hx, hs);
    if got != want { return 1; }
    transfer(back, sums);
    for i in 0..n { if back[i] != hs[i] { return 2; } }
  }
  return 0;
}
"""


def test_the_pipeline_keeps_its_results_on_the_device(tmp_path):
    """Twenty passes on the device, each held to the host's own loops over the same data: typed here, and run only
    under `make gpu`."""
    cpp = compile_source(PIPELINE + CHECKED)[0]
    with on_device():
        done = contract(tmp_path, cpp, "g++", cuda=True, timeout=600)
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])


def test_runtime_files_include_the_execution_header():
    assert "cairn_exec.hpp" in RUNTIME_FILES and '#include "cairn_exec.hpp"' in RUNTIME_FILES["cairn_gpu.hpp"]
    assert "cudaDeviceSynchronize" not in RUNTIME_FILES["cairn_exec.hpp"] + RUNTIME_FILES["cairn_reuse.hpp"]


FAN = """
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
"""

FANNED = r"""
#include <cstdio>
#include <cstdint>
extern "C" std::uint64_t cf_fan(std::size_t, std::uint32_t*, std::uint32_t*, std::uint32_t) noexcept;
int main() {
  const std::size_t n = 5003;
  int wrong = 0;
  cr::gpu::Buffer<std::uint32_t> a(n), b(n);
  for(std::uint32_t k = 0; k < 40; ++k) {
    std::uint64_t want = 0;
    for(std::size_t i = 0; i < n; ++i) want += 2 * std::uint64_t(i % 1000 + k) + 1;
    wrong += cf_fan(n, a.data(), b.data(), k) != want;
  }
  std::printf("{\"wrong\": %d, \"streams\": %zu, \"most_by_one_thread\": %zu}\n", wrong,
              cr::gpu::counted.streams.load(), cr::gpu::counted.most_by_one_thread.load());
  return wrong ? 1 : 0;
}
"""


def test_each_thread_has_its_own_context_and_no_race(tmp_path):
    """Two tasks run device work at once, each on its own thread's context: ThreadSanitizer sees no race in the
    contexts or the lanes, and no thread makes more than the one stream its synchronous work runs on."""
    if not shutil.which("clang++") or not shutil.which("setarch"):
        pytest.skip("needs clang++ and setarch")
    cpp = compile_source(FAN)[0].replace('#include "cairn_gpu.hpp"', '#include "gpu_host.hpp"')
    (tmp_path / "fan.cpp").write_text(cpp)
    (tmp_path / "main.cpp").write_text('#include "gpu_host.hpp"\n' + FANNED)
    exe = tmp_path / "fan"
    line = ["clang++", "-std=c++20", "-O1", "-g", "-fno-exceptions", "-fsanitize=thread", "-pthread",
            f"-I{RUNTIME}", f"-I{HOST}", str(tmp_path / "fan.cpp"), str(tmp_path / "main.cpp"), "-o", str(exe)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-4000:]
    done = subprocess.run(["setarch", "-R", str(exe)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0 and "ThreadSanitizer" not in done.stderr, done.stdout + done.stderr[-4000:]
    found = json.loads(done.stdout.splitlines()[-1])
    assert found["wrong"] == 0 and found["most_by_one_thread"] == 1 and found["streams"] >= 2


LIBRARY = """
pub fn scale(n:usize, y:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { y[i] = a * x[i]; } }
"""

CALLER = r"""
#include <cstdio>
#include "gpu_host.hpp"
#include "devlib.h"
int main() {
  const std::size_t n = 1000;
  cr::gpu::Buffer<float> x(n), y(n);
  for(std::size_t i = 0; i < n; ++i) x.data()[i] = float(i);
  cr::gpu::HostStream mine{0};
  cairn_devlib_device_stream(&mine);  // the caller's stream, as a C host hands over a cudaStream_t
  const std::size_t made = cr::gpu::counted.streams.load();
  cf_scale(n, y.data(), x.data(), 3.0f);
  int wrong = cr::gpu::counted.last_stream.load() != &mine || cr::gpu::counted.streams.load() != made;
  cairn_devlib_device_stream(nullptr);
  cf_scale(n, y.data(), x.data(), 2.0f);
  wrong += cr::gpu::counted.last_stream.load() == &mine;
  for(std::size_t i = 0; i < n; ++i) wrong += y.data()[i] != 2.0f * float(i);
  std::printf("wrong %d\n", wrong);
  return wrong;
}
"""


def test_a_c_host_hands_over_its_own_stream(tmp_path):
    """`cairn build --header` of a device library declares NAME_device_stream and the library defines it, and the C
    caller's stream then carries the thread's device work: run on the host machine here, and built for sm_120 by
    `cairn build` itself where nvcc is present, never run."""
    if not shutil.which("clang++"):
        pytest.skip("clang++ unavailable")
    declared, checks = header(LIBRARY, "devlib", device=True)
    assert "void cairn_devlib_device_stream(void *stream);" in declared
    assert "cairn_devlib_device_stream" not in header(LIBRARY.replace("@device", ""), "devlib")[0]
    cpp = compile_source(LIBRARY)[0] + "\n" + checks
    (tmp_path / "lib.cpp").write_text(cpp.replace('#include "cairn_gpu.hpp"', '#include "gpu_host.hpp"'))
    (tmp_path / "devlib.h").write_text(declared)
    (tmp_path / "main.cpp").write_text(CALLER)
    exe = tmp_path / "caller"
    line = ["clang++", *sanitized("clang++"), f"-I{RUNTIME}", f"-I{HOST}", f"-I{tmp_path}", str(tmp_path / "lib.cpp"),
            str(tmp_path / "main.cpp"), "-o", str(exe)]  # fmt: skip
    built = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr[-4000:]
    done = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout == "wrong 0\n", done.stdout + done.stderr[-3000:]
    if shutil.which("nvcc") and shutil.which("g++"):
        (tmp_path / "project/src").mkdir(parents=True)
        (tmp_path / "project/src/lib.cairn").write_text(LIBRARY)
        (tmp_path / "project/cairn.toml").write_text('[project]\nname = "devlib"\nsources = ["src/lib.cairn"]\n')
        record = build(load_project(tmp_path / "project"), output=tmp_path / "build", cxx="g++", header=True,
                       device_target="sm_120", timeout=300)  # fmt: skip
        assert record["status"] == "native-built", record.get("stderr", "")[-3000:]
        assert "cairn_devlib_device_stream" in Path(record["header"]).read_text()

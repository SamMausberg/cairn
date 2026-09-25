"""Device work held to one wait, and enqueued on a caller's stream with none, run on the host.

A function whose row shows nothing that lets the host observe device memory (compiler/lower/execution.py) runs its body
held when it queues device work more than once: its checked entry waits once, when it returns, instead of after each
operation. A device library's C header gives each such function an enqueued entry, cq_NAME(stream, ...), which queues
the same work on the caller's stream and returns without waiting, allocating, or making a stream or an event, so a
caller may capture it in a CUDA graph. A function that must wait on the host has no enqueued entry, and the header
says why (E-ENQUEUE). The generated library runs here against tests/runtime/gpu_host.hpp, which counts every CUDA
call and every call a stream capture refuses; the CUDA build compiles for sm_120, and `make gpu` alone runs the same
calls on a device, captured into a graph and replayed too.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_program, compile_source
from cairn.compiler.lower import execution
from cairn.compiler.lower.header import binding, header
from emitted import device_build, hosted_library, library_ran_on_device, printed, sanitized

LIBRARY = """
// Two regions and a copy between device views: nothing the host reads before it returns.
pub fn smooth(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, scratch:rw<f32>[n]@device) {
  parallel i in n { scratch[i] = 2.0 * x[i]; }
  parallel i in n { out[i] = scratch[i] + 1.0; }
  transfer(scratch, out);
}

// A cooperative region, then smooth's three operations.
pub fn blocked(g:usize, sums:rw<u32>[g]@device, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device,
               scratch:rw<f32>[n]@device) {
  blocks b in g threads t in 32 {
    if t == 0 { sums[b] = u32(b); }
  }
  smooth(n, out, x, scratch);
}

// One region, run as many times as asked.
pub fn repeat(n:usize, out:rw<f32>[n]@device, times:usize) {
  for k in 0..times { parallel i in n { out[i] = out[i] + 1.0; } }
}

// One region: its entry waits once, as it always did, and its body is not held.
pub fn once(n:usize, out:rw<f32>[n]@device) { parallel i in n { out[i] = 3.0; } }

// A total the host reads, and a copy into host memory: each waits on the host, so neither has an enqueued entry.
pub fn total(n:usize, x:ro<f32>[n]@device) -> f32 {
  let t = reduce + parallel i in n yield x[i];
  return t;
}
pub fn fetch(n:usize, host:rw<f32>[n], x:ro<f32>[n]@device) { transfer(host, x); }
"""

CALLER = r"""
#include <cstdio>
#include <cstdint>
#include "coop_host.hpp"
#include "lib.h"

static cr::gpu::HostStream mine{0};  // the caller's stream, as a C host hands over a cudaStream_t

static void row(const char* what) {
  const auto& c = cr::gpu::counted;
  std::printf("{\"what\": \"%s\", \"streams\": %zu, \"events\": %zu, \"allocations\": %zu, \"scratch\": %zu, "
              "\"frees\": %zu, \"launches\": %zu, \"copies\": %zu, \"stream_waits\": %zu, \"event_waits\": %zu, "
              "\"refused\": %zu, \"on_mine\": %d}\n", what, c.streams.load(), c.events.load(), c.allocations.load(),
              c.scratch_allocations.load(), c.frees.load(), c.launches.load(), c.copies.load(), c.stream_waits.load(),
              c.event_waits.load(), c.refused.load(), c.last_stream.load() == &mine);
}

int main() {
  const std::size_t n = 1000, g = 3;
  int wrong = 0;
  std::vector<float> x(n), out(n), scratch(n), host(n);
  std::vector<std::uint32_t> sums(g);
  for(std::size_t i = 0; i < n; ++i) x[i] = float(i);
  row("start");
  // Captured, as a CUDA graph capture would be: the thread's first CAIRN call, so its context is made here too.
  cr::gpu::counted.capturing = true;
  cq_blocked(&mine, g, sums.data(), n, out.data(), x.data(), scratch.data());
  cr::gpu::counted.capturing = false;
  row("enqueued blocked");
  for(std::size_t i = 0; i < n; ++i) wrong += out[i] != 2.0f * float(i) + 1.0f || scratch[i] != out[i];
  for(std::size_t b = 0; b < g; ++b) wrong += sums[b] != b;
  cq_repeat(&mine, n, out.data(), 5);
  row("enqueued repeat");
  cq_repeat(nullptr, n, out.data(), 1);  // the legacy default stream
  row("enqueued on null");
  for(std::size_t i = 0; i < n; ++i) wrong += out[i] != 2.0f * float(i) + 7.0f;
  cf_blocked(g, sums.data(), n, out.data(), x.data(), scratch.data());
  row("checked blocked");
  cf_repeat(n, out.data(), 5);
  row("checked repeat");
  cf_once(n, out.data());
  row("checked once");
  wrong += cf_total(n, x.data()) != float(n * (n - 1) / 2);
  row("checked total");
  cairn_lib_device_stream(&mine);  // a bound stream: the held run waits on it, once
  cf_smooth(n, out.data(), x.data(), scratch.data());
  row("bound smooth");
  cq_once(&mine, n, out.data());
  cf_fetch(n, host.data(), out.data());
  row("bound fetch after enqueued once");
  cairn_lib_device_stream(nullptr);
  for(std::size_t i = 0; i < n; ++i) wrong += host[i] != 3.0f;
  std::printf("{\"wrong\": %d}\n", wrong);
  return wrong;
}
"""


# The same calls on a device: the enqueued entries on a stream the caller owns, directly and captured into a graph it
# replays, then the checked entries and a bound stream; every result read back once its stream is done. Exit 0 passes.
ON_DEVICE = r"""
#include <cstdio>
#include <cstdint>
#include <vector>
#include <cuda_runtime.h>
#include "lib.h"

#define OK(call) do { cudaError_t e = (call); if(e != cudaSuccess) { \
  std::fprintf(stderr, "%s: %s\n", #call, cudaGetErrorString(e)); return 10; } } while(0)

static int wrong_rows(const char* what, const std::vector<float>& got, float scale, float plus) {
  int wrong = 0;
  for(std::size_t i = 0; i < got.size(); ++i) wrong += got[i] != scale * float(i) + plus;
  if(wrong) std::fprintf(stderr, "%s: %d elements wrong\n", what, wrong);
  return wrong;
}

int main() {
  const std::size_t n = 1000, g = 3;
  std::vector<float> host(n), back(n), kept(n);
  std::vector<std::uint32_t> sums(g);
  for(std::size_t i = 0; i < n; ++i) host[i] = float(i);
  float *x, *out, *scratch;
  std::uint32_t* dsums;
  OK(cudaMalloc(&x, n * sizeof(float)));
  OK(cudaMalloc(&out, n * sizeof(float)));
  OK(cudaMalloc(&scratch, n * sizeof(float)));
  OK(cudaMalloc(&dsums, g * sizeof(std::uint32_t)));
  OK(cudaMemcpy(x, host.data(), n * sizeof(float), cudaMemcpyHostToDevice));
  cudaStream_t mine;
  OK(cudaStreamCreate(&mine));
  int wrong = 0;

  cq_blocked(mine, g, dsums, n, out, x, scratch);  // out = 2i + 1, scratch = out, sums[b] = b
  cq_repeat(mine, n, out, 5);                      // out = 2i + 6
  cudaGraph_t graph;
  cudaGraphExec_t replay;
  OK(cudaStreamBeginCapture(mine, cudaStreamCaptureModeGlobal));  // any call a capture refuses ends it with an error
  cq_repeat(mine, n, out, 1);
  OK(cudaStreamEndCapture(mine, &graph));
  OK(cudaGraphInstantiate(&replay, graph, 0));
  for(int k = 0; k < 3; ++k) OK(cudaGraphLaunch(replay, mine));  // out = 2i + 9
  OK(cudaStreamSynchronize(mine));
  OK(cudaMemcpy(back.data(), out, n * sizeof(float), cudaMemcpyDeviceToHost));
  OK(cudaMemcpy(kept.data(), scratch, n * sizeof(float), cudaMemcpyDeviceToHost));
  OK(cudaMemcpy(sums.data(), dsums, g * sizeof(std::uint32_t), cudaMemcpyDeviceToHost));
  wrong += wrong_rows("enqueued and replayed", back, 2.0f, 9.0f) + wrong_rows("scratch", kept, 2.0f, 1.0f);
  for(std::size_t b = 0; b < g; ++b) wrong += sums[b] != b;

  cf_blocked(g, dsums, n, out, x, scratch);  // the checked entries, on the thread's own stream
  cf_repeat(n, out, 5);
  OK(cudaMemcpy(back.data(), out, n * sizeof(float), cudaMemcpyDeviceToHost));
  wrong += wrong_rows("checked", back, 2.0f, 6.0f);
  cf_once(n, out);
  wrong += cf_total(n, x) != float(n * (n - 1) / 2);

  cairn_lib_device_stream(mine);  // bound: the checked entries run on the caller's stream
  cf_smooth(n, out, x, scratch);
  cq_once(mine, n, out);
  cf_fetch(n, back.data(), out);
  cairn_lib_device_stream(nullptr);
  wrong += wrong_rows("bound", back, 0.0f, 3.0f);

  OK(cudaGraphExecDestroy(replay));
  OK(cudaGraphDestroy(graph));
  OK(cudaStreamDestroy(mine));
  for(void* p : {(void*)x, (void*)out, (void*)scratch, (void*)dsums}) OK(cudaFree(p));
  std::printf("{\"wrong\": %d}\n", wrong);
  return wrong != 0;
}
"""


def steps(tmp_path: Path, cxx: str) -> dict[str, dict]:
    """The library and its caller built with the sanitizers on the host stand-in and run; each step's counts."""
    rows = printed(hosted_library(tmp_path, LIBRARY, CALLER, cxx, *sanitized(cxx), "-pthread"))
    assert rows[-1] == {"wrong": 0}
    return {r["what"]: r for r in rows[:-1]}


def moved(rows: dict[str, dict], before: str, after: str) -> dict[str, int]:
    return {k: rows[after][k] - rows[before][k] for k in rows[after] if k not in {"what", "on_mine"}}


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_an_enqueued_call_waits_for_nothing_and_makes_nothing(tmp_path, cxx):
    rows = steps(tmp_path, cxx)
    first = moved(rows, "start", "enqueued blocked")
    assert first["refused"] == 0, first  # no wait, stream, event, allocation or release, on a fresh thread too
    assert first["stream_waits"] == first["event_waits"] == first["streams"] == first["events"] == 0, first
    assert first["launches"] == 2 and first["copies"] == 1 and rows["enqueued blocked"]["on_mine"], first
    repeat = moved(rows, "enqueued blocked", "enqueued repeat")
    assert repeat["launches"] == 5 and repeat["stream_waits"] == 0 and rows["enqueued repeat"]["on_mine"], repeat
    assert moved(rows, "enqueued repeat", "enqueued on null")["stream_waits"] == 0
    assert not rows["enqueued on null"]["on_mine"]  # queued on the null stream the caller named


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_held_body_waits_once_and_the_rest_wait_as_before(tmp_path, cxx):
    rows = steps(tmp_path, cxx)
    blocked = moved(rows, "enqueued on null", "checked blocked")
    assert blocked["stream_waits"] == 1 and blocked["launches"] == 2 and blocked["copies"] == 1, blocked
    assert not rows["checked blocked"]["on_mine"]  # the enqueued calls left the thread's own stream in place
    assert moved(rows, "checked blocked", "checked repeat")["stream_waits"] == 1  # five regions, one wait
    assert moved(rows, "checked repeat", "checked once")["stream_waits"] == 1
    assert moved(rows, "checked once", "checked total")["stream_waits"] == 1  # the total's own wait
    bound = moved(rows, "checked total", "bound smooth")
    assert bound["stream_waits"] == 1 and rows["bound smooth"]["on_mine"], bound
    fetched = moved(rows, "bound smooth", "bound fetch after enqueued once")
    assert fetched["stream_waits"] == 1 and rows["bound fetch after enqueued once"]["on_mine"], fetched


def test_the_enqueued_entries_run_on_a_device_directly_and_replayed_in_a_graph(tmp_path):
    library_ran_on_device(tmp_path, LIBRARY, ON_DEVICE)


def test_only_functions_that_never_wait_on_the_host_have_an_enqueued_entry():
    declared, checks = header(LIBRARY, "lib", device=True)
    for name in ("smooth", "blocked", "repeat", "once"):
        assert f"void cq_{name}(void *stream, " in declared and f" cq_{name}(void* stream, " in checks, name
    assert "cq_total" not in declared and "cq_fetch" not in declared
    assert "No enqueued entry (E-ENQUEUE)" in declared
    refusal = " ".join(declared.split("No enqueued entry (E-ENQUEUE)")[1].split("*/")[0].split())
    assert "total: it allocates device memory" in refusal and "fetch: it transfers to host memory" in refusal
    host = "pub fn f(n:usize, x:rw<f32>[n]) { parallel i in n { x[i] = 1.0; } }"
    assert "cq_" not in header(host, "lib")[0]  # a host library queues nothing
    assert "lib.cq_smooth.argtypes = [C.c_void_p, C.c_size_t," in binding(LIBRARY, "lib", device=True)


def test_a_body_is_held_only_where_it_queues_device_work_more_than_once():
    cpp = compile_source(LIBRARY)[0]
    bodies = {part.split("(")[0]: part.split("\n}\n")[0] for part in cpp.split("\nvoid ci_")[1:]}
    assert {name for name, body in bodies.items() if "cr::gpu::Held" in body} == {"smooth", "blocked", "repeat"}
    assert "once" in bodies and "fetch" in bodies


# Device work between things the host observes: copies to and from host memory, and an owner's allocation and release.
RUNS = """
fn between_copies(n:usize, host:rw<f32>[n], x:rw<f32>[n]@device) {
  transfer(x, host);
  parallel i in n { x[i] = x[i] + 1.0; }
  parallel i in n { x[i] = x[i] * 2.0; }                 // queued behind the first: the copy back waits for both
  transfer(host, x);
}
fn with_an_owner(n:usize, x:rw<f32>[n]@device) {
  buffer t:f32[n]@device = zeroed;
  parallel i in n { t[i] = x[i]; }
  parallel i in n { x[i] = t[i] + 1.0; }                 // t's release at the end waits for both
}
fn one_at_a_time(n:usize, host:rw<f32>[n], x:rw<f32>[n]@device) {
  transfer(x, host);
  parallel i in n { x[i] = x[i] + 1.0; }
  transfer(host, x);
  parallel i in n { x[i] = x[i] * 2.0; }
  transfer(host, x);
}
fn printed(n:usize, x:rw<f32>[n]@device) {
  parallel i in n { x[i] = x[i] + 1.0; }
  parallel i in n { x[i] = x[i] * 2.0; }
  println(n);
}
fn helper(n:usize) -> usize = n + 1;
fn tasked(n:usize, x:rw<f32>[n]@device) {
  parallel i in n { x[i] = x[i] + 1.0; }
  parallel i in n { x[i] = x[i] * 2.0; }
  let t = spawn helper(n);
  let m = wait(t);
}
fn unified(n:usize, x:rw<f32>[n]@device) {
  buffer u:f32[n]@unified = zeroed;
  parallel i in n { x[i] = x[i] + 1.0; }
  parallel i in n { x[i] = x[i] * 2.0; }
}
"""


def test_a_body_is_held_where_a_run_of_device_work_meets_only_observations_that_wait():
    """A copy to or from host memory and an owner's allocation and release wait for a held run where they stand, so
    they only end a run; I/O, a host task and @unified memory would see it unwaited, so they keep a body from being
    held; a body with no run of two operations between observations gains nothing and is not held."""
    program, checker, _ = compile_program(RUNS)
    held = {f.name for f in program.functions if execution.held(checker, f)}
    assert held == {"between_copies", "with_an_owner"}
    why = {f.name: execution.unheld(checker, f) for f in program.functions}
    assert why["between_copies"] == why["with_an_owner"] == why["one_at_a_time"] == ""
    assert why["printed"] == "it makes a foreign call, which may observe device memory"  # println calls write(2)
    assert why["tasked"].startswith("it starts a host task") and why["unified"].startswith("it declares a @unified")
    declared = header(RUNS, "lib", device=True)[0]
    assert "cq_between_copies" not in declared and "between_copies: it transfers to host memory" in declared


def test_the_enqueued_entries_compile_for_sm_120(tmp_path):
    """The library with its enqueued entries, built by nvcc for sm_120 and never run: the entries are defined, and
    none for a function that waits."""
    checks = header(LIBRARY, "lib", device=True)[1]
    built = device_build(tmp_path, compile_source(LIBRARY)[0] + "\n" + checks, entry=None)
    if shutil.which("nm"):
        symbols = subprocess.run(["nm", str(built)], capture_output=True, text=True, check=True).stdout
        assert " T cq_blocked" in symbols and " T cq_smooth" in symbols and "cq_total" not in symbols


ATOMICS = """
pub fn histogram(n:usize, x:ro<u32>[n]@device, bins:rw<u32>[256]@device) {
  parallel i in n { let _ = atomic_add_wrap(bins[usize(x[i] & 255)], 1); }
}

pub fn counted(n:usize, x:rw<u32>[n]@device, h:ro<u32>[n], hits:rw<u32>[4]) {
  parallel i in n { x[i] = x[i] + 1; }
  parallel i in n { let _ = atomic_add_wrap(hits[usize(h[i] & 3)], 1); }
}
"""


def test_an_atomic_in_a_device_lane_is_device_work_and_one_on_the_host_is_not():
    """A device lane's atomic update touches device memory only; one in a host lane is seen by other host threads,
    which could act on it before the device work before it was waited for."""
    declared = header(ATOMICS, "lib", device=True)[0]
    assert "void cq_histogram(void *stream, " in declared and "cq_counted" not in declared
    refusal = " ".join(declared.split("No enqueued entry (E-ENQUEUE)")[1].split("*/")[0].split())
    assert "counted: it updates host memory atomically" in refusal

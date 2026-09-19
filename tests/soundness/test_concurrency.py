"""Tasks with leases, atomics, mutexes, parallel regions on threads and CUDA lanes, closures.

Accepted programs run natively (and under ThreadSanitizer); every safety rule has a rejection.
"""

import os
import shutil
import subprocess

import pytest

from cairn.agent.agent_tools import canonical_source
from cairn.compiler.cairnc import RUNTIME_FILES, Diagnostic, compile_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from cairn.projects.toolchain import command

HELPERS = """
struct Stats { hits:u64; total:u64; }
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn sum(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut t:u64 = 0;
  for i in 0..n { t = t + xs[i]; }
  return t;
}
fn count_even(n:usize, xs:ro<u64>[n], evens:ro<Atomic[u64]>, stats:ro<Mutex[Stats]>) {
  for i in 0..n { if (xs[i] & 1) == 0 { let before = evens.fetch_add(1, Order.relaxed); } }
  stats.with(|s:rw<Stats>| { s.hits = s.hits + 1; s.total = s.total + u64(n); });
}
fn apply(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }
fn double(x:u64) -> u64 = x * 2;
struct Op { run:fn(u64) -> u64; times:usize; }
fn repeat(op:Op, x:u64) -> u64 {
  let mut v = x;
  let run = op.run;
  for i in 0..op.times { v = run(v); }
  return v;
}
fn poke(n:usize, out:rw<u64>[n]) { out[0] = 1; }
"""

TASKS = """
fn main() -> i32 {
  let n:usize = 1000;
  let mid:usize = 500;
  let mut data = Buf[u64](n);
  let left = spawn fill(mid, data[0..mid], 0);
  let right = spawn fill(mid, data[mid..n], 500);
  wait(left);
  wait(right);
  if sum(len(data), data) != 499500 { return 1; }
  let evens = Atomic[u64](0);
  let stats = Mutex[Stats](Stats(0, 0));
  let a = spawn count_even(mid, data[0..mid], evens, stats);
  let b = spawn count_even(mid, data[mid..n], evens, stats);
  let total = spawn sum(len(data), data);
  let peek = data[3];
  wait(a);
  wait(b);
  if wait(total) != 499500 || peek != 3 { return 2; }
  if evens.load(Order.seq_cst) != 500 { return 3; }
  let hits = stats.with(|s:rw<Stats>| -> u64 { return s.hits * 1000 + s.total; });
  if hits != 3000 { return 4; }
  let mut expected:u64 = 500;
  if !evens.compare_exchange(expected, 7, Order.seq_cst, Order.seq_cst) { return 5; }
  parallel i in n { let seen = evens.fetch_add(1, Order.relaxed); }
  if evens.load(Order.seq_cst) != 1007 { return 6; }
  buffer out:u64[n] = zeroed;
  parallel i in n { out[i] = data[i] * 2; }
  let folded = reduce add_wrap for i in n yield out[i];
  if folded != 999000 { return 7; }
  let bias:u64 = 10;
  let mut calls:u64 = 0;
  apply(n, out, |x:u64| -> u64 { calls = calls + 1; return x + bias; });
  apply(n, out, double);
  if out[1] != 24 || calls != 1000 || repeat(Op(double, 3), 5) != 40 { return 8; }
  return 0;
}
"""

DEVICE = """
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
fn scale(v:u64) -> u64 = mul_wrap(v, 3);
kernel fn at(w:usize, n:usize, grid:ro<f32>[n]@device, x:usize, y:usize) -> f32 = grid[y * w + x];
fn blur(w:usize, n:usize, out:rw<f32>[n]@device, grid:ro<f32>[n]@device) {
  parallel i in n {
    let x = i % w;
    if x > 0 && x + 1 < w { out[i] = (at(w, n, grid, x - 1, i / w) + at(w, n, grid, x + 1, i / w)) * 0.5; }
  }
}
fn main() -> i32 {
  let n:usize = 1000000;
  buffer x:f32[n] = zeroed;
  buffer y:f32[n] = zeroed;
  buffer out:f32[n] = zeroed;
  for i in 0..n { x[i] = f32(i % 1000); y[i] = 1.0; }
  buffer dx:f32[n]@device = zeroed;
  buffer dy:f32[n]@device = zeroed;
  buffer dout:f32[n]@device = zeroed;
  transfer(dx, x);
  transfer(dy, y);
  saxpy(n, dout, dx, dy, 2.0);
  transfer(out, dout);
  for i in 0..n { if out[i] != 2.0 * f32(i % 1000) + 1.0 { return 1; } }
  buffer ids:u64[n]@device = zeroed;
  parallel i in n { ids[i] = scale(u64(i)); }
  let total = reduce add_wrap for i in n yield ids[i];
  if total != 1499998500000 { return 2; }
  buffer evens:u64[n]@device = zeroed;
  let used = compact evens for i in n where (ids[i] & 1) == 0 yield ids[i];
  if used != 500000 { return 3; }
  buffer host_evens:u64[n] = zeroed;
  transfer(host_evens, evens);
  if host_evens[0] != 0 || host_evens[1] != 6 || host_evens[499999] != 2999994 { return 4; }
  blur(1000, n, dout, dx);
  transfer(out, dout);
  if out[1] != 1.0 || out[1500] != 500.0 { return 5; }
  return 0;
}
"""


def build_and_run(tmp_path, source, cxx, extra=(), timeout=240):
    generated, receipt = compile_source(source)
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    cuda = "cuda" in receipt["requires"]
    line = command(cxx, str(tmp_path / "p.cpp"), str(tmp_path / "p"), kind="exe", cuda=cuda)
    subprocess.run([*line, *extra], check=True, timeout=timeout)
    return subprocess.run([tmp_path / "p"], timeout=timeout).returncode, receipt


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_tasks_atomics_mutexes_regions_and_closures_run(tmp_path, cxx):
    if not shutil.which(cxx):
        pytest.skip("Native compiler unavailable")
    code, receipt = build_and_run(tmp_path, HELPERS + TASKS, cxx)
    assert code == 0
    effects = set(receipt["functions"]["main"]["effects"])
    assert {"spawn", "join", "atomic", "lock", "par:host", "indirect_call", "alloc"} <= effects
    assert "indirect_call" in receipt["functions"]["apply"]["effects"]


def test_no_data_race_under_thread_sanitizer(tmp_path):
    if not shutil.which("clang++") or not shutil.which("setarch"):
        pytest.skip("ThreadSanitizer needs clang++ and setarch")
    generated, _ = compile_source(HELPERS + TASKS)
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    build = ["clang++", "-std=c++20", "-O1", "-g", "-fsanitize=thread", "-fno-exceptions"]
    subprocess.run([*build, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=240)
    run = subprocess.run(["setarch", "-R", str(tmp_path / "p")], capture_output=True, text=True, timeout=240)
    assert run.returncode == 0 and "ThreadSanitizer" not in run.stderr


TWO_MODULES = """module fill;
pub fn go(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i) * 2; } }
module app;
import fill;
fn main() -> i32 {
  let n:usize = 100000;
  buffer d:u64[n] = zeroed;
  fill.go(n, d);
  parallel i in n { d[i] = d[i] + 1; }
  let s = reduce add_wrap for i in n yield d[i];
  if s != 10000000000 { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_one_lane_pool_serves_every_translation_unit(tmp_path, cxx):
    """Regions in two separately compiled modules share one pool. The lanes live in a static inside
    an inline function, so the linker keeps one; were that ever to change, a program compiled with
    --incremental would quietly get a full set of lane threads per module."""
    if not shutil.which(cxx) or not shutil.which("nm"):
        pytest.skip(f"needs {cxx} and nm")
    path = tmp_path / "program.cairn"
    path.write_text(TWO_MODULES)
    record = build(load_project(path), kind="exe", cxx=cxx, timeout=300, incremental=True)
    assert record["status"] == "native-built", record.get("stderr")
    assert {unit["unit"] for unit in record["units"]} == {"fill.cpp", "app.cpp", "0start.cpp"}
    ran = subprocess.run([record["artifact"]], timeout=120, env={**os.environ, "CAIRN_LANES": "3"})
    assert ran.returncode == 0
    listed = subprocess.run(["nm", "-C", record["artifact"]], capture_output=True, text=True, timeout=60)
    named = [line for line in listed.stdout.splitlines() if line.endswith("cr::par::lanes::shared()::one")]
    pools = [line for line in named if "guard variable" not in line]
    assert len(pools) == 1 and len(named) == 2, named  # one definition and one guard: one pool


def test_the_same_lane_body_runs_on_the_device(tmp_path):
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here; the host path is covered above")
    code, receipt = build_and_run(tmp_path, DEVICE, "g++")
    assert code == 0 and receipt["requires"] == ["cuda"]
    effects = set(receipt["functions"]["main"]["effects"])
    assert {"par:device", "gpu_alloc", "gpu_free", "transfer:h2d", "transfer:d2h"} <= effects
    host = DEVICE.replace("@device", "").replace("kernel fn", "fn")  # The same program, on host threads.
    assert "cr::par::run" in compile_source(host)[0] and "cr::gpu::launch" in compile_source(DEVICE)[0]


BODY = (
    "let n:usize = 8; let mid:usize = 4; let mut data = Buf[u64](n); buffer out:u64[n] = zeroed; let mut total:u64 = 0;"
)


@pytest.mark.parametrize(
    "code,body",
    [
        ("E-LEASED", "let t = spawn fill(mid, data[0..mid], 0); let x = data[0]; wait(t); return 0;"),
        (
            "E-LEASED",
            "let t = spawn fill(mid, data[0..mid], 0); let u = spawn fill(n, data[0..n], 0); wait(t); wait(u); return 0;",
        ),
        ("E-LEASED", "let t = spawn sum(len(data), data); data[0] = 1; let r = wait(t); return 0;"),
        ("E-LEASED", "let t = spawn sum(len(data), data); let moved = data; let r = wait(t); return 0;"),
        ("E-LINEAR-LEAK", "let t = spawn sum(len(data), data); return 0;"),
        ("E-LINEAR-BRANCH", "let t = spawn sum(len(data), data); if n == 8 { let r = wait(t); } return 0;"),
        ("E-PINNED", "let t = spawn sum(len(data), data); let u = t; let r = wait(u); return 0;"),
        ("E-SPAWN", "let t = spawn apply(n, out, |x:u64| -> u64 { return x; }); wait(t); return 0;"),
        ("E-SPAWN", "let r = 1 + wait(spawn sum(len(data), data)); return 0;"),
        ("E-PARALLEL-RACE", "parallel i in n { out[i] = out[0]; } return 0;"),
        ("E-PARALLEL-RACE", "parallel i in mid { out[i + 1] = 1; } return 0;"),
        ("E-PARALLEL-RACE", "parallel i in n { poke(n, out); } return 0;"),
        ("E-PARALLEL-WRITE", "parallel i in n { total = total + out[i]; } return 0;"),
        ("E-PARALLEL-CONTROL", "parallel i in n { return 1; } return 0;"),
        ("E-PARALLEL-NEST", "parallel i in n { parallel j in n { } } return 0;"),
        ("E-PARALLEL-NEST", "parallel i in n { let u = compact out for j in n where true yield 1; } return 0;"),
        ("E-PARALLEL-NEST", "buffer o:u64[n] = zeroed; parallel i in n { transfer(o, out); } return 0;"),
        ("E-PARALLEL-NEST", "parallel i in n { let s = reduce add_wrap for j in n yield out[j]; } return 0;"),
        ("E-LOOP-CONTROL", "for k in 0..2 { parallel i in n { break; } } return 0;"),
        ("E-REDUCE-OP", "let s = reduce + for i in n yield i64(out[i]); return 0;"),  # Unsigned + is checked and legal.
        ("E-REDUCE-OP", "let s = reduce min for i in n yield f32(out[i]); return 0;"),
        ("E-ARITY", "let a = Atomic[u64](0); let v = a.load(); return 0;"),
        ("E-INFER", "let a = Atomic[f64](0.0); return 0;"),
        ("E-CALLEE", "let m = Mutex[u64](0); m.lock(); return 0;"),
        ("E-CLOSURE", "let f = |x:u64| -> u64 { return x; }; return 0;"),
        ("E-MOVE-IN-LOOP", "apply(n, out, |x:u64| -> u64 { let gone = data; return x; }); return 0;"),
        ("E-FN-TYPE", "let op = Op(sum, 1); return 0;"),
        ("E-TYPE-MISMATCH", "apply(n, out, |x:u32| -> u64 { return 1; }); return 0;"),
    ],
)
def test_rejections(code, body):
    with pytest.raises(Diagnostic) as e:
        compile_source(HELPERS + "fn main() -> i32 {" + BODY + body + "}")
    assert e.value.data["code"] == code


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-PLACEMENT", "fn f(n:usize, d:ro<u64>[n]@device, h:rw<u64>[n]) { parallel i in n { h[i] = d[i]; } }"),
        ("E-PLACEMENT", "fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { buffer t:u64[n] = zeroed; d[i] = t[0]; } }"),
        ("E-PLACE", "fn f() { stack t:u64[4]@device = zeroed; }"),
        ("E-TYPE-MISMATCH", "fn g(n:usize, d:ro<u64>[n]@device) {} fn f(n:usize, h:ro<u64>[n]) { g(n, h); }"),
        ("E-TYPE-MISMATCH", "fn f(n:usize, d:rw<u64>[n]@device, h:ro<u32>[n]) { transfer(d, h); }"),
        ("E-PARALLEL-CALL", "extern fn getpid() -> i32 effects(io); fn pid() -> i32 { unsafe { return getpid(); } }"
         "fn f(n:usize, out:rw<i32>[n]) { parallel i in n { out[i] = pid(); } }"),
        ("E-PLACEMENT", "kernel fn k(n:usize, d:ro<u64>[n]@device) -> u64 = d[0]; fn f(n:usize, d:ro<u64>[n]@device) -> u64 = k(n, d);"),
        ("E-PLACEMENT", "kernel fn k(n:usize, h:ro<u64>[n]) -> u64 = h[0];"),
        ("E-PLACEMENT", "kernel fn k(n:usize) -> u64 { buffer b:u64[n] = zeroed; return b[0]; }"),
        ("E-PARALLEL-NEST", "kernel fn k(n:usize, d:rw<u64>[n]@device) { parallel i in n { d[i] = 1; } }"),
        ("E-PARALLEL-CALL", "extern fn getpid() -> i32 effects(io); kernel fn k() -> i32 { unsafe { return getpid(); } }"),
        ("E-PINNED", "struct S { hits:Atomic[u64]; }"),
        ("E-PINNED", "fn f() -> Atomic[u64] = Atomic[u64](0);"),
    ],
)  # fmt: skip
def test_placement_and_sharing_rejections(code, source):
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code


def test_readers_may_share_what_a_task_reads():
    body = "let t = spawn sum(len(data), data); let x = data[0]; let u = spawn sum(len(data), data);"
    assert compile_source(HELPERS + "fn main() -> i32 {" + BODY + body + " return i32(wait(t) + wait(u) + x); }")


LANE_LOCAL = """
enum Kind { Even(u64); Odd; }
kernel fn classify(v:u64) -> Kind { if (v & 1) == 0 { return Kind.Even(v); } return Kind.Odd; }
fn main() -> i32 {
  let n:usize = 4096;
  buffer out:u64[n]@device = zeroed;
  parallel i in n {
    stack window:u64[4] = zeroed;              // lane-private storage lives where the lane runs
    for k in 0..4 { window[k] = u64(i) + u64(k); }
    let mut best:u64 = 0;
    for k in 0..4 { best = max(best, window[k]); }
    match classify(best) {
      Kind.Even(v) => { out[i] = v; }
      Kind.Odd => { out[i] = 1; }
    }
  }
  buffer back:u64[n] = zeroed;
  transfer(back, out);
  if back[0] != 1 || back[1] != 4 || back[4095] != 4098 { return 1; }
  return 0;
}
"""


def test_lane_local_storage_and_sums_run_on_the_device(tmp_path):
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here")
    assert build_and_run(tmp_path, LANE_LOCAL, "g++")[0] == 0


DEVICE_PARTS = """
struct Gain { k:f32; }
fn scaled(p:ro<Gain>, x:f32) -> f32 = p.k * x;                             // a plain pure helper runs on either side
kernel fn window(k:usize, g:ro<f32>[k]@device) -> f32 { let mut t:f32 = 0.0; for j in 0..k { t = t + g[j]; } return t; }
fn main() -> i32 {
  let n:usize = 1024;
  let w:usize = 4;
  buffer cpu:f32[n] = zeroed;
  for i in 0..n { cpu[i] = 1.0; }
  buffer x:f32[n]@device = zeroed;
  buffer out:f32[n]@device = zeroed;
  transfer(x, cpu);
  let gain = Gain(1.0);
  parallel i in n { if i + w <= n { out[i] = scaled(gain, window(w, x[i..i + w])); } }   // a guarded part in a device lane
  transfer(cpu[0..n], out[0..n]);                                         // parts cross placements too
  let total = reduce add_wrap for i in n yield u64(out[i]);
  if cpu[0] != 4.0 || cpu[n - 1] != 0.0 || total != 4084 { return 1; }
  return 0;
}
"""


def test_parts_are_guarded_inside_device_lanes_and_device_scratch_is_in_the_row(tmp_path):
    rows = compile_source(DEVICE_PARTS)[1]["functions"]["main"]["effects"]
    assert {"gpu_alloc", "gpu_free", "par:device", "transfer:h2d", "transfer:d2h"} <= set(rows)
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here")
    assert build_and_run(tmp_path, DEVICE_PARTS, "g++")[0] == 0


LENT_PLACEMENTS = """
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + xs[i]; } return t; }
fn fill(n:usize, xs:rw<u64>[n], v:u64) { for i in 0..n { xs[i] = v + u64(i); } }
fn upload(n:usize, dst:rw<u64>[n]@device, src:ro<u64>[n]) { transfer(dst, src); }
fn download(n:usize, dst:rw<u64>[n], src:ro<u64>[n]@device) { transfer(dst, src); }
fn bump(n:usize, xs:rw<u64>[n]@device) { parallel i in n { xs[i] = xs[i] + 1; } }
fn main() -> i32 {
  let n:usize = 1000;
  buffer p:u64[n]@pinned = zeroed;
  buffer u:u64[n]@unified = zeroed;
  buffer d:u64[n]@device = zeroed;
  buffer h:u64[n] = zeroed;
  fill(n, p, 1);
  fill(n, u, 2);
  if total(n, p) != 500500 || total(n, u) != 501500 { return 1; }
  upload(n, d, p);
  bump(n, d);
  download(n, h, d);
  if total(n, h) != 501500 { return 2; }
  upload(n, d, u);
  bump(n, d);
  download(n, p, d);
  bump(n, u);
  if total(n, p) != 502500 || total(n, u) != 502500 { return 3; }
  return 0;
}
"""


def test_a_view_is_lent_as_what_its_memory_also_is(tmp_path):
    """Page-locked memory is host memory and managed memory is both, so helpers written for @host (or @device)
    views serve them; nothing is lent the other way, and a device view never reaches host code."""
    declared = "fn f(n:usize, xs:ro<u64>[n]@{want}) {{}} fn main() -> i32 {{ let n:usize = 4; buffer b:u64[n]@{got} = zeroed; f(n, b); return 0; }}"
    for got, want in [
        ("host", "pinned"),
        ("host", "unified"),
        ("device", "host"),
        ("pinned", "device"),
        ("device", "unified"),
    ]:
        with pytest.raises(Diagnostic) as e:
            compile_source(declared.format(got=got, want=want))
        assert e.value.data["code"] == "E-TYPE-MISMATCH"
    for got, want in [("pinned", "host"), ("unified", "host"), ("unified", "device")]:
        compile_source(declared.format(got=got, want=want))
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here")
    assert build_and_run(tmp_path, LENT_PLACEMENTS, "g++")[0] == 0


BACKWARDS_PART = """
import std.io as io;
fn work(n:usize, out:rw<u64>[n]) { io.println(len("task ran"), "task ran"); }
fn main() -> i32 {
  let lo:usize = 6;
  let hi:usize = 3;
  let mut d = Buf[u64](8);
  io.println(len("spawning"), "spawning");
  let t = spawn work(0, d[lo..hi]);
  io.println(len("spawned"), "spawned");
  wait(t);
  return 0;
}
"""


def test_a_part_is_guarded_by_the_spawner_before_its_task_exists(tmp_path):
    """What the Lean calculus assumes of the emitter (`TasksGuarded`): the `lo <= hi` of a lent part has run, on
    the spawning thread, before the task starts, so every fact the disjointness chain uses is true."""
    cpp = compile_source(BACKWARDS_PART)[0]
    spawn = next(line for line in cpp.splitlines() if "::spawn(" in line)
    captures, body = spawn.split("]() mutable noexcept {")  # Capture initializers run where the lambda is written.
    assert "cr::part(" in captures and "cr::part(" not in body
    if not shutil.which("clang++"):
        pytest.skip("Native compiler unavailable")
    (tmp_path / "p.cpp").write_text(cpp + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    line = command("clang++", str(tmp_path / "p.cpp"), str(tmp_path / "p"), kind="exe")
    subprocess.run(line, check=True, timeout=240)
    done = subprocess.run([tmp_path / "p"], capture_output=True, text=True, timeout=60)
    assert done.returncode == -6 and done.stdout == "spawning\n"  # Aborted at the slice: no task, no next statement.


def test_try_returns_from_the_closure_it_is_written_in(tmp_path):
    source = (
        "enum R { Ok(u64); Err(u8); }"
        "fn half(x:u64) -> R { if (x & 1) == 1 { return R.Err(7); } return R.Ok(x / 2); }"
        "fn twice(f:ro<fn(u64) -> R>, x:u64) -> R { let once = try f(x); return f(once); }"
        "fn main() -> i32 {"
        " match twice(|v:u64| -> R { let h = try half(v); return R.Ok(h + 1); }, 6) {"
        "   R.Ok(v) => { if v != 3 { return 1; } } R.Err(code) => { return 2; } }"
        " match twice(|v:u64| -> R { let h = try half(v); return R.Ok(h + 1); }, 4) {"
        "   R.Ok(v) => { return 3; } R.Err(code) => { if code != 7 { return 4; } } }"
        " return 0; }"
    )
    assert build_and_run(tmp_path, source, "g++")[0] == 0


@pytest.mark.parametrize(
    "lane",
    [
        "d[i] = hits.fetch_add(1, Order.relaxed);",
        "d[i] = f(d[i]);",
        "d[i] = area(shape);",
        "defer note(d[i]);",
        "let r = try parse(d[i]);",
    ],
)
def test_device_lanes_refuse_host_only_constructs(lane):
    source = (
        "trait Shape { fn area(self:ro<Self>) -> u64; } enum R { Ok(u64); Err(u8); }"
        "fn parse(v:u64) -> R pure = R.Ok(v); fn note(v:u64) pure {}"
        "fn go(n:usize, d:rw<u64>[n]@device, hits:ro<Atomic[u64]>, f:ro<fn(u64) -> u64>, shape:ro<dyn Shape>) -> R {"
        f" parallel i in n {{ {lane} }} return R.Ok(0); }}"
    )
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] in {"E-PLACEMENT", "E-PARALLEL-CONTROL"}


QUEUED = """
fn main() -> i32 {
  let n:usize = 1048576;
  buffer a:f32[n]@pinned = zeroed;
  buffer b:f32[n]@pinned = zeroed;
  for i in 0..n { a[i] = f32(i % 1000); }
  buffer x:f32[n]@device = zeroed;
  buffer y:f32[n]@device = zeroed;
  let up = spawn transfer(x, a);                                     // queued on a stream of its own: the host goes on
  let scale = spawn parallel i in n after up { y[i] = 2.0 * x[i] + 1.0; };
  let down = spawn transfer(b[0..n], y[0..n]) after scale;           // parts are guarded when the work is queued
  let mut busy:u64 = 0;
  for i in 0..1000 { busy = add_wrap(busy, u64(i)); }                // host work overlaps the device pipeline
  wait(up);
  wait(down);
  wait(scale);
  if busy != 499500 || b[0] != 1.0 || b[999] != 1999.0 || b[n - 1] != 2.0 * f32((n - 1) % 1000) + 1.0 { return 1; }
  return 0;
}
"""


def test_queued_device_work_is_ordered_by_tickets_and_overlaps_the_host(tmp_path):
    generated, receipt = compile_source(QUEUED)
    assert generated.count("cr::gpu::launch_async(") == 1 and "cr::gpu::Dir::d2h, v_scale)" in generated
    assert {"spawn", "join", "par:device", "transfer:h2d", "transfer:d2h"} <= set(
        receipt["functions"]["main"]["effects"]
    )
    assert compile_source(canonical_source(QUEUED))[0] == generated
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here")
    assert build_and_run(tmp_path, QUEUED, "g++")[0] == 0


DEVICE_HEAD = "fn main() -> i32 { let n:usize = 64; buffer a:f32[n]@pinned = zeroed; buffer x:f32[n]@device = zeroed;\n"


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-LEASED", "let up = spawn transfer(x, a); a[0] = 1.0; wait(up); return 0; }"),
        ("E-LEASED", "let up = spawn transfer(x, a); let k = spawn parallel i in n { x[i] = 1.0; }; wait(up); wait(k); return 0; }"),
        ("E-LEASED", "let u = spawn transfer(x, a); let j = spawn parallel i in n after u { x[i] = 1.0; };\n"
         "  let k = spawn parallel i in n after u { x[i] = 2.0; }; wait(u); wait(j); wait(k); return 0; }"),
        ("E-SPAWN", "let k = spawn parallel i in n { a[i] = 1.0; }; wait(k); return 0; }"),
        ("E-SPAWN", "let k = spawn parallel i in n { x[i] = 1.0; }; wait(k);\n"
         "  let j = spawn parallel i in n after k { x[i] = 2.0; }; wait(j); return 0; }"),
        ("E-SPAWN", "buffer b:f32[n]@pinned = zeroed; let t = spawn transfer(b, a); wait(t); return 0; }"),
        ("E-LINEAR-LEAK", "let k = spawn parallel i in n { x[i] = 1.0; }; return 0; }"),
        ("E-SPAWN", "let k = spawn parallel i in n { x[i] = 1.0; }; parallel i in n after k { x[i] = 2.0; } wait(k); return 0; }"),
    ],
)  # fmt: skip
def test_queued_work_holds_what_it_touches_and_only_device_work_is_queued(code, body):
    with pytest.raises(Diagnostic) as e:
        compile_source(DEVICE_HEAD + "  " + body)
    assert e.value.data["code"] == code, e.value.data["message"]


CHECKED_SUM = """
fn host_total(n:usize, xs:ro<u32>[n]) -> u32 { let s = reduce + for i in n yield xs[i]; return s; }
fn main() -> i32 {
  let n:usize = 100000;
  buffer h:u32[n] = zeroed;
  for i in 0..n { h[i] = u32(i % 7) * SCALE; }
  buffer d:u32[n]@device = zeroed;
  transfer(d, h);
  let on_device = reduce + for i in n yield d[i];          // checked: the host traps if the total does not fit
  let on_host = host_total(n, h);
  if on_device != on_host || on_host != 299995 * SCALE { return 1; }
  return 0;
}
"""


def test_unsigned_reduce_plus_is_checked_in_any_order_on_host_and_device(tmp_path):
    """No partial sum of naturals overflows unless the total does, so the trap cannot depend on the order."""
    fits, overflows = "const SCALE:u32 = 1;\n" + CHECKED_SUM, "const SCALE:u32 = 100000;\n" + CHECKED_SUM
    assert compile_source(fits)[1]["functions"]["host_total"]["syntactic_check_sites"]["overflow"] == 1
    signed = "fn f(n:usize, xs:ro<i64>[n]) -> i64 { let s = reduce + for i in n yield xs[i]; return s; }"
    product = "fn f(n:usize, xs:ro<u64>[n]) -> u64 { let s = reduce * for i in n yield xs[i]; return s; }"
    for source in (signed, product):  # A partial signed sum, or a product later multiplied by zero, may overflow alone.
        with pytest.raises(Diagnostic) as e:
            compile_source(source)
        assert e.value.data["code"] == "E-REDUCE-OP"
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here")
    for name, source, status in (("fits", fits, (0,)), ("overflows", overflows, (-6, 134))):
        (tmp_path / name).mkdir()
        assert build_and_run(tmp_path / name, source, "g++")[0] in status

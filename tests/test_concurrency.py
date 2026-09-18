"""Tasks with leases, atomics, mutexes, parallel regions on threads and CUDA lanes, closures.

Accepted programs run natively (and under ThreadSanitizer); every safety rule has a rejection.
"""

import shutil
import subprocess

import pytest

from cairn.cairnc import RUNTIME_FILES, Diagnostic, compile_source
from cairn.toolchain import command

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


def test_the_same_lane_body_runs_on_the_device(tmp_path):
    if not shutil.which("nvcc") or subprocess.run(["nvidia-smi"], capture_output=True).returncode != 0:
        pytest.skip("No CUDA toolkit or device here; the host path is covered above")
    code, receipt = build_and_run(tmp_path, DEVICE, "g++")
    assert code == 0 and receipt["requires"] == ["cuda"]
    effects = set(receipt["functions"]["main"]["effects"])
    assert {"par:device", "gpu_alloc", "gpu_free", "transfer:h2d", "transfer:d2h"} <= effects
    host = DEVICE.replace("@device", "")
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
        ("E-REDUCE-OP", "let s = reduce + for i in n yield out[i]; return 0;"),
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

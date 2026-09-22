"""I/O rings: kernel operations in flight without a thread each.

Submitting moves a `Buf[u8]` into the ring and `q.next(tag, result)` hands it back with the kernel's result, so the
kernel only ever touches storage the ring owns. The native tests run pipes and a lent ring under both compilers
with the sanitizers that bite, including ThreadSanitizer for a ring lent to a task; every rule has a rejection
naming its code. tests/native/io_runtime.cpp holds the runtime to the same promises from C++.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.verify.scalar_semantics import equivalent
from emitted import SANITIZED, WARNINGS, refused, run, sanitized, watched

PIPES = """
extern fn pipe(fds:rw<i32>[2]) -> i32 effects(io);
extern fn close(fd:i32) -> i32 effects(io);

fn unsafe_pipe(fds:rw<i32>[2]) -> i32 { unsafe { return pipe(fds); } }

fn pattern(n:usize, out:rw<u8>[n], seed:u8) { for i in 0..n { out[i] = add_wrap(seed, u8(i % 251)); } }
fn matches(n:usize, xs:ro<u8>[n], seed:u8) -> bool {
  for i in 0..n { if xs[i] != add_wrap(seed, u8(i % 251)) { return false; } }
  return true;
}

// A callee may be lent a ring: what it submits is moved in, not borrowed, so nothing outlives the call.
fn send(q:rw<IoRing>, fd:i32, data:Buf[u8], count:usize) { q.write(fd, data, count, 0, 2); }
"""

ROUND_TRIP = (
    PIPES
    + """
fn main() -> i32 {
  let mut fds = Array[i32, 2]();
  let made = unsafe_pipe(fds);
  if made != 0 { return 1; }
  let mut q = IoRing(4);
  defer wait(q);                                  // every exit lets the kernel finish first
  let into = Buf[u8](64);
  q.read(fds[0], into, 64, 0, 1);                 // waits in the kernel for bytes, not in a thread
  let mut out = Buf[u8](64);
  pattern(out, 5);
  send(q, fds[1], out, 64);
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  let first = q.next(tag, result);
  if tag != 2 || result != 64 || len(first) != 64 { return 2; }
  let second = q.next(tag, result);
  if tag != 1 || result != 64 || !matches(second, 5) { return 3; }
  q.read(99999, first, 8, 0, 3);                  // a bad descriptor: the kernel's -EBADF is a value
  let back = q.next(tag, result);
  if tag != 3 || result != -9 || len(back) != 64 { return 4; }
  unsafe { let a = close(fds[0]); let b = close(fds[1]); }
  return 0;
}
"""
)

TASK = (
    PIPES
    + """
fn pump(q:rw<IoRing>, fd:i32) -> i64 {
  let mut total:i64 = 0;
  for k in 0..8 {
    let mut out = Buf[u8](16);
    pattern(out, u8(k));
    send(q, fd, out, 16);
    let mut tag:u64 = 0;
    let mut result:i64 = 0;
    let back = q.next(tag, result);
    total = total + result;
  }
  return total;
}

fn main() -> i32 {
  let mut fds = Array[i32, 2]();
  let made = unsafe_pipe(fds);
  if made != 0 { return 1; }
  let mut q = IoRing(2);
  let t = spawn pump(q, fds[1]);                  // the ring is lent to the task until wait(t)
  let sent = wait(t);
  let into = Buf[u8](128);
  q.read(fds[0], into, 128, 0, 9);
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  let got = q.next(tag, result);
  wait(q);
  unsafe { let a = close(fds[0]); let b = close(fds[1]); }
  if sent != 128 || tag != 9 || result != 128 || !matches(got[0..16], 0) { return 2; }
  return 0;
}
"""
)


BOUNDED = """
extern fn socketpair(domain:i32, kind:i32, protocol:i32, fds:rw<i32>[2]) -> i32 effects(io);
extern fn close(fd:i32) -> i32 effects(io);

fn paired(fds:rw<i32>[2]) -> i32 { unsafe { return socketpair(1, 1, 0, fds); } }

fn main() -> i32 {
  let mut fds = Array[i32, 2]();
  let made = paired(fds);
  if made != 0 { return 1; }
  let mut q = IoRing(2);
  defer wait(q);
  let into = Buf[u8](16);
  q.recv(fds[1], into, 16, 1);                    // nobody writes: this would wait for ever
  q.timeout(20000000, 2);                         // twenty milliseconds
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  let nothing = q.next(tag, result);
  if tag != 2 || result != -62 { return 2; }      // -ETIME: the bound, reported as a value
  q.cancel(1);
  let back = q.next(tag, result);
  if tag != 1 || result != -125 || len(back) != 16 { return 3; }   // -ECANCELED, and the Buf is back
  unsafe { let a = close(fds[0]); let b = close(fds[1]); }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_timeout_bounds_a_wait_and_a_cancel_returns_the_buffer(tmp_path, cxx):
    done = run(tmp_path, compile_source(BOUNDED)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_ring_is_declared_with_its_costs():
    rows = compile_source(ROUND_TRIP)[1]["functions"]
    assert {"alloc", "free", "io", "trap"} <= set(rows["main"]["effects"])
    assert "io" in rows["send"]["effects"] and "alloc" not in rows["send"]["effects"]  # Submitting never allocates.
    cpp = compile_source(ROUND_TRIP)[0]
    assert '#include "cairn_io.hpp"' in cpp and "cairn_parallel.hpp" not in cpp  # No thread, no pool.
    assert compile_source(canonical_source(ROUND_TRIP))[0] == cpp


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_operations_finish_in_their_own_order_and_hand_their_buffers_back(tmp_path, cxx):
    done = run(tmp_path, compile_source(ROUND_TRIP)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("sanitizer", ["thread", "address,undefined"])
def test_a_ring_lent_to_a_task_is_used_by_one_thread_at_a_time(tmp_path, cxx, sanitizer):
    ran = watched(tmp_path, compile_source(TASK)[0], cxx, sanitizer)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Sanitizer" not in ran.stderr and "runtime error" not in ran.stderr, ran.stderr


def test_a_full_ring_traps(tmp_path):
    source = PIPES + (
        "fn main() -> i32 { let mut fds = Array[i32, 2](); unsafe { let p = pipe(fds); } let q = IoRing(1);"
        " defer wait(q); let a = Buf[u8](1); q.read(fds[0], a, 1, 0, 1); let b = Buf[u8](1);"
        " q.read(fds[0], b, 1, 0, 2); return 0; }"
    )
    assert run(tmp_path, compile_source(source)[0], *SANITIZED).returncode == -6


BODY = "fn main() -> i32 { let q = IoRing(2); let data = Buf[u8](8); "


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-MOVED", BODY + "q.read(0, data, 8, 0, 1); let n = len(data); wait(q); return 0; }"),  # the ring owns it
        ("E-LINEAR-LEAK", BODY + "q.read(0, data, 8, 0, 1); return 0; }"),  # never waited
        ("E-PINNED", "fn keep(q:IoRing) {}"),
        ("E-PINNED", "struct Hold { q:IoRing; }"),
        ("E-MOVE-BORROW", "fn done(q:rw<IoRing>) { wait(q); }"),  # a callee it was lent cannot consume it
        ("E-WRITE-LEASE", "fn peek(q:ro<IoRing>, data:Buf[u8]) { q.read(0, data, 8, 0, 1); }"),
        ("E-TYPE-MISMATCH", BODY + "q.read(0, data, 8, 0, 1u); wait(q); return 0; }".replace("1u", "true")),
        ("E-CALLEE", BODY + "q.seek(0); wait(q); return 0; }"),
        ("E-ARITY", BODY + "q.cancel(1, 2); wait(q); return 0; }"),
        ("E-ARITY", BODY + "q.read(0, data, 8, 1); wait(q); return 0; }"),
        ("E-ARITY", "fn main() -> i32 { let q = IoRing(); wait(q); return 0; }"),
        (
            "E-LEASED",
            "fn use(q:rw<IoRing>) -> u64 = 0;\n"
            "fn main() -> i32 { let mut q = IoRing(2); let t = spawn use(q); let d = Buf[u8](8);"
            " q.read(0, d, 8, 0, 1); let r = wait(t); wait(q); return 0; }",
        ),
    ],
)
def test_what_a_ring_refuses(code, source):
    refused(code, source)


def test_a_ring_stays_on_the_host():
    refused(
        "E-PARALLEL-CALL", "fn go(n:usize, xs:rw<u8>[n]) { parallel i in n { let q = IoRing(1); wait(q); xs[i] = 0; } }"
    )
    refused(
        "E-PLACEMENT", "kernel fn k(n:usize, xs:ro<u8>[n]@device) -> u8 { let q = IoRing(1); wait(q); return xs[0]; }"
    )


def test_the_value_model_leaves_a_ring_unknown():
    source = "fn f(fd:i32) -> u64 { let q = IoRing(1); let d = Buf[u8](8); q.read(fd, d, 8, 0, 1); wait(q); return 0; }"
    assert equivalent(source, source, "f")["status"] == "unknown"

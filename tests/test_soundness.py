"""Regression tests for every hole an adversarial audit found in the 1.0 checker.

Each program below was once accepted and then shown unsound under a sanitizer, shown to
under-report effects, or shown to emit invalid C++. They must stay rejected (or fixed).
"""

import shutil
import subprocess

import pytest

from cairn.cairnc import RUNTIME_FILES, Diagnostic, compile_source

FILL = "fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }\n"
TOKEN = (
    "linear struct Token { id:u64; }\nfn open(id:u64) -> Token = Token(id);\n"
    "fn close(t:Token, c:rw<u64>) { c = add_wrap(c, 1); }\n"
)

APPLY = "fn apply(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }\n"
MAP = "fn map(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }\n"
COUNTER = (
    "trait Counter { fn tick(self:rw<Self>, by:u64) -> u64; }\nstruct C { n:u64; }\n"
    "impl Counter for C { fn tick(self:rw<C>, by:u64) -> u64 { self.n = add_wrap(self.n, by); return self.n; } }\n"
)

REJECTED = {
    "a by-value move of an owner beside a view of it (use after free)": (
        "E-ALIAS",
        "fn drop(b:Buf[u64]) {}\n"
        "fn f(n:usize, xs:rw<u64>[n], owned:Buf[u64]) -> u64 { drop(owned); xs[0] = 5; return xs[0]; }\n"
        "fn main() -> i32 { let mut b = Buf[u64](4); let r = f(len(b), b, b); return i32(r); }",
    ),
    "the extent of one array element vouching for another (buffer overflow)": (
        "E-TYPE-MISMATCH",
        "fn sum(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, xs[i]); } return t; }\n"
        "fn main() -> i32 { let mut g = Array[Buf[u64], 2](); g[0] = Buf[u64](2); g[1] = Buf[u64](64);\n"
        "  let r = sum(len(g[1]), g[0]); return i32(r); }",
    ),
    "defer wait(t) releasing the lease early (data race)": (
        "E-LEASED",
        FILL + "fn main() -> i32 { let n:usize = 8; let mut data = Buf[u64](n);\n"
        "  let t = spawn fill(len(data), data, 0); defer wait(t); data[0] = 99; return 0; }",
    ),
    "reading a leased scalar by name (data race)": (
        "E-LEASED",
        "fn bump(c:rw<u64>, n:usize) { for i in 0..n { c = add_wrap(c, 1); } }\n"
        "fn main() -> i32 { let mut c:u64 = 0; let t = spawn bump(c, 4); let seen = c; wait(t); return i32(seen); }",
    ),
    "reading a field of a leased record (data race)": (
        "E-LEASED",
        "struct S { hits:u64; }\nfn bump(s:rw<S>) { s.hits = 1; }\n"
        "fn main() -> i32 { let mut st = S(0); let t = spawn bump(st); let h = st.hits; wait(t); return i32(h); }",
    ),
    "a mutable part boundary changed between two spawns (data race)": (
        "E-LEASED",
        FILL + "fn main() -> i32 { let n:usize = 8; let mut mid:usize = 4; let mut data = Buf[u64](n);\n"
        "  let a = spawn fill(mid, data[0..mid], 0); mid = 0; let b = spawn fill(n, data[mid..n], 7);\n"
        "  wait(a); wait(b); return 0; }",
    ),
    "a ro dynamic reference calling a member that writes its receiver": (
        "E-WRITE-LEASE",
        "trait Counter { fn tick(self:rw<Self>, by:u64) -> u64; }\nstruct C { n:u64; }\n"
        "impl Counter for C { fn tick(self:rw<C>, by:u64) -> u64 { self.n = add_wrap(self.n, by); return self.n; } }\n"
        "fn sneak(s:ro<dyn Counter>) -> u64 = tick(s, 90);",
    ),
    "linear values minted from zeroed storage": (
        "E-LINEAR-STORAGE",
        TOKEN + "fn main() -> i32 { let mut c:u64 = 0; let mut v = Buf[Token](1);\n"
        "  let a = take(v[0]); let b = take(v[0]); close(a, c); close(b, c); return i32(c); }",
    ),
    "a deferred consumption forgotten after its block": (
        "E-MOVED",
        TOKEN
        + "fn main() -> i32 { let mut c:u64 = 0; let t = open(1); { defer close(t, c); } close(t, c); return i32(c); }",
    ),
    "a declared ceiling hiding what a stored function value may do": (
        "E-EFFECT-CEILING",
        "extern fn getpid() -> i32 effects(io);\n"
        "fn side(x:u64) -> u64 { unsafe { return add_wrap(x, u64(getpid())); } }\nstruct Op { run:fn(u64) -> u64; }\n"
        "fn run(op:Op) -> u64 effects(indirect_call, trap) { let r = op.run; return r(1); }\n"
        "fn main() -> i32 { let o = Op(side); let v = run(o); return i32(v); }",
    ),
    "two closures writing one local inside one expression": (
        "E-EFFECT-ORDER",
        "fn once(f:ro<fn(u64) -> u64>) -> u64 = f(0);\nfn main() -> i32 { let mut c:u64 = 0;\n"
        "  let r = once(|x:u64| -> u64 { c = add_wrap(c, 1); return c; }) * 10\n"
        "        + once(|x:u64| -> u64 { c = add_wrap(c, 1); return c; }); return i32(r); }",
    ),
    "a closure writing a local that a sibling operand reads": (
        "E-EFFECT-ORDER",
        "fn once(f:ro<fn(u64) -> u64>) -> u64 = f(0);\nfn main() -> i32 { let mut c:u64 = 0;\n"
        "  let r = once(|x:u64| -> u64 { c = add_wrap(c, 1); return c; }) * 10 + c; return i32(r); }",
    ),
    "a generic instance sharing a C symbol with a declared function": (
        "E-MANGLE",
        "fn largest[T](a:T, b:T) -> T { if a < b { return b; } return a; }\nfn largest_u64(a:u64, b:u64) -> u64 = a;\n"
        "fn main() -> i32 { return i32(add_wrap(largest(1, 2), largest_u64(3, 4))); }",
    ),
    "method sugar reaching a private function of the receiver's module": (
        "E-PRIVATE",
        "module lib;\npub struct Key { v:u64; }\nfn secret(k:ro<Key>) -> u64 = k.v;\n"
        "module app;\nimport lib;\npub fn main() -> i32 { let k = lib.Key(7); let s = k.secret(); return i32(s); }",
    ),
    # Round two ----------------------------------------------------------------------------------
    "a closure freeing the buffer its own call has lent (use after free)": (
        "E-ALIAS",
        APPLY + "fn main() -> i32 { let mut b = Buf[u64](4);\n"
        "  apply(len(b), b, |x:u64| -> u64 { b = Buf[u64](8); return x; }); return 0; }",
    ),
    "a closure reading what its own call writes through a view": (
        "E-ALIAS",
        APPLY
        + "fn main() -> i32 { let mut b = Buf[u64](4); apply(len(b), b, |x:u64| -> u64 { return x + b[0]; }); return 0; }",
    ),
    "a closure reading an owner the same call moves away": (
        "E-ALIAS",
        "fn both(f:ro<fn(u64) -> u64>, b:Buf[u64]) -> u64 = f(0);\n"
        "fn main() -> i32 { let mut b = Buf[u64](4); let r = both(|x:u64| -> u64 { return u64(len(b)); }, b); return i32(r); }",
    ),
    "a closure that writes what it captured, called from every lane (data race)": (
        "E-PARALLEL-CALL",
        MAP + "fn main() -> i32 { let n:usize = 64; buffer o:u64[n] = zeroed; let mut c:u64 = 0;\n"
        "  map(n, o, |x:u64| -> u64 { c = c + 1; return c; }); return 0; }",
    ),
    "a lane-called closure handed on by a function that never wrote it": (
        "E-PARALLEL-CALL",
        MAP + "fn relay(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { map(n, out, f); }\n"
        "fn main() -> i32 { let n:usize = 64; buffer o:u64[n] = zeroed; let mut c:u64 = 0;\n"
        "  relay(n, o, |x:u64| -> u64 { c = c + 1; return c; }); return 0; }",
    ),
    "a lane calling a function value nobody answers for": (
        "E-PARALLEL-CALL",
        "struct Op { run:fn(u64) -> u64; }\n"
        "fn go(n:usize, out:rw<u64>[n], op:Op) { let run = op.run; parallel i in n { out[i] = run(u64(i)); } }",
    ),
    "a closure following a task to another thread through a parameter (data race)": (
        "E-SPAWN",
        "fn worker(f:ro<fn(u64) -> u64>) -> u64 = f(1);\n"
        "fn run(f:ro<fn(u64) -> u64>) -> u64 { let t = spawn worker(f); let a = f(2); return a + wait(t); }",
    ),
    "every lane writing through one rw dynamic reference (data race)": (
        "E-PARALLEL-RACE",
        COUNTER + "fn go(n:usize, out:rw<u64>[n], c:rw<dyn Counter>) { parallel i in n { out[i] = tick(c, 1); } }",
    ),
    "a lane dispatching to an implementation that performs I/O": (
        "E-PARALLEL-CALL",
        "extern fn getpid() -> i32 effects(io);\ntrait P { fn pid(self:ro<Self>) -> u64; }\nstruct C { n:u64; }\n"
        "impl P for C { fn pid(self:ro<C>) -> u64 { unsafe { return u64(getpid()); } } }\n"
        "fn go(n:usize, out:rw<u64>[n], c:ro<dyn P>) { parallel i in n { out[i] = pid(c); } }",
    ),
    "a lane touching the machine in its own body": (
        "E-PARALLEL-CALL",
        "fn go(n:usize, out:rw<u32>[n]) { parallel i in n { unsafe { out[i] = mmio_read[u32](4096); } } }",
    ),
    "a device lane allocating on the host heap": (
        "E-PARALLEL-CALL",
        "fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { let mut b = Buf[u64](4); b[0] = u64(i); d[i] = b[0]; } }",
    ),
    "a device lane reading a string literal, which is host memory": (
        "E-PLACEMENT",
        'fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { let s = "AB"; d[i] = u64(s[0]); } }',
    ),
    "a kernel reading a string literal": ("E-PLACEMENT", 'kernel fn peek() -> u64 { let s = "AB"; return u64(s[0]); }'),
    "a device lane naming a host owner": (
        "E-PLACEMENT",
        "fn f(n:usize, d:rw<u64>[n]@device) { let mut b = Buf[u64](4); parallel i in n { d[i] = u64(len(b)); } }",
    ),
    "a device compaction whose predicate performs host I/O": (
        "E-PARALLEL-CALL",
        "extern fn getpid() -> i32 effects(io);\n"
        "fn sneaky(v:u64) -> bool { unsafe { let p = getpid(); } return (v & 1) == 0; }\n"
        "fn f(n:usize, d:rw<u64>[n]@device) { let used = compact d for i in n where sneaky(u64(i)) yield u64(i); }",
    ),
    "a device compaction whose predicate reads host memory": (
        "E-PLACEMENT",
        "fn f(n:usize, d:rw<u64>[n]@device) { let mut h = Buf[u64](n);\n"
        "  let used = compact d for i in n where h[i] == 0 yield u64(i); }",
    ),
    "transfer into a buffer a task still writes (data race)": (
        "E-LEASED",
        FILL + "fn main() -> i32 { let n:usize = 8; buffer a:u64[n] = zeroed; buffer b:u64[n] = zeroed;\n"
        "  let t = spawn fill(n, a, 1); transfer(a, b); wait(t); return 0; }",
    ),
    "transfer between overlapping parts of one array": (
        "E-ALIAS",
        "fn main() -> i32 { let n:usize = 8; buffer a:u64[n] = zeroed; transfer(a[0..4], a[2..6]); return 0; }",
    ),
    "compaction into a buffer a task still writes (data race)": (
        "E-LEASED",
        FILL + "fn f(n:usize, out:rw<u64>[n], xs:ro<u64>[n]) -> usize { let t = spawn fill(n, out, 1);\n"
        "  let used = compact out for i in n where xs[i] > 0 yield xs[i]; wait(t); return used; }",
    ),
    "dispatch through a reference a task still holds (data race)": (
        "E-LEASED",
        COUNTER + "fn spin(c:rw<dyn Counter>) { let r = tick(c, 1); }\n"
        "fn two(c:rw<dyn Counter>) -> u64 { let t = spawn spin(c); let r = tick(c, 1); wait(t); return r; }",
    ),
    "locking a mutex again inside its own critical section": (
        "E-ALIAS",
        "fn main() -> i32 { let m = Mutex[u64](0); m.with(|s:rw<u64>| { m.with(|t:rw<u64>| { t = 1; }); }); return 0; }",
    ),
    "a lane inside a closure returning from that closure": (
        "E-PARALLEL-CONTROL",
        "fn once(f:ro<fn(u64) -> u64>) -> u64 = f(0);\n"
        "fn main() -> i32 { let n:usize = 4; buffer o:u64[n] = zeroed;\n"
        "  let r = once(|x:u64| -> u64 { parallel i in n { return 1; } return 0; }); return 0; }",
    ),
}


@pytest.mark.parametrize("name", REJECTED)
def test_the_hole_stays_closed(name):
    code, source = REJECTED[name]
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code, e.value.data["message"]


def run(tmp_path, source, flags=()):
    (tmp_path / "p.cpp").write_text(compile_source(source)[0] + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    build = ["clang++", "-std=c++20", "-O1", "-g", "-fno-exceptions", *flags, str(tmp_path / "p.cpp"), "-o"]
    subprocess.run([*build, str(tmp_path / "p")], check=True, timeout=120)
    return subprocess.run([tmp_path / "p"], capture_output=True, timeout=60)


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_zeroed_function_value_traps_instead_of_jumping_to_null(tmp_path):
    source = (
        "struct Op { run:fn(u64) -> u64; }\n"
        "fn go(n:usize, ops:ro<Op>[n], x:u64) -> u64 { let r = ops[0].run; return r(x); }\n"
        "fn main() -> i32 { let n:usize = 1; let mut table = Buf[Op](n); let v = go(len(table), table, 7); return i32(v); }"
    )
    assert compile_source(source)[1]["functions"]["go"]["syntactic_check_sites"]["callable"] == 1
    assert run(tmp_path, source).returncode == -6  # SIGABRT from the guard, not SIGSEGV from the jump.


def test_an_indirect_call_charges_its_borrows_and_checks_aliasing():
    source = (
        "fn twiddle(f:ro<fn(rw<u64>) -> void>, v:rw<u64>) { f(v); }\n"
        "fn main() -> i32 { let mut x:u64 = 0; twiddle(|s:rw<u64>| { s = add_wrap(s, 1); }, x); return i32(x); }"
    )
    rows = compile_source(source)[1]["functions"]
    assert "write:v" in rows["twiddle"]["effects"] and "local_write" in rows["main"]["effects"]
    both = "fn pair(f:ro<fn(rw<u64>, rw<u64>) -> void>, v:rw<u64>) { f(v, v); }"
    with pytest.raises(Diagnostic) as e:
        compile_source(both)
    assert e.value.data["code"] == "E-ALIAS"


def test_sequenced_and_single_opaque_operands_remain_legal():
    """The audit must not outlaw ordinary code: `&&` is sequenced, and one opaque call may read immutables."""
    source = (
        "fn search(n:usize, xs:ro<u64>[n], key:u64, less:ro<fn(u64, u64) -> bool>) -> usize {\n"
        "  let mut i:usize = 0;\n  while i < n && less(xs[i], key) { i = i + 1; }\n"
        "  if !less(key, key) && i < n { return i; }\n  return n;\n}"
    )
    assert compile_source(source)


def test_visibly_disjoint_parts_with_stable_bounds_are_still_lent_together():
    source = FILL + (
        "fn main() -> i32 { let n:usize = 8; let mid:usize = 4; let mut data = Buf[u64](n);\n"
        "  let a = spawn fill(mid, data[0..mid], 0); let b = spawn fill(n - mid, data[mid..n], 7); wait(a); wait(b); return 0; }"
    )
    assert compile_source(source)


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_lanes_may_call_a_closure_that_writes_nothing_it_captured(tmp_path):
    """The parallel map stays: pure closures, declared functions, atomics and relays of a parameter."""
    source = MAP + (
        "fn twice(x:u64) -> u64 = x * 2;\n"
        "fn relay(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { map(n, out, f); }\n"
        "fn main() -> i32 { let n:usize = 4096; let k:u64 = 3; buffer o:u64[n] = zeroed; let hits = Atomic[u64](0);\n"
        "  relay(n, o, |x:u64| -> u64 { let seen = hits.fetch_add(1, Order.relaxed); return x * k; });\n"
        "  if o[5] != 15 || hits.load(Order.seq_cst) != 4096 { return 1; }\n"
        "  map(n, o, twice);\n  if o[5] != 10 { return 2; }\n  return 0; }"
    )
    rows = compile_source(source)[1]["functions"]
    assert "lane:f" in rows["map"]["effects"] and "lane:f" in rows["relay"]["effects"]
    assert run(tmp_path, source, ["-fsanitize=thread", "-pthread"]).returncode == 0


def test_a_closure_may_read_what_its_call_only_reads_and_write_its_own_captures():
    source = (
        "fn total(n:usize, xs:ro<u64>[n], f:ro<fn(u64) -> u64>) -> u64 {\n"
        "  let mut t:u64 = 0; for i in 0..n { t = t + f(xs[i]); } return t; }\n"
        "fn main() -> i32 { let mut b = Buf[u64](4); let mut calls:u64 = 0;\n"
        "  let r = total(len(b), b, |x:u64| -> u64 { calls = calls + 1; return x + b[0]; }); return i32(r + calls); }"
    )
    assert compile_source(source)


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_parts_of_unequal_length_trap_in_transfer_and_equal_ones_copy(tmp_path):
    body = (
        "fn main() -> i32 {{ let n:usize = 8; let k:usize = {k}; buffer a:u64[n] = zeroed; buffer b:u64[n] = zeroed;\n"
        "  for i in 0..n {{ b[i] = u64(i) + 1; }}\n  transfer(a[0..k], b[2..6]);\n  return i32(a[0] + a[3]); }}"
    )
    assert run(tmp_path, body.format(k=4), ["-fsanitize=address,undefined"]).returncode == 3 + 6
    assert run(tmp_path, body.format(k=8), ["-fsanitize=address,undefined"]).returncode == -6


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_mutex_reached_twice_through_two_borrows_traps_instead_of_relocking(tmp_path):
    source = (
        "fn both(a:ro<Mutex[u64]>, b:ro<Mutex[u64]>) { a.with(|s:rw<u64>| { b.with(|t:rw<u64>| { t = 1; }); }); }\n"
        "fn main() -> i32 { let m = Mutex[u64](0); both(m, m); return 0; }"
    )
    assert run(tmp_path, source, ["-pthread"]).returncode == -6

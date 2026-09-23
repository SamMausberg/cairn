"""The adversarial review of the forms added after 0.8.3: what it tried, refused with the code it must keep.

Each program below was written to break a rule with one of the new forms (bare variants, one-statement arms,
compound assignment, call statements and `let _`, element loops), or through what they meet: guard elision and
the entry that checks views once, the recoverable ring, map Slots, test blocks, storage floats, derived gradients.
All of them were refused. The behaviour tests below them are the attacks that compile: each must stop at a guard
or run race free, under both compilers and the sanitizer that bites. evidence/v1_0/review/ says what was attacked.
"""

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import refused, watched

OPT = "import std.core (Option, Result);\n"
FILL = "fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }\n"
TWO = "fn two(n:usize, a:rw<u64>[n], b:rw<u64>[n]) { for i in 0..n { a[i] = b[i] + 1; b[i] = 7; } }\n"
COPY = "fn copy(n:usize, dst:rw<u64>[n], src:ro<u64>[n]) { for i in 0..n { dst[i] = src[i] + 1; } }\n"
TOKEN = "linear struct Token { id:u64; }\nfn open(id:u64) -> Token = Token(id);\n"
CHART = "struct C { rows:usize; price:Buf[u64][rows]; }\n"
RECORD = "struct S { items:Buf[u64]; }\n"
CALL = "fn call(f:ro<fn()>) { f(); }\n"

REFUSED = {
    # call statements and let _
    "a task started by a bare statement": (
        "E-DISCARD",
        FILL + "fn main() -> i32 { let mut d = Buf[u64](64); spawn fill(len(d), d, 0); d[0] = 9; return 0; }",
    ),
    "a task let go with let _": (
        "E-SPAWN",
        FILL + "fn main() -> i32 { let mut d = Buf[u64](64); let _ = spawn fill(len(d), d, 0); d[0] = 9; return 0; }",
    ),
    "a linear value dropped by a call statement": ("E-LINEAR-LEAK", TOKEN + "fn main() -> i32 { open(1); return 0; }"),
    "a linear value let go with let _": ("E-LINEAR-LEAK", TOKEN + "fn main() -> i32 { let _ = open(1); return 0; }"),
    "a linear value inside an Option let go": (
        "E-LINEAR-LEAK",
        OPT + TOKEN + "fn maybe() -> Option[Token] = Some(open(1));\nfn main() -> i32 { let _ = maybe(); return 0; }",
    ),
    "a linear success dropped by try": (
        "E-LINEAR-LEAK",
        OPT
        + TOKEN
        + "fn get() -> Result[Token, u8] = Ok(open(1));\nfn f() -> Result[u8, u8] { try get(); return Ok(0); }",
    ),
    "a group dropped by a call statement": ("E-LINEAR-LEAK", "fn main() -> i32 { Group[u64](4); return 0; }"),
    "a leased owner let go": (
        "E-LEASED",
        FILL
        + "fn main() -> i32 { let mut d = Buf[u64](64); let t = spawn fill(len(d), d, 0); let _ = d; wait(t); return 0; }",
    ),
    "reading the name _": ("E-UNBOUND", "fn main() -> i32 { let _ = 3; return i32(_); }"),
    # compound assignment
    "+= on a leased element": (
        "E-LEASED",
        FILL
        + "fn main() -> i32 { let mut d = Buf[u64](64); let t = spawn fill(len(d), d, 0); d[0] += 1; wait(t); return 0; }",
    ),
    "+= on a leased scalar": (
        "E-LEASED",
        "fn bump(c:rw<u64>) { c += 1; }\nfn main() -> i32 { let mut c:u64 = 0; let t = spawn bump(c); c += 1; wait(t); return 0; }",
    ),
    "+= on a moved owner": (
        "E-MOVED",
        "fn sink(b:Buf[u64]) {}\nfn main() -> i32 { let mut b = Buf[u64](4); sink(b); b[0] += 1; return 0; }",
    ),
    "+= on a declared extent field": (
        "E-EXTENT-FIELD",
        CHART + "fn main() -> i32 { let mut c = C(4, Buf[u64](4)); c.rows += 100; return 0; }",
    ),
    "+= on a static string": ("E-WRITE-LEASE", 'fn main() -> i32 { let s = "abc"; s[0] += 1; return 0; }'),
    "+= on a storage float": ("E-OPERATOR", "fn main() -> i32 { let mut h = f16(1.0); h += f16(1.0); return 0; }"),
    "+= by every lane on one element": (
        "E-PARALLEL-RACE",
        "fn f(n:usize, out:rw<u64>[n]) { parallel i in n { out[0] += 1; } }",
    ),
    "+= by every lane on a captured scalar": (
        "E-PARALLEL-WRITE",
        "fn f(n:usize, out:rw<u64>[n]) { let mut acc:u64 = 0; parallel i in n { acc += 1; out[i] = 1; } }",
    ),
    "+= whose index writes": (
        "E-EFFECT-ORDER",
        "fn bump(c:rw<usize>) -> usize { c += 1; return 0; }\nfn main() -> i32 { let mut c:usize = 0; let mut b = Buf[u64](4); b[bump(c)] += 1; return 0; }",
    ),
    "+= across two borrows of one scalar": (
        "E-ALIAS",
        "fn f(a:rw<u64>, b:ro<u64>) { a += b; a += b; }\nfn main() -> i32 { let mut x:u64 = 1; f(x, x); return i32(x); }",
    ),
    # element loops
    "an element loop over a leased owner": (
        "E-LEASED",
        FILL
        + "fn main() -> i32 { let mut d = Buf[u64](64); let t = spawn fill(len(d), d, 0); let mut s:u64 = 0; for x in d { s += x; } wait(t); return 0; }",
    ),
    "an element loop over device memory on the host": (
        "E-PLACEMENT",
        "fn f(n:usize, xs:ro<u64>[n]@device) -> u64 { let mut t:u64 = 0; for x in xs { t += x; } return t; }",
    ),
    "an element loop in a lane over what lanes write": (
        "E-PARALLEL-RACE",
        "fn f(n:usize, out:rw<u64>[n]) { parallel i in n { let mut t:u64 = 0; for x in out { t += x; } out[i] = t; } }",
    ),
    "an element loop that moves its collection": (
        "E-MOVE-IN-LOOP",
        "fn sink(b:Buf[u64]) {}\nfn main() -> i32 { let v = Buf[u64](8); for x in v { sink(v); } return 0; }",
    ),
    "an element loop over an array element": (
        "E-ELEMENT-LOOP",
        "fn main() -> i32 { let mut g = Array[Buf[u64], 2](); g[0] = Buf[u64](2); for x in g[0] { } return 0; }",
    ),
    # bare variants and one-statement arms
    "a bare variant beside a function of its name": (
        "E-VARIANT-AMBIGUOUS",
        OPT + "fn Some(x:u64) -> u64 = x;\nfn f() -> Option[u64] = Some(1);",
    ),
    "a bare variant beside a local of its name": (
        "E-VARIANT-AMBIGUOUS",
        OPT + "fn f() -> Option[u64] { let None:u64 = 1; return None; }",
    ),
    "a bare variant beside a constant of its name": (
        "E-VARIANT-AMBIGUOUS",
        OPT + "const None:u64 = 1;\nfn f() -> Option[u64] = None;",
    ),
    "a one-statement arm that breaks out of a lane": (
        "E-LOOP-CONTROL",
        OPT
        + "fn f(n:usize, out:rw<u64>[n], o:Option[u64]) { parallel i in n { match o { Some(v) => break; None => out[i] = 1; } } }",
    ),
    "a one-statement arm that returns from a lane": (
        "E-PARALLEL-CONTROL",
        OPT
        + "fn f(n:usize, out:rw<u64>[n], o:Option[u64]) { parallel i in n { match o { Some(v) => return; None => out[i] = 1; } } }",
    ),
    "an arm that moves its payload twice": (
        "E-MOVED",
        OPT
        + "fn sink(b:Buf[u8]) {}\nfn f(o:Option[Buf[u8]]) { match o { Some(v) => { sink(v); sink(v); } None => return; } }",
    ),
    # closures that alias what a call was lent, the case the lean body relies on
    "a closure replacing the owner behind an ro field": (
        "E-ALIAS",
        RECORD
        + "fn walk(s:ro<S>, f:ro<fn()>) -> u64 { let mut t:u64 = 0; for x in s.items { f(); t += x; } return t; }\nfn main() -> i32 { let mut r = S(Buf[u64](8)); let t = walk(r, || { r.items = Buf[u64](1); }); return i32(t); }",
    ),
    "a closure replacing the owner behind an ro view": (
        "E-ALIAS",
        "fn walk(n:usize, xs:ro<u64>[n], f:ro<fn()>) -> u64 { let mut t:u64 = 0; for i in 0..n { f(); t += xs[i]; } return t; }\nfn main() -> i32 { let mut b = Buf[u64](8); let t = walk(len(b), b, || { b = Buf[u64](1); }); return i32(t); }",
    ),
    "a closure writing the element behind an ro view": (
        "E-ALIAS",
        "fn walk(n:usize, xs:ro<u64>[n], f:ro<fn()>) -> u64 { let a = xs[0]; f(); return a + xs[0]; }\nfn main() -> i32 { let mut b = Buf[u64](8); let t = walk(len(b), b, || { b[0] = 5; }); return i32(t); }",
    ),
    "a closure reading a leased owner": (
        "E-LEASED",
        "fn run(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }\nfn read(f:ro<fn() -> u64>) -> u64 = f();\nfn main() -> i32 { let mut b = Buf[u64](64); let t = spawn run(len(b), b); let v = read(|| -> u64 { return b[0]; }); wait(t); return i32(v); }",
    ),
    "a mutating closure called by lanes": (
        "E-PARALLEL-CALL",
        "fn every(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }\nfn main() -> i32 { let mut t:u64 = 0; buffer o:u64[64] = zeroed; every(64, o, |x:u64| -> u64 { t += 1; return x; }); return 0; }",
    ),
    "an update closure that reaches its map": (
        "E-ALIAS",
        "import std.map (Map);\nfn main() -> i32 { let mut m = map.new[u64, u64](); m.insert(1, 2); let ok = m.update(1, |v:rw<u64>| { m.insert(3, 4); v = 7; }); return 0; }",
    ),
    # the static alias rule, now that a call from CAIRN skips the entry's numeric overlap check
    "two elements of an array of owners, one symbolic": (
        "E-ALIAS",
        TWO
        + "fn main() -> i32 { let mut g = Array[Buf[u64], 2](); g[0] = Buf[u64](4); let k:usize = 0; two(4, g[0][0..4], g[k][0..4]); return 0; }",
    ),
    "two symbolic parts of one array": (
        "E-ALIAS",
        TWO
        + "fn main() -> i32 { let mut b = Buf[u64](16); let i:usize = 2; let j:usize = 2; two(4, b[i..i + 4], b[j..j + 4]); return 0; }",
    ),
    "an rw part over an ro part": (
        "E-ALIAS",
        COPY + "fn main() -> i32 { let mut b = Buf[u64](8); copy(4, b[0..4], b[2..6]); return 0; }",
    ),
    "parts of one part that overlap": (
        "E-ALIAS",
        TWO + "fn main() -> i32 { let mut b = Buf[u64](8); two(2, b[0..4][0..2], b[0..4][1..3]); return 0; }",
    ),
    "a view beside the record that owns it": (
        "E-ALIAS",
        RECORD
        + "fn g(n:usize, v:ro<u64>[n], r:rw<S>) -> u64 { r.items = Buf[u64](1); return v[3]; }\nfn main() -> i32 { let mut x = S(Buf[u64](4)); let t = g(4, x.items[0..4], x); return i32(t); }",
    ),
    "a Vec's elements beside the Vec": (
        "E-ALIAS",
        "import std.vec (Vec);\nfn g(n:usize, v:ro<u64>[n], w:rw<Vec[u64]>) -> u64 { w.push(1); w.push(2); return v[0]; }\nfn main() -> i32 { let mut x = vec.with_capacity[u64](1); x.push(9); let t = g(1, x.data[0..1], x); return i32(t); }",
    ),
    "two tasks lent one part": (
        "E-LEASED",
        TWO
        + "fn main() -> i32 { let mut b = Buf[u64](8); let t = spawn two(4, b[0..4], b[4..8]); let u = spawn two(4, b[0..4], b[4..8]); wait(t); wait(u); return 0; }",
    ),
    # the ring
    "a field's Buf submitted without take": (
        "E-PARTIAL-MOVE",
        "struct R { b:Buf[u8]; }\nfn main() -> i32 { let mut q = IoRing(2); defer wait(q); let mut r = R(Buf[u8](8)); q.read(0, r.b, 8, 0, 1); return 0; }",
    ),
    "a ring queried while a task holds it": (
        "E-LEASED",
        "fn serve(q:rw<IoRing>) { q.timeout(1000, 1); }\nfn main() -> i32 { let mut q = IoRing(2); let t = spawn serve(q); let r = q.room(); wait(t); wait(q); return i32(r); }",
    ),
    "a ring waited while a task holds it": (
        "E-LEASED",
        "fn serve(q:rw<IoRing>) { q.timeout(1000, 1); }\nfn main() -> i32 { let mut q = IoRing(2); let t = spawn serve(q); wait(q); wait(t); return 0; }",
    ),
    "a lane that submits to a ring": (
        "E-PARALLEL-CALL",
        "fn f(n:usize, out:rw<u64>[n], q:rw<IoRing>) { parallel i in n { q.timeout(1000, u64(i)); out[i] = 1; } }",
    ),
    "a device lane that asks a ring": (
        "E-PLACEMENT",
        "fn f(n:usize, out:rw<u64>[n]@device, q:rw<IoRing>) { parallel i in n { out[i] = u64(q.room()); } }",
    ),
    # test blocks, storage floats and gradients
    "a test block that drops a ticket": (
        "E-LINEAR-LEAK",
        FILL + "test t { let mut b = Buf[u64](4); let k = spawn fill(len(b), b, 0); }",
    ),
    "a test called like a function": ("E-CALLEE", "test t { }\nfn main() -> i32 { t(); return 0; }"),
    "an assert message that is not a literal": ("E-ARITY", 'test t { let m = "x"; assert(true, m); }'),
    "bits read back as a bool": (
        "E-MATH-TYPE",
        "fn main() -> i32 { let b = from_bits[bool](2); if b { return 0; } return 1; }",
    ),
    "storage floats compared": (
        "E-OPERATOR",
        "fn main() -> i32 { let h = f16(1.0); if h == h { return 0; } return 1; }",
    ),
    "a gradient whose lanes would add into one slot": (
        "E-GRAD-RACE",
        "fn f(n:usize, w:ro<f64>[n], x:ro<f64>[n], out:rw<f64>[n]) { parallel i in n { out[i] = w[0] * x[i]; } }\nderive grad[w] for f;",
    ),
}


@pytest.mark.parametrize("name", REFUSED)
def test_the_attack_is_refused(name):
    code, source = REFUSED[name]
    refused(code, source)


STOPPED = {  # each compiles; each must end at its guard, never read or write what it does not own
    "an element loop whose owner is replaced": "fn main() -> i32 { let mut v = Buf[u64](8); let mut t:u64 = 0; for x in v { v = Buf[u64](1); t += x; } return i32(t); }",
    "a checked index after a callee shrank the Buf": "fn shrink(b:rw<Buf[u64]>) { b = Buf[u64](1); }\nfn f(b:rw<Buf[u64]>, k:usize) -> u64 { if k >= len(b) { return 0; } shrink(b); return b[k]; }\nfn main() -> i32 { let mut v = Buf[u64](8); let r = f(v, 5); return i32(r); }",
    "a checked index after a field was replaced": RECORD
    + "fn main() -> i32 { let mut s = S(Buf[u64](8)); let k:usize = 5; if k >= len(s.items) { return 0; } s.items = Buf[u64](1); return i32(s.items[k]); }",
    "a declared extent after its record was replaced": CHART
    + "fn main() -> i32 { let mut c = C(8, Buf[u64](8)); let k:usize = 5; if k >= c.rows { return 0; } c = C(1, Buf[u64](1)); return i32(c.price[k]); }",
    "a declared extent after a callee reset the record": CHART
    + "fn reset(c:rw<C>) { c = C(1, Buf[u64](1)); }\nfn main() -> i32 { let mut c = C(8, Buf[u64](8)); let k:usize = 5; if k >= c.rows { return 0; } reset(c); return i32(c.price[k]); }",
    "a declared extent loop whose record a closure replaces": CHART
    + CALL
    + "fn main() -> i32 { let mut c = C(8, Buf[u64](8)); let mut t:u64 = 0; for i in 0..c.rows { call(|| { c = C(1, Buf[u64](1)); }); t += c.price[i]; } return i32(t); }",
    "the right side of || where the left side failed": "fn at(n:usize, xs:ro<u64>[n], k:usize) -> bool { if k < n || xs[k] == 0 { return true; } return false; }\nfn main() -> i32 { let b = Buf[u64](4); if at(len(b), b, 4) { return 0; } return 1; }",
    "the else of an && that did not hold": "fn at(n:usize, xs:ro<u64>[n], k:usize) -> u64 { if k < n && k > 2 { return 0; } else { return xs[k]; } }\nfn main() -> i32 { let b = Buf[u64](4); return i32(at(len(b), b, 9)); }",
    "a part whose bounds are reversed": "fn sum(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for x in xs { t += x; } return t; }\nfn main() -> i32 { let b = Buf[u64](8); let lo:usize = 5; let hi:usize = 3; return i32(sum(b[lo..hi])); }",
    "a ring read longer than its Buf": (
        "extern fn pipe(fds:rw<i32>[2]) -> i32 effects(io);\nfn opened(fds:rw<i32>[2]) -> i32 { unsafe { return pipe(fds); } }\n"
        "fn main() -> i32 { let mut fds = Array[i32, 2](); let made = opened(fds); if made != 0 { return 1; }\n"
        "  let mut q = IoRing(4); defer wait(q); let mut big = Buf[u8](64); q.write(fds[1], big, 64, 0, 2);\n"
        "  let small = Buf[u8](16); q.read(fds[0], small, 64, 0, 1); return 0; }"
    ),
}


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("name", STOPPED)
def test_the_attack_that_compiles_stops_at_its_guard(tmp_path, name, cxx):
    done = watched(tmp_path, compile_source(STOPPED[name])[0], cxx, "address,undefined")
    assert done.returncode == -6, (done.returncode, done.stderr[-2000:])  # the guard's abort, not a sanitizer's
    assert "Sanitizer" not in done.stderr and "runtime error" not in done.stderr, done.stderr[-2000:]


RACE_FREE = {
    "host lanes reading the ring's queries": (
        "fn f(n:usize, out:rw<u64>[n], q:rw<IoRing>) { parallel i in n { out[i] = u64(q.room()) + u64(q.pending()); } }\n"
        "fn main() -> i32 { let n:usize = 100000; buffer o:u64[n] = zeroed; let mut q = IoRing(8); defer wait(q);\n"
        "  q.timeout(1000000000, 1); f(n, o, q); q.cancel(1); let mut t:u64 = 0; let mut r:i64 = 0; q.next(t, r);\n"
        "  if o[7] != 8 { return 1; } return 0; }"
    ),
    "a gradient that sums a shared scalar after its region": (
        "fn f(n:usize, s:f64, x:ro<f64>[n], out:rw<f64>[n]) { parallel i in n { out[i] = s * x[i]; } }\nderive grad[s] for f;\n"
        "fn main() -> i32 { let n:usize = 100000; buffer x:f64[n] = zeroed; buffer o:f64[n] = zeroed; buffer d:f64[n] = zeroed;\n"
        "  for i in 0..n { x[i] = 1.0; d[i] = 1.0; } let mut ds:f64 = 0.0; f_grad(n, 2.0, x, o, ds, d);\n"
        "  if ds != 100000.0 { return 1; } return 0; }"
    ),
}


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("name", RACE_FREE)
def test_the_attack_that_compiles_runs_race_free(tmp_path, name, cxx):
    done = watched(tmp_path, compile_source(RACE_FREE[name])[0], cxx, "thread")
    assert done.returncode == 0, (done.returncode, done.stderr[-2000:])
    assert "ThreadSanitizer" not in done.stderr, done.stderr[-2000:]

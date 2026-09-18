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

SUM = (
    "fn sum(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, xs[i]); } return t; }\n"
)
TWO_TRAITS = (
    "trait A { fn go(self:ro<Self>) -> u64; }\ntrait B { fn go(self:ro<Self>) -> u64; }\nstruct S { v:u64; }\n"
    "impl A for S { fn go(self:ro<S>) -> u64 = 1; }\nimpl B for S { fn go(self:ro<S>) -> u64 = 2; }\n"
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
    "a record that owns itself through a Buf, used twice as if it were a copy (double free)": (
        "E-MOVED",
        "struct Node { kids:Buf[Node]; v:u64; }\nfn eat(n:Node) -> u64 = n.v;\n"
        "fn main() -> i32 { let a = Node(Buf[Node](2), 7); let x = eat(a); let y = eat(a); return i32(x + y); }",
    ),
    "a sum that owns itself through a Buf, used twice": (
        "E-MOVED",
        "enum Tree { Leaf(u64); Fork(Buf[Tree]); }\nfn eat(t:Tree) -> u64 = 1;\n"
        "fn main() -> i32 { let a = Tree.Fork(Buf[Tree](2)); let x = eat(a); let y = eat(a); return i32(x + y); }",
    ),
    "a linear value hidden in a record that owns itself through a Buf": (
        "E-LINEAR-STORAGE",
        TOKEN + "struct Node { t:Token; kids:Buf[Node]; }\n"
        "fn main() -> i32 { let n = Node(open(1), Buf[Node](0)); return 0; }",
    ),
    "an impl whose receiver is rw where its trait says ro (writes through ro<dyn>)": (
        "E-TRAIT-IMPL",
        "trait Shape { fn area(self:ro<Self>) -> u64; }\nstruct Square { side:u64; }\n"
        "impl Shape for Square { fn area(self:rw<Square>) -> u64 { self.side = self.side + 1; return self.side; } }\n"
        "fn measure(s:ro<dyn Shape>) -> u64 = area(s);",
    ),
    "an impl returning another type than its trait declares": (
        "E-TRAIT-IMPL",
        "trait Shape { fn area(self:ro<Self>) -> u64; }\nstruct Square { side:u64; }\n"
        "impl Shape for Square { fn area(self:ro<Square>) -> u32 = 1; }",
    ),
    "an impl that defines a different member than its trait (compiler crash)": (
        "E-TRAIT-IMPL",
        "trait Shape { fn area(self:ro<Self>) -> u64; }\nstruct Square { side:u64; }\n"
        "impl Shape for Square { fn unrelated(self:ro<Square>) -> u64 = self.side; }\n"
        "fn measure(s:ro<dyn Shape>) -> u64 = area(s);",
    ),
    "a generic impl and a concrete impl both matching one type": (
        "E-TRAIT-OVERLAP",
        "trait Tag { fn tag(self:ro<Self>) -> u64; }\nstruct S { v:u64; }\n"
        "impl Tag for S { fn tag(self:ro<S>) -> u64 = 1; }\nimpl[T] Tag for T { fn tag(self:ro<T>) -> u64 = 2; }\n"
        "fn main() -> i32 { let s = S(0); return i32(tag(s)); }",
    ),
    "one member name declared by two traits the type implements": (
        "E-TRAIT-AMBIGUOUS",
        TWO_TRAITS + "fn main() -> i32 { let s = S(0); return i32(go(s)); }",
    ),
    "a dynamic call to a member that returns Self": (
        "E-DYN",
        "trait Shape { fn dup(self:ro<Self>) -> Self; }\nstruct Square { side:u64; }\n"
        "impl Shape for Square { fn dup(self:ro<Square>) -> Square = Square(self.side); }\n"
        "fn twice(s:ro<dyn Shape>) -> u64 { let d = dup(s); return 0; }",
    ),
    "a family over another module's private template": (
        "E-PRIVATE",
        "module lib;\nfn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);\n"
        "module app;\nfamily gain = lib.scale[1..3];\npub fn main() -> i32 { return i32(gain_2(10)); }",
    ),
    "a private family's instances called from another module": (
        "E-PRIVATE",
        "module lib;\npub fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);\n"
        "module mid;\nimport lib;\nfamily gain = lib.scale[1..3];\n"
        "module app;\nimport mid;\npub fn main() -> i32 { return i32(mid.gain_2(10)); }",
    ),
    "derive wire written outside the module that declares the record": (
        "E-PRIVATE",
        "module lib;\nstruct Secret { code:u32; }\nmodule app;\nimport lib;\nderive wire for lib.Secret;",
    ),
    "reading a field of another module's private record": (
        "E-PRIVATE",
        "module lib;\nstruct Secret { code:u32; }\npub fn make() -> Secret = Secret(7);\n"
        "module app;\nimport lib;\npub fn main() -> i32 { let s = lib.make(); return i32(s.code); }",
    ),
    "an imported name silently hiding the importer's own declaration": (
        "E-DUPLICATE",
        "module lib;\npub const N:usize = 99;\nmodule app;\nimport lib (N);\nconst N:usize = 1;\n"
        "pub fn main() -> i32 { return i32(N); }",
    ),
    "an alignment no compiler agrees on": ("E-ALIGN", "struct A align(3) { x:u64; }"),
    "two trait and type pairs that would share one vtable symbol": (
        "E-MANGLE",
        "trait A { fn f(self:ro<Self>) -> u64; }\ntrait A_B { fn f2(self:ro<Self>) -> u64; }\n"
        "struct B_C { v:u64; }\nstruct C { v:u64; }\n"
        "impl A for B_C { fn f(self:ro<B_C>) -> u64 = 1; }\nimpl A_B for C { fn f2(self:ro<C>) -> u64 = 2; }\n"
        "fn g(x:ro<dyn A>) -> u64 = f(x);\nfn h(x:ro<dyn A_B>) -> u64 = f2(x);\n"
        "fn main() -> i32 { let a = B_C(0); let c = C(0); return i32(g(a) + h(c)); }",
    ),
    "an extern link name that is not a C symbol": (
        "E-EXTERN",
        'extern "close\\"); int evil(" fn close_fd(fd:i32) -> i32 effects(io);',
    ),
    "a try leaving from the middle of an expression that already took an owner (leak)": (
        "E-EFFECT-ORDER",
        "enum R { Ok(usize); Err(u8); }\nstruct P { a:Buf[u64]; b:usize; }\n"
        "fn may(v:u64) -> R { if v == 0 { return R.Err(1); } return R.Ok(2); }\n"
        "fn build(v:u64, x:rw<Buf[u64]>) -> R { let p = P(take(x), try may(v)); return R.Ok(p.b + len(p.a)); }",
    ),
    "reading the length of an owner a task may replace (data race)": (
        "E-LEASED",
        "fn repl(v:rw<Buf[u64]>, rounds:usize) { let mut n = Buf[u64](64); for i in 0..rounds { swap(n, v); } }\n"
        "fn main() -> i32 { let mut b = Buf[u64](4); let t = spawn repl(b, 10); let k = len(b); wait(t); return i32(k); }",
    ),
    "an atomic declared inside a device lane": (
        "E-PLACEMENT",
        "fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { let a = Atomic[u64](0); d[i] = u64(i); } }",
    ),
    "a kernel reaching host code as a function value": (
        "E-FN-TYPE",
        "kernel fn k(x:u64) -> u64 = mul_wrap(x, 3);\nfn call(f:ro<fn(u64) -> u64>) -> u64 = f(7);\n"
        "fn main() -> i32 { return i32(call(k)); }",
    ),
    "two parts with nothing lent between them to order their bounds": (
        "E-LEASED",
        FILL + "fn main() -> i32 { let n:usize = 9; let a:usize = 3; let b:usize = 6; let mut d = Buf[u64](n);\n"
        "  let t1 = spawn fill(a, d[0..a], 1); let t3 = spawn fill(n - b, d[b..n], 3); wait(t1); wait(t3); return 0; }",
    ),
    "two parts of parts of one array lent mutably (their bounds are not visible)": (
        "E-ALIAS",
        "fn two(n:usize, a:rw<u64>[n], m:usize, b:rw<u64>[m]) { a[0] = 1; b[0] = 2; }\n"
        "fn main() -> i32 { let mut d = Buf[u64](16); two(2, d[0..8][0..2], 2, d[8..16][0..2]); return 0; }",
    ),
    # Round three: the new rules attacked ------------------------------------------------------
    "a host view reaching device code through a helper the lane calls": (
        "E-PLACEMENT",
        "fn peek(m:usize, h:ro<u64>[m]) -> u64 = h[0];\n"
        "fn f(n:usize, d:rw<u64>[n]@device, m:usize, h:ro<u64>[m]) { parallel i in n { d[i] = peek(m, h); } }",
    ),
    "a string literal reaching device code through a helper": (
        "E-PLACEMENT",
        'fn hostish(x:u64) -> u64 { let s = "hi"; return x + u64(s[0]); }\n'
        "fn f(n:usize, d:rw<u64>[n]@device) { parallel i in n { d[i] = hostish(u64(i)); } }",
    ),
    "a stored function value that performs I/O, called from lanes": (
        "E-PARALLEL-CALL",
        "extern fn getpid() -> i32 effects(io);\n"
        "fn go(n:usize, out:rw<u64>[n], f:fn(u64) -> u64) { parallel i in n { out[i] = f(u64(i)); } }\n"
        "fn noisy(x:u64) -> u64 { unsafe { return x + u64(getpid()); } }\n"
        "fn mid(n:usize, out:rw<u64>[n]) { let g:fn(u64) -> u64 = noisy; go(n, out, g); }",
    ),
    "a closure handed on from inside a lane to a helper that calls it (data race)": (
        "E-PARALLEL-CALL",
        "fn helper(f:ro<fn(u64) -> u64>, x:u64) -> u64 = f(x);\n"
        "fn go(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = helper(f, u64(i)); } }\n"
        "fn main() -> i32 { let n:usize = 64; buffer o:u64[n] = zeroed; let mut c:u64 = 0;\n"
        "  go(n, o, |x:u64| -> u64 { c = c + 1; return c; }); return 0; }",
    ),
    "a lane-called closure dispatching to an implementation that performs I/O": (
        "E-PARALLEL-CALL",
        "extern fn getpid() -> i32 effects(io);\ntrait P { fn pid(self:ro<Self>) -> u64; }\nstruct C { n:u64; }\n"
        "impl P for C { fn pid(self:ro<C>) -> u64 { unsafe { return u64(getpid()); } } }\n"
        + MAP
        + "fn go(n:usize, out:rw<u64>[n], c:ro<dyn P>) { map(n, out, |x:u64| -> u64 { return pid(c); }); }",
    ),
    "a trait with a Self-returning member coerced to dyn without ever calling it": (
        "E-DYN",
        "trait T { fn go(self:ro<Self>) -> Self; }\nstruct S { v:u64; }\nimpl T for S { fn go(self:ro<S>) -> S = S(1); }\n"
        "fn use_it(d:ro<dyn T>) -> u64 = 0;\nfn main() -> i32 { let s = S(1); return i32(use_it(s)); }",
    ),
    "a record holding itself by value through an inline array": (
        "E-RECORD-TYPE",
        "struct N { kids:Array[N, 2]; v:u64; }",
    ),
    "a generic impl with a parameter its Self type does not determine": (
        "E-TRAIT-IMPL",
        "trait T { fn go(self:ro<Self>) -> u64; }\nstruct S { v:u64; }\n"
        "impl[A] T for S { fn go(self:ro<S>, extra:A) -> A = extra; }",
    ),
    "a declared ceiling that hides that lanes call a parameter": (
        "E-EFFECT-CEILING",
        "fn map(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) effects(par:host, indirect_call, write:out, trap,\n"
        "  ffi_precondition) { parallel i in n { out[i] = f(u64(i)); } }",
    ),
    # Found by the maintainer while reviewing queued work; it predates it and three audits missed it.
    "a wait on a path that returns ending the lease on the path that goes on (data race)": (
        "E-LEASED",
        FILL + "fn f(early:bool) -> i32 { let mut d = Buf[u64](8); let t = spawn fill(len(d), d, 1);\n"
        "  if early { wait(t); return 1; }\n  d[0] = 99;\n  wait(t); return 0; }",
    ),
    "the same through a match arm that returns": (
        "E-LEASED",
        FILL + "enum E { A; B; }\nfn f(e:E) -> i32 { let mut d = Buf[u64](8); let t = spawn fill(len(d), d, 1);\n"
        "  match e { E.A => { wait(t); return 1; } E.B => {} }\n  d[0] = 99;\n  wait(t); return 0; }",
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


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_names_that_differ_only_by_underscores_stay_different_variables(tmp_path):
    source = (
        "fn pick(x:u64, x_:u64, _x:u64) -> u64 { let x__ = x_ * 10; return x + x__ + _x * 100; }\n"
        "fn main() -> i32 { return i32(pick(1, 2, 3) - 300); }"
    )
    assert run(tmp_path, source).returncode == 21


BEHAVIOR = {
    "a module's own take wins over the builtin inside that module": (
        75,
        "module m;\npub fn take(x:rw<usize>) -> usize = 7;\npub fn inside(x:rw<usize>) -> usize = take(x);\n"
        "module app;\nimport m;\n"
        "pub fn main() -> i32 { let mut a:usize = 5; let u = m.inside(a); return i32(add_wrap(mul_wrap(u, 10), a)); }",
    ),
    "a dynamic member that takes an owner by value moves it through the thunk": (
        4,
        "trait Sink { fn eat(self:ro<Self>, b:Buf[u64]) -> usize; }\nstruct S { v:u64; }\n"
        "impl Sink for S { fn eat(self:ro<S>, b:Buf[u64]) -> usize = len(b); }\n"
        "fn feed(s:ro<dyn Sink>, b:Buf[u64]) -> usize = eat(s, b);\n"
        "fn main() -> i32 { let s = S(1); let b = Buf[u64](4); return i32(feed(s, b)); }",
    ),
    "a generic impl serves static calls, dyn borrows and owned Dyn values alike": (
        6,
        "trait Tag { fn tag(self:ro<Self>) -> u64; }\nstruct S { v:u64; }\n"
        "impl[T] Tag for T { fn tag(self:ro<T>) -> u64 = 2; }\nfn dynamically(s:ro<dyn Tag>) -> u64 = tag(s);\n"
        "fn main() -> i32 { let s = S(0); let d = Dyn[Tag](S(1));\n"
        "  return i32(tag(s) + dynamically(s) + dynamically(d)); }",
    ),
    "a trait-qualified call picks between two traits, and the only implemented one needs no prefix": (
        23,
        TWO_TRAITS + "trait C { fn go3(self:ro<Self>) -> u64; }\nimpl C for S { fn go3(self:ro<S>) -> u64 = 3; }\n"
        "fn main() -> i32 { let s = S(0); return i32(B.go(s) * 10 + go3(s)); }",
    ),
    "an executable keeps every member its static tables name, dispatched or not": (
        0,
        "trait Shape { fn area(self:ro<Self>) -> u64; fn peri(self:ro<Self>) -> u64; }\nstruct Square { side:u64; }\n"
        "impl Shape for Square { fn area(self:ro<Square>) -> u64 = self.side * self.side;\n"
        "  fn peri(self:ro<Square>) -> u64 = self.side * 4; }\n"
        "fn measure(s:ro<dyn Shape>) -> u64 = area(s);\nfn unused(s:ro<dyn Shape>) -> u64 = 7;\n"
        "fn main() -> i32 { let sq = Square(3); return i32(measure(sq)) - 9; }",
    ),
    "a part of a part carries one guard per level": (
        7,
        SUM + "fn main() -> i32 { let mut b = Buf[u64](8); b[3] = 7; return i32(sum(2, b[1..5][1..3])); }",
    ),
    "a part of a part that leaves the inner part traps": (
        -6,
        SUM + "fn main() -> i32 { let mut b = Buf[u64](8); return i32(sum(4, b[1..3][0..4])); }",
    ),
    "a trait whose name ends in _vt beside the trait it would have shadowed": (
        21,
        "trait Shape { fn area(self:ro<Self>) -> u64; }\ntrait Shape_vt { fn peri(self:ro<Self>) -> u64; }\n"
        "struct Sq { s:u64; }\nimpl Shape for Sq { fn area(self:ro<Sq>) -> u64 = self.s * self.s; }\n"
        "impl Shape_vt for Sq { fn peri(self:ro<Sq>) -> u64 = self.s * 4; }\n"
        "fn m1(x:ro<dyn Shape>) -> u64 = area(x);\nfn m2(x:ro<dyn Shape_vt>) -> u64 = peri(x);\n"
        "fn main() -> i32 { let q = Sq(3); return i32(add_wrap(m1(q), m2(q))); }",
    ),
    "a dispatch on a field place as a whole condition": (
        0,
        "trait Flag { fn get(self:ro<Self>) -> bool; }\nstruct F { on:bool; }\n"
        "impl Flag for F { fn get(self:ro<F>) -> bool = self.on; }\nstruct Box { d:Dyn[Flag]; }\n"
        "fn main() -> i32 { let b = Box(Dyn[Flag](F(true)));\n  if get(b.d) { return 0; }\n  return 1; }",
    ),
    "a three-way chunk split: each lent part's guard orders the next part's bounds": (
        6,
        "fn fill(n:usize, out:rw<u64>[n], s:u64) { for i in 0..n { out[i] = s; } }\n"
        "fn main() -> i32 { let n:usize = 9; let a:usize = 3; let b:usize = 6; let mut d = Buf[u64](n);\n"
        "  let t1 = spawn fill(a, d[0..a], 1); let t2 = spawn fill(b - a, d[a..b], 2);\n"
        "  let t3 = spawn fill(n - b, d[b..n], 3); let whole = len(d);\n"
        "  wait(t1); wait(t2); wait(t3); return i32(d[0] + d[4] + d[8]) + i32(whole) - 9; }",
    ),
    "a task carries a temporary it was given for a single borrow": (
        4,
        "fn one(x:ro<u64>) -> u64 = x + 1;\nfn main() -> i32 { let t = spawn one(3); let r = wait(t); return i32(r); }",
    ),
    "impls that use their own trait: recursively on the same type, and through a bounded generic wrapper": (
        0,
        "trait Depth { fn depth(self:ro<Self>, left:u64) -> u64; }\ntrait Size { fn size(self:ro<Self>) -> u64; }\n"
        "struct S { v:u64; }\nstruct Box[T] { v:T; }\n"
        "impl[T] Depth for T { fn depth(self:ro<T>, left:u64) -> u64 {\n"
        "  if left == 0 { return 0; } return 1 + depth(self, left - 1); } }\n"
        "impl Size for u64 { fn size(self:ro<u64>) -> u64 = 8; }\n"
        "impl[T: Size] Size for Box[T] { fn size(self:ro<Box[T]>) -> u64 = 1 + size(self.v); }\n"
        "fn main() -> i32 { let s = S(0); let b = Box(Box(7)); return i32(depth(s, 5) + size(b)) - 15; }",
    ),
    "lanes reach function values and dynamic calls through helpers, locals and dyn members": (
        0,
        "trait P { fn pid(self:ro<Self>) -> u64; }\nstruct C { n:u64; }\nimpl P for C { fn pid(self:ro<C>) -> u64 = self.n; }\n"
        "trait R { fn run(self:ro<Self>, f:fn(u64) -> u64) -> u64; }\n"
        "impl R for C { fn run(self:ro<C>, f:fn(u64) -> u64) -> u64 { buffer o:u64[64] = zeroed;\n"
        "  parallel i in 64 { o[i] = f(u64(i)); } return o[2]; } }\n"
        "fn one(c:ro<dyn P>) -> u64 = pid(c);\nfn twice(x:u64) -> u64 = x * 2;\n"
        "fn helper(f:ro<fn(u64) -> u64>, x:u64) -> u64 = f(x);\n"
        "fn go(n:usize, out:rw<u64>[n], c:ro<dyn P>, f:ro<fn(u64) -> u64>, g:fn(u64) -> u64) effects(par:host, lane:f,\n"
        "  lane:g, indirect_call, dispatch, read:c, read:f, write:out, trap, ffi_precondition) {\n"
        "  parallel i in n { out[i] = one(c) + helper(f, u64(i)) + g(1); } }\n"
        "fn via(d:ro<dyn R>) -> u64 = run(d, twice);\n"
        "fn main() -> i32 { let n:usize = 8; buffer o:u64[n] = zeroed; let c = C(5); let k:u64 = 3;\n"
        "  let stored:fn(u64) -> u64 = twice;\n  go(n, o, c, |x:u64| -> u64 { return x * k + pid(c); }, stored);\n"
        "  let v = via(c);\n  if o[2] != 5 + 6 + 5 + 2 || v != 4 { return 1; }\n  return 0; }",
    ),
    "a public family over an imported public template belongs to the module that declares it": (
        20,
        "module lib;\npub fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);\n"
        "module mid;\nimport lib as l;\npub family gain = l.scale[1..3];\n"
        "module app;\nimport mid;\npub fn main() -> i32 { return i32(mid.gain_2(10)); }",
    ),
}


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
@pytest.mark.parametrize("name", BEHAVIOR)
def test_what_the_second_audit_found_ambiguous_now_has_one_meaning(tmp_path, name):
    status, source = BEHAVIOR[name]
    entry = "app.main" if "module app;" in source else "main"
    cpp = compile_source(source, roots=(entry,))[0]  # As `cairn build` does: only what main reaches is emitted.
    start = f"int main() {{ return static_cast<int>(cf_{entry.replace('.', '_')}()); }}\n"
    (tmp_path / "p.cpp").write_text(cpp + start)
    for header, text in RUNTIME_FILES.items():
        (tmp_path / header).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-pthread", "-fsanitize=address,undefined"]
    subprocess.run(["clang++", *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120)
    assert subprocess.run([tmp_path / "p"], timeout=60).returncode == status


def test_a_wait_on_every_path_ends_the_lease_for_what_follows():
    source = FILL + (
        "fn f(early:bool) -> i32 { let mut d = Buf[u64](8); let t = spawn fill(len(d), d, 1);\n"
        "  if early { wait(t); } else { wait(t); }\n  d[0] = 99; return 0; }"
    )
    assert compile_source(source)


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_a_backwards_part_used_to_order_two_others_aborts_at_its_spawn(tmp_path):
    """Chaining part bounds assumes every lent part was guarded `lo <= hi` before its task began. Here d[6..3]
    would order d[0..6] before d[3..9], which overlap: the guard must fire at the first spawn, on this thread,
    before either overlapping task exists. If part guards ever move into the task, this becomes a race."""
    source = FILL + (
        "fn main() -> i32 { let n:usize = 9; let a:usize = 6; let b:usize = 3; let z:usize = 0;\n"
        "  let mut d = Buf[u64](n);\n  let t2 = spawn fill(z, d[a..b], 2);\n"
        "  let t1 = spawn fill(a, d[0..a], 1);\n  let t3 = spawn fill(a, d[b..n], 3);\n"
        "  wait(t1); wait(t2); wait(t3); return 0; }"
    )
    assert run(tmp_path, source, ["-pthread", "-fsanitize=thread"]).returncode == -6
    late = source.replace("  let t2 = spawn fill(z, d[a..b], 2);\n", "").replace(
        "  wait(t1);", "  let t2 = spawn fill(z, d[a..b], 2);\n  wait(t1);"
    )
    with pytest.raises(Diagnostic) as e:  # Without the earlier guard there is no fact to chain through.
        compile_source(late)
    assert e.value.data["code"] == "E-LEASED"

"""What the audits found ambiguous now has one meaning, shown by native runs and by rejections."""

import shutil

import pytest
from test_soundness import FILL, MAP, SUM, TWO_TRAITS

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, refused
from emitted import run as native

PLAIN = SANITIZED[:4]  # the sanitized build without its sanitizers; a test adds the one that bites


def run(tmp_path, source, flags=()):
    return native(tmp_path, compile_source(source)[0], *PLAIN, *flags)


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
    refused("E-ALIAS", both)


def test_sequenced_and_single_opaque_operands_remain_legal():
    """The audit must not outlaw ordinary code: `&&` is sequenced, and one opaque call may read immutables."""
    source = (
        "fn search(n:usize, xs:ro<u64>[n], key:u64, less:ro<fn(u64, u64) -> bool>) -> usize {\n"
        "  let mut i:usize = 0;\n  while i < n && less(xs[i], key) { i = i + 1; }\n"
        "  if !less(key, key) && i < n { return i; }\n  return n;\n}"
    )
    assert compile_source(source)


def test_the_shapes_path_writes_with_a_question_mark_stay_conservative():
    """An invisible bound and a bare index overlap everything of their base, and nothing else.

    `proofs/Cairn/Places.lean` models both as `elems`, and these are the two halves that keep the
    model honest: the header stays readable under such a part, and another buffer stays untouched.
    """
    under_a_mutable_bound = FILL + (
        "fn main() -> i32 { let n:usize = 8; let mut m:usize = 4; let mut d = Buf[u64](n);\n"
        "  let t = spawn fill(m, d[0..m], 1); let k = len(d); wait(t); return i32(k) - 8; }"
    )
    beside_another_buffer = FILL + (
        "fn main() -> i32 { let n:usize = 8; let a:usize = 4; let mut d = Buf[u64](n); let mut e = Buf[u64](n);\n"
        "  let t = spawn fill(a, e[0..a], 1); let v = d[6]; wait(t); return i32(v); }"
    )
    assert compile_source(under_a_mutable_bound)
    assert compile_source(beside_another_buffer)


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
    "a reduction reads its extent once, as written": (
        3,
        "fn main() -> i32 { let c = Atomic[usize](0);\n"
        "  let t = reduce add_wrap for i in c.fetch_add(3, Order.relaxed) + 3 yield u64(i);\n"
        "  return i32(c.load(Order.relaxed)); }",
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
        "  parallel i in n { let a = one(c); let b = helper(f, u64(i)); let d = g(1); out[i] = a + b + d; } }\n"
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
    assert native(tmp_path, cpp, *PLAIN, "-pthread", "-fsanitize=address,undefined", entry=entry).returncode == status


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
    refused("E-LEASED", late)  # Without the earlier guard there is no fact to chain through.

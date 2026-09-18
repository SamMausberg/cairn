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

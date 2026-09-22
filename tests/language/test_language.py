"""CAIRN 1.0 breadth: generics, traits, owners, moves, linear values, cleanup, parts and text.

Every accepted construct is executed natively under both compilers; every rule has a rejection.
"""

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, WARNINGS, refused, run

PRELUDE = """
const LIMIT:usize = 8;
struct Pair[T] { a:T; b:T; }
enum Option[T] { Some(T); None; }
trait Shape { fn area(self:ro<Self>) -> u64; }
struct Square { side:u64; }
struct Rect { w:u64; h:u64; }
impl Shape for Square { fn area(self:ro<Square>) -> u64 = self.side * self.side; }
impl Shape for Rect { fn area(self:ro<Rect>) -> u64 = self.w * self.h; }
fn largest[T](a:T, b:T) -> T { if a < b { return b; } return a; }
fn total_area[S: Shape](x:ro<S>, y:ro<S>) -> u64 = area(x) + area(y);
fn first[T](p:Pair[T]) -> T = p.a;
fn find(n:usize, xs:ro<u64>[n], key:u64) -> Option[usize] {
  for i in 0..n { if xs[i] == key { return Option.Some(i); } }
  return Option.None;
}
struct Vec[T] { data:Buf[T]; len:usize; }
fn vec_new[T]() -> Vec[T] = Vec(Buf[T](0), 0);
fn push[T](v:rw<Vec[T]>, item:T) {
  if v.len == len(v.data) {
    let mut bigger = Buf[T](max(4, len(v.data) * 2));
    for i in 0..v.len { swap(bigger[i], v.data[i]); }
    v.data = bigger;
  }
  v.data[v.len] = item;
  v.len = v.len + 1;
}
fn sum(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut total:u64 = 0;
  for i in 0..n { total = total + xs[i]; }
  return total;
}
fn bump(counter:rw<u64>) { counter = counter + 1; }
fn make(n:usize) -> Buf[u64] {
  let mut b = Buf[u64](n);
  for i in 0..n { b[i] = u64(i) * 2; }
  return b;
}
linear struct Token { id:u64; }
fn open(id:u64) -> Token = Token(id);
fn close(t:Token) {}
"""

MAIN = """
fn main() -> i32 {
  if largest(3, 9) != 9 { return 1; }
  let sq = Square(3);
  let other = Square(4);
  if total_area(sq, other) != 25 { return 2; }
  if area(Rect(2, 5)) != 10 { return 3; }
  if first(Pair(7, 8)) != 7 { return 4; }
  let mut v = vec_new[u64]();
  for i in 0..10 { v.push(u64(i)); }
  if v.len != 10 { return 5; }
  if sum(len(v.data), v.data) != 45 { return 6; }
  match find(len(v.data), v.data, 7) {
    Option.Some(i) => { if i != 7 { return 7; } }
    Option.None => { return 8; }
  }
  let mut hits:u64 = 0;
  { defer bump(hits); bump(hits); }
  if hits != 2 { return 9; }
  let b = make(LIMIT);
  if sum(4, b[2..6]) != 4 + 6 + 8 + 10 { return 10; }
  let text = "hi\\n";
  if len(text) != 3 { return 11; }
  if text[0] != 'h' { return 12; }
  let mut grid = Array[u64, 4]();
  grid[3] = 5;
  if sum(4, grid) != 5 { return 13; }
  let mut nested = vec_new[Buf[u64]]();
  let three = make(3);
  nested.push(three);
  if nested.data[0][2] != 4 { return 14; }
  let mut taken = take(nested.data[0]);
  if len(taken) != 3 { return 15; }
  if len(nested.data[0]) != 0 { return 16; }
  taken[0] = 1;
  let token = open(1);
  defer close(token);
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("sanitize", [False, True])
def test_breadth_runs_natively(tmp_path, cxx, sanitize):
    generated, receipt = compile_source(PRELUDE + MAIN)
    flags = SANITIZED if sanitize else ["-std=c++20", "-O3"]
    assert run(tmp_path, generated, *flags, *WARNINGS, cxx=cxx).returncode == 0
    effects = receipt["functions"]["make"]["effects"]
    assert {"alloc", "free", "zero_init", "trap"} <= set(effects) and "local_write" in effects
    assert receipt["functions"]["push[u64]"]["effects"] == sorted(
        {"alloc", "free", "read:v", "trap", "write:v", "zero_init", "local_read", "local_write"}
    )


def test_instances_are_monomorphic_and_named():
    generated, receipt = compile_source(PRELUDE + MAIN)
    assert {"largest[u64]", "push[u64]", "push[Buf[u64]]", "vec_new[u64]"} <= set(receipt["functions"])
    assert "cf_push_Buf_u64" in generated and "struct ct_Vec_Buf_u64" in generated
    assert receipt["uninstantiated_templates"] == []


@pytest.mark.parametrize(
    "code,body",
    [
        ("E-MOVED", "let b = make(2); let c = b; let d = b; return 0;"),
        ("E-MOVED", "let t = open(1); close(t); close(t); return 0;"),
        ("E-MOVED", "let t = open(1); defer close(t); close(t); return 0;"),
        ("E-PARTIAL-MOVE", "let mut v = vec_new[u64](); let d = v.data; return 0;"),
        ("E-MOVE-IN-LOOP", "let b = make(2); for i in 0..3 { let c = b; } return 0;"),
        ("E-LINEAR-LEAK", "let t = open(1); return 0;"),
        ("E-LINEAR-LEAK", "for i in 0..3 { let t = open(1); if i == 1 { break; } close(t); } return 0;"),
        ("E-LINEAR-BRANCH", "let t = open(1); if true { close(t); } return 0;"),
        ("E-WRITE-LEASE", "let b = make(2); b[0] = 1; return 0;"),
        ("E-WRITE-LEASE", "let x:u64 = 1; bump(x); return 0;"),
        ("E-TRAIT-IMPL", "let p = Pair(1, 2); return i32(area(p));"),
        ("E-TRAIT-IMPL", "let p = Pair(1, 2); let q = Pair(1, 2); return i32(total_area(p, q));"),
        ("E-INFER", "let v = vec_new(); return 0;"),
        ("E-INFER", "let o = Option.None; return 0;"),
        ("E-GENERIC-ARITY", "let p:Pair[u64, u64] = Pair(1, 2); return 0;"),
        ("E-TYPE-MISMATCH", "return i32(largest(1, true));"),
        ("E-ALIAS", "let mut b = make(4); swap_parts(2, b[0..2], b[1..3]); return 0;"),
        ("E-CALL-VIEW", "return i32(sum(2, make(2)));"),
        ("E-INDEX", "let x:u64 = 1; return i32(x[0]);"),
    ],
)
def test_rejections(code, body):
    helper = "fn swap_parts(n:usize, a:rw<u64>[n], b:rw<u64>[n]) { swap(a[0], b[0]); }"
    refused(code, PRELUDE + helper + "fn main() -> i32 {" + body + "}")


def test_disjoint_parts_are_accepted_and_guarded():
    source = PRELUDE + (
        "fn swap_parts(n:usize, a:rw<u64>[n], b:rw<u64>[n]) { swap(a[0], b[0]); }"
        "fn main() -> i32 { let mut b = make(4); let mid:usize = 2; swap_parts(2, b[0..mid], b[mid..4]); return 0; }"
    )
    generated, receipt = compile_source(source)
    assert generated.count("cr::part(") == 2
    assert receipt["functions"]["main"]["syntactic_check_sites"]["bounds"] == 2


@pytest.mark.parametrize(
    "code,source",
    [
        ("E-EXTERN-EFFECTS", "extern fn getpid() -> i32;"),
        ("E-UNSAFE", "extern fn getpid() -> i32 effects(io); fn f() -> i32 = getpid();"),
        ("E-EFFECT-CEILING", "fn f(n:usize, x:rw<u64>[n]) pure { x[0] = 1; }"),
        ("E-EFFECT-CEILING", "fn f(n:usize) -> u64 effects(trap) { buffer b:u64[n] = zeroed; return b[0]; }"),
        ("E-PLACEMENT", "fn f(x:ro<u64>[1]@device) -> u64 = x[0];"),
        ("E-PLACE", "fn f(x:ro<u64>[1]@gpu) -> u64 = x[0];"),
        ("E-SUM-PAYLOAD", "enum R { Bad(R); }"),
        ("E-RECORD-TYPE", "struct A { next:A; }"),
        ("E-CONST", "fn f() -> usize = 3;\nconst N:usize = f();"),
        ("E-BUILTIN-NAME", "struct Buf { x:u64; }"),
        ("E-LEX", "fn f() -> u8 = 'ab';"),
    ],
)
def test_boundary_rejections(code, source):
    refused(code, source)


def test_foreign_calls_are_visible_effects():
    source = (
        "extern fn getpid() -> i32 effects(io);"
        "fn pid() -> i32 { unsafe { return getpid(); } }"
        "fn caller() -> i32 = pid();"
    )
    generated, receipt = compile_source(source)
    assert '__asm__("getpid")' in generated
    for name in ("pid", "caller"):
        assert {"io", "ffi:getpid"} <= set(receipt["functions"][name]["effects"])
    assert receipt["functions"]["pid"]["syntactic_check_sites"]["unsafe_blocks"] == 1


def test_recursive_owner_through_buf():
    source = (
        "struct Tree { value:u64; kids:Buf[Tree]; }"
        "fn total(t:ro<Tree>) -> u64 { let mut s = t.value; for i in 0..len(t.kids) { s = s + total(t.kids[i]); } return s; }"
    )
    assert "diverge" in compile_source(source)[1]["functions"]["total"]["effects"]


def test_pure_is_a_checked_ceiling():
    assert compile_source("fn f(n:usize, x:ro<u64>[n]) -> u64 pure = x[0] + 1;")


DYNAMIC = """
trait Shape { fn area(self:ro<Self>) -> u64; fn grow(self:rw<Self>, by:u64); }
struct Square { side:u64; }
struct Rect { w:u64; h:u64; }
struct Dot { x:u64; }
impl Shape for Square {
  fn area(self:ro<Square>) -> u64 = self.side * self.side;
  fn grow(self:rw<Square>, by:u64) { self.side = self.side + by; }
}
impl Shape for Rect {
  fn area(self:ro<Rect>) -> u64 = self.w * self.h;
  fn grow(self:rw<Rect>, by:u64) { self.w = self.w + by; }
}
fn measure(s:ro<dyn Shape>) -> u64 = area(s) + 1;
fn enlarge(s:rw<dyn Shape>, by:u64) -> u64 { s.grow(by); return s.area(); }
fn forward(s:ro<dyn Shape>) -> u64 = measure(s);
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_dynamic_interfaces_are_explicit_fat_references(tmp_path, cxx):
    main = (
        "fn main() -> i32 { let mut sq = Square(3); let mut r = Rect(2, 5);"
        " if measure(sq) != 10 || measure(r) != 11 { return 1; }"
        " let bigger = enlarge(sq, 1); let wider = enlarge(r, 2);"
        " if bigger != 16 || wider != 20 || forward(sq) != 17 { return 2; } return 0; }"
    )
    generated, receipt = compile_source(DYNAMIC + main)
    assert run(tmp_path, generated, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", cxx=cxx).returncode == 0
    assert receipt["functions"]["enlarge"]["effects"] == ["dispatch", "read:s", "trap", "write:s"]
    assert generated.count("static const cdt_Shape") == 2


@pytest.mark.parametrize(
    "code,tail",
    [
        ("E-DYN", "fn f(s:dyn Shape) -> u64 = 0;"),
        ("E-DYN", "struct Holder { s:dyn Shape; }"),
        ("E-TRAIT-IMPL", "fn f() -> u64 { let d = Dot(1); return measure(d); }"),
        ("E-WRITE-LEASE", "fn f() -> u64 { let sq = Square(1); let a = enlarge(sq, 1); return a; }"),
        ("E-WRITE-LEASE", "fn f() -> u64 = measure(Square(1));"),
    ],
)
def test_dynamic_interface_rejections(code, tail):
    refused(code, DYNAMIC + tail)


def test_owned_dynamic_values_hold_heterogeneous_owners(tmp_path):
    """Vec[Dyn[Shape]]: explicit allocation, dispatch through the table, release through the drop word."""
    from pathlib import Path

    source = (Path(__file__).parents[1] / "native/owned_dynamic.cairn").read_text()
    generated, receipt = compile_source(source)
    assert {"alloc", "free", "dispatch"} <= set(receipt["functions"]["main"]["effects"])
    assert run(tmp_path, generated, *SANITIZED, env={"ASAN_OPTIONS": "detect_leaks=1"}).returncode == 0


@pytest.mark.parametrize(
    "code,tail",
    [
        ("E-TRAIT-IMPL", "fn f() -> u64 { let d = Dyn[Shape](Dot(1)); return measure(d); }"),
        ("E-DYN", "fn f() -> u64 { let d = Dyn[Dot](Dot(1)); return 0; }"),
        ("E-MOVED", "fn f() -> u64 { let sq = Square(1); let d = Dyn[Shape](sq); let e = d; return measure(d); }"),
        ("E-WRITE-LEASE", "fn f() -> u64 { let d = Dyn[Shape](Square(1)); d.grow(1); return 0; }"),
    ],
)
def test_owned_dynamic_rejections(code, tail):
    refused(code, DYNAMIC + tail)


def test_an_empty_owned_dynamic_value_traps_when_lent(tmp_path):
    source = DYNAMIC + "fn main() -> i32 { let mut pair = Array[Dyn[Shape], 2](); return i32(measure(pair[0])); }"
    assert run(tmp_path, compile_source(source)[0], "-std=c++20", "-O2", cxx="g++").returncode == -6


def test_the_readme_example_is_real():
    """Documentation that does not compile is a claim nobody checked."""
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    rows = {}
    for sample in [block.split("```", 1)[0] for block in readme.split("```cairn\n")[1:]]:
        rows |= compile_source(sample)[1]["functions"]
    assert "par:device" in rows["saxpy"]["effects"]
    assert set(rows["halves"]["effects"]) == {"spawn", "join", "write:data", "trap", "ffi_precondition"}  # As it says.


def test_multiple_bounds_and_take_operand_order():
    bounded = (
        "import std.core (Hash, Eq);"
        "fn key[K: Hash + Eq](a:ro<K>, b:ro<K>) -> u64 { if same(a, b) { return hash(a); } return 0; }"
        "fn f() -> u64 = key(1, 2);"
    )
    assert "key[u64]" in compile_source(bounded)[1]["functions"]
    pair = "struct P { a:Buf[u64]; b:Buf[u64]; } fn f() -> P { let mut x = Buf[u64](1); let mut y = Buf[u64](2); "
    assert compile_source(pair + "return P(take(x), take(y)); }")
    refused(
        "E-EFFECT-ORDER", pair + "return P(take(x), take(x)); }"
    )  # C++ leaves argument order open: which field would get the zero?

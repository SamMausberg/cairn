"""CAIRN 1.0 breadth: generics, traits, owners, moves, linear values, cleanup, parts and text.

Every accepted construct is executed natively under both compilers; every rule has a rejection.
"""

import shutil
import subprocess

import pytest

from cairn.cairnc import RUNTIME_FILES, Diagnostic, compile_source

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
    if not shutil.which(cxx):
        pytest.skip("Native compiler unavailable")
    generated, receipt = compile_source(PRELUDE + MAIN)
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-O1", "-g"] if sanitize else ["-O3"]
    command = [cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-parameter"]
    subprocess.run([*command, *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120)
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == 0
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
    with pytest.raises(Diagnostic) as e:
        compile_source(PRELUDE + helper + "fn main() -> i32 {" + body + "}")
    assert e.value.data["code"] == code


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
        ("E-CONST", "const N:usize = 1 + 2;"),
        ("E-BUILTIN-NAME", "struct Buf { x:u64; }"),
        ("E-LEX", "fn f() -> u8 = 'ab';"),
    ],
)
def test_boundary_rejections(code, source):
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code


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
    if not shutil.which(cxx):
        pytest.skip("Native compiler unavailable")
    main = (
        "fn main() -> i32 { let mut sq = Square(3); let mut r = Rect(2, 5);"
        " if measure(sq) != 10 || measure(r) != 11 { return 1; }"
        " let bigger = enlarge(sq, 1); let wider = enlarge(r, 2);"
        " if bigger != 16 || wider != 20 || forward(sq) != 17 { return 2; } return 0; }"
    )
    generated, receipt = compile_source(DYNAMIC + main)
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    subprocess.run([cxx, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")],
                   check=True, timeout=120)  # fmt: skip
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == 0
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
    with pytest.raises(Diagnostic) as e:
        compile_source(DYNAMIC + tail)
    assert e.value.data["code"] == code


def test_the_readme_example_is_real():
    """Documentation that does not compile is a claim nobody checked."""
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    sample = readme.split("```cairn\n", 1)[1].split("```", 1)[0]
    receipt = compile_source(sample)[1]
    assert "par:device" in receipt["functions"]["saxpy"]["effects"]
    assert {"spawn", "join"} <= set(receipt["functions"]["halves"]["effects"])


def test_multiple_bounds_and_take_operand_order():
    bounded = (
        "import std.core (Hash, Eq);"
        "fn key[K: Hash + Eq](a:ro<K>, b:ro<K>) -> u64 { if same(a, b) { return hash(a); } return 0; }"
        "fn f() -> u64 = key(1, 2);"
    )
    assert "key[u64]" in compile_source(bounded)[1]["functions"]
    pair = "struct P { a:Buf[u64]; b:Buf[u64]; } fn f() -> P { let mut x = Buf[u64](1); let mut y = Buf[u64](2); "
    assert compile_source(pair + "return P(take(x), take(y)); }")
    with pytest.raises(Diagnostic) as e:  # C++ leaves argument order open: which field would get the zero?
        compile_source(pair + "return P(take(x), take(x)); }")
    assert e.value.data["code"] == "E-EFFECT-ORDER"


FIRST_USERS_FOUND = """
import std.vec;
import std.core (Option);
const N:usize = 8;
enum Maybe[T] { Some(T); None; }
fn consume(b:Buf[u64]) -> usize = len(b);
fn nothing() -> Maybe[Buf[u64]] { return Maybe.None; }
fn either(flag:bool) -> usize { let b = Buf[u64](4); if flag { return consume(b); } return consume(b); }
fn ascending[T](a:T, b:T) -> bool = a < b;
fn pick(x:u64, y:u64, before:ro<fn(u64, u64) -> bool>) -> u64 { if before(x, y) { return x; } return y; }
fn total(n:usize, xs:ro<u64>[n]) -> u64 pure { let s = reduce add_wrap for i in n yield xs[i]; return s; }
fn main() -> i32 {
  buffer a:u64[N] = zeroed;
  stack s:u64[N] = zeroed;
  let n:usize = 4;
  if max(8, n) != 8 || min(2, n) != 2 || len(a) != 8 || len(s) != 8 { return 1; }
  let four_again = either(true);
  if four_again != 4 || pick(3, 9, ascending[u64]) != 3 || total(N, a) != 0 { return 2; }
  match nothing() { Maybe.Some(b) => { return 3; } Maybe.None => {} }
  let mut v = vec.new[Buf[u64]]();
  let four = Buf[u64](4);
  v.push(four);
  match v.pop() { Option.Some(x) => { return i32(len(x)) - 4; } Option.None => { return 5; } }
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_what_the_first_real_users_tripped_over(tmp_path, cxx):
    """Constant capacities, bare owner-sum variants, moves on returning branches, literal min/max,
    instantiated generics as function values, a pure host reduce, and owner-carrying sums under g++."""
    if not shutil.which(cxx):
        pytest.skip("Native compiler unavailable")
    generated, receipt = compile_source(FIRST_USERS_FOUND)
    assert "par:host" not in receipt["functions"]["total"]["effects"]
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable", "-Wno-unused-parameter"]
    subprocess.run([cxx, *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120)
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == 0


def test_a_move_before_break_still_counts_after_the_loop():
    source = (
        "fn consume(b:Buf[u64]) {}\n"
        "fn f(n:usize) -> usize { let b = Buf[u64](4); for i in 0..n { if i == 1 { consume(b); break; } } return len(b); }"
    )
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] in {"E-MOVE-IN-LOOP", "E-MOVED"}


ERGONOMICS = """
module shapes;
pub struct Ring { items:Buf[u64]; used:usize; }
pub fn ring(n:usize) -> Ring = Ring(Buf[u64](n), 0);
pub fn len(r:ro<Ring>) -> usize = r.used;                 // a type may have its own len
pub fn add(r:rw<Ring>, v:u64) { r.items[r.used] = v; r.used = r.used + 1; }
module app;
import shapes;
enum Done[E] { Ok; Err(E); }
enum Result[T, E] { Ok(T); Err(E); }
extern "getpid" fn process_id() -> i32 effects(io);
fn check(v:u64) -> Done[u8] { if v == 0 { return Done.Err(9); } return Done.Ok; }
fn half(v:u64) -> Result[u64, u8] { try check(v); return Result.Ok(v / 2); }   // Done's failure fits Result
fn unused_and_never_emitted() -> i32 { unsafe { return process_id(); } }
fn main() -> i32 {
  let mut r = shapes.ring(4);
  r.add(7);
  if r.len() != 1 || len(r.items) != 4 { return 1; }
  match half(0) { Result.Ok(v) => { return 2; } Result.Err(code) => { if code != 9 { return 3; } } }
  match half(10) { Result.Ok(v) => { if v != 5 { return 4; } } Result.Err(code) => { return 5; } }
  let f:f64 = 1234.9;
  let g:f32 = 0.0 - 7.5;
  if u64(f) != 1234 || i32(g) != 0 - 7 || u8(f / 10.0) != 123 { return 6; }
  return 0;
}
"""


def test_ergonomics_the_first_library_author_asked_for(tmp_path):
    """A type's own len, try across sum families, extern link names, checked float to integer, and
    executables that contain only what main reaches."""
    from cairn.build import build
    from cairn.project import load_project

    path = tmp_path / "app.cairn"
    path.write_text(ERGONOMICS)
    record = build(load_project(path), kind="exe", cxx="g++")
    assert record["status"] == "native-built", record.get("stderr")
    assert subprocess.run([record["artifact"]], timeout=30).returncode == 0
    generated = (tmp_path / "build").glob("*/program.cpp").__next__().read_text()
    assert "unused_and_never_emitted" not in generated and "getpid" not in generated
    rows = record["frontend"]["functions"]
    assert "ffi:getpid" in rows["app.unused_and_never_emitted"]["effects"]  # Checked and reported all the same.
    assert '__asm__("getpid")' in compile_source(ERGONOMICS)[0]


@pytest.mark.parametrize("value", ["0.0 / 0.0", "18446744073709551616.0", "0.0 - 1.0"])
def test_float_to_integer_traps_outside_the_target(tmp_path, value):
    source = f"fn main() -> i32 {{ let zero:f64 = 0.0; let x:f64 = {value} + zero; let y = u64(x); return i32(y); }}"
    (tmp_path / "p.cpp").write_text(compile_source(source)[0] + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    subprocess.run(
        ["g++", "-std=c++20", "-O2", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120
    )
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == -6


def test_owned_dynamic_values_hold_heterogeneous_owners(tmp_path):
    """Vec[Dyn[Shape]]: explicit allocation, dispatch through the table, release through the drop word."""
    from pathlib import Path

    source = (Path(__file__).parent / "native/owned_dynamic.cairn").read_text()
    generated, receipt = compile_source(source)
    assert {"alloc", "free", "dispatch"} <= set(receipt["functions"]["main"]["effects"])
    (tmp_path / "p.cpp").write_text(generated + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    build = [
        "clang++",
        "-std=c++20",
        "-O1",
        "-g",
        "-fno-exceptions",
        "-fsanitize=address,undefined",
        "-fno-sanitize-recover=all",
    ]
    subprocess.run([*build, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120)
    assert subprocess.run([tmp_path / "p"], timeout=60, env={"ASAN_OPTIONS": "detect_leaks=1"}).returncode == 0


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
    with pytest.raises(Diagnostic) as e:
        compile_source(DYNAMIC + tail)
    assert e.value.data["code"] == code


def test_an_empty_owned_dynamic_value_traps_when_lent(tmp_path):
    source = DYNAMIC + "fn main() -> i32 { let mut pair = Array[Dyn[Shape], 2](); return i32(measure(pair[0])); }"
    (tmp_path / "p.cpp").write_text(compile_source(source)[0] + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    subprocess.run(
        ["g++", "-std=c++20", "-O2", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=120
    )
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == -6


def test_a_nat_parameter_is_a_static_extent_and_literals_take_the_expected_result_type(tmp_path):
    """Two false rejections the second audit noted: `rw<u64>[K]` of a `[K:nat]` instance, and `let y:u32 = conv(3)`."""
    source = (
        "fn fill[K:nat](out:rw<u64>[K], v:u64) { for i in 0..K { out[i] = v; } }\n"
        "family fill_n = fill[5..6];\nfn conv[T](x:T) -> T = x;\n"
        "fn main() -> i32 { stack a:u64[4] = zeroed; stack b:u64[5] = zeroed; fill[4](a, 7); fill_n_5(b, 2);\n"
        "  let y:u32 = conv(3); let z:u8 = conv(200);\n"
        "  if a[3] != 7 || b[4] != 2 || y != 3 || z != 200 { return 1; }\n  return 0; }"
    )
    (tmp_path / "p.cpp").write_text(compile_source(source)[0] + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    build = ["clang++", "-std=c++20", "-O1", "-fsanitize=address,undefined", str(tmp_path / "p.cpp"), "-o"]
    subprocess.run([*build, str(tmp_path / "p")], check=True, timeout=120)
    assert subprocess.run([tmp_path / "p"], timeout=30).returncode == 0
    with pytest.raises(Diagnostic) as wrong_extent:
        compile_source(source.replace("fill[4](a, 7)", "fill[8](a, 7)"))
    assert wrong_extent.value.data["code"] == "E-TYPE-MISMATCH"


GENERIC_BOUNDS = """
import std.core (Ord, Option);
trait Score { fn score(self:ro<Self>) -> u64; }
fn larger[T: Score, U: Score](x:ro<T>, y:ro<U>) -> u64 = max(score(x), score(y));
fn biggest[T: Ord](n:usize, xs:ro<T>[n]) -> Option[usize] {
  if n == 0 { return Option.None; }
  let mut best:usize = 0;
  for i in 1..n { if less(xs[best], xs[i]) { best = i; } }
  return Option.Some(best);
}
fn pass[T](x:T) -> T = x;
fn largest[T](a:T, b:T) -> T { if a < b { return b; } return a; }
fn twice[T](x:T) -> u64 { let a = x; let b = x; return 0; }
fn ignore[T](x:T) -> u64 = 0;
fn keep[T](x:T) -> u64 = ignore(pass(x));
fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);
family s = scale[1..3];
"""


def test_a_template_is_certified_once_when_its_body_needs_only_its_bounds():
    """Witness types offer exactly the bounds and must be consumed exactly once, so "ok" holds for every instance."""
    from cairn.cairnc import certify_templates

    verdicts = certify_templates(GENERIC_BOUNDS)
    assert {n for n, v in verdicts.items() if v == "ok"} == {"larger", "biggest", "pass"}
    assert verdicts["largest"].startswith("E-OPERATOR")  # `<` is not something a bare T promises.
    assert verdicts["twice"].startswith("E-MOVED")  # A T may be an owner.
    assert verdicts["ignore"].startswith("E-LINEAR-LEAK") and verdicts["keep"].startswith("E-LINEAR-LEAK")  # Or linear.
    assert "natural" in verdicts["scale"]
    assert compile_source(GENERIC_BOUNDS)  # None of this changes what is accepted: instances are still checked.

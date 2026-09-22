"""What real users tripped over: records taken apart, diagnostics that say what to write instead, and
the ergonomics a library author asked for. Every accepted program runs natively; every rule keeps its rejection.
"""

import subprocess

import pytest

from cairn.agent.agent_tools import canonical_source
from cairn.compiler.cairnc import Diagnostic, compile_source
from emitted import SANITIZED, WARNINGS, refused, run, sanitized

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
    generated, receipt = compile_source(FIRST_USERS_FOUND)
    assert "par:host" not in receipt["functions"]["total"]["effects"]
    assert run(tmp_path, generated, "-std=c++20", "-O2", *WARNINGS, cxx=cxx).returncode == 0


SECOND_USER_FOUND = """
import std.sort;
struct Plain { price:Buf[u64]; }
struct Two[A, B] { left:Buf[A]; right:Buf[B]; }
fn first[T](n:usize, src:ro<T>[n]) -> T = src[0];
fn say(n:usize, s:ro<u8>[n]) -> usize = n;
fn main() -> i32 {
  let n:usize = 8;
  let mut c = Plain(Buf[u64](n));
  let mut two = Two(Buf[u8](n), Buf[f64](n));
  for i in 0..n { c.price[i] = u64(n - i); two.right[i] = 1.5; }
  sort.sort(n, c.price[0..n]);
  if c.price[0] != 1 || first(4, c.price[4..n]) != 5 || first(2, two.right[1..3]) != 1.5 { return 1; }
  let host:u64 = 3;
  let device = host + u64(say(len("hello"), "hello"));
  if device != 8 || len("") != 0 { return 2; }
  return 0;
}
"""


def test_what_the_second_user_tripped_over(tmp_path):
    """A generic callee given a part of a record's field (the element type comes from the field, not from the
    record's own arguments), `len` of a literal as an extent, and placement words as ordinary names."""
    assert run(tmp_path, compile_source(SECOND_USER_FOUND)[0], *SANITIZED, "-Werror").returncode == 0


UNPACK = """
module lib;
pub linear struct Token { id:u64; }
pub struct Open { a:u64; b:Buf[u64]; }
pub fn open(id:u64) -> Token = Token(id);
pub fn close(t:Token) -> u64 { let Token(id) = t; return id; }
module app;
import lib;
import std.vec as vec;
linear struct Conn { token:lib.Token; sent:u64; log:vec.Vec[u64]; }
struct Pair[T] { a:T; b:T; }
fn finish(c:Conn) -> u64 {
  let Conn(token, sent, log) = c;
  let id = lib.close(token);
  return id + sent + u64(log.len);
}
fn split(o:lib.Open) -> usize { let lib.Open(a, b) = o; return len(b) + usize(a); }
pub fn main() -> i32 {
  let t = lib.open(7);
  let mut log = vec.new[u64]();
  vec.push(log, 1);
  vec.push(log, 2);
  let c = Conn(t, 5, log);
  let done = finish(c);
  let p = Pair(1.5, 2.5);
  let mut Pair(x, y) = p;
  x = x + y + p.a;
  let raw = Buf[u64](3);
  let o = lib.Open(1, raw);
  let four = split(o);
  if done != 14 || x != 5.5 || four != 4 { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_record_is_taken_apart_as_it_was_built(tmp_path, cxx):
    """`let Conn(token, sent, log) = c;` consumes the record and binds every field, so a linear value or an owner
    kept inside a record has a way out that leaves no shell behind. Freed exactly once under AddressSanitizer."""
    cpp = compile_source(UNPACK, roots=("app.main",))[0]
    assert canonical_source(UNPACK).count("let Conn(token, sent, log) = c;") == 1
    assert compile_source(canonical_source(UNPACK), roots=("app.main",))[0] == cpp
    assert run(tmp_path, cpp, *sanitized(cxx), *WARNINGS, cxx=cxx, entry="app.main").returncode == 0


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-MOVED", "let raw = Buf[u64](3); let o = lib.Open(1, raw); let lib.Open(a, b) = o; let n = len(o.b);"),
        ("E-PRIVATE", "let t = lib.open(7); let lib.Token(id) = t;"),  # Only its own module ends a linear value.
        ("E-UNPACK", "let p = Pair(1, 2); let Pair(x) = p;"),
        ("E-UNPACK", "let p = Pair(1, 2); let lib.Open(x, y) = p;"),
        ("E-UNPACK", "let v:u64 = 3; let Pair(x, y) = v;"),
        ("E-SHADOW", "let p = Pair(1, 2); let x:u64 = 0; let Pair(x, y) = p;"),
        ("E-LINEAR-LEAK", "let t = lib.open(7); let mut log = vec.new[u64](); let c = Conn(t, 5, log); "
                          "let Conn(token, sent, kept) = c;"),
        ("E-IMMUTABLE", "let p = Pair(1, 2); let Pair(x, y) = p; x = 3;"),
    ],
)  # fmt: skip
def test_taking_apart_obeys_moves_privacy_and_linearity(code, body):
    refused(code, UNPACK + f"fn probe() {{ {body} }}\n")


@pytest.mark.parametrize(
    ("code", "says", "source"),
    [
        ("E-VIEW-ALIAS", "only as a view argument", "fn f() { let mut d = Buf[u64](8); let x = d[0..4]; }"),
        ("E-VIEW-ALIAS", "only as a view argument", "fn f(n:usize, xs:ro<u64>[n]) -> u64 { return xs[0..2][1]; }"),
        ("E-VIEW-ALIAS", "only as a view argument", "fn f(n:usize, xs:ro<u64>[n]) -> bool = xs[0..2] == xs[0..2];"),
        ("E-UNBOUND", "Option is not a type this module can name", "import std.sort as sort;\nfn f(xs:ro<u64>[2]) -> i32 { "
         "match sort.search(2, xs, 7) { Option.Some(i) => { return 1; } Option.None => { return 0; } } }"),
        ("E-PARSE", "shl_wrap(x, k) and shr(x, k)", "fn f(a:u64) -> u64 = a >> 2;"),
        ("E-PARSE", "shl_wrap(x, k) and shr(x, k)", "fn f(a:u64) -> u64 = a << 2;"),
        ("E-REDUCE-OP", "not i64", "fn f(n:usize, xs:ro<i64>[n]) -> i64 { let s = reduce + for i in n yield xs[i]; return s; }"),
        ("E-TYPE-MISMATCH", "Expected ro<u64>[n]@host, got rw<u64>[n]@device",
         "fn g(n:usize, xs:ro<u64>[n]) {} fn f(n:usize, xs:rw<u64>[n]@device) { g(n, xs); }"),
    ],
)  # fmt: skip
def test_a_diagnostic_says_what_to_write_instead(code, says, source):
    with pytest.raises(Diagnostic) as e:
        compile_source(source)
    assert e.value.data["code"] == code and says in e.value.data["message"], e.value.data["message"]


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
    from cairn.projects.build import build
    from cairn.projects.project import load_project

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
    assert run(tmp_path, compile_source(source)[0], "-std=c++20", "-O2", cxx="g++").returncode == -6

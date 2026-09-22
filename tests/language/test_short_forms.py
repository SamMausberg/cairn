"""The short forms: a variant written without its type, an arm without its braces, compound assignment, a call as a
statement, and the rules that keep each one meaning exactly its long form. Every accepted program emits the C++ of its long form or runs natively under both compilers, and every
refusal names its code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import WARNINGS, refused, run, sanitized, watched

LONG = """
import std.core (Option, Result);
enum Op { Read; Write; }
enum Shape { Dot; Line(u64); }
fn half(x:u64) -> Option[u64] {
  if x % 2 != 0 { return Option.None; }
  return Option.Some(x / 2);
}
fn parse(n:usize, bytes:ro<u8>[n]) -> Result[u64, u8] {
  if n == 0 { return Result.Err(1); }
  return Result.Ok(u64(bytes[0]));
}
fn weigh(s:Shape) -> u64 { match s { Shape.Dot => { return 0; } Shape.Line(k) => { return k; } } }
fn owned(n:usize) -> Option[Buf[u8]] {
  if n == 0 { return Option.None; }
  return Option.Some(Buf[u8](n));
}
fn main() -> i32 {
  let empty:Option[u64] = Option.None;
  let mut op = Op.Read;
  if op == Op.Write { return 1; }
  op = Op.Write;
  if Op.Write != op { return 2; }
  match half(6) { Option.Some(v) => { if v != 3 { return 3; } } Option.None => { return 4; } }
  match empty { Option.Some(v) => { return 5; } Option.None => {} }
  match parse("a") { Result.Ok(v) => { if v != 97 { return 6; } } Result.Err(e) => { return 7; } }
  if weigh(Shape.Line(4)) + weigh(Shape.Dot) != 4 { return 8; }
  let nested:Option[Option[u64]] = Option.Some(Option.None);
  match nested {
    Option.Some(inner) => { match inner { Option.Some(x) => { return 9; } Option.None => {} } }
    Option.None => { return 10; }
  }
  match owned(3) { Option.Some(b) => { if len(b) != 3 { return 11; } } Option.None => { return 12; } }
  match owned(0) { Option.Some(b) => { return 13; } Option.None => {} }
  return 0;
}
"""

BARE = """
import std.core (Option, Result);
enum Op { Read; Write; }
enum Shape { Dot; Line(u64); }
fn half(x:u64) -> Option[u64] {
  if x % 2 != 0 { return None; }
  return Some(x / 2);
}
fn parse(n:usize, bytes:ro<u8>[n]) -> Result[u64, u8] {
  if n == 0 { return Err(1); }
  return Ok(u64(bytes[0]));
}
fn weigh(s:Shape) -> u64 { match s { Dot => { return 0; } Line(k) => { return k; } } }
fn owned(n:usize) -> Option[Buf[u8]] {
  if n == 0 { return None; }
  return Some(Buf[u8](n));
}
fn main() -> i32 {
  let empty:Option[u64] = None;
  let mut op = Op.Read;
  if op == Write { return 1; }
  op = Write;
  if Write != op { return 2; }
  match half(6) { Some(v) => { if v != 3 { return 3; } } None => { return 4; } }
  match empty { Some(v) => { return 5; } None => {} }
  match parse("a") { Ok(v) => { if v != 97 { return 6; } } Err(e) => { return 7; } }
  if weigh(Line(4)) + weigh(Dot) != 4 { return 8; }
  let nested:Option[Option[u64]] = Some(None);
  match nested {
    Some(inner) => { match inner { Some(x) => { return 9; } None => {} } }
    None => { return 10; }
  }
  match owned(3) { Some(b) => { if len(b) != 3 { return 11; } } None => { return 12; } }
  match owned(0) { Some(b) => { return 13; } Option.None => {} }
  return 0;
}
"""


def test_a_bare_variant_is_its_qualified_form():
    """Returns, annotated lets, assignments, arguments, payloads, comparisons in either order and match arms:
    the bare program emits the qualified program's C++ exactly, and prints back as it was written."""
    assert compile_source(BARE)[0] == compile_source(LONG)[0]
    assert canonical_source(canonical_source(BARE)) == canonical_source(BARE)
    assert "return None;" in canonical_source(BARE) and "Some(v) =>" in canonical_source(BARE)
    assert compile_source(canonical_source(BARE))[0] == compile_source(BARE)[0]
    assert format_source(format_source(BARE)) == format_source(BARE)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_bare_variants_run_natively(tmp_path, cxx):
    """The owner-carrying `Some(Buf[u8](n))` is released exactly once: AddressSanitizer and its leak check."""
    assert run(tmp_path, compile_source(BARE)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


CLASH = "import std.core (Option);\nstruct Line { a:u64; }\nenum Shape { Dot; Line(Line); }\n"


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-VARIANT-AMBIGUOUS", "fn Some(x:u64) -> Option[u64] = Option.Some(x);\n"
                                "fn f() -> Option[u64] { return Some(1); }"),
        ("E-VARIANT-AMBIGUOUS", "fn f() -> Shape = Line(Line(1));"),  # the record Line, or Shape.Line?
        ("E-VARIANT-AMBIGUOUS", "const None:u64 = 3;\nfn f() -> Option[u64] = None;"),
        ("E-VARIANT-AMBIGUOUS", "fn f(Dot:u64) -> Shape = Dot;"),
        ("E-UNBOUND", "fn f() -> u64 { let x = None; return 0; }"),  # nothing expects a sum here
        ("E-CALLEE", "fn f() -> u64 { let x = Some(3); return 0; }"),
        ("E-ENUM-VARIANT", "fn f() -> Shape = Shape.Circle;"),
        ("E-MATCH-COVERAGE", "fn f(s:Shape) -> u64 { match s { Dot => { return 0; } Circle(r) => { return r; } } }"),
        ("E-MATCH-BINDING", "fn f(s:Shape) -> u64 { match s { Dot(x) => { return 0; } Line(r) => { return r; } } }"),
        ("E-SUM-ARITY", "fn f() -> Option[u64] = Some;"),
    ],
)  # fmt: skip
def test_a_bare_variant_is_refused_where_it_could_mean_something_else(code, body):
    said = refused(code, CLASH + body + "\n")
    if code == "E-UNBOUND":
        assert "None is a variant of Option" in said["message"]


def test_a_private_sum_keeps_its_variants_private():
    """Leaving the type out does not reach past privacy: an arm or a value of a sum another module keeps private
    is refused exactly as `lib.Mode.Fast` is."""
    lib = (
        "module lib;\nenum Mode { Fast; Slow; }\npub fn pick() -> Mode = Mode.Fast;\npub fn cost(m:Mode) -> u64 = 1;\n"
    )
    refused("E-PRIVATE", lib + "module app;\nimport lib;\npub fn main() -> i32 { let c = lib.cost(Fast); return 0; }")
    arms = "match lib.pick() { Fast => { return 0; } Slow => { return 1; } }"
    refused("E-PRIVATE", lib + f"module app;\nimport lib;\npub fn main() -> i32 {{ {arms} }}")


ARMS = """
enum Step { Skip; Stop; Add(u64); Note(u64); }
fn record(log:rw<u64>, v:u64) { log = log * 10 + v; }
fn run(n:usize, steps:ro<Step>[n]) -> u64 {
  let mut total:u64 = 0;
  let mut log:u64 = 0;
  for i in 0..n {
    match steps[i] {
      Skip => continue;
      Stop => break;
      Add(v) => total = total + v;
      Note(v) => record(log, v);
    }
  }
  return total * 1000 + log;
}
fn first(n:usize, steps:ro<Step>[n]) -> u64 { match steps[0] { Add(v) => return v; Skip => return 1; Stop => return 2;
  Note(v) => return v + 10; } }
fn main() -> i32 {
  let mut steps = Buf[Step](6);
  steps[0] = Add(5);
  steps[1] = Skip;
  steps[2] = Note(3);
  steps[3] = Add(7);
  steps[4] = Stop;
  steps[5] = Add(100);
  if run(steps) != 12003 || first(steps) != 5 { return 1; }
  return 0;
}
"""


def braced(source: str) -> str:
    """The same program with every arm written as a block."""
    import re

    return re.sub(r"=> ((?:return|continue|break|total|record)[^;{}]*;)", r"=> { \1 }", source)


def test_an_arm_without_braces_is_its_block():
    """`Skip => continue;` is `Skip => { continue; }`: the same tree, the same C++, and the projection prints the
    short form for both, one line per arm."""
    assert "=> {" not in ARMS and braced(ARMS).count("=> {") == 8
    assert compile_source(ARMS)[0] == compile_source(braced(ARMS))[0]
    assert canonical_source(ARMS) == canonical_source(braced(ARMS))
    assert "      Note(v) => record(log, v);\n" in canonical_source(ARMS)
    laid = format_source(ARMS)  # a match that does not fit one line gets one line per arm
    assert "\n    Skip => return 1;\n    Stop => return 2;\n" in laid and format_source(laid) == laid


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_short_arms_run_natively(tmp_path, cxx):
    assert run(tmp_path, compile_source(ARMS)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


@pytest.mark.parametrize(
    "arm",
    [
        "Add(v) => let w = v;",  # a binding would end with the arm that makes it
        "Add(v) => if v > 1 { return 1; }",
        "Add(v) => while v > 1 { break; }",
        "Add(v) => for i in 0..v { }",
        "Add(v) => defer record(log, v);",
    ],
)
def test_an_arm_without_braces_is_one_simple_statement(arm):
    head = "enum Step { Skip; Add(u64); }\nfn record(log:rw<u64>, v:u64) {}\n"
    body = f"fn f(s:Step) -> u64 {{ let mut log:u64 = 0; match s {{ Skip => return 0; {arm} }} return 0; }}"
    said = refused("E-PARSE", head + body)
    assert "one return, break, continue, assignment or call" in said["message"]


COMPOUND = """
struct Tally { hits:u64; bins:Buf[u32]; }
fn bump(seen:rw<u64>) { seen += 1; }
fn scale(n:usize, out:rw<u64>[n], x:ro<u64>[n]) { parallel i in n { out[i] += x[i] * 3; } }
fn main() -> i32 {
  let mut x:u64 = 10;
  x += 5;
  x -= 3;
  x *= 4;
  x /= 6;
  x %= 5;
  let mut bits:u32 = 0xf0;
  bits &= 0x3c;
  bits |= 1;
  bits ^= 0x21;
  let mut f:f64 = 1.5;
  f *= 2.0;
  let mut seen:u64 = 0;
  bump(seen);
  let mut t = Tally(0, Buf[u32](4));
  t.hits += 7;
  t.bins[2] += 9;
  t.bins[2] *= 2;
  let n:usize = 40000;
  let mut out = Buf[u64](n);
  let mut ones = Buf[u64](n);
  for i in 0..n { ones[i] += 1; }
  scale(out[0..n], ones[0..n]);
  let mut s:i32 = -7;
  s -= 1;
  if x != 3 || bits != 0x10 || f != 3.0 || seen != 1 || t.hits != 7 || t.bins[2] != 18 { return 1; }
  if out[n - 1] != 3 || s != -8 { return 2; }
  return 0;
}
"""


def written_out(source: str) -> str:
    """Every `p op= v;` as `p = p op v;`."""
    import re

    return re.sub(r"([\w.\[\]]+) ([-+*/%&|^])= ([^;]+);", r"\1 = \1 \2 \3;", source)


def test_compound_assignment_is_its_long_form():
    """`x += 5` checks as `x = x + 5`: the same effect rows and guard counts in the receipt, and for a place without
    an index the same C++ line. With an index the emitter finds the element once, so it pays one guard, not two."""
    short, long = compile_source(COMPOUND), compile_source(written_out(COMPOUND))
    assert "+=" not in written_out(COMPOUND) and "t.bins[2] = t.bins[2] * 2;" in written_out(COMPOUND)
    for name, row in long[1]["functions"].items():
        assert short[1]["functions"][name]["effects"] == row["effects"], name
    assert "v_x = cr::add<std::uint64_t>(v_x, static_cast<std::uint64_t>(5ULL));" in short[0]
    assert short[0].count("cr::at((v_t).v_bins.data()") == 3 and long[0].count("cr::at((v_t).v_bins.data()") == 5
    assert canonical_source(COMPOUND).count("+=") == COMPOUND.count("+=")
    assert (
        compile_source(canonical_source(COMPOUND))[0] == compile_source(canonical_source(canonical_source(COMPOUND)))[0]
    )
    assert format_source(COMPOUND) == format_source(format_source(COMPOUND)) and "  bits ^= 0x21;\n" in format_source(
        COMPOUND
    )


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_compound_assignment_runs_natively(tmp_path, cxx):
    assert run(tmp_path, compile_source(COMPOUND)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


def test_compound_assignment_in_lanes_is_race_free(tmp_path):
    """`out[i] += x[i] * 3` in a region of 40000 lanes on the host pool, under ThreadSanitizer."""
    done = watched(tmp_path, compile_source(COMPOUND)[0], "clang++", "thread")
    assert done.returncode == 0, done.stderr[-2000:]


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_compound_assignment_traps_where_its_long_form_does(tmp_path, cxx):
    """Checked arithmetic stays checked: `x += big` past the maximum aborts, and so does the written-out form."""
    trap = "fn main() -> i32 { let mut x:i32 = 2; x += 2147483647; return x; }"
    for source in [trap, written_out(trap)]:
        assert run(tmp_path, compile_source(source)[0], *sanitized(cxx)[:4], cxx=cxx).returncode == -6


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-IMMUTABLE", "let x:u64 = 1; x += 1;"),
        ("E-OPERATOR", "let mut f = true; f |= false;"),  # bitwise forms are unsigned, as their operators are
        ("E-OPERATOR", "let mut s:i32 = 1; s &= 3;"),
        ("E-TYPE-MISMATCH", "let mut x:u64 = 1; x += 1.5;"),
        ("E-TYPE-MISMATCH", "let mut b = Buf[u8](2); b += 1;"),
        ("E-EXTENT-FIELD", "let mut c = C(2, Buf[f64](2)); c.rows += 1;"),
        ("E-LEASED", "let mut d = Buf[u64](4); let t = spawn fill(d); d[0] += 1; wait(t);"),
        ("E-EFFECT-ORDER", "let mut b = Buf[i32](2); b[0] += say(65);"),
        ("E-NAME", "let mut x:u64 = 1; x +%= 1;"),  # wrapping arithmetic stays by name: x = add_wrap(x, 1)
    ],
)  # fmt: skip
def test_compound_assignment_keeps_every_rule_of_its_long_form(code, body):
    head = ("struct C { rows:usize; p:Buf[f64][rows]; }\nfn fill(n:usize, d:rw<u64>[n]) { d[0] = 1; }\n"
            "extern fn putchar(c:i32) -> i32 effects(io);\nfn say(c:i32) -> i32 { unsafe { return putchar(c); } }\n")  # fmt: skip
    refused(code, head + f"fn main() -> i32 {{ {body} return 0; }}\n")


CALLS = """
import std.core (Option, Result);
import std.vec;
fn count(log:rw<u64>) -> u64 { log += 1; return log; }
fn make(n:usize) -> Buf[u8] = Buf[u8](n);
fn check(v:u64) -> Result[u64, u8] { if v > 9 { return Err(1); } return Ok(v); }
fn step(v:u64) -> Result[u64, u8] {
  try check(v);
  let _ = check(v + 100);
  let _ = check(v + 200);
  return Ok(v);
}
fn churn() { make(8); }
fn main() -> i32 {
  let mut log:u64 = 0;
  count(log);
  count(log);
  churn();
  let b = make(4);
  let _ = b;
  let mut v = vec.new[Buf[u8]]();
  let two = make(2);
  v.push(two);
  let three = make(3);
  v.push(three);
  take(v.data[0]);
  let _ = v.pop();
  match step(3) { Ok(x) => { if x != 3 { return 2; } } Err(e) => return 3; }
  match step(12) { Ok(x) => return 4; Err(e) => { if e != 1 { return 5; } } }
  if log != 2 { return 1; }
  return 0;
}
"""


def test_a_call_statement_drops_what_it_returns():
    """`count(log);` drops a u64, `make(8);` releases the Buf it made where the statement ends (so churn's row is
    alloc and free), `try check(v);` drops the success payload, and `let _ = e;` binds nothing, so it repeats."""
    cpp, receipt = compile_source(CALLS)
    assert {"alloc", "free"} <= set(receipt["functions"]["churn"]["effects"])
    assert (
        "static_cast<void>(cf_count(v_log));" in cpp
        and "static_cast<void>(cr::Buf<std::uint8_t>(std::move(v_b)));" in cpp
    )
    assert compile_source(canonical_source(CALLS))[0] == cpp and format_source(format_source(CALLS)) == format_source(
        CALLS
    )


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_dropped_owners_are_released_once(tmp_path, cxx):
    """Every Buf a statement drops, or `let _` lets go, is freed exactly once: AddressSanitizer and its leak check."""
    assert run(tmp_path, compile_source(CALLS)[0], *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


@pytest.mark.parametrize(
    ("code", "body"),
    [
        ("E-DISCARD", "check(3);"),  # an outcome is handled, or let go by name
        ("E-DISCARD", "let mut v = vec.new[u64](); v.pop();"),
        ("E-DISCARD", "let x:u64 = 1; x + 1;"),  # only a call is a statement
        ("E-DISCARD", "let x:u64 = 1; min(x, 2);"),  # and one that only computes does nothing
        ("E-LINEAR-LEAK", "open(7);"),
        ("E-LINEAR-LEAK", "let _ = open(7);"),
        ("E-UNBOUND", "let _ = check(1); let y = _;"),
        ("E-SPAWN", "let _ = spawn work(3);"),  # a task is always named
    ],
)
def test_a_call_statement_never_loses_an_outcome_or_a_linear_value(code, body):
    head = ("import std.core (Option, Result);\nimport std.vec;\nlinear struct Token { id:u64; }\n"
            "fn open(id:u64) -> Token = Token(id);\nfn check(v:u64) -> Result[u64, u8] = Ok(v);\n"
            "fn work(n:usize) -> u64 = 1;\n")  # fmt: skip
    refused(code, head + f"fn main() -> i32 {{ {body} return 0; }}\n")

"""The short forms: a variant written without its type, and the rules that keep each one meaning exactly its long
form. Every accepted program emits the C++ of its long form or runs natively under both compilers, and every
refusal names its code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import WARNINGS, refused, run, sanitized

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

"""Generic calls typed where they are written, bounds by trait, kind and scalar class, natural parameters,
and templates certified once against their bounds.
"""

from cairn.compiler.cairnc import compile_source
from emitted import SANITIZED, refused, run

ACROSS_MODULES = """
module m;
pub fn twice[T:numeric](x:T) -> T = x + x;
pub fn keep[T:affine](x:T) -> T = x;
pub fn mean[T:numeric](n:usize, xs:ro<T>[n]) -> T { let mut t:T = 0; for i in 0..n { t = t + xs[i]; } return t / T(n); }
module app;
import m;
struct P { a:u32; b:f64; }
fn g[U:numeric](x:U) -> U = m.twice(x + U(1));
fn h[U:copy](n:usize) -> usize { let raw = Buf[U](n); let b = m.keep(raw); return len(b); }
fn wrap[T](n:u32) -> T = T(n);
pub fn main() -> i32 {
  let p = P(4, 3.0);
  stack a:u32[4] = zeroed;
  stack b:f64[2] = zeroed;
  a[0] = 8;
  b[1] = 3.0;
  let made:P = P(wrap(7), 0.5);
  let boxed:P = made;
  let four = h[u8](4);
  if g(3) != 8 || g(1.5) != 5.0 || four != 4 || m.twice(p.a) != 8 || m.twice(p.b) != 6.0 { return 1; }
  if m.mean(4, a) != 2 || m.mean(2, b) != 1.5 || boxed.a != 7 { return 2; }
  return 0;
}
"""


def test_a_generic_call_types_its_arguments_where_they_are_written(tmp_path):
    """The template's module decides what its own text means, never what the caller's arguments mean: a private
    field, or the caller's own type parameter, in an argument of another module's generic. `T(x)` converts (or
    constructs) at the instance's T, which is how a class-bounded template computes a mean."""
    cpp = compile_source(ACROSS_MODULES, roots=("app.main",))[0]
    assert run(tmp_path, cpp, *SANITIZED, "-Werror", entry="app.main").returncode == 0


def test_a_nat_parameter_is_a_static_extent_and_literals_take_the_expected_result_type(tmp_path):
    """Two false rejections the second audit noted: `rw<u64>[K]` of a `[K:nat]` instance, and `let y:u32 = conv(3)`."""
    source = (
        "fn fill[K:nat](out:rw<u64>[K], v:u64) { for i in 0..K { out[i] = v; } }\n"
        "family fill_n = fill[5..6];\nfn conv[T](x:T) -> T = x;\n"
        "fn main() -> i32 { stack a:u64[4] = zeroed; stack b:u64[5] = zeroed; fill[4](a, 7); fill_n_5(b, 2);\n"
        "  let y:u32 = conv(3); let z:u8 = conv(200);\n"
        "  if a[3] != 7 || b[4] != 2 || y != 3 || z != 200 { return 1; }\n  return 0; }"
    )
    assert run(tmp_path, compile_source(source)[0], *SANITIZED).returncode == 0
    refused("E-TYPE-MISMATCH", source.replace("fill[4](a, 7)", "fill[8](a, 7)"))


def test_a_value_without_elements_where_a_generic_view_is_expected_is_a_mismatch():
    """`sort.sort(x)` of a scalar or a record ended the check with an internal IndexError; it is a type mismatch."""
    for given in ("let mut x:u8 = 3;", "let mut x = P(1);"):
        source = (
            f"import std.sort as sort;\nstruct P {{ a:u8; }}\nfn main() -> i32 {{ {given} sort.sort(x); return 0; }}"
        )
        assert "has no elements: it does not fit rw<T>[n]" in refused("E-TYPE-MISMATCH", source)["message"]


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
    from cairn.compiler.cairnc import certify_templates

    verdicts = {n: v for n, v in certify_templates(GENERIC_BOUNDS).items() if not n.startswith("std.")}
    assert {n for n, v in verdicts.items() if v == "ok"} == {"larger", "biggest", "pass", "scale"}
    assert verdicts["largest"].startswith("E-OPERATOR")  # `<` is not something a bare T promises.
    assert verdicts["twice"].startswith("E-MOVED")  # A T may be an owner.
    assert verdicts["ignore"].startswith("E-LINEAR-LEAK") and verdicts["keep"].startswith("E-LINEAR-LEAK")  # Or linear.
    assert verdicts["scale"] == "ok"  # A natural's witnesses are its family's instances.
    assert compile_source(GENERIC_BOUNDS)  # None of this changes what is accepted: instances are still checked.


CEILINGS = """
extern fn getpid() -> i32 effects(io);
trait Quiet { fn id(self:ro<Self>) -> u64 pure; }
trait Loud { fn pid(self:ro<Self>) -> u64; }
fn ask[T:Quiet](x:ro<T>) -> u64 pure = id(x);
fn lanes[T:Quiet + copy](n:usize, o:rw<u64>[n], c:T) { parallel i in n { o[i] = id(c); } }
fn loose[T:Loud](x:ro<T>) -> u64 pure = pid(x);
fn lanes_loose[T:Loud + copy](n:usize, o:rw<u64>[n], c:T) { parallel i in n { o[i] = pid(c); } }
fn unceilinged[T:Loud](x:ro<T>) -> u64 = pid(x);
"""


def test_a_verdict_covers_the_rules_that_need_every_row():
    """`ok` is about ceilings and lanes too. A bound promises what its trait's members declare, every impl is held
    to that, and a member that declares nothing may do anything, which no `pure` template or lane can absorb."""
    from cairn.compiler.cairnc import certify_templates

    verdicts = certify_templates(CEILINGS)
    assert {n for n, v in verdicts.items() if v == "ok"} == {"ask", "lanes", "unceilinged"}
    assert verdicts["loose"].startswith("E-EFFECT-CEILING") and "give that member a ceiling" in verdicts["loose"]
    assert verdicts["lanes_loose"].startswith("E-PARALLEL-CALL") and "bound:Loud.pid" in verdicts["lanes_loose"]
    noisy = "struct C { v:u64; }\nimpl Loud for C { fn pid(self:ro<C>) -> u64 { unsafe { return u64(getpid()); } } }\n"
    refused(
        "E-EFFECT-CEILING", CEILINGS + noisy + "fn main() -> i32 { let c = C(1); return i32(loose(c)); }"
    )  # The instance the old verdict called fine.
    broken = certify_templates(CEILINGS + "fn main() -> i32 { let x:u64 = true; return 0; }")
    assert set(broken.values()) == {"unknown: the program does not check (E-TYPE-MISMATCH)"}  # Unknown is never ok.


BOUNDED = """
import std.core (Ord, Option);
linear struct Token { id:u64; }
struct Pair[T: copy] { a:T; b:T; }
fn largest[T: numeric](a:T, b:T) -> T { if a < b { return b; } return a; }
fn clamp[T: integer](x:T, lo:T, hi:T) -> T = max(lo, min(x, hi));
fn twice[T: copy](x:T) -> Pair[T] = Pair(x, x);
fn ignore[T: affine](x:T) -> u64 = 0;
fn stash[T: affine](x:T) -> Buf[T] { let mut b = Buf[T](1); b[0] = x; return b; }
fn best[T: Ord + copy](n:usize, xs:ro<T>[n]) -> Option[T] {
  if n == 0 { return Option.None; }
  let mut at:usize = 0;
  for i in 1..n { if less(xs[at], xs[i]) { at = i; } }
  return Option.Some(xs[at]);
}
fn main() -> i32 {
  let kept = stash(Buf[u64](3));
  let pair = twice(4);
  if largest(3, 9) != 9 || largest(2.5, 1.5) != 2.5 || clamp(5, 1, 3) != 3 || ignore(kept) != 0 || pair.b != 4 { return 1; }
  return 0;
}
"""


def test_kind_and_class_bounds_are_promises_checked_at_the_call_and_certified_once(tmp_path):
    from cairn.compiler.cairnc import certify_templates

    assert set(certify_templates(BOUNDED).values()) == {"ok"}  # `numeric` means: checked at every numeric type.
    promised = {
        "let b = Buf[u64](1); let r = twice(b);": "Buf[u64] is affine, not copy; twice needs [T:copy]",
        "let r = ignore(Token(1));": "Token is linear, not affine; ignore needs [T:affine]",
        "let r = clamp(1.5, 0.5, 2.5);": "f64 is not integer; clamp needs [T:integer]",
        "let p = Pair(Buf[u64](1), Buf[u64](1));": "Buf[u64] is affine, not copy; Pair needs [T:copy]",
    }
    for call, why in promised.items():
        assert why in refused("E-BOUND", BOUNDED.replace("fn main() -> i32 {", "fn main() -> i32 { " + call))["message"]
    broken = BOUNDED.replace(
        "fn ignore[T: affine](x:T) -> u64 = 0;", "fn ignore[T: affine](x:T) -> u64 { let a = x; let b = x; return 0; }"
    )
    assert certify_templates(broken)["ignore"].startswith("E-MOVED")  # affine promises one use, not two.
    assert run(tmp_path, compile_source(BOUNDED)[0], *SANITIZED).returncode == 0

import random
import shutil
import subprocess

import pytest

from cairn.cairnc import RUNTIME_FILES, compile_source
from cairn.scalar_semantics import Concrete, bounds, decoded, encoded, equivalent, identical, prepared, rounded
from cairn.smt_bridge import Solver
from cairn.syntax import CPP


def fn(body, params="x:u64", ret="u64"):
    return f"fn f({params})->{ret}{{{body}}}"


def check(a, b, expected="smt-equivalent", **kw):
    r = equivalent(a, b, "f", **kw)
    assert r["status"] == expected, r
    assert not r["lean_verified"] and not r["native_verified"]
    return r


def refute(a, b, **kw):
    """A rejection counts only when the reported input, replayed independently, really separates the two."""
    r = check(a, b, "counterexample", **kw)
    src, ret = prepared(a), prepared(a).functions["f"].ret
    x = Concrete(src).outcome("f", r["counterexample"])
    y = Concrete(prepared(b)).outcome("f", r["counterexample"])

    def agrees(p, q):
        return p["defined"] == q["defined"] and (not p["defined"] or identical(src, ret, p["return"], q["return"]))

    assert agrees(x, r["expected"]) and agrees(y, r["actual"]) and not agrees(x, y), (r, x, y)
    return r


@pytest.mark.parametrize("ty", ["u8", "u16", "u32", "u64", "usize"])
def test_wrap_and_checked_boundary(ty):
    r = check(fn("return add_wrap(x,1);", f"x:{ty}", ty), fn("return x+1;", f"x:{ty}", ty), "counterexample")
    assert r["counterexample"]["x"] == (1 << ({"u8": 8, "u16": 16, "u32": 32}.get(ty, 64))) - 1
    assert r["expected"]["return"] == 0 and not r["actual"]["defined"]


@pytest.mark.parametrize("ty", ["u8", "u16", "u32", "u64", "usize", "i32", "i64"])
def test_min_branch(ty):
    check(fn("if x<y{return x;}else{return y;}", f"x:{ty},y:{ty}", ty), fn("return min(x,y);", f"x:{ty},y:{ty}", ty))


@pytest.mark.parametrize("ty", ["u8", "u16", "u32", "u64"])
def test_average_total(ty):
    check(
        fn("return (x/2)+(y/2)+((x&1)&(y&1));", f"x:{ty},y:{ty}", ty),
        fn("return (x&y)+shr(x^y,1);", f"x:{ty},y:{ty}", ty),
    )


@pytest.mark.parametrize("op", ["+", "-", "*", "/", "%"])
def test_partial_arithmetic_identity(op):
    a = fn(f"return x{op}y;", "x:u8,y:u8", "u8")
    check(a, a, allow_reference_traps=True)


@pytest.mark.parametrize("ty", ["i32", "i64"])
def test_signed_negation(ty):
    r = check(fn("return x;", f"x:{ty}", ty), fn("return -(-x);", f"x:{ty}", ty), "counterexample")
    assert r["counterexample"]["x"] == -(1 << (int(ty[1:]) - 1))


@pytest.mark.parametrize(
    "params,ref,other",
    [
        ("x:bool,y:bool", "x<y", "(!x)&&y"),
        ("x:bool,y:bool", "x<=y", "(!x)||y"),
        ("x:bool,y:bool", "x>y", "x&&(!y)"),
        ("x:bool,y:bool", "x>=y", "x||(!y)"),
        ("x:bool,y:bool", "x==y", "!(x!=y)"),
    ],
)
def test_bool_order(params, ref, other):
    check(fn("return " + ref + ";", params, "bool"), fn("return " + other + ";", params, "bool"))


@pytest.mark.parametrize("ty", ["u8", "u16", "u32", "u64"])
def test_saturating_add(ty):
    m = (1 << int(ty[1:])) - 1
    a = fn(f"if x>{m}-y{{return {m};}}return x+y;", f"x:{ty},y:{ty}", ty)
    b = fn(f"let z=add_wrap(x,y);if z<x{{return {m};}}return z;", f"x:{ty},y:{ty}", ty)
    check(a, b)


@pytest.mark.parametrize("expr,correct", [("x!=0 && x/x==1", "x!=0"), ("x==0 || x/x==1", "true")])
def test_short_circuit(expr, correct):
    check(fn("return " + expr + ";", ret="bool"), fn("return " + correct + ";", ret="bool"))


def test_eager_condition_is_not_lazy():
    check(fn("return x==0 || x/x==1;", ret="bool"), fn("return x/x==1 || x==0;", ret="bool"), "counterexample")


@pytest.mark.parametrize("src,dst", [("u8", "u32"), ("u8", "i32"), ("i32", "i64"), ("u32", "i64")])
def test_widen_roundtrip(src, dst):
    check(fn("return x;", f"x:{src}", src), fn(f"return {src}({dst}(x));", f"x:{src}", src))


def test_narrow_domain_and_vacuity():
    a = fn("return u8(x);", ret="u8")
    b = fn("return u8(x&255);", ret="u8")
    check(a, b, assume="x<=255")
    check(a, b, "invalid-reference")
    check(a, b, "counterexample", allow_reference_traps=True)
    check(a, b, "invalid-domain", assume="x<x")
    check(a, b, "invalid-domain", assume="x/x==1")


def test_branch_assignment_and_scope():
    a = fn("let mut a=x;if x<3{let v=1;a=v;}else{a=2;}return a;")
    b = fn("if x<3{return 1;}else{return 2;}")
    check(a, b)


def test_user_call_and_early_return():
    a = "fn helper(x:u64)->u64{if x>9{return 9;}return x;}" + fn("return helper(x);")
    b = "fn helper(x:u64)->u64{if x>9{return 9;}return x;}" + fn("return min(x,9);")
    check(a, b)


@pytest.mark.parametrize(
    "source",
    [
        fn("let mut a=x;while a>0{a=a-1;}return a;"),  # An unbounded trip count.
        fn("return f(x);"),
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{return x[0];}",
        "fn f(x:u64){return;}",
        fn("buffer a:u64[4]=zeroed;return a[0];"),
        fn("let mut b=Buf[u64](4);return b[0];"),
        "struct P{a:u64;}\nfn g(p:rw<P>)->u64{p.a=1;return p.a;}\nfn f(x:u64)->u64{let mut p=P(x);return g(p);}",
        "enum Box{Full(Buf[u64]);Empty;}\n"
        + fn("let b=Box.Full(Buf[u64](4));match b{Box.Full(v)=>{return len(v);} Box.Empty=>{return 0;}}", ret="usize"),
        fn("let mut t:u64=0;for i in 0..40{t=add_wrap(t,u64(i));}return t;"),  # Past the unrolling budget.
        "enum Res{Ok(u64);Bad(u32);}\nfn parse(x:u64)->Res{if x>9{return Res.Bad(1);}return Res.Ok(x);}\n"
        "fn f(x:u64)->Res{return Res.Ok(add_wrap(try parse(x),1));}",  # A try nested in a larger expression.
    ],
)
def test_unsupported_not_proved(source):
    check(source, source, "unknown")


# Records ------------------------------------------------------------------------------------

PAIR = "struct Pair { a:u64; b:u64; }\n"
NEST = PAIR + "struct Box { p:Pair; flag:bool; }\n"


def test_record_fields_construction_and_return():
    check(PAIR + fn("let p=Pair(x,add_wrap(x,1));return add_wrap(p.a,p.b);", ret="u64"),
          PAIR + fn("return add_wrap(add_wrap(x,x),1);"))  # fmt: skip
    check(PAIR + "fn f(p:Pair)->Pair=Pair(p.b,p.a);", PAIR + "fn f(p:Pair)->Pair{let q=Pair(p.b,p.a);return q;}")
    check(NEST + "fn f(b:Box)->u64{if b.flag{return b.p.a;}return b.p.b;}",
          NEST + "fn f(b:Box)->u64{let mut r=b.p.b;if b.flag{r=b.p.a;}return r;}")  # fmt: skip


def test_record_field_assignment_near_miss():
    r = refute(PAIR + "fn f(p:Pair)->Pair{let mut q=p;q.a=q.b;return q;}",
               PAIR + "fn f(p:Pair)->Pair{let mut q=p;q.b=q.a;return q;}")  # fmt: skip
    assert r["counterexample"]["p"]["a"] != r["counterexample"]["p"]["b"]


def test_a_read_only_borrow_of_a_record_reads_as_its_value():
    g = PAIR + "fn g(p:ro<Pair>)->u64=p.a;\n"
    check(g + "fn f(p:Pair)->u64{return g(p);}", g + "fn f(p:Pair)->u64=p.a;")
    refute(g + "fn f(p:Pair)->u64{return g(p);}", g + "fn f(p:Pair)->u64=p.b;")


def test_a_record_redefined_in_the_candidate_is_not_a_signature_match():
    r = equivalent(PAIR + "fn f(p:Pair)->u64=p.a;", "struct Pair { a:u64; b:u32; }\nfn f(p:Pair)->u64=p.a;", "f")
    assert r["status"] == "invalid-contract"


# Enums, sums, match and try -----------------------------------------------------------------

OP = "enum Op { Read; Write; }\n"
MAYBE = "enum Maybe { Some(u64); None; }\n"
RESULT = "enum Res { Ok(u64); Bad(u32); }\n"
PARSE = RESULT + "fn parse(x:u64)->Res{if x>99{return Res.Bad(u32(x&255));}return Res.Ok(x);}\n"


def test_tag_only_enum_equality_is_the_match():
    check(OP + "fn f(o:Op)->bool=o==Op.Read;",
          OP + "fn f(o:Op)->bool{match o{Op.Read=>{return true;} Op.Write=>{return false;}}}")  # fmt: skip
    r = refute(OP + "fn f(o:Op)->bool=o==Op.Read;", OP + "fn f(o:Op)->bool=o!=Op.Read;")
    assert r["counterexample"]["o"] == {"variant": "Read"} or r["counterexample"]["o"] == {"variant": "Write"}


def test_payload_sum_match_and_reconstruction():
    check(MAYBE + "fn f(m:Maybe)->Maybe{match m{Maybe.Some(v)=>{return Maybe.Some(v);} Maybe.None=>{return Maybe.None;}}}",
          MAYBE + "fn f(m:Maybe)->Maybe=m;")  # fmt: skip
    check(MAYBE + "fn f(m:Maybe)->u64{match m{Maybe.Some(v)=>{return v;} Maybe.None=>{return 0;}}}",
          MAYBE + "fn f(m:Maybe)->u64{match m{Maybe.None=>{return 0;} Maybe.Some(v)=>{return v;}}}")  # fmt: skip


def test_payload_sum_near_miss():
    r = refute(MAYBE + "fn f(m:Maybe)->u64{match m{Maybe.Some(v)=>{return v;} Maybe.None=>{return 0;}}}",
               MAYBE + "fn f(m:Maybe)->u64{match m{Maybe.Some(v)=>{return v;} Maybe.None=>{return 1;}}}")  # fmt: skip
    assert r["counterexample"]["m"] == {"variant": "None"}
    assert (r["expected"]["return"], r["actual"]["return"]) == (0, 1)


def test_an_inactive_payload_is_not_observed():
    """Two ways to build Maybe.None differ only in storage the emitter never reads."""
    check(MAYBE + "fn f(x:u64)->Maybe{let m=Maybe.Some(x);match m{Maybe.Some(v)=>{return Maybe.None;} Maybe.None=>{return Maybe.None;}}}",
          MAYBE + "fn f(x:u64)->Maybe=Maybe.None;")  # fmt: skip


def test_try_propagates_the_failure():
    check(PARSE + "fn f(x:u64)->Res{let v=try parse(x);return Res.Ok(add_wrap(v,1));}",
          PARSE + "fn f(x:u64)->Res{if x>99{return Res.Bad(u32(x&255));}return Res.Ok(add_wrap(x,1));}")  # fmt: skip


def test_try_near_miss_keeps_going_after_a_failure():
    r = refute(PARSE + "fn f(x:u64)->Res{let v=try parse(x);return Res.Ok(add_wrap(v,1));}",
               PARSE + "fn f(x:u64)->Res{let mut v:u64=0;match parse(x){Res.Ok(k)=>{v=k;} Res.Bad(c)=>{v=u64(c);}}return Res.Ok(add_wrap(v,1));}")  # fmt: skip
    assert r["counterexample"]["x"] > 99 and r["expected"]["return"]["variant"] == "Bad"


def test_only_well_formed_tags_are_quantified_over():
    r = check(OP + "fn f(o:Op)->u64{match o{Op.Read=>{return 0;} Op.Write=>{return 1;}}}",
              OP + "fn f(o:Op)->u64{if o==Op.Read{return 0;}return 1;}")  # fmt: skip
    assert "every tag names a declared variant" in r["quantification"]


# Floating point -----------------------------------------------------------------------------

FINITE = "x==x && y==y"


def test_float_doubling_and_addition_agree():
    check(fn("return x*2.0;", "x:f64", "f64"), fn("return x+x;", "x:f64", "f64"), assume="x==x")
    check(fn("return x*2.0;", "x:f32", "f32"), fn("return x+x;", "x:f32", "f32"), assume="x==x")


def test_float_addition_is_not_associative():
    a = fn("return (x+y)+z;", "x:f64,y:f64,z:f64", "f64")
    b = fn("return x+(y+z);", "x:f64,y:f64,z:f64", "f64")
    refute(a, b, assume="x==x && y==y && z==z")


def test_signed_zero_is_part_of_the_value():
    refute(fn("return x;", "x:f64", "f64"), fn("return x+0.0;", "x:f64", "f64"), assume="x==x")


def test_float_comparisons_order_the_same_way():
    check(fn("return x<y;", "x:f64,y:f64", "bool"), fn("return y>x;", "x:f64,y:f64", "bool"))
    check(fn("return x!=x;", "x:f64", "bool"), fn("return !(x==x);", "x:f64", "bool"))
    refute(fn("if x<y{return y;}return x;", "x:f64,y:f64", "f64"),
           fn("if x>=y{return x;}return y;", "x:f64,y:f64", "f64"))  # fmt: skip
    check(fn("if x<y{return y;}return x;", "x:f64,y:f64", "f64"),
          fn("if x>=y{return x;}return y;", "x:f64,y:f64", "f64"), assume=FINITE)  # fmt: skip


def test_float_width_conversions():
    check(fn("return x;", "x:f32", "f32"), fn("return f32(f64(x));", "x:f32", "f32"), assume="x==x")
    refute(fn("return x;", "x:f64", "f64"), fn("return f64(f32(x));", "x:f64", "f64"), assume="x==x")


def test_an_integer_literal_beside_a_float_is_a_float():
    check(fn("return x*2;", "x:f64", "f64"), fn("return x+x;", "x:f64", "f64"), assume="x==x")
    check(fn("return x*2;", "x:f32", "f32"), fn("return x*2.0;", "x:f32", "f32"), assume="x==x")


def test_integer_to_float_rounds_once():
    """f32(n) is one rounding; going through f64 rounds twice and is a different function."""
    check(fn("return f64(n);", "n:u64", "f64"), fn("return f64(n)*1.0;", "n:u64", "f64"))
    r = refute(fn("return f32(n);", "n:u64", "f32"), fn("return f32(f64(n));", "n:u64", "f32"))
    assert r["counterexample"]["n"] > 2**53


def test_checked_float_to_integer_truncation():
    a = fn("return u8(x);", "x:f64", "u8")
    check(a, a, allow_reference_traps=True)
    check(a, fn("return u8(x);", "x:f64", "u8"), assume="x>=0.0 && x<255.0")
    check(a, a, "invalid-reference")
    refute(fn("if x<0.0{return 0;}return u32(x);", "x:f64", "u32"), fn("return u32(x);", "x:f64", "u32"),
           allow_reference_traps=True)  # fmt: skip


def test_a_returned_nan_is_unknown_not_equivalent():
    r = check(fn("return x*2.0;", "x:f64", "f64"), fn("return x+x;", "x:f64", "f64"), "unknown")
    assert "NaN" in r["reason"] and r["counterexample"]["x"] != r["counterexample"]["x"]
    check(
        "struct V{x:f64;y:f64;}\nfn f(v:V)->f64=v.x+v.y;", "struct V{x:f64;y:f64;}\nfn f(v:V)->f64=v.y+v.x;", "unknown"
    )


# Bounded loops ------------------------------------------------------------------------------


def test_for_loop_is_its_unrolling():
    check(fn("let mut t=x;for i in 0..4{t=add_wrap(t,u64(i));}return t;"), fn("return add_wrap(x,6);"))
    check(fn("let mut t:u64=0;for i in 2..2{t=1;}return t;"), fn("return 0;"))


def test_for_loop_near_miss():
    r = refute(fn("let mut t=x;for i in 0..4{t=add_wrap(t,u64(i));}return t;"), fn("return add_wrap(x,7);"))
    assert r["expected"]["return"] != r["actual"]["return"]


def test_break_and_continue():
    check(fn("let mut t:u64=0;for i in 0..10{if u64(i)>=x{break;}t=add_wrap(t,1);}return t;"),
          fn("return min(x,10);"), assume="x<=32")  # fmt: skip
    check(fn("let mut t:u64=0;for i in 0..6{if (i&1)==1{continue;}t=add_wrap(t,u64(i));}return t;"), fn("return 6;"))
    refute(fn("let mut t:u64=0;for i in 0..6{if (i&1)==1{continue;}t=add_wrap(t,u64(i));}return t;"), fn("return 9;"))


def test_bounded_while_loop():
    check(fn("let mut a=x;let mut t:u64=0;while a>0{t=add_wrap(t,a);a=a-1;}return t;"),
          fn("return (x*(x+1))/2;"), assume="x<=10")  # fmt: skip
    check(fn("let mut a=x;while a>0{a=a-1;}return a;"), fn("return 0;"), assume="x<=16")


def test_a_loop_past_the_budget_is_unknown_not_equivalent():
    r = check(fn("let mut a=x;while a>0{a=a-1;}return a;"), fn("return 0;"), "unknown", assume="x<=17")
    assert "unrolling budget" in r["reason"]


# Fixed local storage ------------------------------------------------------------------------


def test_stack_array_is_zeroed_and_addressable():
    check(fn("stack a:u64[4]=zeroed;a[0]=x;a[1]=add_wrap(x,1);return add_wrap(a[0],a[1]);"),
          fn("return add_wrap(add_wrap(x,x),1);"))  # fmt: skip
    check(fn("stack a:u64[8]=zeroed;return a[7];"), fn("return 0;"))
    check(fn("stack a:u64[4]=zeroed;for i in 0..4{a[i]=add_wrap(x,u64(i));}let mut t:u64=0;"
             "for i in 0..4{t=add_wrap(t,a[i]);}return t;"),
          fn("return add_wrap(mul_wrap(x,4),6);"))  # fmt: skip


def test_stack_array_bounds_are_a_trap():
    a = fn("stack s:u64[4]=zeroed;s[0]=x;return s[i];", "x:u64,i:usize")
    check(a, a, "invalid-reference")
    check(a, a, allow_reference_traps=True)
    refute(a, fn("stack s:u64[4]=zeroed;s[0]=x;if i<4{return s[i];}return 0;", "x:u64,i:usize"),
           allow_reference_traps=True)  # fmt: skip


def test_array_value_local_and_near_miss():
    check(fn("let mut g=Array[u64,3]();g[2]=x;return g[2];"), fn("return x;"))
    refute(fn("let mut g=Array[u64,3]();g[2]=x;return g[2];"), fn("let mut g=Array[u64,3]();g[2]=x;return g[1];"))


def test_records_and_sums_inside_fixed_storage():
    src = "struct Q{a:u64;b:u64;}\nenum M{S(Q);N;}\n"
    check(src + fn("let mut g=Array[M,2]();g[1]=M.S(Q(x,x));match g[1]{M.S(q)=>{return add_wrap(q.a,q.b);} M.N=>{return 0;}}"),
          src + fn("return add_wrap(x,x);"))  # fmt: skip


# The model against the machine ----------------------------------------------------------------

NATIVE = """
struct Pair { a:u64; b:u64; }
enum Sign { Neg(u64); Zero; Pos(u64); }
fn classify(x:i64) -> Sign {
  if x < 0 { return Sign.Neg(u64(-x)); }
  if x == 0 { return Sign.Zero; }
  return Sign.Pos(u64(x));
}
fn magnitude(x:i64) -> u64 {
  match classify(x) { Sign.Neg(v) => { return v; } Sign.Zero => { return 0; } Sign.Pos(v) => { return v; } }
}
fn window(x:u64) -> u64 {
  stack a:u64[4] = zeroed;
  for i in 0..4 { a[i] = add_wrap(x, u64(i)); }
  let mut t:u64 = 0;
  for i in 0..4 { if (i & 1) == 1 { continue; } t = add_wrap(t, a[i]); }
  return t;
}
fn scaled(x:f64) -> u32 { let p = Pair(1, 2); return u32((x * 2.0) + f64(p.b)); }
fn narrow(x:f64, y:f64) -> f32 { return f32(x) / f32(y); }
fn guarded(x:f32, n:u64) -> f32 { if n == 0 { return x; } return x + f32(n); }
"""
POOL = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, 255.5, -255.5, 4294967295.5, 1e18, 1e308, -1e308, 2.0**53 + 1]
POOL += [float("inf"), float("-inf"), float("nan"), 1.1754943508222875e-38, 3.4028234663852886e38]


def sampled(ty, rng):
    if ty == "bool":
        return rng.random() < 0.5
    if ty in {"f32", "f64"}:
        raw = rng.choice(POOL) if rng.random() < 0.5 else decoded(rng.getrandbits(64), "f64")
        return rounded(raw, ty)
    lo, hi = bounds(ty)
    return rng.choice([lo, hi, 0, 1, rng.randint(lo, hi)])


def word(value, ty):
    """One 64-bit observation, the way the generated program prints its result."""
    if ty in {"f32", "f64"}:
        return encoded(value, ty)
    return int(value) % (1 << 64)


def literal(value, ty):
    if ty == "f32":
        return f"asf32({encoded(value, 'f32')}u)"
    if ty == "f64":
        return f"asf64({encoded(value, 'f64')}ull)"
    return f"static_cast<{CPP[ty]}>({int(value) % (1 << 64)}ull)"


HARNESS = """
#include <cstdio>
#include <cstdlib>
#include <cstring>
static float asf32(unsigned int b) { float v; std::memcpy(&v, &b, 4); return v; }
static double asf64(unsigned long long b) { double v; std::memcpy(&v, &b, 8); return v; }
static unsigned long long word(float v) { unsigned int b; std::memcpy(&b, &v, 4); return b; }
static unsigned long long word(double v) { unsigned long long b; std::memcpy(&b, &v, 8); return b; }
template <class T> unsigned long long word(T v) {
  return static_cast<unsigned long long>(static_cast<std::uint64_t>(v));
}
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  switch (std::atoi(argv[1])) {
%s
    default: return 2;
  }
  return 0;
}
"""


PERTURBED = [
    ("magnitude", "Sign.Neg(v) => { return v; }", "Sign.Neg(v) => { return add_wrap(v, 1); }"),
    ("window", "if (i & 1) == 1 { continue; }", "if (i & 1) == 0 { continue; }"),
    ("scaled", "f64(p.b)", "f64(p.a)"),
    ("narrow", "return f32(x) / f32(y);", "return f32(x / y);"),
    ("guarded", "if n == 0 { return x; }", "if n == 1 { return x; }"),
]


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_model_agrees_with_the_machine(tmp_path):
    """Build the fragment natively and confirm the concrete evaluator predicts what it does."""
    src = prepared(NATIVE)
    rng = random.Random(20260918)
    cases = []
    for name, before, after in PERTURBED:  # Every solver counterexample is also run on the machine.
        r = equivalent(NATIVE, NATIVE.replace(before, after), name, allow_reference_traps=True, timeout_ms=20000)
        assert r["status"] == "counterexample", r
        cases.append((name, r["counterexample"]))
    for name in [n for n, _, _ in PERTURBED]:
        f = src.functions[name]
        for _ in range(12):
            cases.append((name, {n: sampled(t.name, rng) for n, t in f.params}))
    arms = []
    for k, (name, args) in enumerate(cases):
        f = src.functions[name]
        passed = ", ".join(literal(args[n], t.name) for n, t in f.params)
        arms.append(f'    case {k}: std::printf("%llu\\n", word(cf_{name}({passed}))); break;')
    (tmp_path / "p.cpp").write_text(compile_source(NATIVE)[0] + HARNESS % "\n".join(arms))
    for header, text in RUNTIME_FILES.items():
        (tmp_path / header).write_text(text)
    build = ["clang++", "-std=c++20", "-O1", "-fno-exceptions", str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")]
    subprocess.run(build, check=True, timeout=180, capture_output=True)
    traps = 0
    for k, (name, args) in enumerate(cases):
        native = subprocess.run([tmp_path / "p", str(k)], capture_output=True, timeout=60)
        model = Concrete(src).outcome(name, args)
        if not model["defined"]:
            traps += 1
            assert native.returncode == -6, (name, args, native)
            continue
        assert native.returncode == 0, (name, args, native.stderr)
        expected = word(model["return"], src.functions[name].ret.name)
        assert int(native.stdout.split()[0]) == expected, (name, args, model, native.stdout)
    assert traps >= 5, "The sample must exercise the guards, not only the total cases."


def test_domain_totality_shortcircuit():
    a = fn("return x/y;", "x:u64,y:u64")
    check(a, a, assume="y!=0 && x/y<=x")
    check(a, a, "invalid-domain", assume="x/y<=x")


def test_type_error_not_semantic_success():
    check(fn("return x;"), fn("return true;"), "rejected")


def test_signature_mismatch():
    check(fn("return x;"), fn("return x;", "x:u32", "u32"), "invalid-contract")


def test_trap_equivalence_is_explicit():
    a = fn("return x/0;")
    b = fn("return x+1;")
    check(a, a, "invalid-reference")
    check(a, a, allow_reference_traps=True)
    check(a, b, "counterexample", allow_reference_traps=True)


def test_api_parse_error_unknown():
    with Solver() as s:
        assert s.check("(assert INVALID)", {})["status"] == "unknown"


@pytest.mark.parametrize("ty", ["i32", "i64"])
def test_signed_remainder_not_python_modulo(ty):
    source = fn("return x%y;", f"x:{ty},y:{ty}", ty)
    c = Concrete(prepared(source))
    assert c.outcome("f", {"x": -7, "y": 3})["return"] == -1
    assert c.outcome("f", {"x": 7, "y": -3})["return"] == 1
    check(source, source, allow_reference_traps=True)


@pytest.mark.parametrize("operation", ["shl_wrap", "shr"])
def test_shift_bounds(operation):
    a = fn(f"return {operation}(x,k);", "x:u8,k:usize", "u8")
    check(a, a, "invalid-reference")
    check(a, a, assume="k<8")
    check(a, a, allow_reference_traps=True)


def test_signed_unsigned_conversion_guard():
    a = fn("return x;", "x:i64", "i64")
    b = fn("return i64(u64(x));", "x:i64", "i64")
    check(a, b, "counterexample")
    check(a, b, assume="x>=0")


def test_solver_obligation_saved():
    logs = []
    r = equivalent(fn("return x;"), fn("return add_wrap(x,0);"), "f", query_log=logs)
    assert r["status"] == "smt-equivalent" and logs and all("(check-sat)" in x["smt2"] for x in logs)


@pytest.mark.parametrize("bad", [None, {}, 15, False])
def test_malformed_source_fails_closed(bad):
    assert equivalent(bad, fn("return x;"), "f")["status"] == "invalid-contract"


def test_precondition_cannot_inject_a_declaration():
    r = equivalent(fn("return x;"), fn("return x;"), "f", assume="true); } fn injected()->bool {return true;")
    assert r["status"] == "rejected"


def test_equivalence_sees_through_generics_traits_modules_families_and_constants():
    """1.0: the scalar model runs on the typed, linked, monomorphized tree, so resolved calls are ordinary calls."""
    reference = "fn f(x:u64, y:u64) -> u64 { if x < y { return y; } return x; }"
    candidate = (
        "import std.core (Ord);\nconst BIAS:u64 = 0;\n"
        "fn pick[T: Ord](a:T, b:T) -> T { if less(a, b) { return b; } return a; }\n"
        "fn scaled[K:nat](v:u64) -> u64 = v * u64(K);\nfamily times = scaled[1..3];\n"
        "fn f(x:u64, y:u64) -> u64 = times_1(pick(x, y)) + BIAS;"
    )
    assert equivalent(reference, candidate, "f")["status"] == "smt-equivalent"
    wrong = equivalent(reference, candidate.replace("return b; } return a;", "return a; } return b;"), "f")
    assert wrong["status"] == "counterexample" and wrong["concrete_replay"] is True
    doubled = equivalent(reference, candidate.replace("times_1", "times_2"), "f")
    assert doubled["status"] == "counterexample"

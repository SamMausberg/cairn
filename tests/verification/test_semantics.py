import random
import shutil
import subprocess

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.compiler.syntax import CPP, VOID, is_view
from cairn.verify.scalar_semantics import Concrete, bounds, decoded, encoded, equivalent, identical, prepared, rounded
from cairn.verify.smt_bridge import Solver


def fn(body, params="x:u64", ret="u64"):
    return f"fn f({params})->{ret}{{{body}}}"


def check(a, b, expected="smt-equivalent", **kw):
    r = equivalent(a, b, "f", **kw)
    assert r["status"] == expected, r
    assert not r["lean_verified"] and not r["native_verified"]
    return r


def agree(src, f, p, q):
    """Two outcomes a caller cannot tell apart: one abort, or the same result and the same rw contents."""
    if p["defined"] != q["defined"]:
        return False
    if not p["defined"]:
        return True
    return (f.ret == VOID or identical(src, f.ret, p["return"], q["return"])) and all(
        identical(src, t, p["written"][n], q["written"][n]) for n, t in f.params if t.mode == "rw"
    )


def refute(a, b, **kw):
    """A rejection counts only when the reported input, replayed independently, really separates the two."""
    r = check(a, b, "counterexample", **kw)
    src = prepared(a)
    f = src.functions["f"]
    x = Concrete(src).outcome("f", r["counterexample"])
    y = Concrete(prepared(b)).outcome("f", r["counterexample"])
    assert agree(src, f, x, r["expected"]) and agree(src, f, y, r["actual"]), (r, x, y)
    assert not agree(src, f, x, y), (r, x, y)
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
        "fn f(n:usize,xs:ro<u64>[n]@device,out:rw<u64>[n])->usize{return n;}",
        "fn f(n:usize)->usize{let mut b=Buf[u64](n);let c=take(b);return len(c);}",  # An owner that moves.
        "fn g(b:Buf[u64])->usize=len(b);\nfn f(n:usize)->usize{let b=Buf[u64](n);return g(b);}",
        "struct V{d:Buf[u64];k:usize;}\nfn f(n:usize)->usize{let v=V(Buf[u64](n),n);return v.k;}",
        "fn f(n:usize,xs:ro<f64>[n])->f64{let s=reduce + for i in n yield xs[i];return s;}",  # An unspecified order.
        "fn f(n:usize,xs:ro<u64>[n],out:rw<u64>[n]){parallel i in n{out[i]=xs[i];}}",
        "fn g(n:usize,o:rw<u64>[n]){o[0]=1;}\nfn f(n:usize,out:rw<u64>[n])->usize{let t=spawn g(n,out);wait(t);return n;}",
        "enum M{S(u64);N;}\nfn f(n:usize,ms:ro<M>[n])->u64{match ms[0]{M.S(v)=>{return v;} M.N=>{return 0;}}}",
        "fn g(a:usize,xs:rw<u64>[a],b:usize,ys:rw<u64>[b]){xs[0]=1;ys[0]=2;}\n"  # Two parts of one array.
        "fn f(n:usize,zs:rw<u64>[n],mid:usize){if mid>n{return;}g(mid,zs[0..mid],n-mid,zs[mid..n]);}",
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
    # Three symbolic doubles make z3's heaviest query in this file; the timeout is wall clock and the
    # suite runs one worker per core, so the default budget flakes to unknown under load. Unknown still fails.
    refute(a, b, assume="x==x && y==y && z==z", timeout_ms=15000)


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


# Array views ---------------------------------------------------------------------------------

SUM = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,xs[i]); } return t; }"
FILL = "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(xs[i],1); } }"


def test_a_view_reads_the_elements_its_extent_lends():
    check(SUM, "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(xs[i],t); } return t; }",
          assume="n<=4")  # fmt: skip
    check("fn f(n:usize, xs:ro<u64>[n]) -> usize = len(xs);", "fn f(n:usize, xs:ro<u64>[n]) -> usize = n;")
    check("fn f(xs:ro<u8>[16]) -> u8 { let mut t:u8=0; for i in 0..16 { t=add_wrap(t,xs[i]); } return t; }",
          "fn f(xs:ro<u8>[16]) -> u8 { let mut t:u8=0; for i in 0..16 { t=add_wrap(xs[i],t); } return t; }")  # fmt: skip


def test_view_near_miss_separates_at_one_element():
    r = refute(SUM, SUM.replace("t=add_wrap(t,xs[i]);", "t=add_wrap(t,add_wrap(xs[i],1));"), assume="n>=1 && n<=3")
    assert len(r["counterexample"]["xs"]) == r["counterexample"]["n"] >= 1
    r = refute("fn f(n:usize, xs:ro<u64>[n]) -> usize = len(xs);", "fn f(n:usize, xs:ro<u64>[n]) -> usize = n+1;")
    assert r["expected"]["return"] == r["counterexample"]["n"] == len(r["counterexample"]["xs"])


def test_an_index_outside_the_extent_traps():
    a = "fn f(n:usize, xs:ro<u64>[n], i:usize) -> u64 = xs[i];"
    check(a, a, "invalid-reference")
    check(a, a, allow_reference_traps=True)
    b = "fn f(n:usize, xs:ro<u64>[n], i:usize) -> u64 { if i<n { return xs[i]; } return 0; }"
    r = refute(a, b, allow_reference_traps=True)
    assert r["counterexample"]["i"] >= r["counterexample"]["n"] and not r["expected"]["defined"]
    check(a, b, assume="i<n")


def test_the_final_contents_of_an_rw_view_are_observed():
    check(FILL, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(1,xs[i]); } }",
          assume="n<=4")  # fmt: skip
    r = refute(FILL, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(xs[i],2); } }",
               assume="n>=1 && n<=3")  # fmt: skip
    assert r["expected"]["return"] is None and r["expected"]["written"]["out"] != r["actual"]["written"]["out"]
    kept = "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { if n<2 { return; } out[0]=add_wrap(xs[0],1); }"
    r = refute(kept, kept.replace("if n<2 { return; }", "if n<2 { return; } out[1]=xs[1];"), assume="n<=2")
    assert r["expected"]["written"]["out"][1] != r["actual"]["written"]["out"][1]


def test_an_untouched_rw_view_keeps_what_the_caller_lent():
    check("fn f(n:usize, out:rw<u8>[n]) -> usize { return n; }", "fn f(n:usize, out:rw<u8>[n]) -> usize = len(out);")
    refute("fn f(n:usize, out:rw<u8>[n]) -> usize { return n; }",
           "fn f(n:usize, out:rw<u8>[n]) -> usize { if n>0 { out[0]=0; } return n; }", assume="n<=2")  # fmt: skip


def test_a_loop_over_a_symbolic_extent_needs_a_bound():
    r = check(SUM, SUM.replace("t=add_wrap(t,xs[i]);", "t=add_wrap(xs[i],t);"), "unknown")
    assert "unrolling budget" in r["reason"]


def test_read_only_views_may_alias_so_the_model_takes_them_apart():
    """Two ro views are independent arrays: that admits the aliased case and more."""
    a = "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(xs[0],ys[0]); }"
    b = "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(ys[0],xs[0]); }"
    check(a, b)
    refute(a, "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(xs[0],xs[0]); }")


# Parts, single borrows, collectors, reductions and local owners --------------------------------

TOTAL = "fn total(m:usize, ys:ro<u8>[m]) -> u8 { let mut t:u8=0; for i in 0..m { t=add_wrap(t,ys[i]); } return t; }\n"
SPLIT = TOTAL + (
    "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 {\n"
    "  if mid > n { return 0; }\n"
    "  return add_wrap(total(mid, xs[0..mid]), total(n-mid, xs[mid..n]));\n}"
)


def test_a_part_is_the_window_its_guard_admits():
    check(SPLIT, TOTAL + "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 { if mid>n { return 0; } return total(n,xs); }",
          assume="n<=3")  # fmt: skip
    r = refute(SPLIT, TOTAL + "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 = total(n, xs);", assume="n<=2")
    assert r["counterexample"]["mid"] > r["counterexample"]["n"]


def test_a_part_that_does_not_fit_its_callee_traps():
    a = TOTAL + "fn f(n:usize, xs:ro<u8>[n], k:usize) -> u8 = total(k, xs[0..k]);"
    guarded = TOTAL + "fn f(n:usize, xs:ro<u8>[n], k:usize) -> u8 { if k>n { return 0; } return total(k,xs[0..k]); }"
    check(a, a, "invalid-reference", assume="k<=3")
    check(a, a, assume="k<=3", allow_reference_traps=True)
    r = refute(a, guarded, assume="k<=3", allow_reference_traps=True)
    assert r["counterexample"]["k"] > r["counterexample"]["n"] and not r["expected"]["defined"]
    check(a, guarded, assume="k<=3 && k<=n")


def test_an_rw_single_borrow_is_written_back():
    p = "struct P{a:u64;b:u64;}\n"
    g = p + "fn g(q:rw<P>)->u64{q.a=add_wrap(q.a,q.b);return q.a;}\n"
    check(g + "fn f(r:P)->P{let mut s=r;let v=g(s);return P(s.a,v);}",
          g + "fn f(r:P)->P{let a=add_wrap(r.a,r.b);return P(a,a);}")  # fmt: skip
    check(p + "fn f(q:rw<P>){q.a=q.b;}", p + "fn f(q:rw<P>){q.a=q.b;q.b=q.b;}")
    r = refute(p + "fn f(q:rw<P>){q.a=q.b;}", p + "fn f(q:rw<P>){q.b=q.a;}")
    assert r["counterexample"]["q"]["a"] != r["counterexample"]["q"]["b"]


COMPACT = (
    "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {\n"
    "  let used = compact out for i in n where xs[i] > 3 yield xs[i];\n  return used;\n}"
)
BYHAND = (
    "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {\n"
    "  let mut k:usize = 0;\n"
    "  for i in 0..n { if xs[i] > 3 { out[k] = xs[i]; k = k + 1; } }\n  return k;\n}"
)


def test_compact_is_the_stable_selected_prefix():
    check(COMPACT, BYHAND, assume="n<=3")
    r = refute(COMPACT, BYHAND.replace("xs[i] > 3", "xs[i] >= 3"), assume="n<=2")
    assert r["expected"]["written"]["out"] != r["actual"]["written"]["out"] or r["expected"]["return"] != r["actual"]["return"]  # fmt: skip
    refute(COMPACT, BYHAND.replace("out[k] = xs[i];", "out[k] = add_wrap(xs[i],1);"), assume="n>=1 && n<=2")


def test_compact_leaves_the_tail_alone():
    nothing = COMPACT.replace("xs[i] > 3", "false")
    check(nothing, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize { return 0; }", assume="n<=3")
    cleared = COMPACT.replace("return used;", "for i in 0..n { if i >= used { out[i] = 0; } }\n  return used;")
    r = refute(COMPACT, cleared, assume="n<=2")
    assert r["expected"]["written"]["out"] != r["actual"]["written"]["out"]


def test_a_collector_over_a_symbolic_extent_needs_a_bound():
    r = check(COMPACT, BYHAND, "unknown")
    assert "unrolling budget" in r["reason"]


REDUCE = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let s = reduce OP for i in n yield xs[i]; return s; }"
FOLD = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut s:u8=SEED; for i in 0..n { s=STEP; } return s; }"


@pytest.mark.parametrize(
    "op,seed,step",
    [
        ("add_wrap", "0", "add_wrap(s,xs[i])"),
        ("mul_wrap", "1", "mul_wrap(s,xs[i])"),
        ("min", "255", "min(s,xs[i])"),
        ("max", "0", "max(s,xs[i])"),
        ("&", "255", "s&xs[i]"),
        ("|", "0", "s|xs[i]"),
        ("^", "0", "s^xs[i]"),
    ],
)
def test_reduce_is_the_fold_the_host_emits(op, seed, step):
    check(REDUCE.replace("OP", op), FOLD.replace("SEED", seed).replace("STEP", step), assume="n<=2")


def test_checked_reduce_traps_exactly_when_the_total_does_not_fit():
    checked = REDUCE.replace("OP", "+")
    wrapping = FOLD.replace("SEED", "0").replace("STEP", "add_wrap(s,xs[i])")
    check(checked, wrapping, "invalid-reference", assume="n<=3")
    r = refute(checked, wrapping, assume="n<=3", allow_reference_traps=True)
    assert sum(r["counterexample"]["xs"]) > 255 and not r["expected"]["defined"]
    check(checked, wrapping, assume="n<=1")  # One u8 always fits, so neither the total nor a prefix traps.


SCRATCH = (
    "fn f(n:usize, xs:ro<u8>[n]) -> u8 {\n"
    "  buffer tmp:u8[n] = zeroed;\n"
    "  for i in 0..n { tmp[i] = add_wrap(xs[i], 1); }\n"
    "  let mut t:u8 = 0;\n"
    "  for i in 0..n { t = add_wrap(t, tmp[i]); }\n  return t;\n}"
)
DIRECT = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,add_wrap(xs[i],1)); } return t; }"  # fmt: skip


def test_a_local_heap_owner_is_zeroed_scratch():
    check(SCRATCH, DIRECT, assume="n<=3")
    check(SCRATCH.replace("buffer tmp:u8[n] = zeroed;", "let mut tmp = Buf[u8](n);"), DIRECT, assume="n<=3")
    check("fn f(n:usize) -> u8 { buffer tmp:u8[n] = zeroed; if n==0 { return 0; } return tmp[0]; }",
          "fn f(n:usize) -> u8 { return 0; }")  # fmt: skip
    refute(SCRATCH, DIRECT.replace("add_wrap(xs[i],1)", "add_wrap(xs[i],2)"), assume="n>=1 && n<=2")


def test_a_local_owner_is_bounded_by_its_own_extent():
    a = "fn f(n:usize, k:usize) -> u8 { buffer tmp:u8[n] = zeroed; return tmp[k]; }"
    check(a, a, "invalid-reference")
    check(a, a, assume="k<n")
    refute(a, "fn f(n:usize, k:usize) -> u8 { buffer tmp:u8[n] = zeroed; if k<n { return tmp[k]; } return 1; }",
           allow_reference_traps=True)  # fmt: skip


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
fn element(n:usize, xs:ro<u64>[n], i:usize) -> u64 { return xs[i]; }
fn stretch(n:usize, xs:ro<u8>[n], out:rw<u8>[n], k:u8) { for i in 0..n { out[i] = mul_wrap(xs[i], k); } }
fn picked(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {
  let used = compact out for i in n where xs[i] > 3 yield xs[i];
  return used;
}
fn summed(n:usize, xs:ro<u8>[n]) -> u8 { let s = reduce + for i in n yield xs[i]; return s; }
fn total(m:usize, ys:ro<u8>[m]) -> u8 { let mut t:u8 = 0; for i in 0..m { t = add_wrap(t, ys[i]); } return t; }
fn halves(n:usize, xs:ro<u8>[n], mid:usize) -> u8 {
  return add_wrap(total(mid, xs[0..mid]), total(n-mid, xs[mid..n]));
}
fn bumped(p:rw<u64>, k:u64) -> u64 { p = add_wrap(p, k); return p; }
fn scratch(n:usize, xs:ro<u8>[n]) -> u8 {
  buffer tmp:u8[n] = zeroed;
  for i in 0..n { tmp[i] = add_wrap(xs[i], 1); }
  let mut t:u8 = 0;
  for i in 0..n { t = add_wrap(t, tmp[i]); }
  return t;
}
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
    ("magnitude", "Sign.Neg(v) => { return v; }", "Sign.Neg(v) => { return add_wrap(v, 1); }", "true"),
    ("window", "if (i & 1) == 1 { continue; }", "if (i & 1) == 0 { continue; }", "true"),
    ("scaled", "f64(p.b)", "f64(p.a)", "true"),
    ("narrow", "return f32(x) / f32(y);", "return f32(x / y);", "true"),
    ("guarded", "if n == 0 { return x; }", "if n == 1 { return x; }", "true"),
    ("element", "return xs[i];", "if i < n { return xs[i]; } return 0;", "true"),
    ("stretch", "out[i] = mul_wrap(xs[i], k);", "out[i] = add_wrap(xs[i], k);", "n<=3"),
    ("picked", "where xs[i] > 3", "where xs[i] >= 3", "n<=2"),
    ("summed", "reduce + for", "reduce add_wrap for", "n<=3"),
    ("halves", "total(n-mid, xs[mid..n])", "total(n-mid, xs[0..n-mid])", "n<=3"),
    ("bumped", "p = add_wrap(p, k);", "p = add_wrap(k, 1);", "true"),
    ("scratch", "tmp[i] = add_wrap(xs[i], 1);", "tmp[i] = add_wrap(xs[i], 2);", "n<=2"),
]


def drawn(f, rng):
    """One input the entry guards admit: an extent, then storage holding exactly that many elements."""
    extents = {t.extent for _, t in f.params if is_view(t)}
    args: dict = {}
    for n, t in f.params:
        if is_view(t):
            args[n] = [sampled(t.name, rng) for _ in range(int(t.extent) if t.extent.isdigit() else args[t.extent])]
        elif n in extents or (t.name == "usize" and rng.random() < 0.5):
            args[n] = rng.choice([0, 1, 2, 3])  # A small extent, or an index that is often inside one.
        else:
            args[n] = sampled(t.name, rng)
    return args


def arm(k, f, name, args):
    """The switch arm that lends this input to the native function and prints what a caller observes."""
    lines, passed, shown = [], [], []
    for i, (n, t) in enumerate(f.params):
        held = f"s{i}"
        if is_view(t):
            items = ", ".join(literal(v, t.name) for v in args[n]) or literal(0, t.name)
            lines.append(f"{CPP[t.name]} {held}[] = {{{items}}};")  # Never empty: the pointer must be valid.
            passed.append(held)
            shown += [f"{held}[{j}]" for j in range(len(args[n]))] if t.mode == "rw" else []
        elif t.mode == "rw":
            lines.append(f"{CPP[t.name]} {held} = {literal(args[n], t.name)};")
            passed += [held]
            shown += [held]
        else:
            passed.append(literal(args[n], t.name))
    call = f"cf_{name}({', '.join(passed)})"
    lines.append(f"{call};" if f.ret == VOID else f'std::printf("%llu ", word({call}));')
    lines += [f'std::printf("%llu ", word({x}));' for x in shown]
    return f"    case {k}: {{ " + " ".join(lines) + ' std::printf("\\n"); break; }'


def expected_words(src, f, outcome):
    """What the harness prints for an outcome that returned: the result, then every rw parameter."""
    words = [] if f.ret == VOID else [word(outcome["return"], f.ret.name)]
    for n, t in f.params:
        if t.mode != "rw":
            continue
        held = outcome["written"][n]
        words += [word(v, t.name) for v in held] if is_view(t) else [word(held, t.name)]
    return words


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_model_agrees_with_the_machine(tmp_path):
    """Build the fragment natively and confirm the concrete evaluator predicts what it does."""
    src = prepared(NATIVE)
    rng = random.Random(20260918)
    cases = []
    for name, before, after, assume in PERTURBED:  # Every solver counterexample is also run on the machine.
        r = equivalent(NATIVE, NATIVE.replace(before, after), name, assume=assume,
                       allow_reference_traps=True, timeout_ms=20000)  # fmt: skip
        assert r["status"] == "counterexample", r
        cases.append((name, r["counterexample"]))
    for name in [n for n, _, _, _ in PERTURBED]:
        for _ in range(10):
            cases.append((name, drawn(src.functions[name], rng)))
    arms = [arm(k, src.functions[name], name, args) for k, (name, args) in enumerate(cases)]
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
        expected = expected_words(src, src.functions[name], model)
        assert [int(w) for w in native.stdout.split()] == expected, (name, args, model, native.stdout)
    assert traps >= 10, "The sample must exercise the guards, not only the total cases."


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

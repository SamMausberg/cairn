"""Solver-backed equivalence over values: scalars, floats, records, sums, bounded loops and fixed storage.

The helpers `fn`, `check`, `agree` and `refute` are shared with the sibling test_semantics_* modules.
"""

from pathlib import Path

import pytest

from cairn.compiler.syntax.tree import VOID
from cairn.verify.scalar.concrete import Concrete
from cairn.verify.scalar.semantics import equivalent, prepared
from cairn.verify.scalar.values import identical


def fn(body, params="x:u64", ret="u64"):
    return f"fn f({params})->{ret}{{{body}}}"


# Z3's timeout is wall clock, and a shared two-core runner answers some of these queries in several times the three
# seconds `equivalent` allows by default, which turns a counterexample into unknown. A test that expects an answer waits
# as long as the solver accepts, and a query that is quick here stays quick. A test that expects unknown keeps the
# default, since a query that never decides would only wait longer for the same answer.
SOLVER_MS = 30000


def check(a, b, expected="smt-equivalent", **kw):
    if expected != "unknown":
        kw.setdefault("timeout_ms", SOLVER_MS)
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


def test_a_witness_is_given_in_the_smallest_numbers_the_solver_finds_quickly():
    """Z3's first witness may be any value of the type; the one shown is asked for again within 16, 256 and 65536
    of zero, array elements included, so a difference that exists only at a boundary still shows that boundary."""
    three = "x:u64,lo:u64,hi:u64"
    r = refute(fn("if x<lo{return lo;} if x>hi{return hi;} return x;", three), fn("return min(max(x,lo),hi);", three))
    assert max(r["counterexample"].values()) <= 16 and r["counterexample"]["lo"] > r["counterexample"]["hi"]
    assert refute(fn("if x<100{return 0;} return 1;"), fn("if x<=100{return 0;} return 1;"))["counterexample"] == {
        "x": 100
    }
    signed = refute(fn("return x;", "x:i64", "i64"), fn("if x<-3{return 0;} return x;", "x:i64", "i64"))
    assert -16 <= signed["counterexample"]["x"] < -3
    lent = "n:usize,xs:ro<u64>[n]"
    view = refute(fn("if n==0{return 0;} return xs[0];", lent), fn("if n==0{return 0;} return min(xs[0],5);", lent))
    assert 5 < view["counterexample"]["xs"][0] <= 16 and view["counterexample"]["n"] <= 16


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
        "struct V{d:Buf[u64];k:usize;}\nfn f(n:usize)->usize{let v=V(Buf[u64](n),n);return v.k;}",
        "fn f(n:usize,xs:ro<f64>[n])->f64{let s=reduce + for i in n yield xs[i];return s;}",  # An unspecified order.
        "fn f(n:usize,xs:ro<u64>[n],out:rw<u64>[n]){parallel i in n{out[i]=xs[i];}}",
        "fn g(n:usize,o:rw<u64>[n]){o[0]=1;}\nfn f(n:usize,out:rw<u64>[n])->usize{let t=spawn g(n,out);wait(t);return n;}",
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


def test_only_the_top_level_tag_of_a_value_parameter_is_guarded():
    """The emitted entry guard reads one tag: `if(v_o >= 2) cr::trap();`. The model admits exactly that."""
    r = check(OP + "fn f(o:Op)->u64{match o{Op.Read=>{return 0;} Op.Write=>{return 1;}}}",
              OP + "fn f(o:Op)->u64{if o==Op.Read{return 0;}return 1;}")  # fmt: skip
    assert "the top-level tag of an enum or sum passed by value names a declared variant" in r["quantification"]
    assert "reached through a borrow or held in storage carries any value" in r["quantification"]


CMD = OP + "struct Cmd { op:Op; n:u64; }\n"
MATCHED = CMD + "fn f(c:PARAM)->u64{match c.op{Op.Read=>{return c.n;} Op.Write=>{return 0;}}}"
TOTAL = CMD + "fn f(c:PARAM)->u64{if c.op==Op.Read{return c.n;}return 0;}"


@pytest.mark.parametrize("param", ["Cmd", "ro<Cmd>"])
def test_a_tag_nested_in_a_parameter_is_admitted_as_storage_is(param):
    """No entry guard reads a tag inside a record, or behind a borrow, so `default: cr::trap()` separates
    a match from a comparison there, exactly as it does for an element of storage."""
    matched, total = MATCHED.replace("PARAM", param), TOTAL.replace("PARAM", param)
    r = refute(total, matched)
    nested = r["counterexample"]["c"]["op"]
    assert set(nested) == {"tag"} and nested["tag"] >= 2  # No variant names it, so it carries no payload.
    assert r["expected"]["return"] == 0 and r["actual"]["trap"] == "unmatched-tag"
    check(total, matched, assume="c.op==Op.Read || c.op==Op.Write")


def test_a_try_over_a_nested_tag_no_variant_names_is_unknown_not_equivalent():
    """The emitted `try` carries the failure payload bits of any tag but zero; the replay holds none, so a
    difference the solver finds there is reported unknown, never smt-equivalent."""
    box = RESULT + "struct Box { r:Res; }\n"
    matched = box + "fn f(b:Box)->Res{match b.r{Res.Ok(v)=>{return Res.Ok(v);} Res.Bad(e)=>{return Res.Bad(e);}}}"
    tried = box + "fn f(b:Box)->Res{let v = try b.r; return Res.Ok(v);}"
    r = check(matched, tried, "unknown", allow_reference_traps=True)
    assert "try over a tag outside the declared variants" in r["reason"]


# Floating point -----------------------------------------------------------------------------

FINITE = "x==x && y==y"


def test_float_doubling_and_addition_agree():
    check(fn("return x*2.0;", "x:f64", "f64"), fn("return x+x;", "x:f64", "f64"), assume="x==x")
    check(fn("return x*2.0;", "x:f32", "f32"), fn("return x+x;", "x:f32", "f32"), assume="x==x")


def test_float_addition_is_not_associative():
    a = fn("return (x+y)+z;", "x:f64,y:f64,z:f64", "f64")
    b = fn("return x+(y+z);", "x:f64,y:f64,z:f64", "f64")
    refute(a, b, assume="x==x && y==y && z==z")  # three symbolic doubles: the heaviest query in this file


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


def test_the_receipt_is_pinned_to_every_file_that_decides_what_a_program_means():
    """A semantic receipt names the compiler that judged it: every file under compiler/, in every subpackage, is in
    the hash beside the SMT path, so a change to a builtin's rule changes every receipt's implementation_sha256 as
    surely as a change to the parser, and a new module is covered without anyone listing it."""
    from cairn.verify.scalar import semantics

    package = Path(semantics.__file__).parents[2]
    pinned = [f.relative_to(package).as_posix() for f in semantics.implementation_files()]
    compiler = {f.relative_to(package).as_posix() for f in (package / "compiler").rglob("*.py")}
    assert compiler <= set(pinned) and {"compiler/primitives/builtins.py", "compiler/syntax/parser.py"} <= compiler
    assert set(semantics.SEMANTIC) <= set(pinned) and pinned == sorted(pinned)


def test_a_writing_call_as_a_conversion_s_operand_is_the_call_bound_first():
    """`usize(next(inp))` runs the call, writes inp, then converts: what binding the call first does."""
    head = "struct Input { at:u64; }\nfn next(inp:rw<Input>) -> u64 { inp.at += 1; return inp.at; }\n"
    bound = head + "fn f(inp:rw<Input>) -> usize { let v = next(inp); return usize(v); }\n"
    check(bound, head + "fn f(inp:rw<Input>) -> usize { return usize(next(inp)); }\n", allow_reference_traps=True)
    twice = head + "fn f(inp:rw<Input>) -> usize { let v = next(inp); return usize(next(inp)); }\n"
    check(bound, twice, "counterexample", allow_reference_traps=True)


def test_a_negated_literal_is_its_value_and_cannot_trap():
    """`-9223372036854775808` is the least i64, not the checked negation of a literal that does not fit."""
    least = fn("return -9223372036854775808;", "", "i64")
    check(least, fn("return 0 - 9223372036854775807 - 1;", "", "i64"))
    check(least, fn("return -9223372036854775807;", "", "i64"), "counterexample")

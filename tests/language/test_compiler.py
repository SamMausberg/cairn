import sys
from pathlib import Path

import pytest

from emitted import refused

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from cairn.compiler.cairnc import compile_source

BAD = [
    ("E-WRITE-LEASE", "fn f(n:usize,x:ro<u64>[n]@host) { x[0]=1; }"),
    ("E-IMMUTABLE", "fn f() { let x:u64=1; x=2; }"),
    ("E-IMMUTABLE", "fn f(x:u64) { x=2; }"),
    ("E-IMMUTABLE", "fn f() { each i in 4 { i=1; } }"),
    ("E-SHADOW", "fn f(x:u64) { reg x:u64=1; }"),
    ("E-RETURN", "fn f(x:u64)->u64 { if x==0 { return 1; } }"),
    ("E-RETURN", "fn f()->u64 { return; }"),
    ("E-RETURN", "fn f() { return 1; }"),
    ("E-TYPE-MISMATCH", "fn f(x:f32)->u64 { return x; }"),
    ("E-TYPE-MISMATCH", "fn f(x:bool)->u64 { return x+1; }"),
    ("E-TYPE-MISMATCH", "fn f(x:u64,y:f64)->u64 { return x+y; }"),
    ("E-EXTENT", "fn f(x:ro<u64>[n]@host,n:usize) {}"),
    ("E-EXTENT", "fn f(n:u64,x:ro<u64>[n]@host) {}"),
    ("E-EXTENT", "struct A { xs:Buf[u64][n]; n:usize; }"),
    ("E-EXTENT", "struct A { n:u32; xs:Buf[u64][n]; }"),
    ("E-EXTENT-FIELD", "struct A { n:usize; xs:Buf[u64][n]; } fn f()->A { let b=Buf[u64](2); return A(2,b); }"),
    ("E-EXTENT-FIELD", "struct A { n:usize; xs:Buf[u64][n]; } fn f(a:rw<A>) { a.n = 0; }"),
    ("E-UNBOUND", "fn f()->u64 { return missing; }"),
    ("E-DUPLICATE", "fn f() {} fn f() {}"),
    ("E-DUPLICATE", "struct A { x:u64; x:u64; }"),
    ("E-RECORD-TYPE", "struct A { x:A; }"),
    ("E-RECORD-TYPE", "struct A { x:ro<u64>[2]@host; }"),
    ("E-FIELD", "struct A { x:u64; } fn f(a:A)->u64 { return a.y; }"),
    ("E-ENUM-VARIANT", "enum A { a; } fn f()->A { return A.b; }"),
    ("E-LITERAL-RANGE", "fn f()->u8 { return 256; }"),
    ("E-LITERAL-RANGE", "fn f()->f32 { return 1e100; }"),
    ("E-WRAP-TYPE", "fn f(x:i64)->i64 { return add_wrap(x,1); }"),
    ("E-OPERATOR", "fn f(x:u64)->u64 { return -x; }"),
    ("E-OPERATOR", "fn f(x:f64)->f64 { return x%2.0; }"),
    ("E-CAST", "fn f(x:bool)->u64 { return u64(x); }"),
    ("E-CALLEE", "fn f()->u64 { return system(1); }"),
    ("E-ARITY", "fn f(x:u64)->u64 { return add_wrap(x); }"),
    ("E-VIEW-ALIAS", "fn f(n:usize,x:ro<u64>[n]@host) { let y=x; }"),
    ("E-UNREACHABLE", "fn f()->u64 { return 1; return 2; }"),
    ("E-DISCARD", "fn f() { 1; }"),
    ("E-DISCARD", "fn f() { u64(1); }"),
    ("E-FAMILY-TARGET", "family f = absent[1..5];"),
    ("E-FAMILY-LIMIT", "fn f[K:nat]()->usize {return K;} family x=f[0..2000];"),
    ("E-UNINSTANTIATED", "fn f[K:nat]()->usize {return K;}"),
    ("E-DUPLICATE", "fn f[K:nat]()->usize{return K;} family x=f[1..3]; family x=f[2..4];"),
    ("E-BUILTIN-NAME", "fn u64()->u64 { return 0; }"),
    ("E-BUILTIN-NAME", "struct f32 { x:u64; }"),
    ("E-TYPE", "fn f(x:Unknown) {}"),
    ("E-ESCAPE", "fn f(n:usize,x:ro<u64>[n]@host)->ro<u64>[n]@host {return x;}"),
    (
        "E-CALL-SHAPE",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{return 0;} fn g(n:usize,x:ro<u64>[n]@host)->u64{return f(n+1,x);}",
    ),
    (
        "E-TYPE-MISMATCH",
        "fn f(n:usize,x:ro<u64>[n]@host)->u64{return 0;} fn g(n:usize,m:usize,x:ro<u64>[n]@host)->u64{return f(m,x);}",
    ),
    ("E-ALIAS", "fn f(n:usize,a:rw<u64>[n]@host,b:ro<u64>[n]@host){} fn g(n:usize,a:rw<u64>[n]@host){f(n,a,a);}"),
    (
        "E-EFFECT-ORDER",
        "fn f(n:usize,a:rw<u64>[n]@host)->u64{a[0]=1;return 0;} fn g(n:usize,a:rw<u64>[n]@host)->u64{return 1+f(n,a);}",
    ),
    ("E-LEX", "fn f() { $; }"),
    ("E-MINMAX", "fn f(x:f64)->f64{return min(x,x);}"),
]


@pytest.mark.parametrize("code,source", BAD)
def test_rejections(code, source):
    refused(code, source)


def test_native_examples():
    root = Path(__file__).resolve().parents[2]
    _, r = compile_source((root / "examples/basics/native.cairn").read_text())
    assert r["function_count"] == 23
    assert "diverge" in r["functions"]["factorial_wrap"]["effects"]
    assert "write:out" in r["functions"]["delegated"]["effects"]
    assert r["functions"]["op_class"]["syntactic_check_sites"]["enum_entry"] == 1


def test_determinism():
    text = "fn f(x:u64)->u64 {return add_wrap(x,1);}"
    assert compile_source(text) == compile_source(text)


def test_family():
    _, r = compile_source("fn f[K:nat]()->usize{return K;} family g=f[1..257];")
    assert r["function_count"] == 256
    assert "g_1" in r["functions"] and "g_256" in r["functions"]


def test_keywords_as_cxx_identifiers():
    cpp, _ = compile_source("fn class(template:u64)->u64 {return template;}")
    assert "cf_class" in cpp and "v_template" in cpp


def test_whole_expression_effect_call():
    compile_source(
        "fn f(n:usize,a:rw<u64>[n]@host)->u64{a[0]=1;return 0;} fn g(n:usize,a:rw<u64>[n]@host)->u64{let q=f(n,a); return q;}"
    )


MORE_BAD = [
    (
        "E-COLLECT-CAPACITY",
        "fn f(n:usize,m:usize,o:rw<u64>[n]@host,x:ro<u64>[m]@host)->usize{let k=compact o for i in m where true yield x[i];return k;}",
    ),
    (
        "E-COLLECT-SELF-READ",
        "fn f(n:usize,o:rw<u64>[n]@host)->usize{let k=compact o for i in n where true yield o[i];return k;}",
    ),
    (
        "E-WRITE-LEASE",
        "fn f(n:usize,o:ro<u64>[n]@host)->usize{let k=compact o for i in n where true yield 0;return k;}",
    ),
    (
        "E-COLLECT-BINDING",
        "fn f(n:usize,o:rw<u64>[n]@host)->usize{reg k=compact o for i in n where true yield 0;return k;}",
    ),
    (
        "E-EFFECT-ORDER",
        "fn w(n:usize,x:rw<u64>[n]@host)->u64{x[0]=1;return 0;} fn f(n:usize,o:rw<u64>[n]@host,x:rw<u64>[n]@host)->usize{let k=compact o for i in n where true yield w(n,x);return k;}",
    ),
    ("E-SHADOW", "fn f(n:usize,o:rw<u64>[n]@host)->usize{let k=compact o for k in n where true yield 0;return k;}"),
    ("E-DERIVE-FIELD", "struct P{x:f32;} derive wire for P;"),
    ("E-DERIVE-TYPE", "derive wire for P;"),
    ("E-DERIVE-COLLISION", "struct P{x:u32;} fn encode_P(){} derive wire for P;"),
]


@pytest.mark.parametrize("code,source", MORE_BAD)
def test_new_rejections(code, source):
    refused(code, source)


def test_wire_derivation():
    _, r = compile_source("struct P{x:u32;y:u64;} derive wire for P;")
    assert r["function_count"] == 3 and r["wire_derivations"] == ["P"]


def test_collector_receipt():
    _, r = compile_source(
        "fn f(n:usize,o:rw<u64>[n]@host)->usize{let k=compact o for i in n where true yield u64(i);return k;}"
    )
    assert r["functions"]["f"]["syntactic_check_sites"]["bounded_collectors"] == 1


def test_decimal_leading_zero_canonicalized():
    cpp, _ = compile_source("fn f()->u64 {return 00010;}")
    assert "00010" not in cpp and "(10ULL)" in cpp


def test_global_family_budget_before_copying():
    refused(
        "E-EXPANSION-LIMIT",
        "fn f[K:nat]()->usize{return K;} family a=f[0..1024];family b=f[0..1024];family c=f[0..1024];",
    )


@pytest.mark.parametrize(
    "code,source,says",
    [
        ("E-PARSE", "fn f(x:u64) -> u32 { return x as u32; }", "CAIRN has no `as`: convert with a call, u64(x)"),
        ("E-PARSE", "fn f() -> i64 { return i64::MIN; }", "an i64's minimum is the literal -9223372036854775808"),
        ("E-NAME", "fn f(c:bool) -> u64 { let x = if c { 1 } else { 2 }; return x; }", "if is a statement, not an"),
        ("E-NAME", "fn f(c:bool) -> u64 { let x = match c { }; return x; }", "match is a statement, not an"),
        ("E-VIEW-ALIAS", "fn f() { let mut b = Buf[u8](4); let p = b[0..2]; }", "write the part in each call"),
        (
            "E-LEN",
            "import std.vec (Vec);\nfn f() -> usize { let mut v = vec.new[u64](); vec.push(v, 1); return len(v); }",
            "v is a Vec: its length is v.len.",
        ),
    ],
)
def test_a_refusal_for_another_language_s_habit_says_what_cairn_writes(code, source, says):
    """The habits the 1.1 evaluation's subjects brought from Rust and C++ (evidence/v1_1/friction)."""
    assert says in refused(code, source)["message"]

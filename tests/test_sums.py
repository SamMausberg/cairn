"""Monomorphic tagged scalar results. No generics or unsafe payload projection."""

import ctypes
import subprocess

import pytest

from cairn.agent_tools import EditSession, canonical_source
from cairn.cairnc import RUNTIME, Diagnostic, compile_source

SOURCE = """
enum Division { Value(u64); Zero; }
enum Parse { Value(u8); Error(u32); }
enum State { Ready; Busy; }
fn divide(a:u64,b:u64)->Division {
  if b==0 { return Division.Zero; }
  return Division.Value(a/b);
}
fn use(a:u64,b:u64)->u64 {
  match divide(a,b) {
    Division.Value(v) => { return v; }
    Division.Zero => { return 42; }
  }
}
fn assign(x:u64)->u64 {
  let mut r=Division.Zero;
  r=Division.Value(x);
  let mut out:u64=0;
  match r { Division.Value(v)=>{out=v;} Division.Zero=>{out=99;} }
  return out;
}
fn parse(c:u8)->Parse {
  if c<48 || c>57 { return Parse.Error(u32(c)); }
  return Parse.Value(c-48);
}
fn decode(c:u8)->u64 {
  match parse(c) {
    Parse.Value(v)=>{ return u64(v); }
    Parse.Error(code)=>{ return u64(code)+1000; }
  }
}
fn tag(s:State)->u64 {
  match s { State.Ready=>{return 0;} State.Busy=>{return 1;} }
}
fn scoped(n:usize)->u64 {
  buffer temporary:u64[n]=zeroed;
  match Division.Value(u64(n)) {
    Division.Value(v)=>{ if v==0 {return 0;} return temporary[0]; }
    Division.Zero=>{return 0;}
  }
}
"""


def test_projection_and_agent_scopes():
    cpp, r = compile_source(SOURCE)
    assert compile_source(canonical_source(SOURCE))[0] == cpp
    sess = EditSession(SOURCE, "decode")
    packet = sess.packet()
    assert "Parse" in str(packet)
    sites = [s for s in sess.sites.values() if s["source"] == "u64(v)"]
    assert sites and "v" in sites[0]["bindings"]
    assert "code" not in sites[0]["bindings"]
    assert r["functions"]["scoped"]["heap_allocations"] == 1


@pytest.mark.parametrize(
    "decl,body,code",
    [
        ("enum R { Good(u64); Bad; }", "return R.Nope;", "E-ENUM-VARIANT"),
        ("enum R { Good(u64); Bad; }", "return R.Good;", "E-SUM-ARITY"),
        ("enum R { Good(u64); Bad; }", "return R.Bad(1);", "E-SUM-ARITY"),
        ("enum R { Good(u64); Bad; }", "return R.Good(true);", "E-TYPE-MISMATCH"),
        ("enum R { Good(u64); Bad; }", "let x=R.Good(1); return x.Good;", "E-FIELD"),
        ("enum R { Good(u64); Bad; }", "match R.Bad {R.Bad=>{return R.Bad;}}", "E-MATCH-COVERAGE"),
        (
            "enum R { Good(u64); Bad; }",
            "match R.Bad {R.Bad=>{return R.Bad;} R.Bad=>{return R.Bad;}}",
            "E-MATCH-DUPLICATE",
        ),
        (
            "enum R { Good(u64); Bad; }",
            "match R.Bad {R.Good=>{return R.Bad;} R.Bad=>{return R.Bad;}}",
            "E-MATCH-BINDING",
        ),
        (
            "enum R { Good(u64); Bad; }",
            "match R.Bad {R.Good(v)=>{return R.Good(v);} R.Bad(v)=>{return R.Bad;}}",
            "E-MATCH-BINDING",
        ),
        (
            "enum R { Good(u64); Bad; }",
            "let v:u64=0;match R.Bad {R.Good(v)=>{return R.Good(v);} R.Bad=>{return R.Bad;}}",
            "E-SHADOW",
        ),
        ("enum R { Good(u64); Bad; }", "match R.Bad {R.Good(v)=>{} R.Bad=>{}} return R.Good(v);", "E-UNBOUND"),
        (
            "enum R { Good(u64); Bad; }",
            "match 1 {R.Good(v)=>{return R.Good(v);} R.Bad=>{return R.Bad;}}",
            "E-MATCH-TYPE",
        ),
        ("enum R { Good(u64); Bad; }", "match R.Bad {R.Good(v)=>{return R.Good(v);} R.Bad=>{}}", "E-RETURN"),
        ("enum R { Good(u64); Bad; }", "let x=R.Bad; return x.fake();", "E-CALLEE"),
        ("enum R { Good(u64); Bad; }", "let x=R.Bad;let b=x==x;return R.Bad;", "E-OPERATOR"),
        (
            "enum R { Good(u64); Bad; }",
            "match R.Bad {R.Good(v)=>{v=2;return R.Good(v);} R.Bad=>{return R.Bad;}}",
            "E-IMMUTABLE",
        ),
    ],
)
def test_reject(decl, body, code):
    with pytest.raises(Diagnostic) as e:
        compile_source(decl + "fn f()->R {" + body + "}")
    assert e.value.data["code"] == code


@pytest.mark.parametrize(
    "decl", ["enum R { Bad(void); }", "enum R { Bad(ro<u64>[1]); }", "enum R { Bad(R); }", "enum R { Bad(rw<u64>); }"]
)
def test_payload_restrictions(decl):
    with pytest.raises(Diagnostic) as e:
        compile_source(decl)
    assert e.value.data["code"] == "E-SUM-PAYLOAD"


def test_sums_compose_with_records_and_views():
    """1.0: payloads are any value type and sums are ordinary array elements."""
    source = (
        "struct S {x:u64;} enum R {Good(S); Bad;} "
        "fn f(n:usize,x:ro<R>[n])->u64 { match x[0] { R.Good(s)=>{return s.x;} R.Bad=>{return 0;} } }"
    )
    assert "ct_S v_Good;" in compile_source(source)[0]


def test_nullary_constructor_and_copy():
    compile_source("enum R {V(u64); E;} fn f()->R {let a=R.E();let b=a;return b;}")


def test_arm_calls_participate_in_effect_analysis():
    src = "enum R {V(u64);} fn write(n:usize,x:rw<u64>[n]){x[0]=1;} fn f(n:usize,out:rw<u64>[n]){match R.V(0) {R.V(v)=>{write(n,out);}}}"
    _, r = compile_source(src)
    assert "write:out" in r["functions"]["f"]["effects"]


def test_nested_writes_in_arm_rejected():
    src = "enum R {V(u64);} fn g(n:usize,x:rw<u64>[n])->u64{x[0]=1;return 0;} fn f(n:usize,out:rw<u64>[n])->u64{match R.V(0) {R.V(v)=>{return g(n,out)+v;}}}"
    with pytest.raises(Diagnostic) as e:
        compile_source(src)
    assert e.value.data["code"] == "E-EFFECT-ORDER"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_native(cxx, tmp_path):
    cpp, _ = compile_source(SOURCE)
    (tmp_path / "p.cpp").write_text(cpp)
    (tmp_path / "cairn_runtime.hpp").write_text(RUNTIME)
    subprocess.run(
        [
            cxx,
            "-std=c++20",
            "-O2",
            "-fno-exceptions",
            "-fno-rtti",
            "-Werror",
            "-shared",
            "-fPIC",
            str(tmp_path / "p.cpp"),
            "-o",
            str(tmp_path / "p.so"),
        ],
        check=True,
        capture_output=True,
    )
    lib = ctypes.CDLL(str(tmp_path / "p.so"))
    lib.cf_use.argtypes = [ctypes.c_uint64] * 2
    lib.cf_use.restype = ctypes.c_uint64
    lib.cf_assign.argtypes = [ctypes.c_uint64]
    lib.cf_assign.restype = ctypes.c_uint64
    for a in [0, 1, 7, 2**32, 2**64 - 1]:
        assert lib.cf_assign(a) == a
        for b in [0, 1, 2, 7, 2**64 - 1]:
            assert lib.cf_use(a, b) == (a // b if b else 42)
    lib.cf_decode.argtypes = [ctypes.c_uint8]
    lib.cf_decode.restype = ctypes.c_uint64
    for c in range(256):
        assert lib.cf_decode(c) == (c - 48 if 48 <= c <= 57 else c + 1000)
    lib.cf_scoped.argtypes = [ctypes.c_size_t]
    lib.cf_scoped.restype = ctypes.c_uint64
    for n in [0, 1, 2, 16]:
        assert lib.cf_scoped(n) == 0


def test_value_equivalence_covers_sums_but_not_heap_storage():
    from cairn.scalar_semantics import equivalent

    assert equivalent(SOURCE, SOURCE, "use")["status"] == "smt-equivalent"
    assert equivalent(SOURCE, SOURCE, "decode")["status"] == "smt-equivalent"
    assert equivalent(SOURCE, SOURCE, "parse")["status"] == "smt-equivalent"
    wrong = equivalent(SOURCE, SOURCE.replace("return 42;", "return 43;"), "use")
    assert wrong["status"] == "counterexample" and wrong["counterexample"]["b"] == 0
    assert equivalent(SOURCE, SOURCE, "scoped")["status"] == "smt-equivalent"  # A local buffer is zeroed scratch.
    moved = "fn scoped(n:usize)->u64 { let mut b=Buf[u64](n); let c=take(b); return u64(len(c)); }"
    assert equivalent(moved, moved, "scoped")["status"] == "unknown"  # An owner that moves is not modeled.

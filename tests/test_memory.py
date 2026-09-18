"""Scope-owned memory and its interfaces. Native checks are finite, not proofs."""

import ctypes
import json
import subprocess
from pathlib import Path

import pytest

from cairn.agent_tools import EditSession, canonical_source
from cairn.cairnc import RUNTIME, Diagnostic, compile_source

SOURCE = """
fn fill(n:usize,x:rw<u64>[n]) { for i in 0..n { x[i]=u64(i); } }
fn sum(n:usize)->u64 {
  buffer scratch:u64[n] = zeroed;
  fill(len(scratch),scratch);
  let mut result:u64=0;
  for i in 0..len(scratch) { result=add_wrap(result,scratch[i]); }
  return result;
}
fn initial(n:usize)->u64 {
  buffer scratch:u64[n] = zeroed;
  let mut total:u64=0;
  for i in 0..n { total=add_wrap(total,scratch[i]); }
  return total;
}
fn early(n:usize)->u64 {
  for i in 0..n {
    buffer scratch:u64[n] = zeroed;
    if i==3 { return scratch[0]; }
  }
  return 0;
}
fn local_select(n:usize)->usize {
  buffer input:u64[n] = zeroed;
  buffer out:u64[n] = zeroed;
  fill(n,input);
  let used=compact out for i in len(out) where (input[i]&1)==0 yield input[i];
  return used;
}
fn fixed()->u64 { stack x:u64[4] = zeroed; fill(4,x); return x[2]; }
fn empty()->usize { stack x:bool[0] = zeroed; buffer y:u64[0] = zeroed; return len(x)+len(y); }
fn zero_float()->f64 { stack x:f64[8] = zeroed; return x[7]; }
"""


def test_effects_and_projection():
    cpp, r = compile_source(SOURCE)
    assert r["functions"]["sum"]["heap_allocations"] == 1
    assert {"alloc", "free", "zero_init", "local_read", "local_write"} <= set(
        r["functions"]["sum"]["effects"]
    )
    assert not any(x.startswith(("read:", "write:")) for x in r["functions"]["sum"]["effects"])
    assert r["functions"]["fixed"]["local_storage"][0]["bytes"] == 32
    assert compile_source(canonical_source(SOURCE))[0] == cpp
    packet = EditSession(SOURCE, "sum").packet()
    assert packet


@pytest.mark.parametrize(
    "body,code",
    [
        ("buffer x:u64[1] = zeroed; return x;", "E-TYPE-MISMATCH"),
        ("buffer x:u64[1] = zeroed; let y=x; return 0;", "E-VIEW-ALIAS"),
        ("if true { buffer x:u64[1] = zeroed; } return x[0];", "E-UNBOUND"),
        ("let mut n:usize=4; buffer x:u64[n] = zeroed; return 0;", "E-OWNER-EXTENT"),
        ("buffer x:u64[1+2] = zeroed; return 0;", "E-OWNER-EXTENT"),
        ("stack x:u64[8193] = zeroed; return 0;", "E-STACK-LIMIT"),
        ("stack x:u64[8192] = zeroed; stack y:u8[1] = zeroed; return 0;", "E-STACK-LIMIT"),
        ("let n:usize=4; stack x:u64[n] = zeroed; return 0;", "E-STACK-EXTENT"),
        ("buffer x:void[1] = zeroed; return 0;", "E-OWNER-ELEMENT"),
        ("buffer x:Missing[1] = zeroed; return 0;", "E-TYPE"),
        ("buffer x:u64[1] = zeroed; x=x; return 0;", "E-IMMUTABLE"),
        ("return len(3);", "E-LEN"),
        ("let x:u64=3; return len(x);", "E-LEN"),
        ("buffer x:u64[1] = zeroed; buffer x:u64[1] = zeroed; return 0;", "E-SHADOW"),
    ],
)
def test_reject(body, code):
    with pytest.raises(Diagnostic) as e:
        compile_source("fn f()->u64 {" + body + "}")
    assert e.value.data["code"] == code


def test_owners_cannot_alias_in_calls():
    with pytest.raises(Diagnostic) as e:
        compile_source(
            "fn copy(n:usize,x:rw<u64>[n],y:ro<u64>[n]){} fn f(){buffer b:u64[4] = zeroed;copy(4,b,b);}"
        )
    assert e.value.data["code"] == "E-ALIAS"


def test_allocating_call_cannot_be_hidden_in_expression():
    with pytest.raises(Diagnostic) as e:
        compile_source("fn g()->u64 {buffer b:u64[0]=zeroed;return 0;} fn f()->u64 = g()+1;")
    assert e.value.data["code"] == "E-EFFECT-ORDER"


def test_shape_identity_is_immutable():
    # Equal values with different names are deliberately not inferred equal.
    with pytest.raises(Diagnostic):
        compile_source(
            "fn g(n:usize,b:rw<u64>[n]){} fn f(n:usize){let m:usize=n;buffer b:u64[m]=zeroed;g(n,b);}"
        )


def test_local_name_collision_does_not_hide_parameter_effect():
    _, r = compile_source(
        "fn g(n:usize,x:rw<u64>[n]){x[0]=1;} fn f(n:usize,out:rw<u64>[n]){if true {buffer x:u64[n]=zeroed;g(n,x);}g(n,out);}"
    )
    assert "write:out" in r["functions"]["f"]["effects"]
    assert "local_write" in r["functions"]["f"]["effects"]


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
    for name in ["sum", "initial", "early", "local_select"]:
        f = getattr(lib, "cf_" + name)
        f.argtypes = [ctypes.c_size_t]
        f.restype = ctypes.c_uint64
        for n in [0, 1, 2, 3, 4, 7, 8, 17, 64, 257]:
            expected = (
                n * (n - 1) // 2 if name == "sum" else (n + 1) // 2 if name == "local_select" else 0
            )
            assert f(n) == expected
    lib.cf_fixed.restype = ctypes.c_uint64
    assert lib.cf_fixed() == 2
    lib.cf_empty.restype = ctypes.c_size_t
    assert lib.cf_empty() == 0
    lib.cf_zero_float.restype = ctypes.c_double
    assert lib.cf_zero_float() == 0.0


def test_semantics_rejects_owned_memory():
    from cairn.scalar_semantics import equivalent

    r = equivalent("fn f()->u64=0;", "fn f()->u64 { buffer b:u64[4]=zeroed;return b[0]; }", "f")
    assert r["status"] not in {"smt-equivalent", "passed"}

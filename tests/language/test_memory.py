"""Scope-owned memory and its interfaces. Native checks are finite, not proofs."""

import ctypes
import shutil
import subprocess

import pytest

from cairn.agent.agent_tools import EditSession, canonical_source
from cairn.compiler.cairnc import RUNTIME, RUNTIME_FILES, Diagnostic, compile_source

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
    assert {"alloc", "free", "zero_init", "local_read", "local_write"} <= set(r["functions"]["sum"]["effects"])
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
        compile_source("fn copy(n:usize,x:rw<u64>[n],y:ro<u64>[n]){} fn f(){buffer b:u64[4] = zeroed;copy(4,b,b);}")
    assert e.value.data["code"] == "E-ALIAS"


def test_allocating_call_cannot_be_hidden_in_expression():
    with pytest.raises(Diagnostic) as e:
        compile_source("fn g()->u64 {buffer b:u64[0]=zeroed;return 0;} fn f()->u64 = g()+1;")
    assert e.value.data["code"] == "E-EFFECT-ORDER"


def test_shape_identity_is_immutable():
    # Equal values with different names are deliberately not inferred equal.
    with pytest.raises(Diagnostic):
        compile_source("fn g(n:usize,b:rw<u64>[n]){} fn f(n:usize){let m:usize=n;buffer b:u64[m]=zeroed;g(n,b);}")


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
            expected = n * (n - 1) // 2 if name == "sum" else (n + 1) // 2 if name == "local_select" else 0
            assert f(n) == expected
    lib.cf_fixed.restype = ctypes.c_uint64
    assert lib.cf_fixed() == 2
    lib.cf_empty.restype = ctypes.c_size_t
    assert lib.cf_empty() == 0
    lib.cf_zero_float.restype = ctypes.c_double
    assert lib.cf_zero_float() == 0.0


RELEASE = """
struct Frame { pixels:Buf[u64]; id:u64; }

fn sink(b:Buf[u64]) {}
fn hand_on(b:Buf[u64]) -> Buf[u64] { return b; }
fn scrap(f:Frame) -> u64 = f.id;
fn recycle(v:rw<Frame>, fresh:Buf[u64]) { v.pixels = fresh; }

fn main() -> i32 {
  let n:usize = 131072;
  for i in 0..1024 {
    let b = Buf[u64](n);
    sink(b);
  }
  for i in 0..1024 {
    let b = Buf[u64](n);
    let c = hand_on(b);
    sink(c);
  }
  for i in 0..1024 {
    let first = Buf[u64](n);
    let mut f = Frame(first, 7);
    let fresh = Buf[u64](n);
    recycle(f, fresh);
    let id = scrap(f);
  }
  return 0;
}
"""


def test_free_is_charged_where_an_owner_is_released():
    """`free` says a release runs here, so it follows the drop and not the allocation."""
    functions = compile_source(RELEASE)[1]["functions"]
    assert functions["sink"]["effects"] == ["free"]  # It only drops what it was given.
    assert functions["hand_on"]["effects"] == []  # It hands the same owner on: nothing is released.
    assert functions["scrap"]["effects"] == ["free"]  # A record reaches its Buf through a field.
    assert {"free", "write:v"} <= set(functions["recycle"]["effects"])  # The old value goes where the new one lands.
    assert {"alloc", "free"} <= set(functions["main"]["effects"])


def test_a_path_that_leaves_early_drops_what_it_still_holds():
    """`x` is handed away on the path that goes on, so only the return itself says the other path releases it."""
    source = (
        "struct Holder { slots:Array[Buf[u64], 2]; }\n"
        "fn zeros() -> Holder = Holder(Array[Buf[u64], 2]());\n"
        "fn eat(x:Holder, c:bool) -> Holder { if c { return zeros(); } return x; }\n"
    )
    functions = compile_source(source)[1]["functions"]
    assert "free" in functions["eat"]["effects"]
    assert "free" not in functions["zeros"]["effects"]  # It builds inline storage and hands it on.


def test_a_linear_marker_alone_releases_nothing():
    """`releases` is not `kind`: a linear record of scalars is consumed exactly once and frees no storage."""
    source = (
        "linear struct Lease { id:u64; }\n"
        "fn open(id:u64) -> Lease = Lease(id);\n"
        "fn close(l:Lease, seen:rw<u64>) { seen = l.id; }\n"
    )
    functions = compile_source(source)[1]["functions"]
    assert "free" not in functions["close"]["effects"]  # The lease ends here and no storage comes back.
    assert functions["open"]["effects"] == []


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_release_runs_at_the_drop(cxx, tmp_path):
    """Each loop passes at least a gigabyte through a release site, one mebibyte at a time. Leak detection
    shows the storage comes back, and the resident cap shows it comes back at the drop rather than at the
    exit: holding any one loop's buffers would pass 768 MiB while the program is still running."""
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")
    (tmp_path / "p.cpp").write_text(compile_source(RELEASE)[0] + "int main() { return static_cast<int>(cf_main()); }\n")
    for name, text in RUNTIME_FILES.items():
        (tmp_path / name).write_text(text)
    flags = ["-std=c++20", "-O1", "-g", "-fno-exceptions", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
    flags += ["-Wno-unused-variable", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
    subprocess.run([cxx, *flags, str(tmp_path / "p.cpp"), "-o", str(tmp_path / "p")], check=True, timeout=180)
    options = {"ASAN_OPTIONS": "detect_leaks=1:hard_rss_limit_mb=768"}
    assert subprocess.run([tmp_path / "p"], timeout=120, env=options).returncode == 0


def test_semantics_models_scratch_storage_but_not_a_moved_owner():
    from cairn.verify.scalar_semantics import equivalent

    zeroed = equivalent("fn f()->u64=0;", "fn f()->u64 { buffer b:u64[4]=zeroed;return b[0]; }", "f")
    assert zeroed["status"] == "smt-equivalent"  # Zeroed scratch; a failed allocation is outside the model.
    moved = "fn f(n:usize)->usize { let mut b=Buf[u64](n); let c=take(b); return len(c); }"
    assert equivalent(moved, moved, "f")["status"] == "unknown"

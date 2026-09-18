import ctypes
import subprocess

import pytest

from cairn.agent_tools import canonical_source
from cairn.cairnc import RUNTIME, Diagnostic, compile_source

SOURCE = """
enum Result {Value(u64); Empty;}
fn loop(n:usize)->u64 {
  let mut total:u64=0;
  for i in 0..n {
    buffer x:u64[4]=zeroed;
    if i==8 {break;}
    if (i&1)==0 {continue;}
    total=add_wrap(total,u64(i));
  }
  return total;
}
fn nested()->u64 {
  let mut total:u64=0;
  for i in 0..4 {
    for j in 0..5 {if j==3 {break;} if j==1 {continue;} total=add_wrap(total,1);}
    if i==2 {break;}
  }
  return total;
}
fn in_match(n:usize)->u64 {
 let mut total:u64=0;
 for i in 0..n {
   buffer x:u64[2]=zeroed;
   match Result.Value(u64(i)) {
     Result.Value(v)=>{if v==4 {break;} if v==1 {continue;} total=add_wrap(total,v);}
     Result.Empty=>{break;}
   }
 }
 return total;
}
fn w()->u64 {
 let mut i:u64=0; let mut sum:u64=0;
 while true {
   buffer x:u64[3]=zeroed;
   i=i+1;
   if i==7 {break;}
   if i==2 {continue;}
   sum=sum+i;
 }
 return sum;
}
"""


def test_projection():
    assert compile_source(SOURCE)[0] == compile_source(canonical_source(SOURCE))[0]


@pytest.mark.parametrize(
    "body,code",
    [
        ("break;", "E-LOOP-CONTROL"),
        ("continue;", "E-LOOP-CONTROL"),
        ("for i in 0..3 {break; let a:u64=3;}", "E-UNREACHABLE"),
        ("while true {continue; return;}", "E-UNREACHABLE"),
    ],
)
def test_errors(body, code):
    with pytest.raises(Diagnostic) as e:
        compile_source("fn f(){" + body + "}")
    assert e.value.data["code"] == code


def test_loop_exit_is_not_a_function_return():
    with pytest.raises(Diagnostic) as e:
        compile_source("fn f()->u64 {while true {break;}}")
    assert e.value.data["code"] == "E-RETURN"


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
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-variable",
            "-Wno-unused-parameter",
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
    for name in ["loop", "in_match"]:
        f = getattr(lib, "cf_" + name)
        f.argtypes = [ctypes.c_size_t]
        f.restype = ctypes.c_uint64
        for n in range(20):
            exp = (
                sum(i for i in range(min(n, 8)) if i % 2)
                if name == "loop"
                else sum(i for i in range(min(n, 4)) if i != 1)
            )
            assert f(n) == exp
    lib.cf_nested.restype = ctypes.c_uint64
    assert lib.cf_nested() == 6
    lib.cf_w.restype = ctypes.c_uint64
    assert lib.cf_w() == 19


def test_run_memory_limit_rejected_before_build(capsys):
    from cairn.cli import main

    assert main(["run", "examples/hello", "--memory-mib", "0"]) == 2
    assert "64..65536" in capsys.readouterr().out

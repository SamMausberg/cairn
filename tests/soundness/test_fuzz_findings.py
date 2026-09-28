"""Programs the typed fuzzer (tools/checks/fuzz) found the compiler, its build or its runtime getting wrong, each cut
down to what shows the fault and kept beside the fix that made it right."""

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import device_build, native

# A program that checks always builds. The default build makes every C++ warning an error, and clang++ refused a local
# assigned to itself (-Wself-assign), both compilers a value compared with itself (-Wtautological-compare), and g++ an
# unsigned value compared with zero (-Wtype-limits), as in the u64 instance of `negative`: 23 of the first 96 programs
# the fuzzer built, seed 2.
POINTLESS = """fn negative[T:integer](x:T) -> bool = x < 0;

fn main() -> i32 {
  let mut seen:u64 = 0;
  seen = seen;
  let ok = seen == 0;
  if ok != ok { return 1; }
  let mut w:u8 = 5;
  while w < 0 { w += 1; }
  let b:i64 = -3;
  if negative(seen) || !negative(b) { return 3; }
  if w >= 0 { return 0; }
  return 2;
}
"""
# The same in device code and beside it, which nvcc's own front end refused as a pointless comparison (#186).
POINTLESS_ON_DEVICE = """fn clear(n:usize, out:rw<u64>[n]@device) {
  parallel i in n {
    let mut v = out[i];
    v = v;
    if v < 0 || v != v { out[i] = 1; }
  }
}

fn main() -> i32 {
  let mut w:u8 = 5;
  while w < 0 { w += 1; }
  return i32(w);
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_a_program_a_cpp_compiler_calls_pointless_builds_as_cairn_build_builds_it(tmp_path, cxx):
    assert native(tmp_path, POINTLESS, cxx).returncode == 0


def test_nvcc_builds_device_code_a_cpp_compiler_calls_pointless(tmp_path):
    assert device_build(tmp_path, compile_source(POINTLESS_ON_DEVICE)[0], entry="main").stat().st_size > 0

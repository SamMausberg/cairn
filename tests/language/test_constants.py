"""Constants fold exactly in any order of declaration, name static extents, and an f32 constant is what
the machine would compute.
"""

import pytest

from cairn.compiler.cairnc import Diagnostic, compile_source
from emitted import SANITIZED, run

CONSTANTS = """
const W:usize = 8;
const N:usize = W * H;                        // order of declaration does not matter
const H:usize = 4;
const MIN:i64 = -7 / 2;                       // toward zero, as at run time
const REST:i64 = -7 % 2;
const HALF:f32 = 1.0 / 2.0;
const SUM:f64 = 0.1 + 0.2;
const BIG:bool = N > 30 && !(W == H);
const BYTE:u8 = u8(255);
fn last(xs:ro<u64>[N]) -> u64 = xs[N - 1];    // a constant is a static extent
fn main() -> i32 {
  stack cells:u64[N] = zeroed;
  cells[N - 1] = 9;
  if last(cells) != 9 || MIN != -3 || REST != -1 || HALF != 0.5 || SUM != 0.30000000000000004 || !BIG || BYTE != 255 { return 1; }
  return 0;
}
"""


def test_a_natural_is_inferred_from_the_extent_it_names(tmp_path):
    """`fn say[N:nat](text:ro<u8>[N])` learns N from a literal, a stack array or an Array: nobody counts by hand."""
    source = (
        "fn say[N:nat](text:ro<u8>[N]) -> usize = N;\n"
        "fn total[N:nat](xs:ro<u64>[N]) -> u64 { let mut t:u64 = 0; for i in 0..N { t = t + xs[i]; } return t; }\n"
        "fn main() -> i32 { stack cells:u64[4] = zeroed; let mut fixed = Array[u64, 3](); cells[3] = 5; fixed[0] = 2;\n"
        '  if say("analytics: rows") != 15 || total(cells) != 5 || total(fixed) != 2 || say[3]("abc") != 3 { return 1; }\n'
        "  return 0; }\n"
    )
    rows = compile_source(source)[1]["functions"]
    assert {"say[15]", "say[3]", "total[4]", "total[3]"} <= set(rows)
    with pytest.raises(Diagnostic) as e:  # A run-time extent names no natural.
        compile_source(source + "fn f(n:usize, xs:ro<u64>[n]) -> u64 = total(xs);")
    assert e.value.data["code"] == "E-INFER"


NATURAL = """
const N:usize = 2 + 2;
struct Grid { cells:Array[u64, N]; }
fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);
family times = scale[4..5];
fn main() -> i32 {
  let mut a = Array[u64, N]();
  a[3] = 7;
  let mut g = Grid(Array[u64, N]());
  g.cells[N - 1] = a[3];
  if g.cells[3] != 7 || scale[N](2) != 8 || len(a) != N { return 1; }
  return 0;
}
"""


def test_a_constant_is_a_natural_wherever_one_is_written(tmp_path):
    """`Array[u64, N]` in a local, in a field and as `scale[N](x)`: the same N that names a view's extent."""
    assert run(tmp_path, compile_source(NATURAL)[0], *SANITIZED).returncode == 0
    with pytest.raises(Diagnostic) as e:  # Not any constant: a natural.
        compile_source("const X:f64 = 1.5;\nfn main() -> i32 { let a = Array[u64, X](); return 0; }")
    assert e.value.data["code"] == "E-TYPE"


SINGLE = """
const A:f32 = 0.71764 + -2.686222;
const B:f32 = 16777217.0;
const N:usize = usize(B);
const C:f32 = f32(0.1 + 0.2);
const D:f64 = f64(f32(0.1)) * 3.0;
const E:f32 = 1.1 * 1.1 / 3.3;
const F:f32 = A * E - C;
fn main() -> i32 {
  let a:f32 = 0.71764;
  let b:f32 = -2.686222;
  let big:f32 = 16777217.0;
  let wide:f64 = 0.1 + 0.2;
  let tenth:f32 = 0.1;
  let x:f32 = 1.1;
  let y:f32 = 3.3;
  let e:f32 = x * x / y;
  if a + b != A { return 1; }
  if usize(big) != N { return 2; }
  if f32(wide) != C { return 3; }
  if f64(tenth) * 3.0 != D { return 4; }
  if e != E { return 5; }
  if (a + b) * e - f32(wide) != F { return 6; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_an_f32_constant_is_what_the_machine_would_compute(tmp_path, cxx):
    """Every literal, conversion and operation rounds once, as it will at run time; a conversion's operand is f64."""
    flags = ["-std=c++20", "-O2", "-ffp-contract=off", "-fno-fast-math"]
    assert run(tmp_path, compile_source(SINGLE)[0], *flags, cxx=cxx).returncode == 0


def test_constants_fold_exactly_and_name_static_extents(tmp_path):
    assert run(tmp_path, compile_source(CONSTANTS)[0], *SANITIZED).returncode == 0
    refused = {
        "const A:u32 = B + 1;\nconst B:u32 = A;": "E-CONST",  # defined in terms of itself
        "const A:u8 = 200 + 100;": "E-LITERAL-RANGE",  # the result must fit its type
        "const A:u32 = 1 / 0;": "E-CONST",
        "const A:u8 = u8(256);": "E-CONST",
        "const A:f64 = 1.0e308 * 10.0;": "E-CONST",  # not finite
    }
    for source, code in refused.items():
        with pytest.raises(Diagnostic) as e:
            compile_source(source)
        assert e.value.data["code"] == code, e.value.data["message"]

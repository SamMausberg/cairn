"""The exact float builtins: sqrt, floor, ceil, trunc, abs and to_bits.

IEEE 754 makes sqrt correctly rounded and the rest exact, so the oracle here is Python's own IEEE arithmetic, and
the same program must print the same bits under both compilers, with and without the sanitizers. The libm
functions whose last bit varies (exp, log, sin) are not builtins and are not tested as if they were.
"""

import math
import struct

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.verify.scalar_semantics import equivalent
from emitted import SANITIZED, WARNINGS, refused, run, sanitized

EXACT = """
extern fn putchar(c:i32) -> i32 effects(io);

fn hex(value:u64) {
  for k in 0..16 {
    let nibble = i32(shr(value, (15 - k) * 4) & 15);
    unsafe { if nibble < 10 { let a = putchar(48 + nibble); } else { let b = putchar(87 + nibble); } }
  }
  unsafe { let c = putchar(10); }
}

fn main() -> i32 {
  let mut xs = Array[f64, 8]();
  xs[0] = 2.0; xs[1] = -2.5; xs[2] = 1e300; xs[3] = 5e-324; xs[4] = 0.1; xs[5] = -0.0; xs[6] = 7.5; xs[7] = 3.0;
  for i in 0..8 {
    let x = xs[i];
    hex(to_bits(sqrt(abs(x))));
    hex(to_bits(floor(x)));
    hex(to_bits(ceil(x)));
    hex(to_bits(trunc(x)));
    let y = f32(x);
    hex(u64(to_bits(sqrt(abs(y)))));
    hex(u64(to_bits(floor(y))));
  }
  return 0;
}
"""


def bits64(x: float) -> str:
    return struct.pack(">d", x).hex()


def bits32(x: float) -> str:
    return "00000000" + struct.pack(">f", x).hex()


def f32(x: float) -> float:
    return math.copysign(math.inf, x) if abs(x) > 3.5e38 else struct.unpack("f", struct.pack("f", x))[0]


def oracle() -> str:
    """Python's float is IEEE binary64 and its math.sqrt is correctly rounded; a binary32 square root is the binary64
    one rounded once more, which is exact rounding because 53 >= 2 * 24 + 2."""
    lines = []
    for x in [2.0, -2.5, 1e300, 5e-324, 0.1, -0.0, 7.5, 3.0]:
        lines += [bits64(math.sqrt(abs(x))), bits64(integral(math.floor, x))]
        lines += [bits64(integral(math.ceil, x)), bits64(integral(math.trunc, x))]
        y = f32(x)  # 1e300 is past binary32: infinity, as the C++ conversion gives
        lines += [bits32(f32(math.sqrt(abs(y)))), bits32(integral(math.floor, y))]
    return "\n".join(lines) + "\n"


def integral(rounding, x: float) -> float:
    """Python's floor, ceil and trunc answer an int, which loses a zero's sign and cannot hold infinity."""
    return x if x == 0 or math.isinf(x) else float(rounding(x))


def test_the_oracle_keeps_negative_zero():
    """floor(-0.0) is -0.0 in IEEE; Python's math.floor answers an int, so the oracle passes zeros through."""
    assert bits64(-0.0) == "8000000000000000" and oracle().count("8000000000000000") >= 3


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_compiler_computes_the_same_bits_as_ieee(tmp_path, cxx):
    done = run(tmp_path, compile_source(EXACT)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, done.stderr
    assert done.stdout == oracle()


def test_the_canonical_projection_emits_the_same_program():
    assert compile_source(canonical_source(EXACT))[0] == compile_source(EXACT)[0]


def test_the_signed_minimum_has_no_magnitude(tmp_path):
    source = "fn main() -> i32 { let low:i32 = -2147483647 - 1; let m = abs(low); return 0; }"
    assert run(tmp_path, compile_source(source)[0], *SANITIZED).returncode == -6


def test_rows_say_only_what_can_happen():
    rows = compile_source(
        "fn hypot(x:f64, y:f64) -> f64 = sqrt(x * x + y * y);\n"
        "fn distance(a:i64, b:i64) -> i64 = abs(a - b);\n"
        "fn pattern(x:f32) -> u32 pure = to_bits(floor(x));\n"
    )[1]["functions"]
    assert rows["hypot"]["effects"] == [] and rows["pattern"]["effects"] == []
    assert rows["distance"]["effects"] == ["trap"]  # a - b, and the magnitude of the minimum


def test_a_function_of_the_same_name_is_the_programs_own():
    cpp = compile_source("fn sqrt(x:u64) -> u64 = x;\nfn main() -> i32 { if sqrt(4) != 4 { return 1; } return 0; }")[0]
    assert "cr::math::sqrt" not in cpp


def test_the_value_model_does_not_claim_them():
    source = "fn root(x:f64) -> f64 = sqrt(x);"
    assert equivalent(source, source, "root")["status"] == "unknown"


@pytest.mark.parametrize(
    "source",
    [
        "fn f(x:u64) -> u64 = sqrt(x);",  # an integer square root is not IEEE's
        "fn f(x:u32) -> u32 = abs(x);",  # an unsigned magnitude is the value itself
        "fn f(x:i64) -> u64 = to_bits(x);",
        "fn f(x:f64) -> f64 = floor(x, 2.0);",
    ],
)
def test_what_the_builtins_refuse(source):
    refused("E-ARITY" if "2.0" in source else "E-MATH-TYPE", source)


LIBM = """
import std.math;

fn main() -> i32 {
  let half = math.sin(math.PI / 6.0);
  let one = math.exp(0.0);
  let back = math.log(math.E);
  let eight = math.pow(2.0, 3.0);
  let three = math.log2(8.0);
  let turn = math.atan2(1.0, 1.0);
  let flat = math.cos(0.0);
  let none = math.tan(0.0);
  if abs(half - 0.5) > 1e-15 || one != 1.0 || abs(back - 1.0) > 1e-15 || eight != 8.0 { return 1; }
  if three != 3.0 || abs(turn - math.PI / 4.0) > 1e-15 || flat != 1.0 || none != 0.0 { return 2; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_libm_module_links_the_c_library(tmp_path, cxx):
    """std.math is libm itself: its results are the ones C gets here, near the exact values, not IEEE-exact ones."""
    done = run(tmp_path, compile_source(LIBM)[0], *sanitized(cxx), *WARNINGS, cxx=cxx)
    assert done.returncode == 0, done.stderr


def test_a_libm_call_says_so_in_every_row():
    rows = compile_source("import std.math;\nfn grow(x:f64) -> f64 = math.exp(x);\n")[1]["functions"]
    assert "ffi:exp" in rows["grow"]["effects"] and "io" not in rows["grow"]["effects"]
    refused("E-EFFECT-ORDER", "import std.math;\nfn both(x:f64) -> f64 = math.exp(x) + math.log(x);\n")

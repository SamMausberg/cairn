"""std.fmt: text built into a Vec, checked against Python's own formatting as an independent oracle.

Python formats a float in fixed point from its exact binary value, rounding half to even as printf does, so
every fixed-point line the library prints must match it byte for byte, for ties, for the largest and smallest
doubles and for every place count up to forty. Integers, hex and padding are checked against Python's format
specifications the same way.
"""

import math
import random
import struct

import pytest

from cairn.compiler.cairnc import compile_source
from emitted import native, refused

BOTH = ["clang++", "g++"]


def literal(x: float) -> str:
    """A CAIRN expression for exactly `x`: repr round-trips, and the specials are computed, not written."""
    if math.isnan(x):
        return "sqrt(-1.0)"
    if math.isinf(x):
        return ("-" if x < 0 else "") + "(1e308 * 10.0)"
    text = repr(x)
    return text if any(c in text for c in ".e") else text + ".0"


def cases() -> list[tuple[float, int]]:
    ties = [(0.125, 2), (0.375, 2), (2.5, 0), (3.5, 0), (0.5, 0), (1.5, 0), (-2.5, 0), (1e-7, 6), (5e-7, 6)]
    edges = [(0.0, 3), (-0.0, 2), (5e-324, 40), (2.2250738585072014e-308, 12), (1.7976931348623157e308, 0)]
    edges += [(1e22, 1), (123456789.123456789, 9), (0.1, 20), (0.1, 40), (-1234.5678, 3), (float("nan"), 2)]
    edges += [(float("inf"), 1), (float("-inf"), 4), (2.0**63, 0), (2.0**-30, 40), (1 / 3, 17), (9.995, 2)]
    rng = random.Random(20260922)
    for _ in range(40):
        bits = rng.getrandbits(64)
        x = struct.unpack("d", struct.pack("Q", bits))[0]
        if not math.isnan(x):
            edges.append((x, rng.randrange(0, 41)))
    for _ in range(40):
        edges.append((rng.uniform(-1e6, 1e6), rng.randrange(0, 12)))
    return ties + edges


def expected(x: float, places: int) -> str:
    if math.isnan(x):
        return "nan"
    if math.isinf(x):
        return "-inf" if x < 0 else "inf"
    return f"{x:.{places}f}"


FIXED_CASES = cases()
FIXED = (
    "import std.fmt;\nimport std.io;\nimport std.vec (Vec);\n\n"
    "fn show(value:f64, places:usize) {\n  let mut line = vec.new[u8]();\n  fmt.fixed(line, value, places);\n"
    "  io.println(line.data[0..line.len]);\n}\n\nfn main() -> i32 {\n"
    + "".join(f"  show({literal(x)}, {p});\n" for x, p in FIXED_CASES)
    + "  return 0;\n}\n"
)


@pytest.mark.parametrize("cxx", BOTH)
def test_fixed_point_is_exact_and_rounds_half_to_even(tmp_path, cxx):
    done = native(tmp_path, FIXED, cxx, timeout=240)
    assert done.stdout.splitlines() == [expected(x, p) for x, p in FIXED_CASES]


INTEGERS = """
import std.fmt;
import std.io;
import std.vec (Vec);

fn main() -> i32 {
  let mut out = vec.new[u8]();
  let count:usize = 42;
  let small:u8 = 255;
  let drift:i32 = -2147483647 - 1;
  fmt.uint(out, count);
  fmt.bytes(out, "|");
  fmt.uint(out, small);
  fmt.bytes(out, "|");
  fmt.int(out, drift);
  fmt.bytes(out, "|");
  let big:i64 = 9223372036854775807;
  fmt.int(out, big);
  fmt.bytes(out, "|");
  fmt.padded(out, count, 6, ' ');
  fmt.bytes(out, "|");
  fmt.padded(out, count, 6, '0');
  fmt.bytes(out, "|");
  fmt.padded(out, count, 1, '0');
  fmt.bytes(out, "|");
  fmt.hex(out, 255, 0);
  fmt.bytes(out, "|");
  fmt.hex(out, 255, 4);
  fmt.bytes(out, "|");
  fmt.hex(out, 0, 0);
  fmt.bytes(out, "|");
  fmt.hex(out, 18446744073709551615, 0);
  fmt.bytes(out, "|");
  fmt.left(out, "ab", 5, '.');
  fmt.bytes(out, "|");
  fmt.right(out, "ab", 5, '.');
  fmt.bytes(out, "|");
  fmt.left(out, "abcdef", 3, '.');
  io.println(out.data[0..out.len]);
  return 0;
}
"""


@pytest.mark.parametrize("cxx", BOTH)
def test_integers_hex_and_padding(tmp_path, cxx):
    done = native(tmp_path, INTEGERS, cxx)
    wanted = [
        f"{42}", f"{255}", f"{-(2**31)}", f"{2**63 - 1}", f"{42:>6}", f"{42:06}", f"{42:01}", f"{255:x}",
        f"{255:04x}", f"{0:x}", f"{2**64 - 1:x}", f"{'ab':.<5}", f"{'ab':.>5}", "abcdef",
    ]  # fmt: skip
    assert done.stdout == "|".join(wanted) + "\n"


def test_forty_places_is_the_limit(tmp_path):
    source = "import std.fmt;\nimport std.vec (Vec);\nfn main() -> i32 { let mut out = vec.new[u8](); fmt.fixed(out, 1.0, 41); return 0; }"
    with pytest.raises(AssertionError, match="exit -6"):
        native(tmp_path, source)


def test_building_text_allocates_and_says_so():
    rows = compile_source(
        "import std.fmt;\nimport std.vec (Vec);\nfn f(out:rw<Vec[u8]>, x:f64) { fmt.fixed(out, x, 2); }"
    )
    row = set(rows[1]["functions"]["f"]["effects"])
    assert {"alloc", "free", "write:out", "trap", "stack_storage"} <= row and "io" not in row


def test_a_float_is_not_an_unsigned_integer():
    refused("E-BOUND", "import std.fmt;\nimport std.vec (Vec);\nfn f(out:rw<Vec[u8]>) { fmt.uint(out, 1.5); }")

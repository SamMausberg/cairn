"""The numerical policy has one definition and two renderings: the host's verdict, which validation, `cairn test`'s
replay and the implementation session use, and the CAIRN function the device generator writes into its tests,
lowered to C++. Both must answer every pair of one table alike, under clang++ and g++.
"""

import math
import struct

import pytest

from cairn.verify import agreement
from cairn.verify.boundaries import Case
from cairn.verify.device_validation import cases_as_tests
from emitted import native

MAX = 1.7976931348623157e308
SUB = 5e-324  # the least subnormal
NAN = struct.unpack("<d", struct.pack("<Q", 0xFFF0000000000001))[0]  # negative, signaling, with a payload
DECODE = {"f64": ("<d", "<Q"), "f32": ("<f", "<I"), "f16": ("<e", "<H")}
TOLERANCES = [
    {"absolute": 0.0, "relative": 0.0},
    {"absolute": 1e-12, "relative": 0.0},
    {"absolute": 0.0, "relative": 0.1},
    {"absolute": 1.0, "relative": 1e-9},
    {"absolute": 0.0, "relative": 2**-40},
    {"absolute": SUB, "relative": 0.0},
    {"absolute": 1e308, "relative": 1e308},
]


def bits_of(ty: str, value: float) -> int:
    value_format, bits_format = DECODE[ty]
    return struct.unpack(bits_format, struct.pack(value_format, value))[0]


F64 = [(100.0, 111.0), (111.0, 100.0), (100.0, 110.0), (100.0, 90.0), (100.0, 89.99999999999999), (math.nan, math.nan),
       (math.nan, NAN), (math.nan, 1.0), (1.0, math.nan), (math.nan, math.inf), (0.0, -0.0), (-0.0, 0.0), (0.0, 0.0),
       (-0.0, -0.0), (0.0, SUB), (math.inf, math.inf), (-math.inf, -math.inf), (math.inf, -math.inf), (math.inf, MAX),
       (MAX, math.inf), (-math.inf, -MAX), (math.inf, 1.0), (SUB, 2 * SUB), (2.2250738585072014e-308,
       2.225073858507201e-308), (SUB, 0.0), (MAX, MAX), (MAX, -MAX), (MAX, math.nextafter(MAX, 0.0)), (-MAX, MAX),
       (1.0, 1.0 + 2**-52), (1.0, 1.0 - 2**-53), (0.1 + 0.2, 0.3), (1e300, 1.0000000001e300), (1e-300, 2e-300),
       (1.0, 1.0 + 1e-9), (1e9, 1e9 + 1.0)]  # fmt: skip
F32 = [(100.0, 111.0), (math.nan, math.nan), (-0.0, 0.0), (math.inf, 3.4028234663852886e38), (1.401298464324817e-45, 0.0),
       (1.0, 1.0000001192092896), (0.1, 0.10000000149011612)]  # fmt: skip
F16 = [(0x3C00, 0x3C01), (0x7E00, 0xFE01), (0x8000, 0x0000), (0x7C00, 0x7BFF), (0x0001, 0x0000), (0x5640, 0x56F0)]
BF16 = [(0x3F80, 0x3F81), (0x7FC0, 0xFFC1), (0x8000, 0x0000), (0x7F80, 0x7F7F), (0x42C8, 0x42DE)]
PAIRS = [
    *(("f64", bits_of("f64", r), bits_of("f64", c)) for r, c in F64),
    *(("f32", bits_of("f32", r), bits_of("f32", c)) for r, c in F32),
    *(("f16", r, c) for r, c in F16),
    *(("bf16", r, c) for r, c in BF16),
]


def value(ty: str, bits: int) -> float:
    """The pattern `bits` of `ty` widened to f64, as the host widens it."""
    if ty == "bf16":
        return struct.unpack("<f", struct.pack("<I", bits << 16))[0]
    value_format, bits_format = DECODE[ty]
    return struct.unpack(value_format, struct.pack(bits_format, bits))[0]


def host(ty: str, r: int, c: int) -> list[bool]:
    return [agreement.agrees(value(ty, r), value(ty, c), t) for t in TOLERANCES]


def program() -> str:
    """Every tolerance as the function the device generator writes, and a main that prints each pair's verdicts."""
    helpers = [agreement.helper(f"agree_{k}", t) for k, t in enumerate(TOLERANCES)]
    lines = ["fn main() -> i32 {"]
    for ty, r, c in PAIRS:
        width = {"f64": 16, "f32": 8}.get(ty, 4)
        a, b = (agreement.widened(f"from_bits[{ty}](0x{x:0{width}x})", ty) for x in (r, c))
        verdicts = ", ' ', ".join(f"agree_{k}({a}, {b})" for k in range(len(TOLERANCES)))
        lines.append(f"  println({verdicts});")
    return "\n".join([*helpers, *lines, "  return 0;", "}"]) + "\n"


def test_the_relative_part_scales_the_reference_and_nothing_else():
    """For reference 100, candidate 111, no absolute part and a relative part of 0.1, host validation scaled by the
    candidate and accepted while the generated device test scaled by the reference and refused."""
    tenth = {"absolute": 0.0, "relative": 0.1}
    assert not agreement.agrees(100.0, 111.0, tenth) and agreement.agrees(111.0, 100.0, tenth)
    assert agreement.same("f64", (100.0).hex(), (111.0).hex(), tenth) is False
    assert agreement.agrees(math.nan, NAN, {}) and not agreement.agrees(math.nan, 1.0, {"absolute": 1e308})
    assert not agreement.agrees(0.0, -0.0, {}) and agreement.agrees(0.0, -0.0, {"absolute": 1e-12})
    assert not agreement.agrees(math.inf, MAX, {"absolute": 1e308, "relative": 1e308})
    assert agreement.stated(tenth)["relative_to"] == "the reference"


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_host_and_the_generated_code_give_every_pair_the_same_verdict(tmp_path, cxx):
    done = native(tmp_path, program(), cxx=cxx)
    printed = done.stdout.splitlines()
    assert len(printed) == len(PAIRS)
    for (ty, r, c), line in zip(PAIRS, printed, strict=True):
        assert [word == "true" for word in line.split()] == host(ty, r, c), (ty, hex(r), hex(c), line)


def test_the_device_generator_writes_this_policy_and_no_other():
    source = "fn f(x:f64) -> f64 = x;\nfn g(x:f64) -> f64 implements f = x + 0.0;\n"
    tolerance = {"absolute": 0.0, "relative": 0.1}
    generated = cases_as_tests(source, "f", "g", [Case({"x": -0.0})], tolerance)
    assert agreement.helper("agree_g", tolerance) in generated.source
    assert "assert(agree_g(want, got));" in generated.source

"""The device side of validation. The default suite generates the device tests of an implementation whose views
live on the device, checks them and compiles them for sm_120 without running anything; only `make gpu` runs them,
under each Compute Sanitizer tool as a result of its own. Every input reaches the generated source exactly, which a
host function's generated tests show by running under both compilers, and a tool that ran nothing is `unknown`.
"""

import math
import struct

import pytest

from cairn.compiler.cairnc import Parser, compile_source
from cairn.projects.project import load_project
from cairn.verify import boundaries
from cairn.verify.boundaries import Case
from cairn.verify.device_validation import TOOLS, cases_as_tests, sanitized, verdict
from cairn.verify.runner import run_tests
from emitted import device_build
from support import device_reason

SOURCE = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i]; }
}

fn scale_twice(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale when n % 2 == 0 {
  parallel i in n / 2 {
    out[2 * i] = a * x[2 * i];
  }
  parallel i in n / 2 {
    out[2 * i + 1] = a * x[2 * i + 1];
  }
}
"""


LONG = [float(i) for i in range(1000)]
LONG[3], LONG[5], LONG[7], LONG[9] = math.nan, -0.0, math.inf, -math.inf
SHORT = {"n": 4, "out": [-0.0] * 4, "x": [math.nan, -0.0, math.inf, -math.inf], "a": math.nan}
EDGES = [  # what the boundaries never generate, and every value that decimal digits or a skipped zero would lose
    Case({"n": 1000, "out": [0.0] * 1000, "x": LONG, "a": -0.0}, {}, "a long view"),
    Case(SHORT, {"out": 1, "x": 1}, "views one element off"),
]


def generated():
    f = next(f for f in Parser(SOURCE).parse().functions if f.name == "scale")
    cases = boundaries.generate(f, {2: "the condition"}, {"largest_extent": 40}, budget=12, device=True)
    return cases_as_tests(SOURCE, "scale", "scale_twice", [c for c in cases if c.args["n"] % 2 == 0] + EDGES)


def test_the_device_tests_are_checked_code_that_calls_both_sides():
    tests, names, coverage = generated()
    assert names and all(name.startswith("device_scale_twice_") for name in names)
    assert coverage["written"] == coverage["generated"] == len(names) and coverage["left_out"] == {}
    assert "transfer(r_x, h_x);" in tests and "scale_twice(" in tests and "assert(agree_scale_twice(" in tests
    assert "from_bits[f32](0x7fc00000)" in tests and "from_bits[f32](0x80000000)" in tests  # NaN and -0.0
    assert "scale(4, r_out[1..5], r_x[1..5]," in tests  # a view one element off an aligned allocation
    receipt = compile_source(SOURCE + "\n" + tests)[1]["functions"]
    assert {f"test${name}" for name in names} <= set(receipt)


def test_the_device_tests_compile_for_sm_120_without_running(tmp_path):
    tests, names, _ = generated()
    cpp, _ = compile_source(SOURCE + "\n" + tests, roots=tuple(f"test${n}" for n in names))
    assert device_build(tmp_path, cpp, entry=None).stat().st_size > 0


SEEN = """fn mix(h:u64, v:u64) -> u64 = mul_wrap(h ^ v, 1099511628211);

fn seen(n:usize, xs:ro<f64>[n], ys:ro<f32>[n], bs:ro<bool>[n], ks:ro<i32>[n], us:ro<u8>[n], a:f64, b:f32,
        flag:bool, k:i64, m:i8) -> u64 {
  let mut h:u64 = 1469598103934665603;
  for i in 0..n {
    h = mix(h, to_bits(xs[i]));
    h = mix(h, u64(to_bits(ys[i])));
    if bs[i] { h = mix(h, 1); } else { h = mix(h, 2); }
    h = mix(h, u64(i64(ks[i]) + 2147483648));
    h = mix(h, u64(us[i]));
  }
  h = mix(h, to_bits(a));
  h = mix(h, u64(to_bits(b)));
  if flag { h = mix(h, 1); } else { h = mix(h, 2); }
  if k < 0 { h = mix(mix(h, u64(0 - (k + 1))), 3); } else { h = mix(h, u64(k)); }
  return mix(h, u64(i64(m) + 128));
}
"""
PAYLOAD = struct.unpack("<d", struct.pack("<Q", 0xFFF0000000000001))[0]  # a negative NaN with a payload
SPECIAL = [math.nan, PAYLOAD, math.inf, -math.inf, -0.0, 5e-324, 1.7976931348623157e308]
INPUTS = {
    "n": 1000,
    "xs": [*SPECIAL, *(i * 0.5 - 7.0 for i in range(1000 - len(SPECIAL)))],
    "ys": [math.nan, math.inf, -math.inf, -0.0, 1.401298464324817e-45, *(i * 0.25 for i in range(995))],
    "bs": [i % 3 == 0 for i in range(1000)],
    "ks": [-(2**31), 2**31 - 1, -1, 0, *(i * 7919 - 3_000_000 for i in range(996))],
    "us": [i % 256 for i in range(1000)],
    "a": -0.0, "b": math.nan, "flag": False, "k": -(2**63), "m": -128,
}  # fmt: skip


def checksum(args: dict) -> int:
    """`seen` of `args`, computed here from the values as the generator was given them."""

    def mix(h: int, v: int) -> int:
        return ((h ^ v) * 1099511628211) % 2**64

    def f64(x: float) -> int:
        return struct.unpack("<Q", struct.pack("<d", x))[0]

    def f32(x: float) -> int:
        return struct.unpack("<I", struct.pack("<f", x))[0]

    h = 1469598103934665603
    for x, y, b, k, u in zip(args["xs"], args["ys"], args["bs"], args["ks"], args["us"], strict=True):
        h = mix(mix(mix(mix(mix(h, f64(x)), f32(y)), 1 if b else 2), k + 2**31), u)
    h = mix(mix(mix(h, f64(args["a"])), f32(args["b"])), 1 if args["flag"] else 2)
    h = mix(mix(h, -(args["k"] + 1)), 3) if args["k"] < 0 else mix(h, args["k"])
    return mix(h, args["m"] + 128)


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_every_input_reaches_the_generated_test_exactly(tmp_path, cxx):
    """NaN with its payload, both infinities, -0.0, the least subnormal, false, the least i64 and i8 and 1000-element
    views of five element types: the implementation returns the checksum computed here from the inputs as given, so
    the test passes only if every bit reached the reference."""
    head = SEEN.split("-> u64 {")[0].split("fn seen")[1]
    fixed = SEEN + f"\nfn seen_fixed{head}-> u64 implements seen = {checksum(INPUTS)};\n"
    tests, _, coverage = cases_as_tests(fixed, "seen", "seen_fixed", [Case(INPUTS), Case(INPUTS, {"xs": 1})])
    assert coverage == {"generated": 2, "written": 2, "left_out": {}}
    path = tmp_path / "seen.cairn"
    path.write_text(fixed + "\n" + tests, encoding="utf-8")
    done = run_tests(load_project(path), cxx=cxx, output=tmp_path / "build")
    assert done["status"] == "passed-test-blocks" and done["passed"] == 2, done
    wrong = fixed.replace(str(checksum(INPUTS)), str(checksum({**INPUTS, "flag": True})))
    path.write_text(wrong + "\n" + tests, encoding="utf-8")  # one input told apart: the same test fails
    assert run_tests(load_project(path), cxx=cxx, output=tmp_path / "build")["failed"] == 2


def test_a_value_the_generator_cannot_write_is_counted_not_dropped():
    source = "fn f(k:i8) -> i8 = k;\nfn g(k:i8) -> i8 implements f = k;\n"
    _, names, coverage = cases_as_tests(source, "f", "g", [Case({"k": 1}), Case({"k": 300}), Case({"k": 1.5})])
    assert names == ["device_g_0"] and coverage["written"] == 1 and sum(coverage["left_out"].values()) == 2
    assert all("cannot write" in why for why in coverage["left_out"])


def test_a_tool_that_ran_nothing_is_unknown_never_clean():
    assert verdict([])["status"] == "unknown"
    assert verdict([{"test": "t", "exit_code": 0}])["status"] == "clean"
    assert verdict([{"test": "t", "exit_code": 0}, {"test": "u", "exit_code": 97}])["status"] == "reported"


def test_outside_make_gpu_nothing_runs(monkeypatch):
    monkeypatch.delenv("CAIRN_GPU_TESTS", raising=False)
    result = sanitized(SOURCE, [])
    assert result["status"] == "not-run" and set(result["tools"]) == set(TOOLS)


def test_each_sanitizer_tool_is_a_result_of_its_own_on_the_device():
    if reason := device_reason():
        pytest.skip(reason)
    tests, names, _ = generated()
    result = sanitized(SOURCE + "\n" + tests, names)
    assert result["status"] == "run" and set(result["tools"]) == set(TOOLS)
    assert all(result["tools"][tool]["status"] == "clean" for tool in TOOLS), result

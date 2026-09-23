"""The device side of validation. The default suite generates the device tests of an implementation whose views
live on the device, checks them and compiles them for sm_120 without running anything; only `make gpu` runs them,
under each Compute Sanitizer tool as a result of its own.
"""

import pytest

from cairn.compiler.cairnc import Parser, compile_source
from cairn.verify import boundaries
from cairn.verify.device_validation import TOOLS, cases_as_tests, sanitized
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


def generated():
    f = next(f for f in Parser(SOURCE).parse().functions if f.name == "scale")
    cases = boundaries.generate(f, {2: "the condition"}, {"largest_extent": 40}, budget=12, device=True)
    return cases_as_tests(SOURCE, "scale", "scale_twice", [c for c in cases if c.args["n"] % 2 == 0])


def test_the_device_tests_are_checked_code_that_calls_both_sides():
    tests, names = generated()
    assert names and all(name.startswith("device_scale_twice_") for name in names)
    assert "transfer(r_x, h_x);" in tests and "scale_twice(" in tests and "assert(" in tests
    receipt = compile_source(SOURCE + "\n" + tests)[1]["functions"]
    assert {f"test${name}" for name in names} <= set(receipt)


def test_the_device_tests_compile_for_sm_120_without_running(tmp_path):
    tests, names = generated()
    cpp, _ = compile_source(SOURCE + "\n" + tests, roots=tuple(f"test${n}" for n in names))
    assert device_build(tmp_path, cpp, entry=None).stat().st_size > 0


def test_outside_make_gpu_nothing_runs(monkeypatch):
    monkeypatch.delenv("CAIRN_GPU_TESTS", raising=False)
    result = sanitized(SOURCE, [])
    assert result["status"] == "not-run" and set(result["tools"]) == set(TOOLS)


def test_each_sanitizer_tool_is_a_result_of_its_own_on_the_device():
    if reason := device_reason():
        pytest.skip(reason)
    tests, names = generated()
    result = sanitized(SOURCE + "\n" + tests, names)
    assert result["status"] == "run" and set(result["tools"]) == set(TOOLS)
    assert all(result["tools"][tool]["status"] == "clean" for tool in TOOLS), result

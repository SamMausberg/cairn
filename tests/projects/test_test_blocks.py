"""`test name { }` blocks and `assert`: what is refused, that no ordinary build holds a test, and the runner that gives
each test a process of its own, natively under both compilers and, on the runner's own executable, under ASan and
UBSan. A test passes only when its process exits with status 0, whatever it printed first."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.agent.explain import explain
from cairn.agent.projection import canonical_source
from cairn.cli import main
from cairn.compiler.cairnc import Parser, compile_source
from cairn.editor.document import Document, symbols
from cairn.projects.build import build, dispatcher
from cairn.projects.project import load_project
from cairn.verify.runner import run_tests
from emitted import build as build_cpp
from emitted import code_of, device_build, refused, sanitized

LIBRARY = """import std.vec;

fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

fn total(n:usize) -> u64 {
  let mut v = vec.new[u64]();
  for i in 0..n { v.push(u64(i)); }
  let mut t:u64 = 0;
  for i in 0..v.len { t += v.data[i]; }
  return t;
}
"""

TESTS = """
test average {
  assert(average(10, 20) == 15);
  assert(average(1, 2) == 1, "rounds down");
}

test owners {
  let sum = total(100);
  assert(sum == 4950, "the sum of 0..100");
}

test wrong { assert(average(4, 4) == 5, "four is not five"); }

test overflow {
  let big:u64 = 18446744073709551615;
  let more = big + average(2, 2);
}
"""


def project(tmp_path: Path, tests: str = TESTS, library: str = LIBRARY, main_body: str = "return 0;") -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/lib.cairn").write_text(library)
    (tmp_path / "src/main.cairn").write_text(f"fn main() -> i32 {{ {main_body} }}\n{tests}")
    (tmp_path / "cairn.toml").write_text(
        '[project]\nname = "blocks"\nsources = ["src/lib.cairn", "src/main.cairn"]\n'
        '[build]\nkind = "exe"\narch = "baseline"\n'
    )
    return tmp_path


def available(cxx: str) -> None:
    if not shutil.which(cxx):
        pytest.skip(f"{cxx} unavailable")


@pytest.mark.parametrize(
    "source",
    [
        "test sums(x:u64) { }",  # a test is run by name alone
        "test sums -> u64 { return 1; }",  # and nothing reads what it would return
        "test sums { }\ntest sums { }",
        "module m;\ntest sums { }\ntest sums { }",
    ],
)
def test_a_test_takes_nothing_returns_nothing_and_has_one_name_per_module(source):
    refused("E-TEST", source)


@pytest.mark.parametrize(
    "call,code",
    [
        ("assert()", "E-ARITY"),
        ('assert(true, "a", "b")', "E-ARITY"),
        ("assert(true, 3)", "E-ARITY"),  # the text is a literal, so the message is fixed when the program is built
        ("assert(3)", "E-TYPE-MISMATCH"),
    ],
)
def test_assert_takes_a_condition_and_one_literal_at_most(call, code):
    refused(code, f"fn f() {{ {call}; }}")


def test_a_test_is_not_a_function_and_test_stays_a_name():
    both = "fn sums() -> u64 = 6;\nfn test_sums() -> u64 = 1;\ntest sums { assert(sums() == 6); }\n"
    compile_source(both + "fn main() -> i32 { let test = sums(); return i32(test + test_sums()) - 7; }")
    refused("E-CALLEE", "test sums { }\nfn main() -> i32 { sums(); return 0; }")  # nothing can call a test


def test_the_outline_lists_tests_and_the_projection_keeps_them():
    source = LIBRARY + TESTS
    doc = Document(source)
    shown = [(s["name"], s["detail"]) for s in symbols(doc)]
    assert ("average", "test") in shown and ("wrong", "test") in shown and ("average", "fn") in shown
    assert doc.diagnostics == []
    view = canonical_source(source)
    assert "test wrong {" in view and canonical_source(view) == view
    assert compile_source(view)[0] == compile_source(source)[0]  # a plain compile names no line in its asserts


def test_explain_prices_what_a_build_holds_and_no_test():
    shown = explain(LIBRARY + TESTS, "p.cairn", cxx="g++")["functions"]
    assert "average" in shown and "total" in shown and not any(name.startswith("test$") for name in shown)


def test_no_ordinary_build_holds_a_test(tmp_path):
    root = project(tmp_path)
    cpp, receipt = compile_source(load_project(root).source)
    assert "ctest_" not in cpp and "cairn_assert.hpp" not in cpp
    assert receipt["functions"]["test$average"]["test"] is True
    assert receipt["functions"]["test$average"]["syntactic_check_sites"]["assert"] == 2
    assert "trap" in receipt["functions"]["test$average"]["effects"]
    for kind in ("exe", "library"):
        record = build(load_project(root), kind=kind)
        assert record["status"] == "native-built", record.get("stderr")
        assert "ctest_" not in (Path(record["directory"]) / "program.cpp").read_text()


def test_a_freestanding_image_keeps_no_test_and_audits_none(tmp_path):
    """A test may do what an image cannot: its effects are no image's, and no image holds it."""
    root = project(tmp_path, tests='import std.io;\ntest talks { io.print("hi"); }\n')
    manifest = root / "cairn.toml"
    manifest.write_text(manifest.read_text().replace('arch = "baseline"', 'arch = "baseline"\ntarget = "aarch64-virt"'))
    assert code_of(lambda: run_tests(load_project(root))) == "E-TEST"
    cpp, receipt = compile_source(load_project(root).source)
    assert "io" in receipt["functions"]["test$talks"]["effects"] and "ctest_" not in cpp


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_each_test_runs_alone_and_fails_alone(tmp_path, cxx):
    available(cxx)
    record = run_tests(load_project(project(tmp_path)), cxx=cxx, jobs=2)
    by = {t["name"]: t for t in record["tests"]}
    assert [t["name"] for t in record["tests"]] == ["average", "owners", "wrong", "overflow"]  # source order
    assert by["average"]["status"] == by["owners"]["status"] == "passed"
    assert by["wrong"]["reason"] == "assertion failed at src/main.cairn:13: four is not five"
    assert by["overflow"]["reason"] == "stopped by SIGABRT: a guard failed"
    assert (by["wrong"]["file"], by["wrong"]["line"]) == ("src/main.cairn", 13)
    assert record["status"] == "test-blocks-failed" and (record["passed"], record["failed"]) == (2, 2)


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_test_that_prints_a_pass_and_then_exits_badly_has_failed(tmp_path, cxx):
    available(cxx)
    tests = "import std.io;\nextern fn exit(code:i32) effects(io);\n"
    tests += 'test liar { io.print("pass\\n"); unsafe { exit(3); } }\n'
    record = run_tests(load_project(project(tmp_path, tests=tests)), cxx=cxx)
    (liar,) = record["tests"]
    assert liar["status"] == "failed" and liar["reason"] == "exited with status 3" and liar["stdout"] == "pass\n"


def test_a_test_that_runs_too_long_is_stopped_and_fails(tmp_path):
    available("clang++")
    tests = "import std.time;\ntest slow { time.sleep(30000000000); }\ntest quick { assert(true); }\n"
    record = run_tests(load_project(project(tmp_path, tests=tests)), timeout=1)
    by = {t["name"]: t for t in record["tests"]}
    assert by["slow"]["status"] == "failed" and by["slow"]["reason"] == "timed out after 1 s"
    assert by["quick"]["status"] == "passed"


def test_parallel_tests_keep_their_own_results(tmp_path):
    """Twelve processes, four at a time: each failure is reported with its own message, in source order."""
    available("clang++")
    tests = "".join(
        f'test t{k} {{ assert(average({k}, {k}) == {k + k % 2}, "case {k}"); }}\n' for k in range(12)
    )  # odd k fail
    record = run_tests(load_project(project(tmp_path, tests=tests)), jobs=4)
    for k, t in enumerate(record["tests"]):
        assert t["name"] == f"t{k}"
        assert t["status"] == ("failed" if k % 2 else "passed"), t
        assert k % 2 == 0 or t["reason"].endswith(f": case {k}")


def test_a_filter_runs_only_the_tests_it_names(tmp_path, capsys):
    available("clang++")
    root = project(tmp_path)
    assert main(["test", str(root), "--filter", "ave", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [t["name"] for t in out["blocks"]["tests"]] == ["average"] and out["status"] == "passed-finite-tests"
    assert main(["test", str(root), "--format", "human"]) == 1
    shown = capsys.readouterr().out.splitlines()
    assert shown[0] == "tests-not-passed: 2 of 4 tests failed"
    assert shown[1:] == [
        "  test wrong: assertion failed at src/main.cairn:13: four is not five",
        "  test overflow: stopped by SIGABRT: a guard failed",
    ]
    assert main(["test", str(root), "--filter", "nothing"]) == 2  # no tests is not a pass


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_the_runner_executable_under_the_sanitizers(tmp_path, cxx):
    """The executable `cairn test` builds, compiled with ASan and UBSan under clang++: a passing test that owns heap
    storage exits 0 without a report, a failed assert aborts with its message, and a bad index exits 2."""
    source = LIBRARY + TESTS
    tests = tuple(f.name for f in Parser(source).parse().functions if f.test)
    cpp, _ = compile_source(source, roots=tests)
    exe = build_cpp(tmp_path, cpp + dispatcher(tests), *sanitized(cxx), cxx=cxx, entry=None)
    runs = [subprocess.run([exe, str(i)], capture_output=True, text=True, timeout=60) for i in range(len(tests))]
    assert [r.returncode for r in runs] == [0, 0, -6, -6], [r.stderr for r in runs]
    assert runs[2].stderr.strip() == "assertion failed in test wrong: four is not five"
    assert all("Sanitizer" not in r.stderr and "runtime error" not in r.stderr for r in runs)
    for bad in ([exe], [exe, "4"], [exe, "x"], [exe, "0", "1"]):
        assert subprocess.run(bad, capture_output=True, timeout=60).returncode == 2


def test_an_assert_in_a_device_lane_compiles_for_the_device(tmp_path):
    """Compiled for sm_120 by nvcc and never run: device code runs only under make gpu."""
    cpp, receipt = compile_source(
        'fn mark(n:usize, xs:rw<u32>[n]@device) { parallel i in n { assert(xs[i] < 7, "small"); xs[i] = 1; } }'
    )
    assert "cr::check(" in cpp and "par:device" in receipt["functions"]["mark"]["effects"]
    device_build(tmp_path, cpp)

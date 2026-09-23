"""assert_eq(a, b) and assert_eq(a, b, "why"): assert(a == b) that prints both values when they differ. Its operands
are checked as `a == b` is, only a scalar prints, and a failure prints the site and both values, then traps. Runs
natively under both compilers, sanitizer-clean when it holds; a device lane's compiles for sm_120 and never runs.
"""

import shutil
import subprocess

import pytest

from cairn.compiler.cairnc import RUNTIME_FILES, compile_source
from cairn.projects.toolchain import command
from emitted import WARNINGS, refused, run, sanitized

HOLDS = """
fn main() -> i32 {
  let a:u64 = 3;
  assert_eq(a, 3);
  assert_eq(true, a > 2, "a is big");
  assert_eq(0.5, 1.0 / 2.0);
  let b:i32 = -7;
  assert_eq(b, -3 - 4);
  let c:u8 = 200;
  assert_eq(200, c);
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_an_assert_eq_that_holds_costs_a_comparison(tmp_path, cxx):
    cpp, receipt = compile_source(HOLDS)
    assert cpp.count("cr::check_eq(") == 5 and receipt["functions"]["main"]["syntactic_check_sites"]["assert"] == 5
    assert run(tmp_path, cpp, *sanitized(cxx), *WARNINGS, cxx=cxx).returncode == 0


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize(
    ("setup", "call", "said"),
    [
        ("let a:u64 = 4;", 'assert_eq(a, 5, "four is not five")', "four is not five: left 4, right 5"),
        ("let a:i16 = -3;", "assert_eq(a, 3)", ": left -3, right 3"),
        ("let a = true;", "assert_eq(a, false)", ": left true, right false"),
        ("let a:f64 = 0.1 + 0.2;", "assert_eq(a, 0.3)", ": left 0.30000000000000004, right 0.29999999999999999"),
        ("let a:f32 = 1.5;", "assert_eq(a, 2.25)", ": left 1.5, right 2.25"),
    ],
)
def test_a_failing_assert_eq_prints_both_values_and_traps(tmp_path, cxx, setup, call, said):
    cpp = compile_source(f"fn main() -> i32 {{\n  {setup}\n  {call};\n  return 0;\n}}\n")[0]
    done = run(tmp_path, cpp, *sanitized(cxx)[:4], cxx=cxx)
    assert done.returncode == -6 and done.stderr.startswith("assertion failed in main") and said in done.stderr


@pytest.mark.parametrize(
    ("code", "source"),
    [
        ("E-ASSERT-EQ", "enum Op { Read; Write; }\nfn main() -> i32 { assert_eq(Op.Read, Op.Write); return 0; }"),
        ("E-OPERATOR", "struct P { x:u64; }\nfn main() -> i32 { assert_eq(P(1), P(1)); return 0; }"),
        ("E-TYPE-MISMATCH", "fn main() -> i32 { let a:u32 = 1; let b:u64 = 1; assert_eq(a, b); return 0; }"),
        ("E-ARITY", "fn main() -> i32 { assert_eq(1); return 0; }"),
        ("E-ARITY", 'fn main() -> i32 { let m = "why"; assert_eq(1, 1, m); return 0; }'),
        ("E-EFFECT-ORDER", "extern fn putchar(c:i32) -> i32 effects(io);\nfn say(c:i32) -> i32 { unsafe { return "
         "putchar(c); } }\nfn main() -> i32 { assert_eq(say(65), say(66)); return 0; }"),
    ],
)  # fmt: skip
def test_what_assert_eq_refuses(code, source):
    refused(code, source)


def test_a_program_s_own_assert_eq_wins():
    assert "cr::check_eq(" not in compile_source("fn assert_eq(a:u64, b:u64) {}\nfn main() -> i32 { assert_eq(1, 2); "
                                                 "return 0; }")[0]  # fmt: skip


def test_an_assert_eq_in_a_device_lane_compiles_for_the_device(tmp_path):
    """Compiled for sm_120 by nvcc and never run: device code runs only under make gpu."""
    if not shutil.which("nvcc") or not shutil.which("g++"):
        pytest.skip("needs nvcc and g++")
    cpp = compile_source('fn mark(n:usize, xs:rw<u32>[n]@device) { parallel i in n { assert_eq(xs[i], 0, "zeroed"); '
                         "xs[i] = 1; } }")[0]  # fmt: skip
    assert "cr::check_eq(" in cpp
    for name, text in {"p.cu": cpp, **RUNTIME_FILES}.items():
        (tmp_path / name).write_text(text)
    line = command("g++", str(tmp_path / "p.cu"), str(tmp_path / "p.o"), kind="library", cuda=True)
    line = [("-arch=sm_120" if part == "-arch=native" else part) for part in line if part != "-shared"] + ["-c"]
    done = subprocess.run(line, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-4000:]


def test_the_value_model_answers_unknown_for_assert_eq():
    """A function that holds an assert_eq is outside the value fragment, as one that holds an assert is: unknown,
    never an equivalence that ignores the trap."""
    from cairn.verify.scalar_semantics import equivalent

    with_check = "fn f(x:u64) -> u64 { assert_eq(x, 3); return x; }"
    assert equivalent(with_check, "fn f(x:u64) -> u64 { return x; }", "f")["status"] == "unknown"


def test_explain_counts_an_assert_eq_as_an_assert_guard():
    from cairn.agent.explain import GUARDS

    assert "cr::check_eq(" in GUARDS["assert"]

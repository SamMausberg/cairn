"""Alternative implementations: `fn g(...) implements f when cond { }` beside a reference `f`, run by `plan f use g;`.

An implementation keeps its reference's signature, ceiling and roundings, its condition cannot trap, and only a test
calls it by name; each refusal names its code. A selected implementation runs where its condition holds and the
reference runs everywhere else, natively under both compilers with the address and leak sanitizers, against results
Python computes on its own. The canonical projection keeps the native code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.compiler.implementations import targeted
from cairn.editor.formatting import format_source
from cairn.projects.build import build
from cairn.projects.project import load_project
from cairn.projects.target import parse
from emitted import refused, watched

TOTAL = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s += xs[i]; }
  return s;
}
"""
BY4 = """fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 {
  let mut a:u64 = 0;
  let mut b:u64 = 0;
  for k in 0..n / 4 { a += xs[4 * k] + xs[4 * k + 1]; b += xs[4 * k + 2] + xs[4 * k + 3]; }
  return a + b;
}
"""
PAIRS = """fn total_pairs(n:usize, xs:ro<u64>[n]) -> u64 implements total {
  let mut s:u64 = 0;
  for k in 0..n / 2 { s += xs[2 * k] + xs[2 * k + 1]; }
  if n % 2 == 1 { s += xs[n - 1]; }
  return s;
}
"""
# Which body ran: the implementation answers 1 and the reference 0, so the exit status shows the dispatch.
WHICH = """fn which(n:usize) -> u64 { return 0; }
fn which_even(n:usize) -> u64 implements which when n % 2 == 0 && n >= 4 { return 1; }
plan which use which_even;
fn release(xs:Buf[u64]) -> usize { return len(xs); }
fn release_fast(xs:Buf[u64]) -> usize implements release { return len(xs); }
plan release use release_fast;
fn clear(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 0; } }
fn clear_fast(n:usize, out:rw<u64>[n]) implements clear when n > 2 { for i in 0..n { out[i] = 7 - 7; } }
plan clear use clear_fast;

fn main() -> i32 {
  if which(8) != 1 || which(6) != 1 || which(2) != 0 || which(7) != 0 { return 1; }
  if release(Buf[u64](5)) != 5 { return 2; }
  let mut out = Buf[u64](3);
  out[1] = 9;
  clear(out);
  if out[1] != 0 { return 3; }
  return 0;
}
"""


def summing(selected: str) -> str:
    """A program that sums 0..n-1 for n of 0 to 13 through `total`, whichever implementation runs it."""
    return (TOTAL + BY4 + PAIRS + selected + """
fn main() -> i32 {
  for n in 0..14 {
    let mut xs = Buf[u64](n);
    for i in 0..n { xs[i] = u64(i) * 1000003; }
    let want = u64(n) * u64(n) / 2 - u64(n) / 2;
    if total(xs) != want * 1000003 { return i32(n) + 1; }
  }
  return 0;
}
""")  # fmt: skip


def test_the_receipt_lists_each_implementation_and_the_one_a_plan_runs():
    _, receipt = compile_source(summing("plan total use total_by4;\n"))
    total = receipt["functions"]["total"]
    assert total["runs"] == "total_by4" and set(total["implementations"]) == {"total_by4", "total_pairs"}
    by4 = total["implementations"]["total_by4"]
    assert by4["when"] == "n % 4 == 0" and by4["applies"] == "tested at entry" and len(by4["identity"]) == 64
    assert total["implementations"]["total_pairs"]["applies"] == "always"
    assert by4["requires"] == {"target": "host", "host_lanes": False, "tasks": False, "heap_allocation_sites": 0,
                               "stack_bytes": 0}  # fmt: skip
    assert receipt["functions"]["total_by4"]["implements"] == "total"
    assert total["effects"] == receipt["functions"]["total_by4"]["effects"] == ["ffi_precondition", "read:xs", "trap"]


def test_the_default_is_the_reference_and_a_selection_tests_its_condition_at_entry():
    alone, _ = compile_source(summing(""))
    chosen, _ = compile_source(summing("plan total use total_by4;\n"))
    always, _ = compile_source(summing("plan total use total_pairs;\n"))
    head = "std::uint64_t ci_total(std::size_t v_n, const std::uint64_t* v_xs) noexcept {\n"
    assert alone.split(head)[1].split("\n")[0].strip().startswith("std::uint64_t v_s")
    assert "cr::remainder<std::size_t>(v_n" in chosen.split(head)[1].split("\n")[0]
    assert "return ci_total_by4(v_n, v_xs);" in chosen.split(head)[1].split("\n")[0]
    assert always.split(head)[1].split("\n")[0].strip() == "if (true) return ci_total_pairs(v_n, v_xs);"


def test_the_identity_is_the_two_declarations_as_written():
    _, a = compile_source(summing(""))
    _, b = compile_source(summing("").replace("let mut s:u64 = 0;\n  for i", "let mut s:u64 = 0;  // sum\n  for i"))
    _, c = compile_source(summing("").replace("s += xs[i]", "s = s + xs[i]"))
    ids = [r["functions"]["total"]["implementations"]["total_by4"]["identity"] for r in (a, b, c)]
    assert ids[0] == ids[1] != ids[2]  # a comment is not a token; a changed reference is a new identity


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("plan", ["", "plan total use total_by4;\n", "plan total use total_pairs;\n"])
def test_every_selection_computes_what_python_computes(tmp_path, cxx, plan):
    done = watched(tmp_path, compile_source(summing(plan))[0], cxx, "address")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_implementation_runs_only_where_its_condition_holds(tmp_path, cxx):
    done = watched(tmp_path, compile_source(WHICH)[0], cxx, "address")
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_test_block_calls_an_implementation_to_compare_it_with_its_reference():
    source = (
        TOTAL + BY4 + "test by4 { buffer xs:u64[4] = zeroed; xs[3] = 5; assert_eq(total_by4(4, xs), total(4, xs)); }\n"
    )
    compile_source(source)


def test_a_declared_ceiling_admits_a_parallel_implementation_and_the_row_joins_it():
    source = """fn scale(n:usize, xs:rw<u64>[n]) effects(pure, write:xs, par:host) { for i in 0..n { xs[i] = 2 * xs[i]; } }
fn scale_lanes(n:usize, xs:rw<u64>[n]) implements scale { parallel i in n { xs[i] = 2 * xs[i]; } }
fn caller(n:usize, xs:rw<u64>[n]) { scale(xs); }
"""
    functions = compile_source(source)[1]["functions"]
    assert "par:host" in functions["scale"]["effects"] and "par:host" in functions["caller"]["effects"]
    assert functions["scale"]["implementations"]["scale_lanes"]["requires"]["host_lanes"] is True


def test_the_projection_keeps_the_native_code_and_the_formatter_keeps_the_tokens():
    source = summing("plan total use total_by4;\n")
    canonical = canonical_source(source)
    assert "implements total when ((n % 4) == 0)" in canonical and "plan total use total_by4;" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical
    long = TOTAL + BY4.replace("when n % 4 == 0", "when n % 4 == 0 && n >= 4 && n <= 4000000 && n != 12")
    shaped = format_source(long)
    assert "u64\n  implements total when" in shaped and format_source(shaped) == shaped


REFUSED = [
    ("E-IMPLEMENTS", TOTAL + "fn f(n:usize, xs:ro<u64>[n]) -> u64 implements nothing { return 0; }"),
    ("E-IMPLEMENTS", TOTAL + BY4 + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total_by4 { return 0; }"),
    (
        "E-IMPLEMENTS",
        "module a;\npub fn f(x:u64) -> u64 = x;\nmodule b;\nimport a;\nfn g(x:u64) -> u64 implements a.f = x;",
    ),
    ("E-IMPLEMENTS", "fn f[T:integer](x:T) -> T = x;\nfn g(x:u64) -> u64 implements f = x;"),
    ("E-IMPLEMENTS", "fn f(x:u64) -> u64 = x;\nfn g[T:integer](x:T) -> T implements f = x;"),
    ("E-IMPLEMENTS", "fn f(x:u64) -> u64 = x;\nfn g(x:u64) -> u64 implements f needs(fast) = x;"),
    ("E-IMPL-SIGNATURE", "fn f(x:u64) -> u64 = x;\nfn g(x:u32) -> u64 implements f = u64(x);"),
    ("E-IMPL-SIGNATURE", "fn f(x:u64) -> u64 = x;\nfn g(y:u64) -> u64 implements f = y;"),
    ("E-IMPL-SIGNATURE", "fn f(x:u64) -> u64 = x;\nfn g(x:u64) -> u32 implements f = u32(x);"),
    ("E-IMPL-SIGNATURE", TOTAL + "fn g(n:usize, xs:ro<u64>[n]@unified) -> u64 implements total { return 0; }"),
    ("E-IMPL-SIGNATURE", TOTAL + "fn g(n:usize, xs:ro<u64>[4]) -> u64 implements total { return 0; }"),
    ("E-IMPL-WHEN", TOTAL + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total when n + 1 > 4 { return 0; }"),
    ("E-IMPL-WHEN", TOTAL + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total when xs[0] > 4 { return 0; }"),
    (
        "E-IMPL-WHEN",
        "fn f(n:usize, k:usize) -> u64 = 0;\nfn g(n:usize, k:usize) -> u64 implements f when n % k == 0 = 0;",
    ),
    ("E-IMPL-WHEN", "fn f(n:usize) -> u64 = 0;\nfn g(n:usize) -> u64 implements f when u32(n) > 4 = 0;"),
    ("E-IMPL-WHEN", "fn f(n:usize) -> u64 = 0;\nfn g(n:usize) -> u64 implements f when 1 == 2 = 0;"),
    ("E-TYPE-MISMATCH", "fn f(n:usize) -> u64 = 0;\nfn g(n:usize) -> u64 implements f when n = 0;"),
    (
        "E-IMPL-EFFECT",
        TOTAL + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total { let b = Buf[u64](n); return 0; }",
    ),
    (
        "E-IMPL-EFFECT",
        TOTAL + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total { let mut s:u64 = 0; "
        "let mut i:usize = 0; while i < n { s += xs[i]; i += 1; } return s; }",
    ),
    (
        "E-IMPL-EFFECT",
        "fn f(n:usize, xs:rw<u64>[n]) { for i in 0..n { xs[i] = 0; } }\n"
        "fn g(n:usize, xs:rw<u64>[n]) implements f { parallel i in n { xs[i] = 0; } }",
    ),
    ("E-IMPL-EFFECT", "fn f(n:usize) -> u64 pure = 0;\nfn g(n:usize) -> u64 implements f { println(n); return 0; }"),
    ("E-IMPL-NUMERICS", "fn f(x:f32) -> f32 = x * 0.5;\nfn g(x:f32) -> f32 implements f = f32(f16(x)) * 0.5;"),
    ("E-IMPL-CALL", TOTAL + BY4 + "fn main() -> i32 { buffer xs:u64[4] = zeroed; return i32(total_by4(4, xs)); }"),
    ("E-IMPL-CALL", TOTAL + "fn g(n:usize, xs:ro<u64>[n]) -> u64 implements total { return total(n, xs); }"),
    (
        "E-IMPL-CALL",
        "fn f(x:u64) -> u64 = x;\nfn g(x:u64) -> u64 implements f = x;\n"
        "fn apply(h:fn(u64) -> u64, x:u64) -> u64 = h(x);\nfn main() -> i32 { return i32(apply(g, 1)); }",
    ),
    ("E-IMPL-USE", TOTAL + BY4 + "fn other(n:usize, xs:ro<u64>[n]) -> u64 = 0;\nplan total use other;"),
    ("E-IMPL-USE", TOTAL + BY4 + "plan nothing use total_by4;"),
    ("E-IMPL-USE", TOTAL + BY4 + PAIRS + "plan total use total_by4;\nplan total use total_pairs;"),
    ("E-IMPLEMENTS", TOTAL + BY4.replace("when n % 4 == 0", "needs(sm_120)")),  # a target, not a feature
    ("E-IMPLEMENTS", TOTAL + BY4.replace("when n % 4 == 0", "needs(cp_async)")),  # and it runs no device code
]


@pytest.mark.parametrize(("code", "source"), REFUSED)
def test_each_rule_refuses_with_its_code(code, source):
    refused(code, source)


def test_an_implementation_lives_in_its_reference_s_module_and_a_plan_there_selects_it():
    source = (
        "import m;\nfn main() -> i32 { buffer xs:u64[8] = zeroed; for i in 0..8 { xs[i] = u64(i); } "
        "if m.total(8, xs) != 28 { return 1; } return 0; }\n\nmodule m;\npub "
        + TOTAL
        + BY4
        + "plan total use total_by4;\n"
    )
    cpp, receipt = compile_source(source)
    assert receipt["functions"]["m.total"]["runs"] == "m.total_by4" and "return ci_m_total_by4(v_n, v_xs);" in cpp
    assert receipt["functions"]["m.total_by4"]["implements"] == "m.total"


SCALE = """fn scale(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) { parallel i in n { out[i] = a * x[i]; } }
fn scale_tc(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) implements scale needs(tcgen05) {
  parallel i in n { out[i] = a * x[i]; }
}
"""


def test_a_selected_implementation_s_needs_reach_the_build_s_device_target(tmp_path):
    unselected = compile_source(SCALE)[1]
    assert "tcgen05" not in unselected["device_features"]
    assert unselected["functions"]["scale"]["implementations"]["scale_tc"]["needs"] == ["tcgen05"]
    _, receipt = compile_source(SCALE + "plan scale use scale_tc;\n")
    assert "tcgen05" in receipt["device_features"]
    targeted(receipt["functions"], parse("sm_100a"))  # a target that provides it
    with pytest.raises(Diagnostic) as refused_here:
        targeted(receipt["functions"], parse("sm_120"))
    assert refused_here.value.data["code"] == "E-IMPL-TARGET" and refused_here.value.data["features"] == ["tcgen05"]
    (tmp_path / "scale.cairn").write_text(SCALE + "plan scale use scale_tc;\n")
    with pytest.raises(Diagnostic) as at_build:  # refused before anything is compiled
        build(load_project(tmp_path / "scale.cairn"), output=tmp_path / "build", kind="library", device_target="sm_120")
    assert at_build.value.data["code"] == "E-IMPL-TARGET"


def test_a_call_whose_arguments_decide_the_condition_reaches_the_implementation_directly():
    source = (
        TOTAL
        + BY4
        + """plan total use total_by4;
const EIGHT:usize = 8;
fn sums(n:usize, xs:ro<u64>[n], eight:ro<u64>[8], five:ro<u64>[5]) -> u64 {
  return total(8, eight) + total(EIGHT, eight) + total(5, five) + total(n, xs);
}
"""
    )
    cpp, _ = compile_source(source)
    body = cpp.split("ci_sums(std::size_t v_n")[2].split("\n}\n")[0]
    assert body.count("ci_total_by4(") == 2 and body.count("ci_total(") == 2  # 8 is decided; 5 and n are tested

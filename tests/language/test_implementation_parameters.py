"""Parameterized implementations: `fn g[K:nat](...) implements f when cond tune K in [2, 4, 8] { }` and
`plan f use g[4];`.

Every instance the `tune` clause lists is made and held to every rule an implementation keeps, whether or not a plan
selects it, and one that breaks a rule is refused with that rule's code and named. A value outside the list, a
parameter without one, or a selection without values is E-IMPL-PARAM. The selected instance runs where its condition
holds, natively under both compilers with the address and undefined-behaviour sanitizers, against sums Python
computes on its own. The canonical projection keeps the native code.
"""

import pytest

from cairn.agent.projection import canonical_source
from cairn.compiler.cairnc import compile_source
from cairn.editor.formatting import format_source
from emitted import contract, refused

TOTAL = """fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut s:u64 = 0;
  for i in 0..n { s += xs[i]; }
  return s;
}
"""
BY = """fn total_by[K:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n % K == 0 tune K in [2, 4, 8] {
  let mut s:u64 = 0;
  for k in 0..n / K {
    for j in 0..K { s += xs[K * k + j]; }
  }
  return s;
}
"""
# Which body ran: the reference answers 0 and an instance its K, so the exit status shows the dispatch.
WHICH = """fn which(n:usize) -> usize { return 0; }
fn which_by[K:nat](n:usize) -> usize implements which when n % K == 0 && n >= K tune K in [2, 3] { return K; }
plan which use which_by[3];

fn main() -> i32 {
  if which(6) != 3 || which(9) != 3 || which(3) != 3 || which(4) != 0 || which(0) != 0 || which(7) != 0 { return 1; }
  return 0;
}
"""


def summing(selected: str) -> str:
    """A program that sums xs[i] = 1000003 * i + 7 for n of 0 to 17 through `total`, checked against sums Python
    computed, whichever instance runs it."""
    checks = "\n".join(
        f"  if total({n}, xs[0..{n}]) != {sum(1000003 * i + 7 for i in range(n))} {{ return {n + 1}; }}"
        for n in range(18)
    )
    body = "  let mut xs = Buf[u64](17);\n  for i in 0..17 { xs[i] = 1000003 * u64(i) + 7; }\n" + checks
    return TOTAL + BY + selected + "\nfn main() -> i32 {\n" + body + "\n  return 0;\n}\n"


def test_each_listed_value_is_an_instance_in_the_receipt_and_a_plan_runs_one():
    _, receipt = compile_source(summing("plan total use total_by[4];\n"))
    total = receipt["functions"]["total"]
    assert total["runs"] == "total_by[4]" and list(total["implementations"]) == [f"total_by[{k}]" for k in (2, 4, 8)]
    four = total["implementations"]["total_by[4]"]
    assert four["when"] == "n % 4 == 0" and four["applies"] == "tested at entry"
    assert four["instance_of"] == "total_by" and four["parameters"] == {"K": 4}
    assert receipt["functions"]["total_by[4]"]["implements"] == "total"
    assert len({r["identity"] for r in total["implementations"].values()}) == 3  # the values are in each identity
    assert "total_by" not in receipt["functions"]  # the template itself is no function of the build


def test_listing_another_value_leaves_every_other_instance_s_identity_as_it_was():
    def identities(source):
        return {n: r["identity"] for n, r in compile_source(source)[1]["functions"]["total"]["implementations"].items()}

    before = identities(summing(""))
    after = identities(summing("").replace("tune K in [2, 4, 8]", "tune K in [2, 4, 8, 16]"))
    assert {n: after[n] for n in before} == before and "total_by[16]" in after
    edited = identities(summing("").replace("s += xs[K * k + j];", "s = s + xs[K * k + j];"))
    assert not set(edited.values()) & set(before.values())  # an edit of the body is a new identity for each


def test_the_selected_instance_tests_its_own_condition_at_entry():
    cpp, _ = compile_source(summing("plan total use total_by[8];\n"))
    head = cpp.split("std::uint64_t ci_total(std::size_t v_n, const std::uint64_t* v_xs) noexcept {\n")[1]
    assert "static_cast<std::size_t>(8ULL)" in head.split("\n")[0] and "return ci_total_by_8(v_n, v_xs);" in head
    assert "std::uint64_t ci_total_by_2(" in cpp  # every instance is built, so each can be validated
    direct, _ = compile_source(TOTAL + BY + "plan total use total_by[4];\n"
                               "fn sums(eight:ro<u64>[8], six:ro<u64>[6]) -> u64 = total(8, eight) + total(6, six);\n")  # fmt: skip
    body = direct.split("ci_sums(const std::uint64_t* v_eight")[2].split("\n}\n")[0]
    assert body.count("ci_total_by_4(") == 1 and body.count("ci_total(") == 1  # 8 is decided at compile time


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
@pytest.mark.parametrize("plan", ["", "plan total use total_by[2];\n", "plan total use total_by[8];\n"])
def test_every_selected_instance_computes_what_python_computes(tmp_path, cxx, plan):
    done = contract(tmp_path, compile_source(summing(plan))[0], cxx, "-g", "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all")  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_the_instance_runs_only_where_its_own_condition_holds(tmp_path, cxx):
    done = contract(tmp_path, compile_source(WHICH)[0], cxx, "-g", "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all")  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


def test_two_parameters_list_every_combination():
    two = BY.replace("[K:nat]", "[K:nat, W:nat]").replace("tune K in [2, 4, 8]", "tune K in [2, 4], W in [1, 3]")
    total = compile_source(TOTAL + two + "plan total use total_by[4, 3];\n")[1]["functions"]["total"]
    assert list(total["implementations"]) == ["total_by[2, 1]", "total_by[2, 3]", "total_by[4, 1]", "total_by[4, 3]"]
    assert total["runs"] == "total_by[4, 3]" and total["implementations"]["total_by[4, 3]"]["parameters"] == {
        "K": 4, "W": 3}  # fmt: skip


def test_a_test_block_calls_a_listed_instance_by_name():
    compile_source(TOTAL + BY + "test by4 { buffer xs:u64[4] = zeroed; assert_eq(total_by[4](4, xs), total(4, xs)); }")


def test_the_projection_keeps_the_native_code_and_the_formatter_keeps_the_tokens():
    source = summing("plan total use total_by[4];\n")
    canonical = canonical_source(source)
    assert "implements total when ((n % K) == 0) tune K in [2, 4, 8]" in canonical
    assert "plan total use total_by[4];" in canonical
    assert compile_source(canonical)[0] == compile_source(source)[0] and canonical_source(canonical) == canonical
    shaped = format_source(source)
    assert "implements total when n % K == 0 tune K in [2, 4, 8] {" in shaped and format_source(shaped) == shaped
    assert compile_source(shaped)[0] == compile_source(source)[0]


def instance_fails(code: str, source: str, instance: str) -> None:
    data = refused(code, source)
    assert data["instance"] == instance and data["message"].startswith(instance), data["message"]


REFUSED = [
    ("E-IMPL-PARAM", TOTAL + BY.replace(" tune K in [2, 4, 8]", "")),  # a natural with no values
    ("E-IMPL-PARAM", TOTAL + BY.replace("tune K in", "tune W in")),  # not a parameter
    ("E-IMPL-PARAM", TOTAL + BY.replace("tune K in [2, 4, 8]", "tune K in [2], K in [4]")),
    ("E-IMPL-PARAM", TOTAL + BY.replace("[2, 4, 8]", "[2, 4, 4]")),
    ("E-IMPL-PARAM", TOTAL + BY.replace("[2, 4, 8]", "[]")),
    ("E-IMPL-PARAM", TOTAL + BY.replace("[2, 4, 8]", "[" + ", ".join(str(2 * k + 2) for k in range(17)) + "]")),
    ("E-IMPL-PARAM", TOTAL + BY.replace("[K:nat]", "[K:nat, W:nat]")),  # W has no values
    (
        "E-IMPL-PARAM",
        TOTAL + "fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total tune K in [4] { return 0; }",
    ),
    ("E-IMPL-PARAM", TOTAL + BY + "plan total use total_by[3];"),  # not a listed value
    ("E-IMPL-PARAM", TOTAL + BY + "plan total use total_by;"),  # which instance?
    ("E-IMPL-PARAM", TOTAL + BY + "plan total use total_by[4, 2];"),
    (
        "E-IMPL-PARAM",
        TOTAL + "fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total { return 0; }\n"
        "plan total use total_by4[4];",
    ),
    ("E-IMPL-PARAM", TOTAL + BY + "test by3 { buffer xs:u64[3] = zeroed; assert_eq(total_by[3](3, xs), 0); }"),
    ("E-IMPLEMENTS", TOTAL + BY.replace("[K:nat]", "[K:nat, T:integer]")),  # generic over a type
    ("E-IMPL-CALL", TOTAL + BY + "fn main() -> i32 { buffer xs:u64[4] = zeroed; return i32(total_by[4](4, xs)); }"),
    ("E-IMPL-USE", TOTAL + BY + "plan total use total_by[4];\nplan total use total_by[8];"),
]


@pytest.mark.parametrize(("code", "source"), REFUSED)
def test_each_rule_refuses_with_its_code(code, source):
    refused(code, source)


def test_an_instance_that_breaks_a_rule_is_refused_by_that_rule_and_named():
    instance_fails("E-IMPL-WHEN", TOTAL + BY.replace("[2, 4, 8]", "[4, 0]"), "total_by[0]")  # n % 0 could trap
    shifted = BY.replace("n % K == 0", "shr(n, K) > 0")
    compile_source(TOTAL + shifted)  # a shift by each listed value below the width cannot trap
    instance_fails("E-IMPL-WHEN", TOTAL + shifted.replace("[2, 4, 8]", "[2, 64]"), "total_by[64]")
    instance_fails("E-IMPL-WHEN", TOTAL + BY.replace("n % K == 0", "K <= 4"), "total_by[8]")  # never true
    wider = TOTAL.replace("-> u64 {", "-> u64 effects(pure, par:host) {", 1)
    lanes = BY.replace("for k in 0..n / K {\n    for j in 0..K { s += xs[K * k + j]; }\n  }",
                       "if K > 2 { let r = reduce + parallel i in n yield xs[i]; return r; }\n"
                       "  for i in 0..n { s += xs[i]; }")  # fmt: skip
    compile_source(wider + lanes)  # the declared ceiling admits the lanes each instance may use
    instance_fails("E-IMPL-EFFECT", TOTAL + lanes, "total_by[2]")  # total's own row is the ceiling

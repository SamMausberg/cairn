"""The verification contract: domains, trap policy, signatures, malformed input, and what a query resolves."""

import pytest
from test_semantics import check, fn

from cairn.verify.scalar_concrete import Concrete
from cairn.verify.scalar_semantics import equivalent, prepared
from cairn.verify.smt_bridge import Solver


def test_domain_totality_shortcircuit():
    a = fn("return x/y;", "x:u64,y:u64")
    check(a, a, assume="y!=0 && x/y<=x")
    check(a, a, "invalid-domain", assume="x/y<=x")


def test_type_error_not_semantic_success():
    check(fn("return x;"), fn("return true;"), "rejected")


def test_signature_mismatch():
    check(fn("return x;"), fn("return x;", "x:u32", "u32"), "invalid-contract")


def test_trap_equivalence_is_explicit():
    a = fn("return x/0;")
    b = fn("return x+1;")
    check(a, a, "invalid-reference")
    check(a, a, allow_reference_traps=True)
    check(a, b, "counterexample", allow_reference_traps=True)


def test_api_parse_error_unknown():
    with Solver() as s:
        assert s.check("(assert INVALID)", {})["status"] == "unknown"


def test_a_check_that_runs_past_its_watchdog_is_interrupted_and_unknown():
    """Factoring a 62-bit semiprime keeps Z3 busy for far longer than a tenth of a second."""
    import time

    semiprime = 2305843009213693951 * 3  # a Mersenne prime times three, above 2**62
    query = ("(declare-const x (_ BitVec 128)) (declare-const y (_ BitVec 128))"
             f"(assert (= (bvmul x y) (_ bv{semiprime * 5 + 2} 128)))"
             "(assert (bvugt x (_ bv1 128))) (assert (bvugt y (_ bv1 128)))"
             "(assert (bvult x (_ bv18446744073709551616 128))) (assert (bvult y (_ bv18446744073709551616 128)))")  # fmt: skip
    with Solver(30000, watchdog_ms=200) as s:  # Z3's own timeout is far away; the watchdog is what stops it
        start = time.monotonic()
        result = s.check(query, {})
    assert result["status"] == "unknown" and "interrupted at 200 ms" in result["reason"]
    assert time.monotonic() - start < 10


@pytest.mark.parametrize("ty", ["i32", "i64"])
def test_signed_remainder_not_python_modulo(ty):
    source = fn("return x%y;", f"x:{ty},y:{ty}", ty)
    c = Concrete(prepared(source))
    assert c.outcome("f", {"x": -7, "y": 3})["return"] == -1
    assert c.outcome("f", {"x": 7, "y": -3})["return"] == 1
    check(source, source, allow_reference_traps=True)


@pytest.mark.parametrize("operation", ["shl_wrap", "shr"])
def test_shift_bounds(operation):
    a = fn(f"return {operation}(x,k);", "x:u8,k:usize", "u8")
    check(a, a, "invalid-reference")
    check(a, a, assume="k<8")
    check(a, a, allow_reference_traps=True)


def test_signed_unsigned_conversion_guard():
    a = fn("return x;", "x:i64", "i64")
    b = fn("return i64(u64(x));", "x:i64", "i64")
    check(a, b, "counterexample")
    check(a, b, assume="x>=0")


def test_solver_obligation_saved():
    logs = []
    r = equivalent(fn("return x;"), fn("return add_wrap(x,0);"), "f", query_log=logs)
    assert r["status"] == "smt-equivalent" and logs and all("(check-sat)" in x["smt2"] for x in logs)


@pytest.mark.parametrize("bad", [None, {}, 15, False])
def test_malformed_source_fails_closed(bad):
    assert equivalent(bad, fn("return x;"), "f")["status"] == "invalid-contract"


def test_precondition_cannot_inject_a_declaration():
    r = equivalent(fn("return x;"), fn("return x;"), "f", assume="true); } fn injected()->bool {return true;")
    assert r["status"] == "rejected"


def test_equivalence_sees_through_generics_traits_modules_families_and_constants():
    """1.0: the scalar model runs on the typed, linked, monomorphized tree, so resolved calls are ordinary calls."""
    reference = "fn f(x:u64, y:u64) -> u64 { if x < y { return y; } return x; }"
    candidate = (
        "import std.core (Ord);\nconst BIAS:u64 = 0;\n"
        "fn pick[T: Ord](a:T, b:T) -> T { if less(a, b) { return b; } return a; }\n"
        "fn scaled[K:nat](v:u64) -> u64 = v * u64(K);\nfamily times = scaled[1..3];\n"
        "fn f(x:u64, y:u64) -> u64 = times_1(pick(x, y)) + BIAS;"
    )
    assert equivalent(reference, candidate, "f")["status"] == "smt-equivalent"
    wrong = equivalent(reference, candidate.replace("return b; } return a;", "return a; } return b;"), "f")
    assert wrong["status"] == "counterexample" and wrong["concrete_replay"] is True
    doubled = equivalent(reference, candidate.replace("times_1", "times_2"), "f")
    assert doubled["status"] == "counterexample"

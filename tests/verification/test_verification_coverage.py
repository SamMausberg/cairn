"""A proved scalar entry must not confer status on the rest of a program."""

import json

from cairn.cli import main
from cairn.verify.verification import verify_module

REF = "fn id(x:u64)->u64=x; fn twice(x:u64)->u64=add_wrap(id(x),x);"


def test_every_entry_checked():
    r = verify_module(REF, REF)
    assert r["status"] == "smt-module-equivalent"
    assert r["covered"] == ["id", "twice"]
    assert r["lean_proof"] is False and r["native_proof"] is False


def test_extra_function_is_uncovered():
    r = verify_module(REF, REF + " fn hidden()->u64=0;")
    assert r["status"] == "incomplete" and r["extra"] == ["hidden"]
    assert r["uncovered"] == ["hidden"]


def test_missing_function_blocks():
    r = verify_module(REF, "fn id(x:u64)->u64=x;")
    assert r["status"] == "incomplete" and r["missing"] == ["twice"]


def test_wrong_unused_function_cannot_hide():
    r = verify_module(REF, REF.replace("add_wrap(id(x),x)", "x"))
    assert r["status"] == "incomplete" and r["covered"] == ["id"]
    assert r["results"]["twice"]["status"] == "counterexample"


def test_moved_owner_function_is_not_covered():
    src = REF + " fn moved(n:usize)->usize {let mut b=Buf[u64](n);let c=take(b);return len(c);}"
    r = verify_module(src, src)
    assert r["status"] == "incomplete" and "moved" in r["uncovered"]
    assert r["results"]["moved"]["status"] == "unknown"


VALUES = (
    "struct Pair { a:u64; b:u64; }\nenum Maybe { Some(u64); None; }\n"
    "fn swapped(p:Pair)->Pair = Pair(p.b, p.a);\n"
    "fn unwrap(m:Maybe)->u64 { match m { Maybe.Some(v) => { return v; } Maybe.None => { return 0; } } }\n"
    "fn below(x:f64, y:f64)->bool = x < y;\n"
    "fn window(x:u64)->u64 { stack a:u64[4]=zeroed; for i in 0..4 { a[i]=add_wrap(x,u64(i)); }\n"
    "  let mut t:u64=0; for i in 0..4 { t=add_wrap(t,a[i]); } return t; }\n"
)
VIEWS = (
    "fn first(n:usize, xs:ro<u64>[n])->u64 { if n == 0 { return 0; } return xs[0]; }\n"
    "fn extent(n:usize, xs:ro<u64>[n])->usize = len(xs);\n"
    "fn bump(p:rw<u64>) { p = add_wrap(p, 1); }\n"
    "fn head(xs:ro<u8>[4], out:rw<u8>[4]) { for i in 0..4 { out[i] = add_wrap(xs[i], 1); } }\n"
)


def test_records_sums_floats_and_bounded_loops_are_covered():
    r = verify_module(VALUES, VALUES)
    assert r["status"] == "smt-module-equivalent"
    assert r["covered"] == ["below", "swapped", "unwrap", "window"] and not r["uncovered"]


def test_views_and_borrows_are_covered():
    r = verify_module(VIEWS, VIEWS)
    assert r["status"] == "smt-module-equivalent"
    assert r["covered"] == ["bump", "extent", "first", "head"] and not r["uncovered"]


PASS = VIEWS + "fn total(n:usize, xs:ro<u8>[n])->u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,xs[i]); } return t; }"  # fmt: skip


def test_a_symbolic_pass_over_a_view_is_not_covered_without_a_bound():
    r = verify_module(PASS, PASS)
    assert r["status"] == "incomplete" and r["uncovered"] == ["total"]
    assert "unrolling budget" in r["results"]["total"]["reason"]
    assert "16 or below" in r["results"]["total"]["reason"] and r["preconditions"] == {}


def test_a_precondition_covers_that_pass_and_the_receipt_says_where_it_holds():
    r = verify_module(PASS, PASS, preconditions={"total": "n<=4"})
    assert r["status"] == "smt-module-equivalent" and "total" in r["covered"]
    assert r["preconditions"] == {"total": "n<=4"}
    assert "total only where n<=4" in r["domain"]
    assert r["results"]["total"]["assume"] == "n<=4" and r["results"]["first"]["assume"] == "true"


def test_a_precondition_naming_no_declared_function_is_refused():
    r = verify_module(REF, REF, preconditions={"twce": "x<=1"})
    assert r["status"] == "incomplete" and "twce" in r["reason"] and not r["results"]


TAGGED = (
    "enum Op { Read; Write; }\n"
    "fn reads(n:usize, ops:ro<Op>[n], i:usize)->bool { if i >= n { return false; } return ops[i] == Op.Read; }\n"
    "fn chosen(ops:ro<Op>[1])->u64 { match ops[0] { Op.Read => { return 0; } Op.Write => { return 1; } } }\n"
)


def test_a_tag_in_storage_is_covered_only_where_the_reference_stays_total():
    """Storage may hold any tag, so reading one is covered while matching on one is a partial reference."""
    r = verify_module(TAGGED, TAGGED)
    assert r["status"] == "incomplete" and r["covered"] == ["reads"] and r["uncovered"] == ["chosen"]
    assert r["results"]["chosen"]["status"] == "invalid-reference"


def test_a_precondition_that_admits_nothing_cannot_cover():
    r = verify_module(PASS, PASS, preconditions={"total": "n<n"})
    assert r["status"] == "incomplete" and r["results"]["total"]["status"] == "invalid-domain"


def test_one_uncovered_value_function_still_blocks_the_module():
    src = VALUES + "fn drifting(x:f64)->f64 = x * 2.0;"  # A returned NaN is unknown, never equivalent.
    r = verify_module(src, src)
    assert r["status"] == "incomplete" and r["uncovered"] == ["drifting"]
    assert r["results"]["drifting"]["status"] == "unknown"


def test_empty_module_is_not_a_proof():
    assert verify_module("", "")["status"] == "incomplete"


def test_signature_change_cannot_pass():
    r = verify_module("fn f(x:u64)->u64=x;", "fn f(x:u32)->u32=x;")
    assert r["status"] == "incomplete"


def test_trapping_reference_not_total():
    assert verify_module("fn f(x:u64)->u64=x+1;", "fn f(x:u64)->u64=x+1;")["status"] == "incomplete"


def test_bad_candidate():
    assert verify_module(REF, "fn not valid")["status"] == "rejected"


def test_size_limit():
    assert verify_module(" " * 64001, "")["status"] == "incomplete"


def test_cli(tmp_path, capsys):
    a = tmp_path / "a.cairn"
    b = tmp_path / "b.cairn"
    a.write_text(REF)
    b.write_text(REF)
    assert main(["verify", str(a), str(b), "--all"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "smt-module-equivalent"
    b.write_text(REF + "fn extra()->u64=0;")
    assert main(["verify", str(a), str(b), "--all"]) == 2


def test_cli_assume_is_what_completes_a_symbolic_pass(tmp_path, capsys):
    a = tmp_path / "a.cairn"
    a.write_text(PASS)
    assert main(["verify", str(a), str(a), "--all"]) == 2
    assert json.loads(capsys.readouterr().out)["uncovered"] == ["total"]
    assert main(["verify", str(a), str(a), "--all", "--assume", "total=n<=4"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["status"] == "smt-module-equivalent" and r["preconditions"] == {"total": "n<=4"}
    assert main(["verify", str(a), str(a), "--symbol", "total", "--assume", "total=n<=4"]) == 0
    assert json.loads(capsys.readouterr().out)["assume"] == "n<=4"
    assert main(["verify", str(a), str(a), "--symbol", "total", "--assume", "first=n<=4"]) == 2
    assert main(["verify", str(a), str(a), "--all", "--assume", "total"]) == 2


def test_unknown_solver_cannot_cover(monkeypatch):
    import cairn.verify.verification as v

    monkeypatch.setattr(v, "equivalent", lambda *a, **k: {"status": "unknown", "reason": "solver unavailable"})
    r = v.verify_module(REF, REF)
    assert r["status"] == "incomplete" and not r["covered"]


def test_changed_public_type_blocks_module_status():
    r = verify_module("struct A {x:u64;} " + REF, "struct A {x:u32;} " + REF)
    assert r["status"] == "incomplete" and not r["public_types_match"]
    assert r["reference_intent_proved"] is False


def test_total_budget_exhaustion_is_unknown(monkeypatch):
    import cairn.verify.verification as v

    times = iter([0, 31, 32])
    monkeypatch.setattr(v.time, "monotonic", lambda: next(times))
    r = v.verify_module(REF, REF)
    assert r["status"] == "incomplete" and not r["covered"]

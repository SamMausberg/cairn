"""Equivalence over storage: array views, parts, single borrows, collectors, reductions and local owners."""

import pytest
from test_semantics import OP, check, refute

# Array views ---------------------------------------------------------------------------------

SUM = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,xs[i]); } return t; }"
FILL = "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(xs[i],1); } }"


def test_a_view_reads_the_elements_its_extent_lends():
    check(SUM, "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(xs[i],t); } return t; }",
          assume="n<=4")  # fmt: skip
    check("fn f(n:usize, xs:ro<u64>[n]) -> usize = len(xs);", "fn f(n:usize, xs:ro<u64>[n]) -> usize = n;")
    check("fn f(xs:ro<u8>[16]) -> u8 { let mut t:u8=0; for i in 0..16 { t=add_wrap(t,xs[i]); } return t; }",
          "fn f(xs:ro<u8>[16]) -> u8 { let mut t:u8=0; for i in 0..16 { t=add_wrap(xs[i],t); } return t; }")  # fmt: skip


def test_view_near_miss_separates_at_one_element():
    r = refute(SUM, SUM.replace("t=add_wrap(t,xs[i]);", "t=add_wrap(t,add_wrap(xs[i],1));"), assume="n>=1 && n<=3")
    assert len(r["counterexample"]["xs"]) == r["counterexample"]["n"] >= 1
    r = refute("fn f(n:usize, xs:ro<u64>[n]) -> usize = len(xs);", "fn f(n:usize, xs:ro<u64>[n]) -> usize = n+1;")
    assert r["expected"]["return"] == r["counterexample"]["n"] == len(r["counterexample"]["xs"])


def test_an_index_outside_the_extent_traps():
    a = "fn f(n:usize, xs:ro<u64>[n], i:usize) -> u64 = xs[i];"
    check(a, a, "invalid-reference")
    check(a, a, allow_reference_traps=True)
    b = "fn f(n:usize, xs:ro<u64>[n], i:usize) -> u64 { if i<n { return xs[i]; } return 0; }"
    r = refute(a, b, allow_reference_traps=True)
    assert r["counterexample"]["i"] >= r["counterexample"]["n"] and not r["expected"]["defined"]
    check(a, b, assume="i<n")


def test_the_final_contents_of_an_rw_view_are_observed():
    check(FILL, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(1,xs[i]); } }",
          assume="n<=4")  # fmt: skip
    r = refute(FILL, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { for i in 0..n { out[i]=add_wrap(xs[i],2); } }",
               assume="n>=1 && n<=3")  # fmt: skip
    assert r["expected"]["return"] is None and r["expected"]["written"]["out"] != r["actual"]["written"]["out"]
    kept = "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) { if n<2 { return; } out[0]=add_wrap(xs[0],1); }"
    r = refute(kept, kept.replace("if n<2 { return; }", "if n<2 { return; } out[1]=xs[1];"), assume="n<=2")
    assert r["expected"]["written"]["out"][1] != r["actual"]["written"]["out"][1]


def test_an_untouched_rw_view_keeps_what_the_caller_lent():
    check("fn f(n:usize, out:rw<u8>[n]) -> usize { return n; }", "fn f(n:usize, out:rw<u8>[n]) -> usize = len(out);")
    refute("fn f(n:usize, out:rw<u8>[n]) -> usize { return n; }",
           "fn f(n:usize, out:rw<u8>[n]) -> usize { if n>0 { out[0]=0; } return n; }", assume="n<=2")  # fmt: skip


TWO_PARTS = (
    "fn g(a:usize, xs:rw<u64>[a], b:usize, ys:rw<u64>[b]) { for i in 0..a { xs[i]=1; } for i in 0..b { ys[i]=2; } }\n"
    "fn f(n:usize, zs:rw<u64>[n], mid:usize) { if mid>n { return; } g(mid, zs[0..mid], n-mid, zs[mid..n]); }"
)
ONE_PASS = "fn f(n:usize, zs:rw<u64>[n], mid:usize) { if mid>n { return; } for i in 0..n { if i<mid { zs[i]=1; } else { zs[i]=2; } } }"


def test_two_views_of_one_array_in_one_call_write_one_storage():
    """A part is a window into its base: what a callee writes through either part is in the caller's array."""
    check(TWO_PARTS, ONE_PASS, assume="n<=3")
    check(ONE_PASS, TWO_PARTS, assume="n<=3")
    r = refute(TWO_PARTS, ONE_PASS.replace("zs[i]=2;", "zs[i]=3;"), assume="n<=3")
    zs = r["counterexample"]["zs"]
    assert r["counterexample"]["mid"] < len(zs) and r["expected"]["written"]["zs"] != r["actual"]["written"]["zs"]
    assert any(x != y for x, y in zip(r["expected"]["written"]["zs"], r["actual"]["written"]["zs"], strict=True))


def test_read_only_views_of_one_array_may_alias():
    twice = (
        "fn g(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,add_wrap(xs[i],ys[i])); } return t; }\n"
        "fn f(n:usize, b:ro<u8>[n]) -> u8 = g(n, b, b);"
    )
    check(twice, "fn f(n:usize, b:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,mul_wrap(b[i],2)); } return t; }",
          assume="n<=3")  # fmt: skip
    refute(twice, "fn f(n:usize, b:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,b[i]); } return t; }",
           assume="n>=1 && n<=3")  # fmt: skip


def test_a_part_of_a_part_beside_its_sibling_reads_the_same_storage():
    nested = (
        "fn g(a:usize, xs:ro<u8>[a], b:usize, ys:ro<u8>[b]) -> u8 { let mut t:u8=0; for i in 0..a { t=add_wrap(t,xs[i]); } for i in 0..b { t=add_wrap(t,ys[i]); } return t; }\n"
        "fn f(n:usize, zs:ro<u8>[n], mid:usize, k:usize) -> u8 { if mid>n || k>mid { return 0; } return g(k, zs[0..mid][0..k], n-mid, zs[mid..n]); }"
    )
    flat = "fn f(n:usize, zs:ro<u8>[n], mid:usize, k:usize) -> u8 { if mid>n || k>mid { return 0; } let mut t:u8=0; for i in 0..k { t=add_wrap(t,zs[i]); } for i in mid..n { t=add_wrap(t,zs[i]); } return t; }"
    check(nested, flat, assume="n<=3")
    refute(nested, flat.replace("for i in 0..k", "for i in 0..mid"), assume="n<=3")


def test_a_loop_over_a_symbolic_extent_needs_a_bound():
    r = check(SUM, SUM.replace("t=add_wrap(t,xs[i]);", "t=add_wrap(xs[i],t);"), "unknown")
    assert "unrolling budget" in r["reason"]


ITEM = "enum Item { Ok(u32); Bad; }\n"
TALLY = ITEM + (
    "fn f(n:usize, xs:ro<Item>[n]) -> u32 { let mut t:u32=0;\n"
    "  for i in 0..n { match xs[i] { Item.Ok(v) => { t=add_wrap(t,v); } Item.Bad => {} } } return t; }"
)


def test_a_payload_sum_in_a_view_is_read_through_its_tag():
    """Storage carries any tag, and both sides abort on one no variant names, so the two agree there too."""
    other = ITEM + (
        "fn f(n:usize, xs:ro<Item>[n]) -> u32 { let mut t:u32=0;\n"
        "  for i in 0..n { let mut d:u32=0; match xs[i] { Item.Ok(v) => { d=v; } Item.Bad => { d=0; } }\n"
        "    t=add_wrap(t,d); } return t; }"
    )
    check(TALLY, other, assume="n<=3", allow_reference_traps=True)
    r = refute(TALLY, TALLY.replace("t=add_wrap(t,v);", "t=add_wrap(t,add_wrap(v,1));"),
               assume="n>=1 && n<=2", allow_reference_traps=True)  # fmt: skip
    assert any(x.get("variant") == "Ok" for x in r["counterexample"]["xs"])


def test_a_tag_no_variant_names_traps_where_the_emitted_switch_does():
    """No entry guard reads a tag in storage, so `default: cr::trap()` is what separates these two."""
    total = OP + "fn f(o:ro<Op>[1]) -> u64 { if o[0]==Op.Read { return 0; } return 1; }"
    matched = OP + "fn f(o:ro<Op>[1]) -> u64 { match o[0] { Op.Read => { return 0; } Op.Write => { return 1; } } }"
    r = refute(total, matched)
    element = r["counterexample"]["o"][0]
    assert set(element) == {"tag"} and element["tag"] >= 2  # No variant names it, so no payload is carried.
    assert r["expected"]["return"] == 1 and r["actual"]["trap"] == "unmatched-tag"
    check(total, matched, assume="o[0]==Op.Read || o[0]==Op.Write")


def test_read_only_views_may_alias_so_the_model_takes_them_apart():
    """Two ro views are independent arrays: that admits the aliased case and more."""
    a = "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(xs[0],ys[0]); }"
    b = "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(ys[0],xs[0]); }"
    check(a, b)
    refute(a, "fn f(n:usize, xs:ro<u8>[n], ys:ro<u8>[n]) -> u8 { if n==0 { return 0; } return add_wrap(xs[0],xs[0]); }")


# Parts, single borrows, collectors, reductions and local owners --------------------------------

TOTAL = "fn total(m:usize, ys:ro<u8>[m]) -> u8 { let mut t:u8=0; for i in 0..m { t=add_wrap(t,ys[i]); } return t; }\n"
SPLIT = TOTAL + (
    "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 {\n"
    "  if mid > n { return 0; }\n"
    "  return add_wrap(total(mid, xs[0..mid]), total(n-mid, xs[mid..n]));\n}"
)


def test_a_part_is_the_window_its_guard_admits():
    check(SPLIT, TOTAL + "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 { if mid>n { return 0; } return total(n,xs); }",
          assume="n<=3")  # fmt: skip
    r = refute(SPLIT, TOTAL + "fn f(n:usize, xs:ro<u8>[n], mid:usize) -> u8 = total(n, xs);", assume="n<=2")
    assert r["counterexample"]["mid"] > r["counterexample"]["n"]


def test_a_part_that_does_not_fit_its_callee_traps():
    a = TOTAL + "fn f(n:usize, xs:ro<u8>[n], k:usize) -> u8 = total(k, xs[0..k]);"
    guarded = TOTAL + "fn f(n:usize, xs:ro<u8>[n], k:usize) -> u8 { if k>n { return 0; } return total(k,xs[0..k]); }"
    check(a, a, "invalid-reference", assume="k<=3")
    check(a, a, assume="k<=3", allow_reference_traps=True)
    r = refute(a, guarded, assume="k<=3", allow_reference_traps=True)
    assert r["counterexample"]["k"] > r["counterexample"]["n"] and not r["expected"]["defined"]
    check(a, guarded, assume="k<=3 && k<=n")


def test_an_rw_single_borrow_is_written_back():
    p = "struct P{a:u64;b:u64;}\n"
    g = p + "fn g(q:rw<P>)->u64{q.a=add_wrap(q.a,q.b);return q.a;}\n"
    check(g + "fn f(r:P)->P{let mut s=r;let v=g(s);return P(s.a,v);}",
          g + "fn f(r:P)->P{let a=add_wrap(r.a,r.b);return P(a,a);}")  # fmt: skip
    check(p + "fn f(q:rw<P>){q.a=q.b;}", p + "fn f(q:rw<P>){q.a=q.b;q.b=q.b;}")
    r = refute(p + "fn f(q:rw<P>){q.a=q.b;}", p + "fn f(q:rw<P>){q.b=q.a;}")
    assert r["counterexample"]["q"]["a"] != r["counterexample"]["q"]["b"]


COMPACT = (
    "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {\n"
    "  let used = compact out for i in n where xs[i] > 3 yield xs[i];\n  return used;\n}"
)
BYHAND = (
    "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize {\n"
    "  let mut k:usize = 0;\n"
    "  for i in 0..n { if xs[i] > 3 { out[k] = xs[i]; k = k + 1; } }\n  return k;\n}"
)


def test_compact_is_the_stable_selected_prefix():
    check(COMPACT, BYHAND, assume="n<=3")
    r = refute(COMPACT, BYHAND.replace("xs[i] > 3", "xs[i] >= 3"), assume="n<=2")
    assert r["expected"]["written"]["out"] != r["actual"]["written"]["out"] or r["expected"]["return"] != r["actual"]["return"]  # fmt: skip
    refute(COMPACT, BYHAND.replace("out[k] = xs[i];", "out[k] = add_wrap(xs[i],1);"), assume="n>=1 && n<=2")


def test_compact_leaves_the_tail_alone():
    nothing = COMPACT.replace("xs[i] > 3", "false")
    check(nothing, "fn f(n:usize, xs:ro<u8>[n], out:rw<u8>[n]) -> usize { return 0; }", assume="n<=3")
    cleared = COMPACT.replace("return used;", "for i in 0..n { if i >= used { out[i] = 0; } }\n  return used;")
    r = refute(COMPACT, cleared, assume="n<=2")
    assert r["expected"]["written"]["out"] != r["actual"]["written"]["out"]


def test_a_collector_over_a_symbolic_extent_needs_a_bound():
    r = check(COMPACT, BYHAND, "unknown")
    assert "unrolling budget" in r["reason"]


REDUCE = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let s = reduce OP for i in n yield xs[i]; return s; }"
FOLD = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut s:u8=SEED; for i in 0..n { s=STEP; } return s; }"


@pytest.mark.parametrize(
    "op,seed,step",
    [
        ("add_wrap", "0", "add_wrap(s,xs[i])"),
        ("mul_wrap", "1", "mul_wrap(s,xs[i])"),
        ("min", "255", "min(s,xs[i])"),
        ("max", "0", "max(s,xs[i])"),
        ("&", "255", "s&xs[i]"),
        ("|", "0", "s|xs[i]"),
        ("^", "0", "s^xs[i]"),
    ],
)
def test_reduce_is_the_fold_the_host_emits(op, seed, step):
    check(REDUCE.replace("OP", op), FOLD.replace("SEED", seed).replace("STEP", step), assume="n<=2")


def test_checked_reduce_traps_exactly_when_the_total_does_not_fit():
    checked = REDUCE.replace("OP", "+")
    wrapping = FOLD.replace("SEED", "0").replace("STEP", "add_wrap(s,xs[i])")
    check(checked, wrapping, "invalid-reference", assume="n<=3")
    r = refute(checked, wrapping, assume="n<=3", allow_reference_traps=True)
    assert sum(r["counterexample"]["xs"]) > 255 and not r["expected"]["defined"]
    check(checked, wrapping, assume="n<=1")  # One u8 always fits, so neither the total nor a prefix traps.


SCRATCH = (
    "fn f(n:usize, xs:ro<u8>[n]) -> u8 {\n"
    "  buffer tmp:u8[n] = zeroed;\n"
    "  for i in 0..n { tmp[i] = add_wrap(xs[i], 1); }\n"
    "  let mut t:u8 = 0;\n"
    "  for i in 0..n { t = add_wrap(t, tmp[i]); }\n  return t;\n}"
)
DIRECT = "fn f(n:usize, xs:ro<u8>[n]) -> u8 { let mut t:u8=0; for i in 0..n { t=add_wrap(t,add_wrap(xs[i],1)); } return t; }"  # fmt: skip


def test_a_local_heap_owner_is_zeroed_scratch():
    check(SCRATCH, DIRECT, assume="n<=3")
    check(SCRATCH.replace("buffer tmp:u8[n] = zeroed;", "let mut tmp = Buf[u8](n);"), DIRECT, assume="n<=3")
    check("fn f(n:usize) -> u8 { buffer tmp:u8[n] = zeroed; if n==0 { return 0; } return tmp[0]; }",
          "fn f(n:usize) -> u8 { return 0; }")  # fmt: skip
    refute(SCRATCH, DIRECT.replace("add_wrap(xs[i],1)", "add_wrap(xs[i],2)"), assume="n>=1 && n<=2")


def test_a_local_owner_is_bounded_by_its_own_extent():
    a = "fn f(n:usize, k:usize) -> u8 { buffer tmp:u8[n] = zeroed; return tmp[k]; }"
    check(a, a, "invalid-reference")
    check(a, a, assume="k<n")
    refute(a, "fn f(n:usize, k:usize) -> u8 { buffer tmp:u8[n] = zeroed; if k<n { return tmp[k]; } return 1; }",
           allow_reference_traps=True)  # fmt: skip

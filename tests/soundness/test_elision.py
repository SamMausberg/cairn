"""A guard is left out only where `verify/elision.py`, which does not use `compiler/check/facts.py`, accepts its proof.

facts.py proposes: each discharged site keeps the facts its decision used, each naming the loop, `let`, condition,
`&&` or exit it came from. The audit walks the function itself, holds each cited fact to an origin in force at the
site and to what that origin says, and decides the guard again from those facts. These tests hold that every proof
the library, the examples and the docs produce is accepted, that a proof tampered with in any one way is refused and
its guard written, that a fault planted in facts.py reaches no emitted program, and that a conservative build keeps
every guard.
"""

import collections
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_program, compile_source
from cairn.compiler.check import facts
from cairn.compiler.check.facts import Fact
from cairn.compiler.lower.codegen import Emitter
from cairn.compiler.syntax.tree import Expr, Stmt
from cairn.projects.project import load_project
from emitted import build, sanitized

ROOT = Path(__file__).resolve().parents[2]
GUARD = re.compile(r"\bcr::(?:at|part|add|sub|mul|convert|shr|shl_wrap)\b")


def corpus() -> dict[str, str]:
    """Every example project, one program importing all of std, and every accepted block of the docs."""
    out = {str(m.relative_to(ROOT)): load_project(m).source for m in sorted((ROOT / "examples").rglob("cairn.toml"))}
    std = sorted("std." + p.stem for p in (ROOT / "src/cairn/std").glob("*.cairn"))
    out["std"] = "".join(f"import {m};\n" for m in std) + "fn main() -> i32 { return 0; }\n"
    for doc in sorted((ROOT / "docs").glob("*.md")):
        if doc.name != "std_api.md":  # signatures, not programs
            for i, block in enumerate(
                re.findall(r"^```cairn\n(.*?)^```", doc.read_text(encoding="utf-8"), re.S | re.M)
            ):
                out[f"{doc.name}:{i}"] = block
    return out


def test_every_proof_the_library_the_examples_and_the_docs_produce_is_accepted():
    accepted = 0
    for name, source in corpus().items():
        for fn, rows in compile_source(source)[1]["functions"].items():
            assert "refused_discharges" not in rows, (name, fn, rows["refused_discharges"])
            accepted += sum(rows["discharged_check_sites"].values())
    assert accepted > 1000


def established(p) -> list[Expr]:
    out = []

    def walk(node):
        if isinstance(node, Expr):
            out.append(node) if node.established else None
            for a in node.args:
                walk(a)
        elif isinstance(node, Stmt):
            for x in [*node.exprs, *node.body, *node.other, *(s for a in node.arms for s in a.body)]:
                walk(x)

    for f in p.functions:
        for s in f.body:
            walk(s)
    return out


EARLY = "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { let mut j = k; if k >= n { return 0; } j = 0; return x[k] + u64(j); }"
MUTABLE = "fn f(n:usize, x:ro<u64>[n]) -> u64 { let mut j:usize = 0; for i in 0..n { j = i; } return x[j]; }"


def elsewhere(site: Expr, p) -> tuple:
    """A proof whose one fact has an origin that is real but not in force at the site: the loop of `MUTABLE`."""
    loop = next(s for f in p.functions for s in f.body if s.tag == "for")
    return ("facts", tuple(Fact(*f, ("binder", loop)) for f in site.proof[1]))


TAMPERED = {
    "no_facts": lambda site, p: ("facts", ()),
    "a_stronger_fact": lambda site, p: ("facts", tuple(Fact(f[0], f[1], f[2] - 5, f.origin) for f in site.proof[1])),
    "the_other_arm": lambda site, p: ("facts", tuple(Fact(*f, ("arm", f.origin[1], True)) for f in site.proof[1])),
    "a_mutable_name": lambda site, p: ("facts", tuple(Fact("j", f[1], f[2], f.origin) for f in site.proof[1])),
    "a_span_outside_its_call": lambda site, p: ("span", site),
    "no_proof": lambda site, p: None,
}


@pytest.mark.parametrize("how", TAMPERED)
def test_a_tampered_proof_is_refused_and_its_guard_written(how):
    p, checker, _ = compile_program(EARLY)
    [site] = [e for e in established(p) if e.tag == "index"]
    site.proof = TAMPERED[how](site, p)
    emitter = Emitter(p, checker)
    assert emitter.elision["f"]["refused"] == {"bounds": 1} and "bounds" not in emitter.elision["f"]["accepted"]
    assert "cr::at(v_x, v_k, v_n)" in "\n".join(emitter.units()[1][0][1])


def test_an_origin_from_another_place_is_refused():
    p, checker, _ = compile_program(EARLY + "\n" + MUTABLE.replace("fn f(", "fn g("))
    [site] = [e for e in established(p) if e.tag == "index" and e.args[1].val == "k"]
    site.proof = elsewhere(site, p)
    assert Emitter(p, checker).elision["f"]["refused"] == {"bounds": 1}


PLANTED = {  # Each is refused by a sound rule; an off-by-one in facts.py must not reach the C++.
    "one_past": "fn f(n:usize, x:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, x[i + 1]); } return t; }",
    "last_of_maybe_empty": "fn f(n:usize, x:ro<u64>[n]) -> u64 { return x[n - 1]; }",
    "the_bound_itself": "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if k > n { return 0; } return x[k]; }",
}


@pytest.mark.parametrize("name", PLANTED)
def test_a_fault_planted_in_facts_reaches_no_emitted_program(name, monkeypatch):
    honest = compile_source(PLANTED[name])[0]
    real = facts.at_most
    monkeypatch.setattr(facts, "at_most", lambda c, x, y, slack=0: real(c, x, y, slack + 1))
    cpp, receipt = compile_source(PLANTED[name])
    assert receipt["functions"]["f"].get("refused_discharges"), "the planted fault discharged nothing"
    assert len(GUARD.findall(cpp)) == len(GUARD.findall(honest)), cpp


# `x * C` and `let q = a / C`: k < q = n / 4 gives k * 4 <= q * 4 - 4 <= n - 4, so neither the multiply nor the index
# below keeps a guard. The audit derives the quotient's facts from the `let` itself, so a proof that cites them from
# anywhere else, with another divisor, or tighter than the `let` gives, is refused.
QUOTIENT = """
fn f(n:usize, x:ro<u64>[n]) -> u64 {
  let q = n / 4;
  let p = n / 3;
  let r = n - q;
  let mut t:u64 = 0;
  for k in 0..q { t = add_wrap(t, x[k * 4 + 3]); }
  return add_wrap(t, u64(p + r));
}
fn g(k:usize) -> usize = k * 4;
"""


def let(p, name: str) -> Stmt:
    return next(s for f in p.functions for s in f.body if s.tag == "let" and s.name == name)


def product(p, function: str) -> Expr:
    f = next(f for f in p.functions if f.name == function)
    return next(e for s in f.body for e in walk(s) if e.tag == "binary" and e.val == "*")


def walk(node):
    if isinstance(node, Expr):
        yield node
        for a in node.args:
            yield from walk(a)
    elif isinstance(node, Stmt):
        for x in [*node.exprs, *node.body, *node.other, *(s for a in node.arms for s in a.body)]:
            yield from walk(x)


def moved(site: Expr, origin, atom: str = "q*4", k: int | None = None) -> tuple:
    """The site's proof with its quotient fact (on `atom`) given another origin, another atom or another constant."""
    return ("facts", tuple(Fact(atom, f[1], f[2] if k is None else k, origin) if f[0] == "q*4" else f
                           for f in site.proof[1]))  # fmt: skip


FORGED = {
    "another_divisor": lambda site, p: moved(site, ("let", let(p, "p"))),
    "not_a_quotient": lambda site, p: moved(site, ("let", let(p, "r"))),
    "another_stride": lambda site, p: moved(site, ("let", let(p, "q")), atom="q*8"),
    "tighter_than_the_let": lambda site, p: moved(site, ("let", let(p, "q")), k=2**64 - 9),
    "no_cap": lambda site, p: ("facts", tuple(f for f in site.proof[1] if f[0] != "q*4")),
}


def test_the_quotient_and_the_product_are_discharged_and_their_proofs_accepted():
    p, checker, _ = compile_program(QUOTIENT)
    site = product(p, "f")
    assert site.established and any(f[0] == "q*4" for f in site.proof[1])
    emitter = Emitter(p, checker)
    assert emitter.elision["f"]["refused"] == {} and emitter.elision["f"]["accepted"]["overflow"] >= 2
    f, g = ("\n".join(lines) for _, lines in emitter.units()[1])
    assert "(v_k * static_cast<std::size_t>(4ULL))" in f and not re.search(r"cr::(at|mul)", f)
    assert "cr::mul<std::size_t>(v_k, static_cast<std::size_t>(4ULL))" in g


@pytest.mark.parametrize("how", FORGED)
def test_a_forged_quotient_or_product_proof_is_refused_and_its_guard_written(how):
    p, checker, _ = compile_program(QUOTIENT)
    site = product(p, "f")
    site.proof = FORGED[how](site, p)
    emitter = Emitter(p, checker)
    assert emitter.elision["f"]["refused"].get("overflow") == 1, emitter.elision["f"]
    assert "cr::mul<std::size_t>(v_k, static_cast<std::size_t>(4ULL))" in "\n".join(emitter.units()[1][0][1])


def test_a_product_facts_py_never_discharged_is_refused_whatever_it_cites():
    """A multiply marked by hand, citing the one fact that would cap it, from a `let` that says nothing of it."""
    p, checker, _ = compile_program(QUOTIENT)
    site = product(p, "g")
    assert not site.established
    site.established, site.proof = True, ("facts", (Fact("k*4", "", 2**64 - 1, ("let", let(p, "q"))),))
    emitter = Emitter(p, checker)
    assert emitter.elision["g"]["refused"] == {"overflow": 1}
    assert "cr::mul<std::size_t>(v_k, static_cast<std::size_t>(4ULL))" in "\n".join(emitter.units()[1][1][1])


PRODUCTS = {  # facts.py once folded two constants and named (k * 4) * 2 where the audit and Facts.lean did neither
    "two_constants": ("fn f(x:ro<u64>[8]) -> u64 { return x[2 * 3]; }", {"bounds": 1}),
    "a_folded_divisor": (
        "fn f(n:usize, x:ro<u64>[n]) -> u64 { let q = n / (2 * 4); let mut t:u64 = 0; "
        "for k in 0..q { t = add_wrap(t, x[k * 8]); } return t; }",
        {"overflow": 1, "bounds": 1},
    ),
    "a_nested_product": (
        "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if k * 4 * 2 < n { return x[k * 4 * 2]; } return 0; }",
        {},
    ),
}


@pytest.mark.parametrize("name", PRODUCTS)
def test_facts_the_audit_and_lean_read_a_product_alike(name):
    """Two constants multiply on all three sides, and a product atom times a constant names nothing on any, so every
    discharge proposed here is accepted. Before they agreed, each of these programs had a proposal the audit refused
    and a guard it kept."""
    source, discharged = PRODUCTS[name]
    rows = compile_source(source)[1]["functions"]["f"]
    assert "refused_discharges" not in rows and rows["discharged_check_sites"] == discharged, rows


PLANTED_RULES = {  # Each plants one slip in a new rule of facts.py; the audit, which derives its own, refuses it.
    "a_quotient_one_step_short": (
        "fn f(n:usize, x:ro<u64>[n]) -> u64 { let q = n / 4; let mut t:u64 = 0; "
        "for k in 0..q { t = add_wrap(t, x[k * 4 + 4]); } return t; }",
        "quotient",
        lambda real: lambda c, name, value: [(a, b, k - 4 if b else k) for a, b, k in real(c, name, value)],
    ),
    "a_product_with_no_cap": (
        "fn f(k:usize) -> usize = k * 4;",
        "product",
        lambda real: lambda c, e: (setattr(c, "cited", []), True)[1],
    ),
    "a_sum_one_past_its_side": (
        "fn f(i:usize, k:usize) -> usize { if i < 16 && k < 16 { let s = i + k; return s - (k + 1); } return 0; }",
        "bounds",
        lambda real: lambda c, e: plus_one(real, c, e),
    ),
    "two_constants_added": (
        "fn f(x:ro<u64>[6]) -> u64 { return x[2 * 3]; }",
        "times",
        lambda real: lambda c, a, b: added(real, c, a, b),
    ),
    "a_nested_product_named": (
        "fn f(n:usize, x:ro<u64>[n], k:usize) -> u64 { if k * 4 * 2 < n { return x[k * 4 * 2]; } return 0; }",
        "times",
        lambda real: lambda c, a, b: renamed(real, c, a, b),
    ),
}


def plus_one(real, c, e):
    """The bounds of `e`, with a sum's lower bounds each one higher than its sides give."""
    high, low = real(c, e)
    return high, [(x, k + 1) if e.tag == "binary" and e.val == "+" and x else (x, k) for x, k in low]


def added(real, c, a, b):
    """`a * b` as a term, with two constants added where they multiply."""
    x, y = facts.exact(c, a), facts.exact(c, b)
    return (facts.ZERO, x[1] + y[1]) if x and y and x[0] == y[0] == facts.ZERO else real(c, a, b)


def renamed(real, c, a, b):
    """`a * b` as a term, with a product atom times a constant named as an atom of its own (`k*4*2`)."""
    (x, j), (y, k) = (facts.exact(c, e) or (None, 0) for e in (a, b))
    return (f"{x}*{k}", 0) if x and "*" in x and y == facts.ZERO and not j and k > 0 else real(c, a, b)


@pytest.mark.parametrize("name", PLANTED_RULES)
def test_a_fault_planted_in_a_new_rule_reaches_no_emitted_program(name, monkeypatch):
    """Every guard the honest rule keeps is still written. A proof that cites the planted fact is refused whole, so a
    guard the honest rule could drop may be written too."""
    source, rule, slip = PLANTED_RULES[name]
    honest = collections.Counter(GUARD.findall(compile_source(source)[0]))
    monkeypatch.setattr(facts, rule, slip(getattr(facts, rule)))
    cpp, receipt = compile_source(source)
    assert receipt["functions"]["f"].get("refused_discharges"), "the planted fault discharged nothing"
    assert not honest - collections.Counter(GUARD.findall(cpp)), cpp


def test_a_conservative_build_writes_every_guard():
    for source in [EARLY, MUTABLE, PLANTED["one_past"]]:
        optimized, conservative = compile_source(source)[0], compile_source(source, keep_guards=True)
        assert compile_source(source, keep_guards=True)[1]["functions"]["f"]["discharged_check_sites"] == {}
        assert len(GUARD.findall(conservative[0])) >= len(GUARD.findall(optimized))
    assert "v_x[v_k]" in compile_source(EARLY)[0]
    assert "cr::at(v_x, v_k, v_n)" in compile_source(EARLY, keep_guards=True)[0]


SUM = "fn sum(n:usize, xs:ro<u8>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + u64(xs[i]); } return t; }\n"


def calls(source: str, name: str = "f") -> str:
    cpp = compile_source(SUM + source)[0]
    return "".join(re.findall(rf"c[fi]_{name}\([^;\n]*\{{\n(.*?)^\}}", cpp, re.S | re.M))


def test_an_omitted_extent_costs_no_check_the_part_does_not_already_make():
    body = calls("fn f(n:usize, s:ro<u8>[n], lo:usize, hi:usize) -> u64 = sum(s[lo..hi]);")
    assert "cr::sub" not in body and body.count("cr::part(") == 1, body
    written = calls("fn f(n:usize, s:ro<u8>[n], lo:usize, hi:usize) -> u64 = sum(hi - lo, s[lo..hi]);")
    assert "cr::sub" not in written, written
    other = calls("fn f(n:usize, s:ro<u8>[n], lo:usize, hi:usize) -> u64 = sum(hi - lo, s[lo + 1..hi + 1]);")
    assert "cr::sub" in other, other  # Not the part's own span: its subtraction is guarded.


PARTS = {  # The part guard goes where lo <= hi <= len and the extent is hi - lo.
    "prefix": "fn f(n:usize, s:ro<u8>[n], m:usize) -> u64 { if m > n { return 0; } return sum(s[0..m]); }",
    "suffix": "fn f(n:usize, s:ro<u8>[n], m:usize) -> u64 { if m > n { return 0; } return sum(s[n - m..n]); }",
    "window": "fn f(n:usize, s:ro<u8>[n], i:usize) -> u64 { if i + 4 > n { return 0; } return sum(4, s[i..i + 4]); }",
    "tested": "fn f(n:usize, s:ro<u8>[n], m:usize) -> bool = m <= n && sum(s[n - m..n]) == 0;",
    "named": "fn f(n:usize, s:ro<u8>[n], m:usize) -> u64 { if m > n { return 0; } let k = n - m; return sum(m, s[k..n]); }",
}
KEPT_PARTS = {
    "unordered": "fn f(n:usize, s:ro<u8>[n], lo:usize, hi:usize) -> u64 { if hi > n { return 0; } return sum(s[lo..hi]); }",
    "past_the_end": "fn f(n:usize, s:ro<u8>[n], m:usize) -> u64 { if m > n + 1 { return 0; } return sum(s[0..m]); }",
    "another_extent": "fn f(n:usize, s:ro<u8>[n], m:usize, k:usize) -> u64 { if m > n { return 0; } return sum(k, s[0..m]); }",
    "guarded_bound": "fn f(n:usize, s:ro<u8>[n], m:usize, d:usize) -> u64 { if m > n { return 0; } return sum(s[0..min(m, n / d)]); }",
}


@pytest.mark.parametrize("name", PARTS)
def test_a_settled_part_loses_its_guard(name):
    body = calls(PARTS[name])
    assert "cr::part(" not in body, body


@pytest.mark.parametrize("name", KEPT_PARTS)
def test_a_part_keeps_its_guard_where_nothing_settles_it(name):
    assert "cr::part(" in calls(KEPT_PARTS[name]), KEPT_PARTS[name]


DRIVER = """
fn check(n:usize, s:ro<u8>[n], m:usize) -> u64 { if m > n { return 99; } return sum(s[n - m..n]) + sum(s[0..m]); }
fn main() -> i32 {
  let s = "abcdef";
  let mut total:u64 = 0;
  for m in 0..8 { total = total + check(len(s), s, m); }
  if total != 4278 { return 1; }
  return 0;
}
"""


@pytest.mark.parametrize("cxx", ["clang++", "g++"])
def test_settled_parts_compute_what_guarded_ones_do_natively(tmp_path, cxx):
    source = SUM + DRIVER
    for keep in (False, True):
        (tmp_path / str(keep)).mkdir()
        exe = build(tmp_path / str(keep), compile_source(source, keep_guards=keep)[0], *sanitized(cxx), cxx=cxx)
        assert subprocess.run([exe], capture_output=True).returncode == 0


@pytest.mark.skipif(not shutil.which("clang++"), reason="needs clang++")
def test_the_differential_harness_agrees_and_sees_a_guard_dropped_behind_the_audit(tmp_path, monkeypatch):
    from checks import differential_guards as harness
    from checks.emission_identity import UNGUARDED, arguments

    kept = harness.generated(7, 0, 8)
    sources, names = [s for s, _ in kept], [s.split("(")[0][3:] for s, _ in kept]
    assert harness.compare(sources, names, "clang++", tmp_path / "honest") == []
    real = harness.compile_source

    def planted(source, keep_guards=False, **options):  # every index guard dropped after the audit ran
        cpp, receipt = real(source, keep_guards=keep_guards, **options)
        while not keep_guards and (at := cpp.find("cr::at(")) >= 0:
            args, end = arguments(cpp, at + len("cr::at("))
            cpp = cpp[:at] + UNGUARDED["at"]("", args) + cpp[end:]
        return cpp, receipt

    monkeypatch.setattr(harness, "compile_source", planted)
    # Hundreds of planted cases end in an AddressSanitizer report the comparison never reads. Symbolizing them took 47
    # of this test's 56 seconds on a sixteen-thread machine; a report without symbols ends its case the same way.
    monkeypatch.setenv("ASAN_OPTIONS", "symbolize=0")
    assert harness.compare(sources, names, "clang++", tmp_path / "planted")

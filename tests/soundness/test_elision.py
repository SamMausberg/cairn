"""A guard is left out only where `verify/elision.py`, which does not use `compiler/facts.py`, accepts its proof.

facts.py proposes: each discharged site keeps the facts its decision used, each naming the loop, `let`, condition,
`&&` or exit it came from. The audit walks the function itself, holds each cited fact to an origin in force at the
site and to what that origin says, and decides the guard again from those facts. These tests hold that every proof
the library, the examples and the docs produce is accepted, that a proof tampered with in any one way is refused and
its guard written, that a fault planted in facts.py reaches no emitted program, and that a conservative build keeps
every guard.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.compiler import facts
from cairn.compiler.cairnc import compile_program, compile_source
from cairn.compiler.codegen import Emitter
from cairn.compiler.facts import Fact
from cairn.compiler.tree import Expr, Stmt
from cairn.projects.project import load_project
from emitted import build, sanitized

ROOT = Path(__file__).resolve().parents[2]
GUARD = re.compile(r"\bcr::(?:at|part|add|sub|convert|shr|shl_wrap)\b")


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
    return re.search(rf"cf_{name}\([^;\n]*\{{\n(.*?)^\}}", cpp, re.S | re.M).group(1)


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
    assert harness.compare(sources, names, "clang++", tmp_path / "planted")

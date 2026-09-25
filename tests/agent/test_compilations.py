"""One compile per distinct source in a process (compiler/compilations.py): what it keeps, when it compiles again, that
no caller can change what another reads, and that a copy emits the same C++ and receipt as a compile from scratch."""

import shutil
import tomllib
from pathlib import Path

import pytest

from cairn.compiler import cairnc, compilations, modules
from cairn.compiler.cairnc import Diagnostic, compile_program, compile_source
from cairn.compiler.compilations import Cache, Compilation
from cairn.projects.project import load_project
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]
S = (
    "fn step(x:u64) -> u64 { return x + 1; }\n"
    "fn caller(x:u64) -> u64 { return step(x); }\n"
    "fn fill(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = step(u64(i)); } }\n"
)
BROKEN = S.replace("return step(x);", "return step(true);") + "fn other() -> u64 { return false; }\n"
# Project manifests only: a harness mapping under examples/ is a .toml too.
MANIFESTS = [p for p in (ROOT / "examples").rglob("*.toml") if "project" in tomllib.loads(p.read_text())]
EXAMPLES = sorted([*MANIFESTS, *cairn_sources(ROOT / "examples/basics")])


@pytest.fixture
def cache():
    return Cache()


def test_a_second_request_for_a_source_answers_from_the_first_compile(cache, monkeypatch):
    ran = []
    monkeypatch.setattr(compilations, "compile_program", lambda *a, **k: ran.append(a) or compile_program(*a, **k))
    first = Compilation(S, cache=cache)
    p, _, receipts = first.program()
    assert not first.cached and len(ran) == 1
    again = Compilation(S, cache=cache)
    q, _, kept = again.program()
    assert (
        again.cached
        and len(ran) == 1
        and kept == receipts
        and [f.name for f in q.functions] == [f.name for f in p.functions]
    )
    emitted = Compilation(S, cache=cache)
    assert emitted.emitted() == compile_source(S) and not emitted.cached and len(ran) == 1  # emitted once, not checked
    assert Compilation(S, cache=cache).emitted() == compile_source(S)
    assert Compilation(S + "\n", cache=cache).program()[2] == receipts and len(ran) == 2  # one more byte: a miss


def test_a_check_with_sites_answers_one_without_and_an_accepted_check_answers_either_every(cache):
    sites = Compilation(S, sites=True, cache=cache)
    assert sites.program()[1].sites and not sites.cached
    plain = Compilation(S, cache=cache)
    _, checker, receipts = plain.program()
    assert plain.cached and checker.sites == [] and not checker.capture_sites and checker.refusals is None
    every = Compilation(S, every=True, cache=cache)
    _, checker, _ = every.program()
    assert every.cached and checker.refusals == [] and every.program()[2] == receipts
    fresh = compile_program(S, capture_sites=True)[1].sites
    assert Compilation(S, sites=True, every=True, cache=cache).program()[1].sites == fresh


def test_a_refusal_is_kept_as_its_record_and_answers_only_the_setting_it_was_made_under(cache):
    with pytest.raises(Diagnostic) as first:
        Compilation(BROKEN, cache=cache).program()
    assert "further" not in first.value.data
    again = Compilation(BROKEN, cache=cache)
    with pytest.raises(Diagnostic) as kept:
        again.emitted()
    assert again.cached and kept.value.data == first.value.data and kept.value is not first.value
    every = Compilation(BROKEN, every=True, cache=cache)
    with pytest.raises(Diagnostic) as each:
        every.program()
    assert not every.cached and [d["code"] for d in each.value.data["further"]] == ["E-TYPE-MISMATCH"]
    unparsed = Compilation("fn f() -> u64 { return 1 }", cache=cache)
    with pytest.raises(Diagnostic) as parse:
        unparsed.parsed()
    assert parse.value.data["code"] == "E-PARSE"
    with pytest.raises(Diagnostic):
        Compilation("fn f() -> u64 { return 1 }", cache=cache).emitted()


def test_a_check_that_a_fault_ended_is_answered_and_never_kept(cache, monkeypatch):
    def faulted(*_, **__):
        error = Diagnostic("E-TYPE-MISMATCH", "refused, then a fault")
        error.abandoned = RuntimeError("the check stopped on a fault")
        raise error

    monkeypatch.setattr(compilations, "compile_program", faulted)
    with pytest.raises(Diagnostic):
        Compilation(S, cache=cache).program()
    assert not any(entry.checks for entry in cache.held.values())


def test_what_one_caller_changes_no_other_caller_reads(cache):
    p, checker, receipts = Compilation(S, sites=True, cache=cache).program()  # the objects the compile made
    p.functions.clear()
    checker.sites.clear()
    checker.fs.clear()
    receipts["step"]["effects"].append("alloc")
    q, again, kept = Compilation(S, sites=True, cache=cache).program()
    assert {f.name for f in q.functions} == {"step", "caller", "fill"} and again.sites and again.fs
    assert kept["step"]["effects"] == ["trap"]
    q.functions.clear()  # a copy, changed the same way
    assert {f.name for f in Compilation(S, cache=cache).program()[0].functions} == {"step", "caller", "fill"}
    _, manifest = Compilation(S, cache=cache).emitted()
    manifest["functions"]["step"]["effects"].clear()
    assert Compilation(S, cache=cache).emitted()[1]["functions"]["step"]["effects"] == ["trap"]
    tree = Compilation(S, cache=cache).parsed()
    tree.functions.clear()
    assert len(Compilation(S, cache=cache).parsed().functions) == 3
    with pytest.raises(Diagnostic) as first:
        Compilation(BROKEN, cache=cache).program()
    first.value.data["line"] = 99  # as agent/diagnostics.located moves a refusal into a reply
    with pytest.raises(Diagnostic) as again_refused:
        Compilation(BROKEN, cache=cache).program()
    assert again_refused.value.data["line"] == 2


def test_a_different_compiler_or_library_is_a_different_compile(cache, monkeypatch, tmp_path):
    kept = Compilation(S, cache=cache)
    kept.program()
    monkeypatch.setattr(compilations, "compiler", lambda: "0" * 64)  # the process loaded other compiler files
    other = Compilation(S, cache=cache)
    other.program()
    assert other.key != kept.key and not other.cached

    from cairn.verify import scalar_semantics

    monkeypatch.undo()
    compilations.compiler.cache_clear()
    loaded = compilations.compiler()
    monkeypatch.setattr(scalar_semantics, "implementation_hash", lambda: "1" * 64)
    compilations.compiler.cache_clear()
    try:
        assert compilations.compiler() != loaded  # the compiler's own files are in its digest
    finally:
        compilations.compiler.cache_clear()

    std = tmp_path / "std"
    shutil.copytree(modules.STD, std)
    monkeypatch.setattr(modules, "STD", std)
    monkeypatch.setattr(compilations, "STD", std)
    source = "import std.math;\n\nfn grow(x:f64) -> f64 { return math.exp(x); }\n"
    assert "std.math.twice" not in Compilation(source, cache=cache).program()[2]
    (std / "math.cairn").write_text((std / "math.cairn").read_text() + "\npub fn twice(x:f64) -> f64 = x + x;\n")
    grown = Compilation(source, cache=cache)
    assert "std.math.twice" in grown.program()[2] and not grown.cached  # a library file edited on disk


def test_the_cache_keeps_at_most_its_count_and_its_bytes():
    small = Cache(entries=2)
    sources = [S.replace("x + 1", f"x + {k}") for k in range(1, 4)]
    for text in sources:
        Compilation(text, cache=small).program()
    held = small.stats()
    assert held["sources"] == 2 and held["bytes"] == sum(e.size() for e in small.held.values()) > 0
    newest, oldest = Compilation(sources[2], cache=small), Compilation(sources[0], cache=small)
    newest.program()
    oldest.program()
    assert newest.cached and not oldest.cached  # the one used least recently went first
    tight = Cache(size=held["bytes"] * 3 // 4)  # room for one of these sources, not two
    for text in sources[:2]:
        Compilation(text, cache=tight).program()
    assert tight.stats()["sources"] == 1 and tight.stats()["bytes"] <= tight.size
    none = Cache(size=1)  # an entry larger than the whole allowance is not kept either
    Compilation(S, cache=none).program()
    assert none.stats() == {"sources": 0, "bytes": 0, "most_sources": 16, "most_bytes": 1}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: str(p.relative_to(ROOT)))
def test_a_kept_compile_answers_what_a_compile_from_scratch_answers(path):
    """Every example: the C++ and the manifest emitted from a copy, the receipts of a copy, and the same check with
    and without sites and every, are what the compiler answers from scratch."""
    source = load_project(path).source
    cache = Cache()
    try:
        expected = compile_source(source)
    except Diagnostic as error:
        with pytest.raises(Diagnostic) as kept:
            Compilation(source, cache=cache).emitted()
        assert kept.value.data == error.data
        return
    _, _, receipts = compile_program(source)
    live = Compilation(source, cache=cache)
    assert live.program()[2] == receipts  # the objects the check made
    assert live.emitted() == expected  # emitted from a copy of them
    assert Compilation(source, every=True, cache=cache).emitted() == expected
    assert Compilation(source, cache=cache).program()[2] == receipts
    assert compile_program(source, every=True)[2] == receipts == compile_program(source, capture_sites=True)[2]
    assert cairnc.compile_source(source, every=True) == expected

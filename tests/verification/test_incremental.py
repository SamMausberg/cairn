"""A body edit checked from a kept walk (compiler/check/incremental.py) answers what a whole check of the edited source
answers: acceptance and every diagnostic with `further` and `not_judged`, the checked program and checker object for
object, the receipts with every effect row, and the emitted C++ and manifest.

Every example is edited in the first, middle and last body the walk takes, and a program written for it is edited to
add and remove calls, effects, a loop, a spawn, recursion, a device lane's reach and a call through a function value,
and to refuse a correct program and correct a refused one. `CAIRN_INCREMENTAL_EVERY=1` edits every body of every
example instead, which evidence/v1_1/workspace records.
"""

import os
import pickle
import sys
import tomllib
from pathlib import Path

import pytest

from cairn.compiler.cairnc import Diagnostic, compile_program, generate, joined
from cairn.compiler.check import incremental
from cairn.compiler.compilations import Cache, Compilation
from cairn.compiler.syntax.parser import Parser
from cairn.compiler.syntax.tree import Type
from cairn.projects.project import load_project
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = [p for p in (ROOT / "examples").rglob("*.toml") if "project" in tomllib.loads(p.read_text())]
EXAMPLES = sorted([*MANIFESTS, *cairn_sources(ROOT / "examples/basics")])
EVERY = os.environ.get("CAIRN_INCREMENTAL_EVERY") == "1"

# What a check keeps that nothing reads once it is over, and so what the comparison leaves out: the memos of what a
# type is, which answer the same whichever check asked first (either may hold an entry the other did not need); the
# arguments typed ahead of their call, which a rule may have replaced in the tree since; and a cooperative region's
# tables keyed by the ids of nodes, which differ between any two processes.
UNREAD = {("Checker", "kinds"), ("Checker", "frees"), ("Checker", "early"), ("Block", "top"), ("Block", "reach"),
          ("Block", "levels")}  # fmt: skip

PROGRAM = """struct Pair { a:u64; b:u64; }

fn leaf(x:u64) -> u64 { return x + 1; }

fn noisy(x:u64) -> u64 {
  println("noisy");
  return x;
}

fn twice(x:u64) -> u64 { return add_wrap(x, x); }

fn middle(x:u64) -> u64 {
  return leaf(x);
}

fn capped(x:u64) -> u64 effects(trap, diverge, indirect_call) {
  return middle(x);
}

fn lanes(n:usize, out:rw<u64>[n]) {
  parallel i in n { out[i] = middle(u64(i)); }
}

fn device(n:usize, out:rw<u64>[n]@device) {
  parallel i in n { out[i] = twice(u64(i)); }
}

fn apply(f:ro<fn(u64) -> u64>, x:u64) -> u64 { return f(x); }

fn through(x:u64) -> u64 { return apply(leaf, x); }

fn fill(n:usize, out:rw<u64>[n], v:u64) { for i in 0..n { out[i] = v; } }

fn tasks(n:usize, out:rw<u64>[n]) {
  fill(n, out, 1);
}

fn pick[T:copy](a:T, b:T, first:bool) -> T {
  if first { return a; }
  return b;
}

fn chooser(x:u64) -> u64 { return pick(x, 1, x > 3); }

fn later(x:u64) -> u64 { return pick(x, 2, x > 4); }

fn main() -> i32 {
  let heard = noisy(3);
  let called = through(2);
  if capped(1) + called + heard == 0 { return 1; }
  return 0;
}
"""
# The body each edit gives one function of PROGRAM.
TARGETED = {
    "add a call": ("middle", "{\n  return add_wrap(leaf(x), twice(x));\n}"),
    "remove a call": ("middle", "{\n  return x;\n}"),
    "add an effect past a ceiling": ("middle", '{\n  println("m");\n  return leaf(x);\n}'),
    "remove an effect": ("noisy", "{\n  return x;\n}"),
    "add a loop": ("middle", "{\n  let mut y:u64 = x;\n  while y > 10 { y = y - 1; }\n  return leaf(y);\n}"),
    "add a spawn": ("tasks", "{\n  let t = spawn fill(n, out, 1);\n  wait(t);\n}"),
    "reach more from a device lane": ("device", "{\n  parallel i in n { out[i] = middle(u64(i)); }\n}"),
    "host code a device lane reaches": ("twice", '{\n  println("t");\n  return add_wrap(x, x);\n}'),
    "recursion": ("middle", "{\n  if x > 100 { return middle(x - 1); }\n  return leaf(x);\n}"),
    "call through a function value": ("middle", "{\n  return apply(twice, x);\n}"),
    "refuse a correct program": ("leaf", "{ return true; }"),
    "use again an instance it made first": ("chooser", "{ return pick(x, 5, x > 6); }"),
    "no longer make an instance it made first": ("chooser", "{ return x; }"),
    "make first an instance a later body made": ("leaf", "{ return pick(x, 1, true) + 1; }"),
    "a body no longer parses": ("leaf", "{ return x + ; }"),
}
# What a whole check, and no walk, answers: the parse error where a whole parse meets it; an instance a later body may
# read that the edited body no longer makes; and an instance the edited body now makes that a later body made.
WHOLE = {
    "a body no longer parses",
    "no longer make an instance it made first",
    "make first an instance a later body made",
}
# Edits of a body's text that any example takes: a line more, a comment, an effect more, and a statement fewer.
EDITS = {
    "lines": lambda body: body[:1] + "\n\n" + body[1:],
    "comment": lambda body: body[:-1] + "  // edited\n}" if body.endswith("}") else None,
    "println": lambda body: body[:1] + ' println("edited");' + body[1:] if body.startswith("{") else None,
    "fewer": lambda body: body[:1] + body[body.index(";") + 1 :] if body.startswith("{") and ";" in body else None,
}


def canonical(root) -> tuple:
    """`root` as nested tuples, every object that is reached twice written as a reference to where it was first
    reached, sets sorted: two graphs of objects are equal, sharing included, when these are."""
    seen: dict[int, int] = {}

    def visit(x):
        if x is None or isinstance(x, bool | int | float | str | bytes | Type):
            return x
        if isinstance(x, tuple):
            return ("tuple", [visit(a) for a in x])
        if id(x) in seen:
            return ("ref", seen[id(x)])
        seen[id(x)] = mine = len(seen)
        if isinstance(x, list):
            return ("list", mine, [visit(a) for a in x])
        if isinstance(x, dict):
            return ("dict", mine, [(visit(k), visit(v)) for k, v in x.items()])
        if isinstance(x, set | frozenset):
            return ("set", mine, sorted(repr(visit(a)) for a in x))
        fields = vars(x) if hasattr(x, "__dict__") else {s: getattr(x, s) for s in type(x).__slots__}
        kind = type(x).__name__
        return (kind, mine, [(k, visit(v)) for k, v in sorted(fields.items()) if (kind, k) not in UNREAD])

    return visit(root)


def answer(check) -> tuple:
    """What a check answered and everything made from it: the objects, the receipts, the C++ and the manifest; or
    the refusal's record."""
    try:
        p, checker, receipts = check()
    except Diagnostic as error:
        return ("refused", error.data)
    state = canonical((p, checker, receipts))
    interface, bodies, manifest = generate("", "", (), checked=(p, checker, receipts))
    return ("typed", state, receipts, joined(interface, bodies), manifest, checker.kinds, checker.frees)


def same(mine: tuple, whole: tuple) -> bool:
    """Two answers agree, and the memos of what a type is say the same of every type both asked about."""
    if mine[0] == "typed" and whole[0] == "typed":
        memos = zip(mine[-2:], whole[-2:], strict=True)
        return mine[:-2] == whole[:-2] and all(a[k] == b[k] for a, b in memos for k in a.keys() & b.keys())
    return mine == whole


def walked(source: str, sites: bool = False) -> tuple[bytes, incremental.Walk]:
    kept = []
    compile_program(source, sites, walked=lambda c, walk: kept.append((pickle.dumps((c.p, c, walk)), walk)))
    return kept[0]


def replayed(base: tuple[bytes, incremental.Walk], before: str, after: str, sites: bool, every: bool):
    """The check of `after` from the walk of `before`: its answer, or None when it falls back to a whole check."""
    data, walk = base
    edit = incremental.edited(before, after, walk.spans)
    if edit is None:
        return None
    p, checker, walk = pickle.loads(data)
    try:
        return answer(lambda: (p, checker, incremental.recheck(checker, walk, edit, after, sites, every,
                                                               lambda c, w: None)))  # fmt: skip
    except incremental.Fallback:
        return None


def agrees(base, before: str, after: str, sites: bool = False) -> bool:
    """Whether the check of `after` from the walk of `before` answered as a whole check does, with and without
    every refusal; False when it fell back to the whole check, which answers by construction."""
    ran = False
    for every in (False, True):
        whole = answer(lambda: compile_program(after, sites, every=every))
        mine = replayed(base, before, after, sites, every)
        assert mine is None or same(mine, whole), (every, mine and mine[:2], whole[:2])
        ran = ran or mine is not None
        if whole[0] == "typed":  # an accepted check is the same with and without `every`
            break
    return ran


def spliced(source: str, span: tuple[int, int, int, bool], body: str) -> str:
    _, start, end, _ = span
    return source[:start] + body + source[end:]


def edited_bodies(walk: incremental.Walk) -> list[str]:
    editable = [name for name, span in walk.spans.items() if span[3]]
    return editable if EVERY else sorted({*editable[:1], *editable[len(editable) // 2 : len(editable) // 2 + 1],
                                          *editable[-1:]})  # fmt: skip


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: str(p.relative_to(ROOT)))
def test_an_edited_body_of_every_example_checks_as_a_whole_check_does(path):
    source = load_project(path).source
    try:
        base = walked(source, sites=True)
    except Diagnostic:
        return  # a refused program keeps no walk
    replays = edits = 0
    for name in edited_bodies(base[1]):
        span = base[1].spans[name]
        for label, edit in EDITS.items():
            body = edit(source[span[1] : span[2]])
            if body is not None:
                edits += 1
                replays += agrees(base, source, spliced(source, span, body), sites=label == "comment")
    assert not edits or replays, f"no edit of {path.name} was checked from its walk"


@pytest.mark.parametrize("label", TARGETED)
def test_each_kind_of_edit_checks_as_a_whole_check_does(label):
    base = walked(PROGRAM)
    name, body = TARGETED[label]
    after = spliced(PROGRAM, base[1].spans[name], body)
    assert agrees(base, PROGRAM, after) == (label not in WHOLE)


def walks(cache: Cache) -> list:
    return [entry for entry in cache.recent() if entry.walked is not None]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: str(p.relative_to(ROOT)))
def test_an_edited_body_of_every_example_parses_from_the_kept_parse_as_a_whole_parse_does(path):
    source = load_project(path).source
    tree = Parser(source).parse()
    spans = incremental.bodies(tree)
    for name, span in spans.items():
        for edit in EDITS.values():
            body = edit(source[span[1] : span[2]]) if span[3] else None
            after = body and spliced(source, span, body)
            if after and (found := incremental.edited(source, after, spans)) is not None:
                assert found.name == name
                try:
                    mine = incremental.spliced(pickle.loads(pickle.dumps(tree)), found, after)
                except incremental.Fallback:
                    continue  # a body that does not parse alone is parsed whole, where the whole parse refuses it
                assert canonical(mine) == canonical(Parser(after).parse()), (name, edit)


def test_a_kept_walk_answers_an_edit_and_the_edit_keeps_one_for_the_next():
    """compiler/compilations.py: an edit is checked from the walk of the source it edits, a refusal keeps no walk, and
    an edit of an edit is checked from the first edit's walk. Every answer is a whole check's."""
    cache = Cache()
    Compilation(PROGRAM, cache=cache).program()
    spans = walks(cache)[0].walked.spans
    added = spliced(PROGRAM, spans["middle"], TARGETED["add a call"][1])
    refused = spliced(PROGRAM, spans["leaf"], TARGETED["refuse a correct program"][1])
    for source, every, name in [(added, False, "middle"), (refused, True, "leaf"), (refused, False, "leaf")]:
        compiled = Compilation(source, every=every, cache=cache)
        assert same(answer(compiled.program), answer(lambda s=source, e=every: compile_program(s, every=e)))
        assert compiled.edit == name
    assert [entry.source for entry in walks(cache)] == [added, PROGRAM]
    assert Compilation(added, cache=cache).emitted() == Compilation(added, cache=Cache()).emitted()
    again = spliced(added, walks(cache)[0].walked.spans["middle"], TARGETED["recursion"][1])
    chained = Compilation(again, cache=cache)
    assert same(answer(chained.program), answer(lambda: compile_program(again))) and chained.edit == "middle"
    assert walks(cache)[0].source == again
    parse = Compilation(PROGRAM, cache=cache)
    assert canonical(parse.parsed()) == canonical(Parser(PROGRAM).parse())  # kept, where the next one is made from
    reparsed = Compilation(added, cache=cache)
    assert canonical(reparsed.parsed()) == canonical(Parser(added).parse()) and reparsed.edit == "middle"


def test_an_edit_outside_one_body_is_checked_whole():
    _, walk = walked(PROGRAM)
    two = PROGRAM.replace("x + 1;", "x + 2;").replace("add_wrap(x, x)", "add_wrap(x, 1)")
    assert incremental.edited(PROGRAM, PROGRAM.replace("x + 1;", "x + 2;"), walk.spans).name == "leaf"
    for before, after in [("fn leaf(x:u64)", "fn leaf(y:u64)"), ("struct Pair { a:u64;", "struct Pair { a:u32;"),
                          ("return x + 1; }\n\nfn noisy", "return x + 1; }\n// between\nfn noisy"),
                          ("}\n\nfn capped", "}  fn later() {}\n\nfn capped"), (PROGRAM, two)]:  # fmt: skip
        assert incremental.edited(PROGRAM, PROGRAM.replace(before, after), walk.spans) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

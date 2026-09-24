"""Every independent refusal of one check: which refusal is kept while the check goes on, what a failed check takes
back, what can still be judged after a refusal, and the record that carries them all.

Without `every` (compile_program) nothing here changes a check: the first refusal ends it. With it, the check of each
declaration runs inside `refusing`. A refusal there is kept, what that check added to the program-wide tables (an
instance, a layout, an implementation it began) is taken back, and the next declaration is checked. The first
refusal is the one a check that stops meets. A later one is reported only when no other refusal can explain it: a
rule that reads the rows of what a function calls is judged only where no refused body is reached, nor a reference
whose row its implementations have not yet joined, and a refusal met
again through what two checks share is said once. Nothing here is mechanically proved.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from itertools import islice
from typing import TYPE_CHECKING

from .effects import audit, fixed_point
from .scope import Scope
from .traits import PENDING, connect_dispatches
from .tree import VOID, Diagnostic, Function

if TYPE_CHECKING:
    from .checking import Checker

FATAL = {"E-AST-LIMIT", "E-EXPANSION-LIMIT", "E-EFFECT-LIMIT", "E-INTERNAL"}  # after one, nothing more is judged
FURTHER = 20  # further refusals one record lists; it counts the rest
# The program-wide tables a check adds to, in insertion order, so what a failed one added can be taken back.
TABLES = ("fs", "layouts", "kinds", "frees", "early", "impls", "bounds", "folded", "local_effects", "calls", "checks",
          "discharges", "call_edges", "resources", "numerics", "hostish", "alternatives", "selected")  # fmt: skip
LISTS = ("sites", "lane_calls", "fn_sites", "dispatches", "unchecked")


class Stopped(Exception):
    """A check that reports every refusal met something after which it can judge nothing more."""


def refusing(c: Checker, about: str, rows: bool = False):
    """Around the check of one declaration. Where every refusal is reported, one raised inside is kept, what the check
    added to the program-wide tables is taken back, and the check goes on. `rows` marks a rule that reads the rows of
    what `about` calls: its refusal may follow from a refusal of something it reaches."""
    return nullcontext() if c.refusals is None else kept(c, about, rows)


@contextmanager
def kept(c: Checker, about: str, rows: bool):
    assert c.refusals is not None
    mark = None if rows else marked(c)
    try:
        yield
    except Stopped:
        raise
    except Exception as error:
        refusal = isinstance(error, Diagnostic) and error.data["code"] not in FATAL
        if not refusal and not c.refusals:
            raise  # the first thing that ends the check ends it as it always has
        if not refusal:
            raise stop(c, error) from error
        assert isinstance(error, Diagnostic)
        d = error.data  # A refusal met while a type or an implementation was open is about it wherever it is met.
        shared = mark is not None and opened(c, mark)
        key = (
            (d["code"], d["message"]) if shared else (d["code"], d["message"], d.get("module"), d["line"], d["column"])
        )
        if mark is not None:
            rollback(c, mark)
        c.refusals.append((about, rows, error, key))


def stop(c: Checker, error: Exception) -> Stopped:
    """What ends the check after a refusal: a limit, or a fault that `abandoned` keeps for whoever tests the check."""
    fault = not isinstance(error, Diagnostic) or error.data["code"] == "E-INTERNAL"
    c.abandoned = error if fault else None
    return Stopped()


def marked(c: Checker) -> tuple:
    return ([len(getattr(c, n)) for n in TABLES], [len(getattr(c, n)) for n in LISTS], len(c.p.functions),
            set(c.address_taken))  # fmt: skip


def opened(c: Checker, mark: tuple) -> bool:
    """Was a type being defined, or a trait's implementation decided, when the check failed?"""
    layouts, impls = mark[0][TABLES.index("layouts")], mark[0][TABLES.index("impls")]
    return None in islice(c.layouts.values(), layouts, None) or any(
        v is PENDING for v in islice(c.impls.values(), impls, None)
    )


def rollback(c: Checker, mark: tuple):
    """Take back what a failed check added: the instances it made, the layouts and implementations it began."""
    tables, lists, functions, taken = mark
    c.signed.difference_update(list(c.fs)[tables[0] :])
    for name, count in zip(TABLES, tables, strict=True):
        table = getattr(c, name)
        for key in list(table)[count:]:
            del table[key]
    for name, count in zip(LISTS, lists, strict=True):
        del getattr(c, name)[count:]
    del c.p.functions[functions:]
    c.address_taken.intersection_update(taken)
    c.s, c.reaching, c.borrowed = Scope(Function("", [], VOID, [])), 0, []


def rest(c: Checker):
    """After a refusal: the effect ceilings and the operand order of every function no refusal reaches. A row that
    reaches a refused body, or an indirect call that may reach one, is never judged."""
    try:
        clean = independent(c)
        audit(c, fixed_point(c, clean), clean)
    except Stopped:
        raise
    except Exception as error:  # a limit met, or a fault: what was found so far is still the answer
        raise stop(c, error) from error


def independent(c: Checker) -> set[str]:
    """The functions whose rows reach no refused body: every one whose check finished, but those that call one that
    did not, those whose row an implementation joins, those that reach them, and those that make an indirect call
    that may reach any of these. The others join `unjudged`."""
    connect_dispatches(c)
    known, callers = set(c.local_effects), dict[str, set[str]]()
    for n in known:
        for q in c.calls[n]:
            callers.setdefault(q, set()).add(n)
    todo = [n for n in known if any(q not in known for q in c.calls[n])]
    todo += sorted(references(c) & known)
    indirect = [n for n in known if "indirect_call" in c.local_effects[n]]
    if any(g not in known for g in c.address_taken):
        todo += indirect
    while todo:
        n = todo.pop()
        if n not in c.unjudged:
            c.unjudged.add(n)
            todo += callers.get(n, ())
        if not todo and any(g not in known or g in c.unjudged for g in c.address_taken):
            todo = [n for n in indirect if n not in c.unjudged]  # a function value may be one without a verdict
    return known - c.unjudged


def references(c: Checker) -> set[str]:
    """Every function an implementation names. A reference's row joins its implementations' only once the rules of
    implementations have run (implementations.joined), which a check with a refusal never reaches."""
    named = set()
    for f in c.p.functions:
        if f.implements is not None:
            try:
                with c.within(f.module):
                    named.add(c.qualify(f.implements.reference, c.fs))
            except Diagnostic:  # a reference it may not name is the implementation rules' refusal, never reached
                continue
    return named - {None}


def reaches(c: Checker, name: str, targets: set[str]) -> bool:
    seen, todo = {name}, list(c.calls.get(name, ()))
    while todo:
        n = todo.pop()
        if n in targets:
            return True
        if n not in seen:
            seen.add(n)
            todo += c.calls.get(n, ())
    return False


def order(d: dict) -> tuple:
    """Where a refusal is, for source order: the program's own lines first, then each library module's."""
    return bool(d.get("module")), d.get("module", ""), d.get("line", 0), d.get("column", 0)


def verdict(c: Checker) -> Diagnostic:
    """The first refusal exactly as the check met it, carrying in source order every further refusal no other refusal
    can explain, what the cap left out, and how many functions of the program got no verdict."""
    assert c.refusals
    refused = {about for about, *_ in c.refusals}
    (about, _, first, key), further = c.refusals[0], []
    told, seen = {about}, {key}
    for about, rows, error, key in c.refusals[1:]:
        if key in seen or (rows and reaches(c, about, refused - {about})):
            c.unjudged.add(about)  # met again through what both use, or perhaps only because of another
        else:
            told.add(about)
            seen.add(key)
            further.append(error.data)
    further.sort(key=order)
    lost = [n for n in c.unjudged - told if (f := c.fs.get(n)) and f.module not in c.p.sources]
    lost = [n for n in lost if c.fs[n].bindings or not c.fs[n].generics]  # a template is judged per instance
    first.data.update({"further": further[:FURTHER]} if further else {})
    first.data.update({"further_omitted": len(further) - FURTHER} if len(further) > FURTHER else {})
    first.data.update({"not_judged": len(lost)} if lost else {})
    first.abandoned = c.abandoned
    return first

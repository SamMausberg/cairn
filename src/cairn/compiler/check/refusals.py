"""Every independent refusal of one check: which refusal is kept while the check goes on, what a failed check takes
back, what can still be judged after a refusal, and the record that carries them all.

Without `every` (compile_program) nothing here changes a check: the first refusal ends it. With it, the check of each
declaration runs inside `refusing`. A refusal there is kept, what that check added to the program-wide tables (an
instance, a layout, an implementation it began) is taken back, and the next declaration is checked. Inside a body the
walk checks, a refused statement is taken back the same way and the rest of its block is checked (`statement`), so
independent faults in one function come back from one check. The first refusal is the one a check that stops meets. A later one is reported only when no other refusal can explain it: a
rule that reads the rows of what a function calls is judged only where no refused body is reached, nor a reference
whose row its implementations have not yet joined, a refusal met
again through what two checks share is said once, and a statement that names what a refused statement bound or
touched is not judged again. A limit or an internal fault met after the first refusal ends the
check there, and the record names it under `further_stopped`. Nothing here is mechanically proved.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any

from ..syntax.tree import VOID, Arm, Diagnostic, Expr, Function, Stmt
from .effects import audit, fixed_point
from .scope import SCOPED, Scope
from .traits import PENDING, connect_dispatches

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


class Refused(Exception):
    """The body the walk checked had a refused statement: each refusal of it is kept already, and the body is taken back
    as a refused one is."""


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
    except Refused:
        if mark is not None:
            rollback(c, mark)
    except Exception as error:
        refusal = isinstance(error, Diagnostic) and error.data["code"] not in FATAL
        if not refusal and not c.refusals:
            raise  # the first thing that ends the check ends it as it always has
        if not refusal:
            raise stop(c, error) from error
        assert isinstance(error, Diagnostic)
        key = keyed(c, error, mark)
        if mark is not None:
            rollback(c, mark)
        c.refusals.append((about, rows, error, key))


def keyed(c: Checker, error: Diagnostic, mark: tuple | None) -> tuple:
    """What one refusal is said once under. A refusal met while a type or an implementation was open is about it
    wherever it is met."""
    d = error.data
    if mark is not None and opened(c, mark):
        return d["code"], d["message"]
    return d["code"], d["message"], d.get("module"), d["line"], d["column"]


@dataclass
class Walked:
    """The body the walk is checking, where a refused statement does not end the check: whether one was refused, and
    the locals whose value a refused statement left unknown: those it binds, those it assigns whole, in the blocks it
    holds too, and those the condition of an `if` names, which an arm that leaves would have taught the rest of the
    block."""

    f: Function
    refused: bool = False
    unknown: set[str] = field(default_factory=set)


@contextmanager
def walking(c: Checker, f: Function):
    """Around the check of one body the walk checks. Where every refusal is reported, its refused statements are kept
    as `statement` meets them, and the body then ends as a refused one. A refusal met where the body ends, after one of
    them, is not reported: whether a path returns, or a value is still held, is unknown once a statement was taken
    back."""
    if c.refusals is None:
        yield
        return
    outer, c.walked = c.walked, Walked(f)
    try:
        yield
        refused = c.walked.refused
    except Diagnostic as error:
        if error.data["code"] in FATAL or not c.walked.refused:
            raise
        refused = True
    finally:
        walked, c.walked = c.walked, outer
    if refused:
        raise Refused(walked.f.name)


def statement(c: Checker, s: Stmt) -> Any:
    """One statement of a block, as `Checker.block` walks it. Where every refusal is reported and the statement is one
    of the body the walk is checking, a refusal of it is kept and the statement is taken back: what it bound, moved,
    lent or learned, and what it added to the program-wide tables. The block goes on after it: a `return` still
    returns and a `break` or `continue` still jumps, and anything else falls through. A later statement that names a
    local whose value it left unknown (`Walked`) is refused, if at all, without being reported, since that refusal may
    only follow from this one. An owner, a linear value, a group or a ticket it names may have been consumed, so none is
    held to being consumed on every path (`Scope.unsure`); a use of one is judged as before the statement. A statement
    inside a lane, a cooperative region or a closure, or in a generic instance checked where it is called, is refused
    whole with the statement that holds it, since their rules judge the whole of what they hold once it is checked."""
    walked = c.walked
    if walked is None or c.s.f is not walked.f or c.lanes or c.coop or c.closure:
        return c.stmt(s)
    before = Before.of(c)
    try:
        return c.stmt(s)
    except Diagnostic as error:
        if error.data["code"] in FATAL:
            raise
        c.placed(walked.f, error)
        assert c.refusals is not None
        names, assigned = mentioned(s)
        if not walked.refused or not names & walked.unknown:
            c.refusals.append((walked.f.name, False, error, keyed(c, error, before.mark)))
        walked.refused = True
        walked.unknown |= bound(s) | assigned | (mentioned(s.exprs[0])[0] if s.tag == "if" else set())
        before.restore(c)
        c.unsure |= {n for n in names if n in c.env and c.kind(c.env[n].ty) != "copy"}
        return {"return": True, "break": "jump", "continue": "jump"}.get(s.tag, False)


# How `Before` holds each field of a function's Scope. A copy of what a statement both adds to and takes from; the
# length of what it only appends to; and the object itself for the rest: what is fixed for the function or set whole,
# and what a statement only adds to on the way to the function's row, which a refused body never gets.
COPIED = frozenset({"env", "moved", "deferred", "holds", "unsure", "leases", "before", "values"})
APPENDED = frozenset({"facts", "touched"})
KEPT = frozenset({"f", "tenv", "module", "effects", "callset", "counts", "discharged", "cited", "loop_depth",
                  "unsafe_depth", "device_depth", "lanes", "coop", "closure", "spawning"})  # fmt: skip
if COPIED | APPENDED | KEPT != SCOPED:  # a field no class names would be taken back as the object it is, unseen
    raise TypeError(f"refusals.Before does not say how to hold {sorted(SCOPED - COPIED - APPENDED - KEPT)}.")


@dataclass
class Before:
    """What the checker knew before one statement of a body: the function's scope, the field path and the last call's
    loans, and how far each program-wide table had grown."""

    scope: Scope
    fields: dict[str, Any]
    lengths: dict[str, int]
    reaching: int
    borrowed: list[tuple[str, str]]
    mark: tuple

    @classmethod
    def of(cls, c: Checker) -> Before:
        fields = dict(vars(c.s))
        fields.update((n, fields[n].copy()) for n in COPIED)
        lengths = {n: len(fields[n]) for n in APPENDED if fields[n] is not None}
        return cls(c.s, fields, lengths, c.reaching, list(c.borrowed), marked(c))

    def restore(self, c: Checker) -> None:
        taken_back(c, self.mark)
        vars(self.scope).update(self.fields)
        for n, length in self.lengths.items():
            del self.fields[n][length:]
        c.s, c.reaching, c.borrowed = self.scope, self.reaching, self.borrowed


def mentioned(node: Stmt | Expr) -> tuple[set[str], set[str]]:
    """Every name a statement or an expression binds, reads or writes, or calls a function on as `v.f(..)`, and every
    name it assigns, in the blocks and closures it holds too."""
    names: set[str] = set()
    assigned: set[str] = set()
    todo: list[Any] = [node]
    while todo:
        x = todo.pop()
        if isinstance(x, Stmt):
            names |= bound(x)
            assigned |= {x.exprs[0].val} if x.tag == "assign" and x.exprs[0].tag == "name" else set()
            todo += [*x.exprs, *x.other_names, *x.body, *x.other, *x.arms]
        elif isinstance(x, Arm):
            names.add(x.binder)
            todo += x.body
        elif isinstance(x, Expr):
            names |= {x.val} if x.tag == "name" else {x.val.split(".")[0]} if x.tag == "call" else set()
            todo += x.args
            if isinstance(x.ref, Function):  # a closure
                todo += x.ref.body
            elif isinstance(x.ref, Stmt):  # a region spawned as a task
                todo.append(x.ref)
    return names - {"", "_"}, assigned


def bound(s: Stmt) -> set[str]:
    """The locals one statement itself binds: a `let`'s or a loop's name, the fields a record is taken apart into, and
    an `asm` statement's outputs."""
    if s.tag == "unpack":  # its name is the record's type
        return {e.val for e in s.other_names}
    return {s.name, s.binder, *(name for name, *_ in (s.assembly.outputs if s.assembly else ()))} - {"", "_"}


def stop(c: Checker, error: Exception) -> Stopped:
    """What ends the check after a refusal: a limit, or a fault that `abandoned` keeps for whoever tests the check.
    `stopped` names it for the record: the limit's code, or the fault's class."""
    fault = not isinstance(error, Diagnostic) or error.data["code"] == "E-INTERNAL"
    c.abandoned = error if fault else None
    c.stopped = error.data["code"] if isinstance(error, Diagnostic) else type(error).__name__
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
    taken_back(c, mark)
    c.s, c.reaching, c.borrowed = Scope(Function("", [], VOID, [])), 0, []


def taken_back(c: Checker, mark: tuple):
    """What the program-wide tables, the program's functions and the addresses taken held at `mark`."""
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
    can explain, what the cap left out, how many functions of the program got no verdict, and what ended the check
    early, when something did."""
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
    first.data.update({"further_stopped": c.stopped} if c.stopped else {})
    first.abandoned = c.abandoned
    return first

"""A function's plan as source text: its items in the checker's order, where it is written, and replacing it.

A plan may be written anywhere its function's name resolves: in the function's own module under its short name, or
in another module under a name that module resolves to it. `Placement` finds every plan the checker resolves to one
function, removes them, and writes the new plan right after the function's declaration under the name its own module
gives it, so a function of any module of a project takes a plan, and a function of the same short name elsewhere
keeps its own. `write_plan` makes the same edits in the files of a project, all of them or none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_program
from ..compiler.concurrency import PLAN_ITEMS, planned_functions
from ..compiler.lexing import lex
from ..compiler.tree import Function, fail

Plan = tuple[tuple[str, int], ...]  # the items a plan sets, in the checker's order, zeros left out
KEEP = object()  # a selection left as it is


def written(items: dict[str, int]) -> Plan:
    return tuple((k, items[k]) for k in PLAN_ITEMS if items.get(k))


def text(name: str, plan: Plan) -> str:
    return f"plan {name} {{ {' '.join(f'{k} {v};' for k, v in plan)} }}" if plan else ""


def shown(name: str, plan: Plan) -> str:
    return text(name, plan) or f"(no plan for {name})"


def selecting(name: str, use: str) -> str:
    """`plan f use g;`, or `plan f use g[8];` for an instance: the one name a candidate that selects an implementation
    has wherever it is recorded, by `cairn tune`, `cairn validate` or an implementation session."""
    return f"plan {name.rsplit('.', 1)[-1]} use {use.rsplit('.', 1)[-1]};"


def replanned(source: str, name: str, plan: str) -> str:
    """`source` with `name`'s plan replaced by `plan`, or its plan removed when `plan` is empty: a textual edit for a
    source of one module, where a plan is found by the name it is written under. `Placement` resolves names."""
    found = re.compile(rf"^\s*plan\s+{re.escape(name)}\s*\{{[^}}]*\}}[ \t]*\n?", re.M)
    stripped = found.sub("", source)
    return stripped if not plan else stripped.rstrip("\n") + "\n\n" + plan + "\n"


def short(f: Function) -> str:
    """The name `f`'s own module calls it by."""
    return f.name.removeprefix(f.module + ".") if f.module else f.name


def function(program: Any, symbol: str, what: str = "plan") -> Function:
    """The one written function of the program's own modules that `symbol` names by its qualified name; a near miss
    is named in the refusal."""
    found = [f for f in program.functions if symbol in (f.name, f.source_name) and not f.extern]
    if len(found) == 1 and not found[0].module.startswith("std."):
        return found[0]
    near = sorted(f.name for f in program.functions if f.name.rsplit(".", 1)[-1] == symbol.rsplit(".", 1)[-1]
                  and not f.module.startswith("std.") and f.name != symbol)  # fmt: skip
    hint = f"; a function of a named module is named with its module: {', '.join(near[:4])}" if near else ""
    fail("E-SYMBOL", f"No function {symbol} of this program to {what}{hint}.")


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


def line_span(source: str, start: int, end: int) -> tuple[int, int]:
    """[start, end) widened to its whole line when nothing but blanks shares the line with it."""
    head = source.rfind("\n", 0, start) + 1
    tail = len(source) if (nl := source.find("\n", end)) < 0 else nl
    if source[head:start].strip() or source[end:tail].strip():
        return start, end
    return head, min(tail + 1, len(source))


class Placement:
    """Where the plan of one function of one checked source is written: the plans the checker resolves to it, the
    `plan f use g;` that selects one of its implementations, and the point right after its declaration. `apply` gives
    the source with a given plan, and a given selection or none, in their place."""

    def __init__(self, source: str, symbol: str):
        self.source = source
        program, checker, _ = compile_program(source)
        self.f = function(program, symbol)
        self.name = short(self.f)
        tokens = lex(source)
        self.removed: list[Edit] = []
        for module, name, _, token in program.plans:
            if self.f.name not in {g.name for g in planned_functions(checker, module, name)}:
                continue
            end = next(t.end for t in tokens if t.start > token.start and t.s == "}")  # items are `name N;`
            self.removed.append(Edit(*line_span(source, token.start, end), ""))
        self.unselected: list[Edit] = []  # every `plan f use g;` the checker resolves to this function
        for module, name, _, token in getattr(program, "selections", ()):
            with checker.within(module):
                if checker.qualify(name, checker.fs) != self.f.name:
                    continue
            end = next(t.end for t in tokens if t.start > token.start and t.s == ";")
            self.unselected.append(Edit(*line_span(source, token.start, end), ""))

    def edits(self, plan: Plan, use: Any = KEEP) -> list[Edit]:
        """The edits that put `plan` in place of every plan the function has now, latest first; and, unless `use` is
        KEEP, a selection of the implementation `use` (a name its module resolves) in place of the one it has, or
        none when `use` is None."""
        written_ = "\n" + text(self.name, plan) if plan else ""
        removed = list(self.removed)
        if use is not KEEP:
            removed += self.unselected
            written_ += f"\nplan {self.name} use {use};" if use else ""
        added = [Edit(self.f.end, self.f.end, written_)] if written_ else []
        return sorted([*removed, *added], key=lambda e: (e.start, e.end), reverse=True)

    def apply(self, plan: Plan, use: Any = KEEP) -> str:
        out = self.source
        for e in self.edits(plan, use):
            out = out[: e.start] + e.text + out[e.end :]
        return out


def contract(source: str, symbol: str) -> dict[str, Any]:
    """What every plan of `symbol` must preserve, as a candidate's identity names it: the function's signature and
    effect row, which the checker holds a plan to, and its body, which a plan cannot touch (`agent/history.py`)."""
    from ..agent.projection import signature

    program, _, receipts = compile_program(source)
    f = function(program, symbol)
    return {"kind": "plan", "signature": signature(f), "effects": receipts[f.name]["effects"]}


def placed(source: str, symbol: str, plan: Plan) -> str:
    """`source` with `symbol`'s plan replaced by `plan`, or removed when `plan` is empty, by resolution."""
    return Placement(source, symbol).apply(plan)


def write_plan(manifest: Any, symbol: str, chosen: dict[str, Any], use: Any = KEEP) -> str:
    """Write `chosen` as `symbol`'s plan into the files of the project, and return the path of the file that
    declares the function, where the plan is written.

    The plans the checker resolves to the function are removed from whichever file holds them, and the new one is
    written after the declaration under the name its own module gives it. Unless `use` is KEEP, the selection of an
    implementation is replaced the same way: by `plan f use g;` for `use` g, or by none for None. The files are
    written only when the whole project still checks with every edit in place, and never a vendored file."""
    from ..compiler.cairnc import compile_source
    from ..projects.project import ProjectError, contained_file, load_project

    project = load_project(manifest)
    nowhere = f"{symbol} is not declared in a file of this project, so its plan has nowhere to go."
    try:
        where = Placement(project.source, symbol)
    except Diagnostic as error:
        raise ProjectError(nowhere) from error
    unit = project.unit_at(where.f.line)
    if unit is None or unit.path in project.vendored_units:
        raise ProjectError(nowhere)
    starts = [0, *(i + 1 for i, ch in enumerate(project.source) if ch == "\n")]
    files: dict[str, tuple[Path, int, str]] = {}  # unit path -> (file, where it starts in the source, its text)
    for edit in where.edits(written(chosen), use):
        owner = project.unit_at(project.source.count("\n", 0, edit.start) + 1)
        if owner is None or owner.path in project.vendored_units:
            raise ProjectError(f"A plan of {symbol} is written outside this project's own files; nothing was written.")
        path = contained_file(project.root, owner.path, ".cairn")
        at = starts[owner.first_line - 1]
        _, _, body = files.get(owner.path, (path, at, path.read_text(encoding="utf-8")))
        files[owner.path] = (path, at, body[: edit.start - at] + edit.text + body[edit.end - at :])
    given = {path.resolve(): body for path, _, body in files.values()}
    try:
        compile_source(load_project(manifest, given=given).source)
    except Diagnostic as error:
        raise ProjectError(f"The plan chosen for {symbol} would leave the project refused ({error.data['code']}: "
                           f"{error.data['message']}); nothing was written.") from error  # fmt: skip
    for path, _, body in files.values():
        path.write_text(body, encoding="utf-8")
    return unit.path

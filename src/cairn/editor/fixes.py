"""Code actions: a quick fix for a diagnostic whose repair is one edit that nobody has to choose.

Three are offered. A `match` missing variants gets one empty arm for each (`E-MATCH-COVERAGE`), spelled as its
other arms spell theirs. An assignment to a `let` local makes that binding `let mut` (`E-IMMUTABLE`). A name of a
packaged module used without its import gets the `import` (`E-CALLEE`, `E-TYPE`). Nothing here widens a
contract: an effect ceiling, a borrow mode or a signature is never changed for the program's sake.
"""

from __future__ import annotations

import re

from ..agent.projection import local
from ..compiler.modules import library_path, link
from ..compiler.syntax import IDENT, Parser, Program
from ..compiler.tree import Diagnostic
from .document import Document, declarations, flatten

UNKNOWN_TYPE = re.compile(r"Unknown type ([a-z_]\w*)\.")


def edit(doc: Document, start: int, end: int, text: str) -> dict:
    return {"range": doc.span(start, end), "newText": text}


def parsed(doc: Document) -> Program | None:
    """The buffer's declarations with the library it imports, when it parses: a checker error still does."""
    try:
        return link(Parser(doc.text).parse())
    except Diagnostic:
        return None


def fresh(doc: Document, want: str) -> str:
    """A binder name nothing in the document uses, so the arm it goes into cannot shadow (`E-SHADOW`)."""
    taken = {t.s for t in doc.code}
    return next(n for n in (want, *(f"{want}{k}" for k in range(2, 99))) if n not in taken)


def missing_arms(doc: Document, error: dict, at: int) -> list[tuple[str, list[dict]]]:
    cs, p = doc.code, parsed(doc)
    missing = error.get("missing_variants") or []
    if p is None or at < 0 or cs[at].s != "match" or not missing:
        return []
    opening = next((k for k in range(at + 1, len(cs)) if cs[k].s == "{"), -1)
    if opening < 0 or cs[opening].pair < 0:
        return []
    closing = cs[opening].pair
    first = opening + 1 if opening + 1 < closing else -1  # an existing arm shows how this match spells a variant
    spelled = ""
    if first > 0:
        k = first
        while k + 2 < closing and cs[k + 1].s == ".":
            k += 2
        spelled = "".join(t.s for t in cs[first:k]) if k > first else ""
    arms = []
    for full in missing:
        sum_name, variant = full.rsplit(".", 1)
        payload = dict(p.sums.get(sum_name, [])).get(variant)
        head = spelled if first > 0 else local(sum_name) + "."
        binder = f"({fresh(doc, variant.lower())})" if payload is not None else ""
        arms.append(f"{head}{variant}{binder} => {{ }}")
    line_start = doc.text.rfind("\n", 0, cs[closing].start) + 1
    if doc.text[line_start : cs[closing].start].strip():  # `match x { A => { } }` on one line
        text = " ".join(arms) + " "
        return [
            (f"Add the missing arm{'s' * (len(arms) > 1)}", [edit(doc, cs[closing].start, cs[closing].start, text)])
        ]
    arm_line = doc.text.rfind("\n", 0, cs[first].start) + 1 if first > 0 else line_start
    indent = (
        re.match(r"[ \t]*", doc.text[arm_line:]).group()
        if first > 0
        else doc.text[line_start : cs[closing].start] + "  "
    )
    text = "".join(f"{indent}{arm}\n" for arm in arms)
    return [(f"Add the missing arm{'s' * (len(arms) > 1)}", [edit(doc, line_start, line_start, text)])]


def make_mutable(doc: Document, error: dict, at: int) -> list[tuple[str, list[dict]]]:
    cs = doc.code
    name = str(error.get("message", "")).split(" ", 1)[0]
    if at < 0 or cs[at].s != name:
        return []
    around = [d for d in flatten(declarations(cs, 0, len(cs))) if d["head"] <= cs[at].start <= d["tail"]]
    if not around:
        return []
    d = min(around, key=lambda d: d["tail"] - d["head"])
    binder = [
        k for k, t in enumerate(cs) if d["head"] <= t.start < cs[at].start and t.s == "let" and cs[k + 1].s == name
    ]
    if len(binder) != 1:
        return []
    return [(f"Declare {name} as let mut", [edit(doc, cs[binder[0] + 1].start, cs[binder[0] + 1].start, "mut ")])]


def add_import(doc: Document, error: dict, at: int) -> list[tuple[str, list[dict]]]:
    cs = doc.code
    named = UNKNOWN_TYPE.search(str(error.get("message", "")))
    module = named.group(1) if named else cs[at].s if at >= 0 and at + 1 < len(cs) and cs[at + 1].s == "." else ""
    if not IDENT.fullmatch(module) or library_path(f"std.{module}") is None:
        return []
    written = [k for k, t in enumerate(cs) if t.s == "import"]
    if any(cs[k + 1].s == "std" and k + 3 < len(cs) and cs[k + 3].s == module for k in written):
        return []
    anchor = [k for k, t in enumerate(cs) if t.s in {"import", "module"}]
    end = next((k for k in range(anchor[-1], len(cs)) if cs[k].s == ";"), -1) if anchor else -1
    after = doc.text.find("\n", cs[end].end) if end >= 0 else -1
    offset = 0 if end < 0 else after + 1 if after >= 0 else len(doc.text)
    return [(f"Import std.{module}", [edit(doc, offset, offset, f"import std.{module};\n")])]


FIXES = {"E-MATCH-COVERAGE": missing_arms, "E-IMMUTABLE": make_mutable, "E-CALLEE": add_import, "E-TYPE": add_import}


def code_actions(doc: Document, uri: str, span: dict) -> list[dict]:
    """The quick fixes for each of the buffer's diagnostics whose range the requested range touches."""
    lo, hi = doc.offset(span.get("start") or {}), doc.offset(span.get("end") or {})
    actions = []
    for error, shown in zip(doc.errors, doc.diagnostics, strict=False):
        start, end = doc.offset(shown["range"]["start"]), doc.offset(shown["range"]["end"])
        if error["code"] not in FIXES or hi < start or lo > end:
            continue
        at = next((k for k, t in enumerate(doc.code) if t.start == start), -1)
        actions += [
            {
                "title": title,
                "kind": "quickfix",
                "diagnostics": [shown],
                "isPreferred": True,
                "edit": {"changes": {uri: edits}},
            }
            for title, edits in FIXES[error["code"]](doc, error, at)
        ]
    return actions

"""The document model of `cairn lsp`: one open buffer, its positions, its diagnostics, and what its
current tokens say about the cursor.

A buffer being typed usually does not compile, so every feature reads its context from the current tokens
and its meaning from the last analysis that succeeded (`Document.good`), matching the two by name: offsets
of an older analysis are stale after an edit, but the name of a function and of its locals is not.
Positions are UTF-16 code units, as the protocol requires.
"""

from __future__ import annotations

import bisect
import re
from typing import Any

from ...agent.hosts.edits import explain
from ...agent.skill import card_link
from ...compiler import compilations
from ...compiler.cairnc import Diagnostic
from ...compiler.syntax.modules import library_path
from ...compiler.syntax.parser import IDENT, RESERVED, Program
from ..formatting import CLOSERS, OPENERS, Item, roles, scan

DECLARATIONS = {"fn": 12, "test": 12, "struct": 23, "enum": 10, "trait": 11, "const": 14, "impl": 5}

MODIFIERS = {"pub", "linear", "extern"}

BINDERS = {"let", "reg", "buffer", "stack", "for", "parallel", "each"}  # `<word> [mut] name`

# Positions and analysis --------------------------------------------------------------------------


def line_starts(text: str) -> list[int]:
    return [0, *(m.end() for m in re.finditer("\n", text))]


def _units(s: str) -> int:
    """UTF-16 code units in `s`; astral characters take two."""
    return len(s) + sum(ord(c) > 0xFFFF for c in s)


class Document:
    """One open buffer with its scan, its checker sites and its diagnostics.

    `good` is the last analysis that compiled: this one, or the one an earlier edit left behind. A document of a
    project is analysed `within` it: (the project's combined source, where this text starts in it, and a function
    naming the file and line of a combined line), so what it imports from the project's other files resolves.
    """

    def __init__(self, text: str, previous: Document | None = None, analyse: bool = True, within: Any = None):
        self.text, self.within = text, within
        self.starts = line_starts(text)
        self.code = roles([t for t in scan(text) if not t.comment])
        self.program: Program | None = None
        self._scopes: list[tuple[int, str]] | None = None  # where each module this text sits in begins, once asked
        self.rows: dict[str, list[str]] = {}  # function -> its effect row, for hovers
        self.errors: list[dict] = []  # each diagnostic's record with every detail it carries, for code actions
        self.sites, self.diagnostics = self._analyse() if analyse else ([], [])
        self.good: Document | None = self if self.program is not None else previous.good if previous else None

    def position(self, offset: int) -> dict:
        offset = max(0, min(offset, len(self.text)))
        line = bisect.bisect_right(self.starts, offset) - 1
        return {"line": line, "character": _units(self.text[self.starts[line] : offset])}

    def offset(self, position: dict) -> int:
        line = max(0, min(int(position.get("line", 0)), len(self.starts) - 1))
        stop = self.starts[line + 1] - 1 if line + 1 < len(self.starts) else len(self.text)
        want, i = int(position.get("character", 0)), self.starts[line]
        while i < stop and want > 0:
            want -= 2 if ord(self.text[i]) > 0xFFFF else 1
            i += 1
        return i

    def span(self, start: int, end: int) -> dict:
        return {"start": self.position(start), "end": self.position(end)}

    def scopes(self) -> list[tuple[int, str]]:
        """Where each module this text sits in begins, as (offset in this text, module), the first at -1: the
        module in force where this text starts. A project's file that opens no module continues the one the file
        before it opened, as the compiler reads the combined source, so the answer is the compiler's own parse
        when this analysis compiled; for a buffer that does not, the same rule is read from the source's tokens."""
        if self._scopes is None:
            start = self.within[1] if self.within else 0
            if self.program is not None:  # the library's modules were parsed from their own files: not ours
                opened = [(at - start, m) for at, m in self.program.scopes if m not in self.program.sources]
            else:
                source = self.within[0] if self.within else self.text
                cs = roles([t for t in scan(source) if not t.comment])
                opened = [(t.start - start, ahead(cs, i + 1)) for i, t in enumerate(cs) if t.s == "module"]
            before = [m for at, m in opened if at < 0]
            self._scopes = [(-1, before[-1] if before else ""), *((at, m) for at, m in opened if at >= 0)]
        return self._scopes

    def module_at(self, offset: int) -> str:
        """The module whose declarations surround `offset`; the root module is ""."""
        opened = self.scopes()
        return opened[bisect.bisect_left([at for at, _ in opened], offset) - 1][1]

    def modules(self) -> list[str]:
        """The module of every token of `code`, a `module` line's own tokens already in the module it opens."""
        opened = self.scopes()
        starts = [at for at, _ in opened]
        return [opened[bisect.bisect_right(starts, t.start) - 1][1] for t in self.code]

    def _analyse(self) -> tuple[list[dict], list[dict]]:
        """The buffer's sites and diagnostics; its program and effect rows when it compiles."""
        source, start = (self.within[0], self.within[1]) if self.within else (self.text, 0)
        try:
            program, checker, receipts = analysis(source)
        except Diagnostic as error:  # the first refusal on every file of a project, each further one on its own
            first = self._report(error)
            further = (self._report(Diagnostic.of(d), anywhere=False) for d in error.data.get("further", []))
            reported = [r for r in (first, *further) if r is not None]
            self.errors = [d for _, d in reported]
            return [], [shown for shown, _ in reported]
        except Exception as error:  # A compiler failure is reported, never raised at the client.
            return [], [problem(self.span(0, 0), "E-INTERNAL", f"{type(error).__name__}: {error}")]
        self.program = program
        self.rows = {n: r["effects"] for n, r in receipts.items()}
        linked = tuple(module + "." for module in program.sources)
        sites = [
            {**s, "start": s["start"] - start, "end": s["end"] - start}
            for s in checker.sites
            if s["end"] > s["start"] >= start
            and s["end"] <= start + len(self.text)
            and not s["symbol"].startswith(linked)
        ]
        return sites, []

    def _report(self, error: Diagnostic, anywhere: bool = True) -> tuple[dict, dict] | None:
        """One refusal as an LSP diagnostic, beside its record for code actions. A refusal in another file of the
        project, or in a library module, is said at the top of this one with where it is, or with `anywhere` false
        not at all."""
        d = explain(error, self.within[0] if self.within else self.text, host=False)
        line, column = int(d.get("line") or 0), int(d.get("column") or 0)
        if not anywhere and d.get("module"):
            return None
        if self.within and line > 0:  # a line of the project: this file's own, or another file's, said where
            first = self.within[0].count("\n", 0, self.within[1])
            if d.get("module") or not first < line <= first + len(self.starts):
                if not anywhere:
                    return None
                file, at = (library_path(d["module"]), line) if d.get("module") else self.within[2](line)
                where = f"{file}:{at}"
                d = {**d, "message": f"{where}: {d['message']}", "line": 0, "column": 0}
                line = column = 0
            else:
                line -= first
                d = {**d, "line": line}
        start = end = 0
        if line > 0:
            start = min(self.starts[min(line, len(self.starts)) - 1] + max(column - 1, 0), len(self.text))
            end = next((t.end for t in self.code if t.start == start), start)
        hint, card = d.get("repair_hint"), d.get("card")
        shown = problem(self.span(start, end), d["code"], d["message"] + ("\n" + hint if hint else ""))
        if card:  # the card that states the rule, read where the skill keeps it
            shown["codeDescription"] = {"href": card_link(card)}
        return {**shown, "data": {k: d[k] for k in ("code", "repair_hint", "card", "source_line") if k in d}}, d


_LAST: list[Any] = []  # the one source analysed last, and its answer: the open files of a project share it


def analysis(source: str) -> tuple[Any, Any, Any]:
    """`compile_program` with its sites, once per distinct source in this process (compiler/compilations.py), so
    every open file of a project reads one analysis and a text typed again is not checked again; a refusal is raised
    again for each of them."""
    if not _LAST or _LAST[0] != source:
        try:
            answer: Any = compilations.program(source, sites=True, every=True)
            # The checker takes each template out of the program once it has checked it; an editor wants every
            # declared function back, and none of the instances it made along the way.
            program, checker, _ = answer
            program.functions = [f for f in checker.fs.values() if f.name in program.modules]
        except Diagnostic as error:
            answer = error
        _LAST[:] = [source, answer]
    if isinstance(_LAST[1], Diagnostic):
        raise _LAST[1]
    return _LAST[1]


def problem(where: dict, code: str, message: str) -> dict:
    """One LSP error diagnostic from the compiler."""
    return {"range": where, "severity": 1, "source": "cairn", "code": code, "message": message}


# Declarations ------------------------------------------------------------------------------------


def declarations(cs: list[Item], lo: int, hi: int) -> list[dict]:
    """Top-level declarations in `cs[lo:hi]`, with trait and impl members as children."""
    out: list[dict] = []
    i = head = lo
    while i < hi:
        if cs[i].s in MODIFIERS:
            i += 1
            continue
        if cs[i].s not in DECLARATIONS:
            i += 1
            head = i
            continue
        j = i
        while j < hi and cs[j].s not in {";", "{"}:
            j += 1
        body = j if j < hi and cs[j].s == "{" and cs[j].pair > j else -1
        end = cs[j].pair if body >= 0 else min(j, hi - 1)
        named = cs[i + 1] if i + 1 < hi else cs[i]
        out.append(
            {
                "name": " ".join(t.s for t in cs[i + 1 : body]) if cs[i].s == "impl" else named.s,
                "kind": DECLARATIONS[cs[i].s],
                "detail": cs[i].s,
                "head": cs[head].start,
                "tail": cs[end].end,
                "mark": (named.start, named.end) if cs[i].s != "impl" else (cs[i].start, cs[i].end),
                "children": declarations(cs, body + 1, end) if body >= 0 and cs[i].s in {"trait", "impl"} else [],
            }
        )
        i = head = end + 1
    return out


def symbols(doc: Document) -> list[dict]:
    def shape(d: dict) -> dict:
        return {
            "name": d["name"],
            "kind": d["kind"],
            "detail": d["detail"],
            "range": doc.span(d["head"], d["tail"]),
            "selectionRange": doc.span(*d["mark"]),
            "children": [shape(c) for c in d["children"]],
        }

    return [shape(d) for d in declarations(doc.code, 0, len(doc.code))]


def flatten(ds: list[dict]) -> list[dict]:
    return [x for d in ds for x in [d, *flatten(d["children"])]]


# Context, read from the current tokens -----------------------------------------------------------


def before(cs: list[Item], offset: int) -> int:
    """The last token before the cursor, not counting the word the cursor is writing."""
    i = -1
    for j, t in enumerate(cs):
        if t.start >= offset:
            break
        i = j
    return i - 1 if i >= 0 and cs[i].end >= offset and IDENT.fullmatch(cs[i].s) else i


def dotted(cs: list[Item], i: int) -> str:
    """The dotted path ending at token `i`, taken backwards from its last name."""
    j = i
    while j >= 2 and cs[j - 1].s == "." and IDENT.fullmatch(cs[j - 2].s):
        j -= 2
    return ".".join(t.s for t in cs[j : i + 1] if t.s != ".")


def ahead(cs: list[Item], i: int) -> str:
    """The dotted path starting at token `i`, as `module a.b;` and `import a.b;` write it."""
    j = i
    while j + 2 < len(cs) and cs[j + 1].s == "." and IDENT.fullmatch(cs[j + 2].s):
        j += 2
    return ".".join(t.s for t in cs[i : j + 1] if t.s != ".") if i < len(cs) else ""


def unclosed(cs: list[Item], i: int) -> int:
    """The innermost bracket still open at token `i`, or -1."""
    depth = 0
    for j in range(i, -1, -1):
        if cs[j].s in CLOSERS:
            depth += 1
        elif cs[j].s in OPENERS:
            if depth == 0:
                return j
            depth -= 1
    return -1


def call_at(cs: list[Item], i: int) -> int:
    """The `(` of the innermost call still open at token `i`, or -1."""
    while i >= 0:
        i = unclosed(cs, i)
        if i < 0 or cs[i].s == "(":
            return i
        i -= 1
    return -1


def statement(cs: list[Item], i: int, word: str) -> bool:
    """Is token `i` inside a `<word> ...;` statement?"""
    while i >= 0 and cs[i].s not in {";", "{", "}"}:
        if cs[i].s == word:
            return True
        i -= 1
    return False


def promising(cs: list[Item], i: int) -> bool:
    """`fn f[T: |` or `struct S[T: A + |`: the cursor writes what a generic parameter promises."""
    j = unclosed(cs, i)
    return (
        j > 1
        and cs[j].s == "["
        and (cs[j - 1].s == "impl" or cs[j - 2].s in DECLARATIONS)
        and any(cs[k].s in {":", "+"} for k in range(j, i + 1))
    )


def bound(cs: list[Item], lo: int, hi: int) -> list[tuple[int, str, bool]]:
    """Where `cs[lo:hi]` introduces a local, as (its token, what it is, whether it may be assigned): a binder word's
    name, the element of `for i, x in xs`, a parameter of a function or of a closure, a match payload."""
    out: list[tuple[int, str, bool]] = []
    for i in range(max(lo, 1), min(hi, len(cs))):
        word, then = cs[i].s, cs[i + 1].s if i + 1 < hi else ""
        if word in BINDERS:
            j = i + 1 + (then == "mut")
            if j < hi and IDENT.fullmatch(cs[j].s) and cs[j].s not in RESERVED:
                out.append((j, "variable", then == "mut" or word in {"reg", "buffer", "stack"}))
            if word == "for" and j + 2 < hi and cs[j + 1].s == "," and IDENT.fullmatch(cs[j + 2].s):
                out.append((j + 2, "element", False))
        elif then == ":" and cs[i - 1].s in {"(", ",", "|"} and IDENT.fullmatch(word) and word not in RESERVED:
            out.append((i, "parameter", i + 2 < hi and cs[i + 2].s == "rw"))
        elif word == "(" and i + 3 < hi and (cs[i + 2].s, cs[i + 3].s) == (")", "=>") and IDENT.fullmatch(then):
            out.append((i + 1, "variable", False))
    return out


def binders(cs: list[Item], lo: int, hi: int) -> list[int]:
    """Where `cs[lo:hi]` introduces a local: a binder word, a parameter, a match payload."""
    return [j for j, _, _ in bound(cs, lo, hi)]


def enclosing(doc: Document, offset: int) -> tuple[dict | None, bool]:
    """The innermost declaration covering `offset`, and whether it is a trait or impl member."""
    over = [d for d in flatten(declarations(doc.code, 0, len(doc.code))) if d["head"] <= offset <= d["tail"]]
    return min(over, key=lambda d: d["tail"] - d["head"], default=None), len(over) > 1


def word_at(doc: Document, offset: int) -> tuple[int, Item] | None:
    """The identifier the cursor stands on, with its index in the current tokens."""
    found = ((i, t) for i, t in enumerate(doc.code) if t.start <= offset < t.end and IDENT.fullmatch(t.s))
    return next((x for x in found if x[1].s not in RESERVED), None)

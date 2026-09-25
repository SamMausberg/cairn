"""The edits `cairn lsp` offers: references and rename where one document can answer soundly, and
`Format Document`, which is `cairn fmt`."""

from __future__ import annotations

from ..compiler.primitives.builtins import TABLE
from ..compiler.syntax.parser import IDENT, RESERVED
from .document import DECLARATIONS, Document, Item, binders, declarations, enclosing, flatten, word_at
from .formatting import format_source
from .names import TYPES

DECLARING = set(DECLARATIONS) - {"impl"}  # the word before a name that a declaration introduces


def occurrences(doc: Document, offset: int) -> list[Item]:
    """Every token that names what the cursor stands on, where one document can answer soundly:
    a top-level declaration, unless its name is ever written after a `.` or after `import`, is bound
    as a local anywhere, or is declared twice (another declaration's own name is never touched); or a
    local, from its binder to the end of its declaration, when it is bound exactly once there -- so
    nothing can shadow it, CAIRN having none (`E-SHADOW`) -- and is not also a declaration, an import
    alias or an imported name. `docs/tools.md` states the rule for the people it refuses."""
    cs = doc.code
    at = word_at(doc, offset)
    if at is None or doc.good is None:
        return []
    name, p = at[1].s, doc.good.program
    ds = declarations(cs, 0, len(cs))
    bound = [cs[i].s for i in binders(cs, 0, len(cs))]
    reachable = any(t.s == name and i and cs[i - 1].s in {".", "import"} for i, t in enumerate(cs))
    top = [d for d in ds if d["name"] == name]
    if top:
        marks = {d["mark"][0] for d in flatten(ds) if d is not top[0]}
        found = [] if len(top) > 1 or reachable or name in bound else [t for t in cs if t.s == name]
        found = [t for t in found if t.start not in marks]
    else:
        d = enclosing(doc, offset)[0]
        lo = next((i for i, t in enumerate(cs) if t.start >= d["head"]), len(cs)) if d else 0
        hi = next((i for i, t in enumerate(cs) if t.end > d["tail"]), len(cs)) if d else 0
        mine = [i for i in binders(cs, lo, hi) if cs[i].s == name]
        taken = {a for _, _, a in p.imports} | {bare for _, bare in p.uses} | {x["name"] for x in flatten(ds)}
        found = [] if len(mine) != 1 or name in taken else [
            t for i, t in enumerate(cs) if t.s == name and lo <= i < hi and t.start >= cs[mine[0]].start
            and cs[i - 1].s != "."]  # fmt: skip
    return found if any(t is at[1] for t in found) else []


def references(doc: Document, uri: str, offset: int) -> list[dict]:
    """Where the name under the cursor is written in this document, or nothing."""
    return [{"uri": uri, "range": doc.span(t.start, t.end)} for t in occurrences(doc, offset)]


ASSIGNING = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="}


def highlights(doc: Document, starts: set[int]) -> list[dict]:
    """The tokens of this document that start at `starts`, each a write where it is declared, bound or assigned,
    and a read everywhere else (LSP DocumentHighlightKind 3 and 2)."""
    cs = doc.code
    bound = set(binders(cs, 0, len(cs))) | {i for i, t in enumerate(cs) if i and cs[i - 1].s in DECLARING}
    out = []
    for i, t in enumerate(cs):
        if t.start in starts:
            written = i in bound or (i + 1 < len(cs) and cs[i + 1].s in ASSIGNING)
            out.append({"range": doc.span(t.start, t.end), "kind": 3 if written else 2})
    return out


def document_highlights(doc: Document, offset: int) -> list[dict]:
    """Where the name under the cursor is written in this document, by the one-document rule."""
    return highlights(doc, {t.start for t in occurrences(doc, offset)})


def prepare_rename(doc: Document, offset: int) -> dict | None:
    """The range a rename would replace, or null when this name cannot be renamed soundly."""
    found = [t for t in occurrences(doc, offset) if t.start <= offset < t.end]
    return {"range": doc.span(found[0].start, found[0].end), "placeholder": found[0].s} if found else None


def rename(doc: Document, uri: str, offset: int, fresh: str) -> dict:
    """One edit per occurrence, or a refusal: the new name must be a free identifier of this document."""
    found = occurrences(doc, offset)
    if not found:
        raise ValueError("This name cannot be renamed from one document alone.")
    if not IDENT.fullmatch(fresh) or fresh in RESERVED or fresh in TABLE or fresh in TYPES:
        raise ValueError(f"{fresh} is a reserved word, a builtin or not an identifier.")
    taken = {d["name"] for d in flatten(declarations(doc.code, 0, len(doc.code)))}
    taken |= {doc.code[i].s for i in binders(doc.code, 0, len(doc.code))}
    if fresh in taken:
        raise ValueError(f"{fresh} is already declared or bound in this document.")
    return {"changes": {uri: [{"range": doc.span(t.start, t.end), "newText": fresh} for t in found]}}


def formatted(doc: Document) -> list[dict]:
    """One whole-document edit from `cairn fmt`, or none when the buffer is already formatted."""
    out = format_source(doc.text)
    return [] if out == doc.text else [{"range": doc.span(0, len(doc.text)), "newText": out}]

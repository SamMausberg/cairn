"""Hover and go to definition: what the checker saw under the cursor, and where a name is declared."""

from __future__ import annotations

from ...agent.projection import local, signature
from ...compiler.syntax.modules import library_path
from .document import Document, declarations, dotted, flatten, word_at
from .names import callee, declared, qualified, template


def comment_above(text: str, head: int) -> str:
    """The `//` lines directly above the line a declaration starts on, as one paragraph."""
    lines = text[: text.rfind("\n", 0, head) + 1].split("\n")[:-1]
    said: list[str] = []
    for line in reversed(lines):
        if not line.strip().startswith("//"):
            break
        said.append(line.strip()[2:].strip())
    return " ".join(reversed(said))


def packaged(doc: Document, module: str, path: str) -> tuple[Document, dict, str] | None:
    """A name of a linked library module: the library's text, the declaration and the packaged file's uri."""
    p = doc.good.program if doc.good else None
    if p is None:
        return None
    tables = (declared(p), p.records, p.sums, p.enums, p.traits, p.consts, p.recipes)
    name = next((n for n in (qualified(p, module, path, table) for table in tables) if n), "")
    source, file = p.sources.get(p.modules.get(name, "")), library_path(p.modules.get(name, ""))
    if not name or source is None or file is None:
        return None
    library = Document(source, analyse=False)
    found = (d for d in flatten(declarations(library.code, 0, len(library.code))) if d["name"] == local(name))
    return next(((library, d, file.as_uri()) for d in found), None)


def declaration(doc: Document, offset: int, own: bool = True) -> tuple[Document, dict, str] | None:
    """The declaration the name under the cursor refers to, in this document ("" as its uri) or the library;
    with `own` false, never the declaration whose name the cursor stands on."""
    at = word_at(doc, offset)
    if at is None:
        return None
    i, word = at
    path = dotted(doc.code, i)
    elsewhere = packaged(doc, doc.module_at(word.start), path)
    if elsewhere and "." in path:  # `vec.push` is that module's, whatever this document declares.
        return elsewhere
    for d in flatten(declarations(doc.code, 0, len(doc.code))):
        if d["name"] == word.s and (own or d["mark"][0] != word.start):
            return doc, d, ""
    return elsewhere


def hover(doc: Document, offset: int) -> dict | None:
    """The smallest checked expression covering `offset`, with its type and its binding; for the name of a
    function, the signature and effect row the last good analysis gave it; for any declared name, the comment
    written above its declaration."""
    best: dict | None = None
    for s in doc.sites:
        if s["start"] <= offset < s["end"] and (best is None or s["end"] - s["start"] < best["end"] - best["start"]):
            best = s
    body: list[str] = []
    if best is not None:
        source = doc.text[best["start"] : best["end"]]
        body = ["```cairn", source, "```", "", "type `" + best["type"] + "`"]
        if best.get("expected_type"):
            body[-1] += ", expected `" + best["expected_type"] + "`"
        binding = best["bindings"].get(source) if best["tag"] == "name" else None
        if binding:
            body += ["", ("mutable" if binding["mutable"] else "immutable") + " binding of `" + binding["type"] + "`"]
    at = word_at(doc, offset)
    f = callee(doc, doc.module_at(offset), dotted(doc.code, at[0]), offset)[0] if at and doc.good else None
    if f is not None:  # A template's row is the join of its instances', as `cairn doc` prints it.
        effects = {x for n, xs in doc.good.rows.items() if template(n) == f.name for x in xs}
        body += ["", "```cairn", signature(f), "```", ""]
        body += [f"Effects: {', '.join(f'`{x}`' for x in sorted(effects)) or 'none'}."]
    found = declaration(doc, offset) if at else None
    if found is not None:
        text, d = found[0].text, found[1]
        if not body:  # a type, a trait or a constant: the line that declares it
            line_end = text.find("\n", d["head"])
            body = ["```cairn", text[d["head"] : line_end if line_end >= 0 else len(text)].strip()[:200], "```"]
        comment = comment_above(text, d["head"])
        body += ["", comment] if comment else []
    if not body:
        return None
    where = doc.span(best["start"], best["end"]) if best is not None else doc.span(at[1].start, at[1].end)
    return {"contents": {"kind": "markdown", "value": "\n".join(body)}, "range": where}


def definition(doc: Document, uri: str, offset: int) -> dict | None:
    """A declaration of the name under the cursor: this document, else the packaged module it comes from."""
    found = declaration(doc, offset, own=False)
    if found is None:
        return None
    library, d, file = found
    return {"uri": file or uri, "range": library.span(*d["mark"])}

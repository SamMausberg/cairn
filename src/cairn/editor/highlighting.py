"""Semantic tokens: every name colored by what it is, which only the checker knows.

The TextMate grammar colors by spelling; this layer tells a parameter from a local, a mutable local from an
immutable one, a record from an enum, a field from a method, a variant from a constructor and an effect from a
word. Declarations and the binders of each function come from the current tokens, so a buffer being typed is
still colored; what a name elsewhere means comes from the last analysis that compiled, as `names.py` reads it.
"""

from __future__ import annotations

from ..agent.projection import local
from ..compiler.builtins import TABLE
from ..compiler.effects import EFFECTS
from ..compiler.syntax import IDENT, RESERVED, Program
from .document import Document, Item, _units, bound, declarations, dotted, statement
from .grammar import FAMILIES
from .names import TYPES, declared, qualified, visible

KINDS = ["namespace", "type", "struct", "enum", "interface", "typeParameter", "parameter", "variable", "property"]
KINDS += ["enumMember", "function", "method", "macro", "effect"]
MODIFIERS = ["declaration", "readonly", "defaultLibrary", "mutable"]
LEGEND = {"tokenTypes": KINDS, "tokenModifiers": MODIFIERS}
TABLES = (("records", "struct"), ("sums", "enum"), ("enums", "enum"), ("traits", "interface"),
          ("consts", "variable"), ("recipes", "macro"))  # fmt: skip
DECLARED = {"fn": "function", "struct": "struct", "enum": "enum", "trait": "interface", "const": "variable"}
Binders = dict[str, tuple[str, bool]]  # name -> (kind, may be assigned)


def names_of(p: Program | None, module: str) -> dict[str, str]:
    """Bare name -> kind, for every declaration and library variant `module` may write without a prefix."""
    if p is None:
        return {}
    out = {v: "enumMember" for variants in p.sums.values() for v, _ in variants}
    out |= {v: "enumMember" for variants in p.enums.values() for v in variants}
    for table, kind in TABLES:
        out |= dict.fromkeys(visible(p, module, getattr(p, table)), kind)
    return out | dict.fromkeys(visible(p, module, declared(p)), "function")


def member_kind(p: Program | None, module: str, path: str, following: str) -> str:
    """What `a.b.name` is, from the path before it: a module's declaration, a variant, else a field or method."""
    head = path.rsplit(".", 1)[0]
    if p is not None and (qualified(p, module, head, p.sums) or qualified(p, module, head, p.enums)):
        return "enumMember"
    if p is not None and not qualified(p, module, head, p.records):
        for table, kind in (*TABLES, ("functions", "function")):
            if qualified(p, module, path, declared(p) if table == "functions" else getattr(p, table)):
                return kind
    return "method" if following == "(" else "property"


def is_module(p: Program | None, module: str, name: str) -> bool:
    """Whether `name` is an import alias of `module`, or the last part of a module it imports."""
    return p is not None and any(o == module and name in (a, local(path)) for o, path, a in p.imports)


def signature_binders(cs: list[Item], i: int, hi: int) -> tuple[Binders, int]:
    """The generic and value parameters of the declaration whose name is token `i`, and where its body starts."""
    out: Binders = {}
    j = i + 1
    if j < hi and cs[j].s == "[" and cs[j].pair > j:
        for k in range(j + 1, cs[j].pair):
            if IDENT.fullmatch(cs[k].s) and cs[k - 1].s in {"[", ","} and cs[k + 1].s in {":", ",", "]"}:
                out[cs[k].s] = ("typeParameter", False)
        j = cs[j].pair + 1
    if j < hi and cs[j].s == "(" and cs[j].pair > j:
        for k in range(j + 1, cs[j].pair):
            if IDENT.fullmatch(cs[k].s) and cs[k - 1].s in {"(", ","} and cs[k + 1].s == ":":
                out[cs[k].s] = ("parameter", cs[k + 2].s == "rw")
        j = cs[j].pair + 1
    return out, j


def body_binders(cs: list[Item], lo: int, hi: int) -> Binders:
    """Every local a body binds, and whether it may be assigned. CAIRN never shadows, so one name is one local."""
    return {cs[j].s: (kind, mutable) for j, kind, mutable in bound(cs, lo, hi) if kind != "element"}


def declared_names(cs: list[Item]) -> tuple[dict[int, tuple[str, set[str]]], list[tuple[int, int, Binders]]]:
    """What the current tokens declare: the kind of each declaring token, and each declaration's binders."""
    index = {t.start: k for k, t in enumerate(cs)}
    marks: dict[int, tuple[str, set[str]]] = {}
    scopes: list[tuple[int, int, Binders]] = []

    def walk(ds: list[dict], member: bool) -> None:
        for d in ds:
            walk(d["children"], True)
            at, word = index.get(d["mark"][0], -1), d["detail"]
            if at < 0 or word == "impl":
                continue
            lo = index.get(d["head"], at)
            hi = next((k for k in range(at, len(cs)) if cs[k].end >= d["tail"]), len(cs) - 1) + 1
            kind = "method" if word == "fn" and member else DECLARED[word]
            marks[at] = (kind, {"declaration", "readonly"} if word == "const" else {"declaration"})
            bound, body = signature_binders(cs, at, hi)
            scopes.append((lo, hi, bound | (body_binders(cs, body, hi) if word == "fn" else {})))
            for k in range(body, hi) if word in {"struct", "enum"} else ():
                if IDENT.fullmatch(cs[k].s) and cs[k - 1].s in {"{", ";"}:
                    marks[k] = ("property" if word == "struct" else "enumMember", {"declaration"})

    walk(declarations(cs, 0, len(cs)), False)
    return marks, sorted(scopes, key=lambda s: s[1] - s[0])


def classify(doc: Document) -> list[tuple[Item, str, set[str]]]:
    """Every name token the document writes, with its kind and modifiers, in order."""
    cs, p = doc.code, doc.good.program if doc.good else None
    marks, scopes = declared_names(cs)
    modules: dict[str, dict[str, str]] = {}
    paths = set(p.modules.values()) if p else set()
    out: list[tuple[Item, str, set[str]]] = []
    within = doc.modules()
    row_ends = -1  # inside `effects(...)`, a word is an effect and the name after `read:` a parameter
    for i, t in enumerate(cs):
        if t.s == "effects" and i + 1 < len(cs) and cs[i + 1].s == "(":
            row_ends = cs[i + 1].pair
        if not IDENT.fullmatch(t.s) or t.s in RESERVED:
            continue
        before, after = cs[i - 1].s if i else "", cs[i + 1].s if i + 1 < len(cs) else ""
        if i < row_ends:
            kind = "effect" if t.s in EFFECTS or (t.s in FAMILIES and after == ":") else "parameter"
            out.append((t, kind, set()))
            continue
        if i in marks:
            out.append((t, *marks[i]))
            continue
        if before == "@":  # a placement, which the grammar colors
            continue
        module = within[i]
        known = modules.setdefault(module, names_of(p, module))
        binders = next((b for lo, hi, b in scopes if lo <= i < hi and t.s in b), None)
        if before in {"import", "module"} or (before == "." and statement(cs, i, "import")):
            out.append((t, "namespace", set()))
        elif before == "." and i >= 2 and IDENT.fullmatch(cs[i - 2].s):
            path = dotted(cs, i)
            kind = "namespace" if path in paths or (after == "." and path.startswith("std.")) else ""
            out.append((t, kind or member_kind(p, module, path, after), set()))
        elif binders is not None:
            kind, mutable = binders[t.s]
            out.append((t, kind, {"mutable"} if mutable else set() if kind == "typeParameter" else {"readonly"}))
        elif after == "." and (t.s == "std" or is_module(p, module, t.s)):
            out.append((t, "namespace", set()))
        elif t.s in TYPES:
            out.append((t, "type" if t.s[0].islower() else "struct", {"defaultLibrary"}))
        elif t.s in TABLE and after in {"(", "["}:
            out.append((t, "function", {"defaultLibrary"}))
        elif t.s in known:
            out.append((t, known[t.s], {"readonly"} if known[t.s] == "variable" else set()))
    return out


def semantic_tokens(doc: Document) -> dict:
    """The full-document answer: five integers per token, each position relative to the one before."""
    data: list[int] = []
    line = column = 0
    for t, kind, modifiers in classify(doc):
        where = doc.position(t.start)
        delta = where["line"] - line
        data += [delta, where["character"] - (column if delta == 0 else 0), _units(t.s), KINDS.index(kind)]
        data.append(sum(1 << MODIFIERS.index(m) for m in modifiers))
        line, column = where["line"], where["character"]
    return {"data": data}

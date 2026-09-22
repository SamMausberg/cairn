"""A Language Server Protocol server for CAIRN: stdio JSON-RPC, standard library only.

One open buffer is analysed at a time; `std.*` imports are linked by the compiler
itself. Positions are UTF-16 code units, as the protocol requires. A buffer that
does not compile yields diagnostics, never an exception: the server answers every
request it accepted and stays up.

A buffer being typed usually does not compile, so every feature reads its context
from the current tokens and its meaning from the last analysis that succeeded
(`Document.good`), matching the two by name: offsets of an older analysis are
stale after an edit, but the name of a function and of its locals is not.
"""

from __future__ import annotations

import bisect
import json
import re
import sys
from typing import Any, BinaryIO

from ..agent.agent_tools import explain
from ..agent.projection import local, signature
from ..compiler.builtins import TABLE
from ..compiler.cairnc import Diagnostic, compile_program
from ..compiler.calls import extents
from ..compiler.modules import STD, library_path
from ..compiler.syntax import IDENT, INTRINSIC_TYPES, RESERVED, SCALAR, Function, Program
from ..compiler.traits import CLASSES, KINDS
from ..version import VERSION
from .formatting import CLOSERS, OPENERS, Item, format_source, roles, scan

DECLARATIONS = {"fn": 12, "struct": 23, "enum": 10, "trait": 11, "const": 14, "impl": 5}
MODIFIERS = {"pub", "linear", "extern"}
CAPABILITIES = {
    "positionEncoding": "utf-16",
    "textDocumentSync": {"openClose": True, "change": 1},
    "hoverProvider": True,
    "documentSymbolProvider": True,
    "documentFormattingProvider": True,
    "definitionProvider": True,
    "completionProvider": {"triggerCharacters": ["."]},
    "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
    "referencesProvider": True,
    "renameProvider": {"prepareProvider": True},
}
IGNORED = {"initialized", "$/cancelRequest", "$/setTrace", "workspace/didChangeConfiguration"}
UNSUPPORTED: Any = object()
ITEM = {  # LSP CompletionItemKind, by what the name is
    "fn": 3, "struct": 22, "enum": 13, "trait": 8, "const": 21, "impl": 7, "recipe": 15, "module": 9,
    "field": 5, "variant": 20, "local": 6, "method": 2, "type": 7, "word": 14,
}  # fmt: skip
TABLES = (("records", "struct"), ("sums", "enum"), ("enums", "enum"), ("traits", "trait"),
          ("consts", "const"), ("recipes", "recipe"))  # fmt: skip
TYPES = SCALAR | {"void"} | set(INTRINSIC_TYPES)
BINDERS = {"let", "reg", "buffer", "stack", "for", "parallel", "each"}  # `<word> [mut] name`
BORROW = re.compile(r"(?:ro|rw)<(.*)>(?:\[[^\]]*\]@\w+)?\Z")


# Framing and positions ---------------------------------------------------------------------------


def read_message(stream: BinaryIO) -> dict | None:
    """One `Content-Length` framed JSON-RPC message, or None at end of input."""
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        if not line.strip():
            break
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip() or b"0")
    body = stream.read(length) if length > 0 else b""
    return json.loads(body) if body else None


def write_message(stream: BinaryIO, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    stream.flush()


def line_starts(text: str) -> list[int]:
    out, i = [0], text.find("\n")
    while i >= 0:
        out.append(i + 1)
        i = text.find("\n", i + 1)
    return out


def _units(s: str) -> int:
    """UTF-16 code units in `s`; astral characters take two."""
    return len(s) + sum(ord(c) > 0xFFFF for c in s)


class Document:
    """One open buffer with its scan, its checker sites and its diagnostics.

    `good` is the last analysis that compiled: this one, or the one an earlier edit left behind.
    """

    def __init__(self, text: str, previous: Document | None = None, analyse: bool = True):
        self.text = text
        self.starts = line_starts(text)
        self.code = roles([t for t in scan(text) if not t.comment])
        self.program: Program | None = None
        self.rows: dict[str, list[str]] = {}  # function -> its effect row, for hovers
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

    def _analyse(self) -> tuple[list[dict], list[dict]]:
        """The buffer's sites and diagnostics; its program and effect rows when it compiles."""
        try:
            program, checker, receipts = compile_program(self.text, capture_sites=True)
        except Diagnostic as error:
            return [], [self._report(error)]
        except Exception as error:  # A compiler failure is reported, never raised at the client.
            zero = {"line": 0, "character": 0}
            return [], [
                {
                    "range": {"start": zero, "end": zero},
                    "severity": 1,
                    "source": "cairn",
                    "code": "E-INTERNAL",
                    "message": f"{type(error).__name__}: {error}",
                }
            ]
        # The checker takes each template out of the program once it has checked it; an editor wants
        # every declared function back, and none of the instances it made along the way.
        program.functions = [f for f in checker.fs.values() if f.name in program.modules]
        self.program = program
        self.rows = {n: r["effects"] for n, r in receipts.items()}
        linked = tuple(module + "." for module in program.sources)
        sites = [
            s
            for s in checker.sites
            if s["end"] > s["start"] >= 0 and s["end"] <= len(self.text) and not s["symbol"].startswith(linked)
        ]
        return sites, []

    def _report(self, error: Diagnostic) -> dict:
        d = explain(error, self.text)
        line, column = int(d.get("line") or 0), int(d.get("column") or 0)
        start = end = 0
        if line > 0:
            start = min(self.starts[min(line, len(self.starts)) - 1] + max(column - 1, 0), len(self.text))
            end = next((t.end for t in self.code if t.start == start), start)
        hint = d.get("repair_hint")
        return {
            "range": self.span(start, end),
            "severity": 1,
            "source": "cairn",
            "code": d["code"],
            "message": d["message"] + ("\n" + hint if hint else ""),
            "data": {k: d[k] for k in ("code", "repair_hint", "source_line") if k in d},
        }


# Language features -------------------------------------------------------------------------------


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


# Context, read from the current tokens -------------------------------------------------------------


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


def binders(cs: list[Item], lo: int, hi: int) -> list[int]:
    """Where `cs[lo:hi]` introduces a local: a binder word, a parameter, a match payload."""
    out: list[int] = []
    for i in range(max(lo, 1), min(hi, len(cs))):
        word, then = cs[i].s, cs[i + 1].s if i + 1 < hi else ""
        if word in BINDERS:
            j = i + 1 + (then == "mut")
            out += [j] if j < hi and IDENT.fullmatch(cs[j].s) and cs[j].s not in RESERVED else []
        elif then == ":" and cs[i - 1].s in {"(", ",", "|"} and IDENT.fullmatch(word) and word not in RESERVED:
            out.append(i)  # a parameter of a function or of a closure
        elif word == "(" and i + 3 < hi and (cs[i + 2].s, cs[i + 3].s) == (")", "=>") and IDENT.fullmatch(then):
            out.append(i + 1)  # a match payload binder
    return out


def enclosing(doc: Document, offset: int) -> tuple[dict | None, bool]:
    """The innermost declaration covering `offset`, and whether it is a trait or impl member."""
    over = [d for d in flatten(declarations(doc.code, 0, len(doc.code))) if d["head"] <= offset <= d["tail"]]
    return min(over, key=lambda d: d["tail"] - d["head"], default=None), len(over) > 1


def module_at(cs: list[Item], offset: int) -> str:
    """The module whose declarations surround `offset`, from its `module a.b;`; the root module is ""."""
    out = ""
    for i, t in enumerate(cs):
        if t.start >= offset:
            break
        out = ahead(cs, i + 1) if t.s == "module" else out
    return out


# Meaning, read from the last good analysis ---------------------------------------------------------


def entry(label: str, word: str, detail: str = "") -> dict:
    """One completion item: what to write, what kind of name it is, and how it reads."""
    return {"label": label, "kind": ITEM[word], "detail": detail}


def declared(p: Program) -> dict[str, Function]:
    """Every function that can be named by itself: the members of an impl are reached through a value."""
    return {f.name: f for f in p.functions if not f.owner}


def template(symbol: str) -> str:
    """A generic instance's symbol without its arguments: `m.f[u64]` was written `m.f`."""
    return symbol[: symbol.rindex("[")] if symbol.endswith("]") and "[" in symbol else symbol


def qualified(p: Program, module: str, name: str, table: Any) -> str:
    """`Checker.qualify` without a checker: an imported name, an alias, this module, then the root."""
    head, _, rest = name.partition(".")
    alias = next((path for owner, path, a in p.imports if owner == module and a == head), "")
    tries = [
        p.uses[module, head] + ("." + rest if rest else "") if (module, head) in p.uses else "",
        f"{alias}.{rest}" if rest and alias else "",
        f"{module}.{name}" if module else name,
        name,
    ]
    return next((n for n in tries if n in table and (p.modules.get(n, "") in ("", module) or n in p.public)), "")


def visible(p: Program, module: str, table: Any) -> dict[str, str]:
    """Bare name -> declaration, for every name of `table` that `module` may write without a prefix."""
    reachable = {n for n in table if p.modules.get(n, "") in ("", module) or n in p.public}
    own = {local(n): n for n in table if p.modules.get(n, "") in ("", module)}
    return own | {bare: full for (owner, bare), full in p.uses.items() if owner == module and full in reachable}


def named(shown: str) -> tuple[str, list[str]]:
    """A displayed type as (name, arguments), with any borrow, extent and placement stripped."""
    borrow = BORROW.fullmatch(shown)
    name, _, rest = (borrow.group(1) if borrow else shown).partition("[")
    args: list[str] = []
    depth, part = 0, ""
    for c in rest[:-1]:
        depth += (c == "[") - (c == "]")
        args, part = ([*args, part.strip()], "") if c == "," and depth == 0 else (args, part + c)
    return name, [*args, part.strip()] if rest else []


def fields(p: Program, module: str, shown: str) -> list[tuple[str, str]]:
    """The fields of the record a displayed type names, with its type arguments substituted."""
    name, args = named(shown)
    if name not in p.records or (p.modules.get(name, "") not in ("", module) and name not in p.public):
        return []
    put = dict(zip((g for g, _ in p.generics.get(name, [])), args, strict=False))
    carried = p.field_extents.get(name, {})  # `price:Buf[f64][rows]`: the extent is part of what the field says.
    substituted = (
        (n, re.sub(r"[\w.]+", lambda m: put.get(m.group(), m.group()), t.display())) for n, t in p.records[name]
    )
    return [(n, t + (f"[{carried[n]}]" if n in carried else "")) for n, t in substituted]


def methods(p: Program, module: str, shown: str) -> list[Function]:
    """Every function `value.name(...)` reaches: those of the receiver's module, and the members
    implementing a trait that `module` can see for it. Both take the receiver as first argument."""
    name = named(shown)[0]
    home, base, out = p.modules.get(name, ""), local(name), []
    for f in p.functions:
        if not f.params or local(f.params[0][1].name) != base:
            continue
        owner = qualified(p, f.module, f.owner[0], p.traits) if f.owner else ""
        reached = owner or (f.name if p.modules.get(f.name, "") == home else "")
        if reached and (p.modules.get(reached, "") in ("", module) or reached in p.public):
            out.append(f)
    return out


def members(p: Program, module: str, target: str) -> list[dict]:
    """The declarations of module `target` that `target.name` reaches from `module`."""

    def shown(n: str) -> bool:
        return p.modules.get(n, "") == target and (n in p.public or target == module)

    out = [entry(local(n), "fn", signature(f)) for n, f in declared(p).items() if shown(n)]
    for table, word in TABLES:
        out += [entry(local(n), word, word) for n in getattr(p, table) if shown(n)]
    return out


def recipes(p: Program, module: str) -> list[str]:
    """Every recipe a `derive` may name: one visible bare, or the packaged `std.r.r` and `std.derived.r`."""
    packaged = {local(n) for n in p.recipes if n.startswith("std.derived.") or n == f"std.{local(n)}.{local(n)}"}
    return sorted(set(visible(p, module, p.recipes)) | packaged)


def modules(p: Program | None) -> list[str]:
    """Every module an import may name: the packaged library, and the document's own."""
    own = set(p.modules.values()) if p else set()
    return sorted(({"std." + path.stem for path in STD.glob("*.cairn")} | own) - {""})


def scope(doc: Document, offset: int) -> dict[str, str]:
    """Every local of the declaration around `offset`, with its type: the bindings the last good
    analysis saw anywhere in that function, plus the binders the current tokens add before it."""
    d, member = enclosing(doc, offset)
    if d is None:
        return {}
    module = module_at(doc.code, d["head"])
    prefix = module + "." if module else ""
    out: dict[str, str] = {}
    for s in doc.good.sites if doc.good else []:
        base = template(s["symbol"])
        if base == prefix + d["name"] or (member and base.startswith(prefix) and base.endswith("." + d["name"])):
            for n, b in s["bindings"].items():
                out.setdefault(n, b["type"])
    lo = next((i for i, t in enumerate(doc.code) if t.start >= d["head"]), len(doc.code))
    hi = next((i for i, t in enumerate(doc.code) if t.start >= offset), len(doc.code))
    return {doc.code[i].s: "" for i in binders(doc.code, lo, hi)} | out


def walk(doc: Document, module: str, parts: list[str], offset: int) -> str:
    """The displayed type of `a.b.c` when `a` is a local: the field walk, or "" if anything is unknown."""
    shown = scope(doc, offset).get(parts[0], "")
    for name in parts[1:]:
        shown = dict(fields(doc.good.program, module, shown)).get(name, "") if shown else ""
    return shown


def callee(doc: Document, module: str, path: str, offset: int) -> tuple[Function | None, int]:
    """The function a call names, and 1 when method syntax has already passed the receiver."""
    p, parts = doc.good.program, path.split(".")
    if len(parts) > 1 and parts[0] in scope(doc, offset):
        shown = walk(doc, module, parts[:-1], offset)
        found = [f for f in methods(p, module, shown) if local(f.name) == parts[-1]] if shown else []
        return (found[0] if found else None), 1
    name = qualified(p, module, path, declared(p))
    return (declared(p)[name] if name else None), 0


def word_at(doc: Document, offset: int) -> tuple[int, Item] | None:
    """The identifier the cursor stands on, with its index in the current tokens."""
    found = ((i, t) for i, t in enumerate(doc.code) if t.start <= offset < t.end and IDENT.fullmatch(t.s))
    return next((x for x in found if x[1].s not in RESERVED), None)


def hover(doc: Document, offset: int) -> dict | None:
    """The smallest checked expression covering `offset`, with its type, its binding and, for the
    name of a function, the signature and effect row the last good analysis gave it."""
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
    f = callee(doc, module_at(doc.code, offset), dotted(doc.code, at[0]), offset)[0] if at and doc.good else None
    if f is not None:  # A template's row is the join of its instances', as `cairn doc` prints it.
        effects = {x for n, xs in doc.good.rows.items() if template(n) == f.name for x in xs}
        body += ["", "```cairn", signature(f), "```", ""]
        body += [f"Effects: {', '.join(f'`{x}`' for x in sorted(effects)) or 'none'}."]
    if not body:
        return None
    where = doc.span(best["start"], best["end"]) if best is not None else doc.span(at[1].start, at[1].end)
    return {"contents": {"kind": "markdown", "value": "\n".join(body)}, "range": where}


def packaged(doc: Document, module: str, path: str) -> dict | None:
    """Where a name of a linked library module is declared: its packaged file and the range of its name."""
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
    return next(({"uri": file.as_uri(), "range": library.span(*d["mark"])} for d in found), None)


def definition(doc: Document, uri: str, offset: int) -> dict | None:
    """A declaration of the name under the cursor: this document, else the packaged module it comes from."""
    at = word_at(doc, offset)
    if at is None:
        return None
    i, word = at
    path = dotted(doc.code, i)
    elsewhere = packaged(doc, module_at(doc.code, word.start), path)
    if elsewhere and "." in path:  # `vec.push` is that module's, whatever this document declares.
        return elsewhere
    for d in flatten(declarations(doc.code, 0, len(doc.code))):
        if d["name"] == word.s and d["mark"][0] != word.start:
            return {"uri": uri, "range": doc.span(*d["mark"])}
    return elsewhere


def completion(doc: Document, offset: int) -> list[dict]:
    """What may be written at `offset`: the members of what precedes a `.`, the modules of an import,
    the recipes of a derive, what a bound may promise, else every name in scope. The client filters."""
    cs, p = doc.code, doc.good.program if doc.good else None
    i, module = before(cs, offset), module_at(cs, offset)
    if statement(cs, i, "import"):
        return [entry(m, "module", "module") for m in modules(p)]
    if p is not None and i >= 0 and cs[i].s == "derive":
        return [entry(n, "recipe", "recipe") for n in recipes(p, module)]
    if p is not None and i >= 1 and cs[i].s == "." and IDENT.fullmatch(cs[i - 1].s):
        return member_items(doc, module, dotted(cs, i - 1), offset)
    if p is not None and promising(cs, i):
        return [entry(n, "trait", "trait") for n in visible(p, module, p.traits)] + [
            entry(n, "word", kind) for kind, names in (("kind", KINDS), ("class", CLASSES)) for n in names
        ]
    return scope_items(doc, module, offset)


def member_items(doc: Document, module: str, path: str, offset: int) -> list[dict]:
    """After `name.`: the fields and methods of a local, the variants of an enum, a module's names."""
    p, parts = doc.good.program, path.split(".")
    if parts[0] in scope(doc, offset):
        shown = walk(doc, module, parts, offset)
        found = [entry(n, "field", t) for n, t in fields(p, module, shown)]
        return found + [entry(local(f.name), "method", signature(f)) for f in methods(p, module, shown)]
    total = qualified(p, module, path, p.sums)
    if total:
        return [entry(v, "variant", t.display() if t else "") for v, t in p.sums[total]]
    plain = qualified(p, module, path, p.enums)
    if plain:
        return [entry(v, "variant", "") for v in p.enums[plain]]
    alias = next((target for owner, target, a in p.imports if owner == module and a == path), "")
    target = alias or (path if path in set(p.modules.values()) else "")
    return members(p, module, target) if target else []


def scope_items(doc: Document, module: str, offset: int) -> list[dict]:
    """Every name the cursor may write bare: locals, this module's declarations and imports, the
    builtins the checker knows, the intrinsic types and the reserved words."""
    p = doc.good.program if doc.good else None
    out = [entry(n, "local", t) for n, t in scope(doc, offset).items()]
    if p is not None:
        functions = declared(p)
        out += [entry(n, "fn", signature(functions[f])) for n, f in visible(p, module, functions).items()]
        for table, word in TABLES:
            out += [entry(n, word, word) for n in visible(p, module, getattr(p, table))]
        out += [entry(a, "module", target) for owner, target, a in p.imports if owner == module]
    out += [entry(d["name"], d["detail"], d["detail"]) for d in declarations(doc.code, 0, len(doc.code))
            if IDENT.fullmatch(d["name"])]  # fmt: skip
    out += [entry(n, "fn", "builtin") for n in TABLE]
    out += [entry(n, "type", "type") for n in sorted(TYPES)]
    out += [entry(n, "word", "keyword") for n in sorted(RESERVED)]
    seen: dict[str, dict] = {}
    for one in out:
        seen.setdefault(one["label"], one)
    return list(seen.values())


def signature_help(doc: Document, offset: int) -> dict | None:
    """The innermost call open at the cursor: its signature and the parameter being written."""
    cs = doc.code
    i = call_at(cs, before(cs, offset))
    if i <= 0 or doc.good is None or not IDENT.fullmatch(cs[i - 1].s) or cs[i - 1].s in RESERVED:
        return None
    f, receiver = callee(doc, module_at(cs, offset), dotted(cs, i - 1), offset)
    if f is None:
        return None
    depth, commas = 0, 0
    for t in cs[i + 1 :]:
        if t.start >= offset:
            break
        depth += (t.s in OPENERS) - (t.s in CLOSERS)
        commas += t.s == "," and depth == 0
    params = [{"label": n + ":" + t.display()} for n, t in f.params]
    # Past the last parameter, the client highlights nothing.
    signatures = [{"label": signature(f), "parameters": params, "activeParameter": receiver + commas}]
    implied = extents(f)  # `checksum(frame)`: a call may leave out the extents its views carry.
    if implied and not receiver and f.params[0][0] in implied:
        short = [p for p, (n, _) in zip(params, f.params, strict=True) if n not in implied]
        label = f"{signature(f)}  // extents left out: {', '.join(implied)}"
        signatures.append({"label": label, "parameters": short, "activeParameter": commas})
    written = i + 1 < len(cs) and cs[i + 1].start < offset and cs[i + 1].s != ")"
    short_form = len(signatures) > 1 and written and cs[i + 1].s != "len"
    chosen = signatures[short_form]
    return {"signatures": signatures, "activeSignature": int(short_form), "activeParameter": chosen["activeParameter"]}


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


# What each request asks of an open document: (document, its uri, the cursor's offset, the request's params).
ANSWERS: dict[str, Any] = {
    "textDocument/hover": lambda d, u, at, p: hover(d, at),
    "textDocument/definition": lambda d, u, at, p: definition(d, u, at),
    "textDocument/completion": lambda d, u, at, p: completion(d, at),
    "textDocument/signatureHelp": lambda d, u, at, p: signature_help(d, at),
    "textDocument/references": lambda d, u, at, p: references(d, u, at),
    "textDocument/prepareRename": lambda d, u, at, p: prepare_rename(d, at),
    "textDocument/rename": lambda d, u, at, p: rename(d, u, at, str(p.get("newName") or "")),
    "textDocument/documentSymbol": lambda d, u, at, p: symbols(d),
    "textDocument/formatting": lambda d, u, at, p: formatted(d),
}


# Server ------------------------------------------------------------------------------------------


class Server:
    def __init__(self, source: BinaryIO, sink: BinaryIO):
        self.source, self.sink = source, sink
        self.docs: dict[str, Document] = {}
        self.stopping = False

    def send(self, payload: dict) -> None:
        write_message(self.sink, {"jsonrpc": "2.0", **payload})

    def refresh(self, uri: str, text: str) -> None:
        self.docs[uri] = doc = Document(text, self.docs.get(uri))
        self.send({"method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": doc.diagnostics}})

    def handle(self, method: str, p: dict) -> Any:
        if method == "initialize":
            return {"capabilities": CAPABILITIES, "serverInfo": {"name": "cairn-lsp", "version": VERSION}}
        if method == "shutdown":
            self.stopping = True
            return None
        if method in IGNORED:
            return None
        uri = (p.get("textDocument") or {}).get("uri", "")
        if method == "textDocument/didOpen":
            self.refresh(uri, (p.get("textDocument") or {}).get("text", ""))
            return None
        if method == "textDocument/didChange":
            changes = p.get("contentChanges") or [{}]
            self.refresh(uri, changes[-1].get("text", self.docs[uri].text if uri in self.docs else ""))
            return None
        if method == "textDocument/didClose":
            self.docs.pop(uri, None)
            self.send({"method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": []}})
            return None
        doc = self.docs.get(uri)
        if doc is None:
            return None if method.startswith("textDocument/") else UNSUPPORTED
        answer = ANSWERS.get(method)
        return answer(doc, uri, doc.offset(p.get("position") or {}), p) if answer else UNSUPPORTED

    def dispatch(self, message: dict) -> bool:
        """Answer one message; True when the server must exit."""
        method, request = str(message.get("method") or ""), message.get("id")
        if method == "exit":
            return True
        try:
            result = self.handle(method, message.get("params") or {})
        except Exception as error:  # Robustness over strictness: a bad request never kills the server.
            refused = isinstance(error, ValueError)  # A refused rename is a bad request, not a failure.
            if request is not None:
                code, said = (-32602, str(error)) if refused else (-32603, f"{type(error).__name__}: {error}")
                self.send({"id": request, "error": {"code": code, "message": said}})
            return False
        if request is None:
            return False
        if result is UNSUPPORTED:
            self.send({"id": request, "error": {"code": -32601, "message": "Unsupported method " + method}})
        else:
            self.send({"id": request, "result": result})
        return False

    def run(self) -> int:
        while True:
            try:
                message = read_message(self.source)
            except ValueError:  # A malformed frame is skipped, not fatal.
                continue
            if message is None:
                return 0 if self.stopping else 1
            if isinstance(message, dict) and self.dispatch(message):
                return 0


def serve() -> int:
    """`cairn lsp`: speak LSP over stdin/stdout until the client sends `exit`."""
    return Server(sys.stdin.buffer, sys.stdout.buffer).run()

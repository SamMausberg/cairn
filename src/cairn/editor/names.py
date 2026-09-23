"""What a name means, read from the last analysis of a document that compiled: the declarations a
module may write, the fields and methods of a displayed type, and the locals around the cursor."""

from __future__ import annotations

import re
from typing import Any

from ..agent.projection import local, signature
from ..compiler.modules import STD
from ..compiler.syntax import INTRINSIC_TYPES, SCALAR, Function, Program
from ..compiler.tree import STORAGE
from .document import Document, binders, enclosing

ITEM = {  # LSP CompletionItemKind, by what the name is
    "fn": 3, "struct": 22, "enum": 13, "trait": 8, "const": 21, "impl": 7, "recipe": 15, "module": 9,
    "field": 5, "variant": 20, "local": 6, "method": 2, "type": 7, "word": 14,
}  # fmt: skip

TABLES = (("records", "struct"), ("sums", "enum"), ("enums", "enum"), ("traits", "trait"),
          ("consts", "const"), ("recipes", "recipe"))  # fmt: skip

TYPES = SCALAR | STORAGE.keys() | {"void"} | set(INTRINSIC_TYPES)

BORROW = re.compile(r"(?:ro|rw)<(.*)>(?:\[[^\]]*\]@\w+)?\Z")


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
    module = doc.module_at(d["head"])
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

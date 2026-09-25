"""Completion and signature help: what may be written at the cursor, and the call being written."""

from __future__ import annotations

from ...agent.projection import local, signature
from ...compiler.check.calls import extents
from ...compiler.check.traits import CLASSES, KINDS
from ...compiler.primitives.builtins import TABLE
from ...compiler.syntax.parser import IDENT, RESERVED
from ..formatting import CLOSERS, OPENERS
from .document import Document, before, call_at, declarations, dotted, promising, statement
from .names import (
    TABLES,
    TYPES,
    callee,
    declared,
    entry,
    fields,
    members,
    methods,
    modules,
    qualified,
    recipes,
    scope,
    visible,
    walk,
)


def completion(doc: Document, offset: int) -> list[dict]:
    """What may be written at `offset`: the members of what precedes a `.`, the modules of an import,
    the recipes of a derive, what a bound may promise, else every name in scope. The client filters."""
    cs, p = doc.code, doc.good.program if doc.good else None
    i, module = before(cs, offset), doc.module_at(offset)
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
    f, receiver = callee(doc, doc.module_at(offset), dotted(cs, i - 1), offset)
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

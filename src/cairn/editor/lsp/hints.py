"""Inlay hints: what the checker inferred and the source leaves unwritten.

Three things are shown in place: each function's effect row after its signature, the extents a call left out
(`dot(v, v)` passes `n = len(v)`), and the type of a `let` that writes none. Rows and types come from the last
analysis that compiled, matched by name, so a hint never points at stale offsets.
"""

from __future__ import annotations

import re

from ...compiler.check.calls import extents
from ...compiler.syntax.parser import IDENT, RESERVED
from .document import Document, Item, dotted, enclosing
from .names import callee, instance_of, template

QUALIFIER = re.compile(r"\b(?:[a-z_]\w*\.)+(?=[A-Za-z_])")  # `std.vec.Vec[u64]` reads as `Vec[u64]`
PART = re.compile(r"(?s)(.*)\[(.*)\.\.(.*)\]")
ROW = "The effect row the checker inferred: what this function may do, and all it may do."


def hint(doc: Document, offset: int, label: str, kind: int | None = None, tooltip: str = "", **pad) -> dict:
    out = {"position": doc.position(offset), "label": label, **pad}
    return out | ({"kind": kind} if kind else {}) | ({"tooltip": tooltip} if tooltip else {})


def body_start(cs: list[Item], at: int) -> int:
    """The token that opens the body of the declaration named at `at`: its `{`, its `=`, or its `;`."""
    k, depth = at + 1, 0
    while k < len(cs):
        depth += (cs[k].s in {"(", "["}) - (cs[k].s in {")", "]"})
        if depth == 0 and cs[k].s in {"{", "=", ";"}:
            return k
        k += 1
    return k


def rows(doc: Document, lo: int, hi: int) -> list[dict]:
    """After each function's signature, the effect row the checker inferred for it."""
    if doc.good is None:
        return []
    cs, out = doc.code, []
    index = {t.start: k for k, t in enumerate(cs)}
    for d in doc.declarations:
        at = index.get(d["mark"][0], -1)
        if d["detail"] != "fn" or at < 0 or not lo <= cs[at].start <= hi:
            continue
        module, member = doc.module_at(d["head"]), all(d is not t for t in doc.outline)
        found = [xs for n, xs in doc.good.rows.items() if instance_of(n, module, d["name"], member)]
        body = body_start(cs, at)
        if found and body < len(cs) and cs[body].s != ";":
            effects = sorted({x for xs in found for x in xs})
            label = "effects: " + (", ".join(effects) or "none")
            out.append(hint(doc, cs[body].start, label, tooltip=ROW, paddingRight=True))
    return out


def arguments(doc: Document, cs: list[Item], opening: int) -> list[str]:
    """The written arguments of the call whose `(` is token `opening`."""
    out, start, depth = [], opening + 1, 0
    for k in range(opening + 1, cs[opening].pair + 1):
        if k == cs[opening].pair or (cs[k].s == "," and depth == 0):
            out += [doc.text[cs[start].start : cs[k - 1].end]] if k > start else []
            start = k + 1
        depth += (cs[k].s in {"(", "[", "{"}) - (cs[k].s in {")", "]", "}"})
    return out


def extent_of(argument: str) -> str:
    """What the checker writes for the extent a view argument carries: `len(v)`, or `hi - lo` for a part."""
    part = PART.fullmatch(argument)
    return f"{part.group(3).strip()} - {part.group(2).strip()}" if part else f"len({argument})"


def extents_left_out(doc: Document, lo: int, hi: int) -> list[dict]:
    """Before the first argument of a call that leaves its extents out, the extents it passes."""
    cs, out, modules = doc.code, [], doc.modules()
    for i, t in enumerate(cs):
        if doc.good is None or t.s != "(" or not i or t.pair <= i or not lo <= t.start <= hi:
            continue
        name = cs[i - 1].s
        if not IDENT.fullmatch(name) or name in RESERVED or (i > 1 and cs[i - 2].s in {"fn", "kernel"}):
            continue
        path = dotted(cs, i - 1)
        f, receiver = callee(doc, modules[i], path, t.start)
        implied = extents(f) if f is not None else []
        args = [path.rsplit(".", 1)[0]] * receiver + arguments(doc, cs, i)
        if f is None or not implied or len(args) != len(f.params) - len(implied):
            continue
        given = dict(zip((n for n, _ in f.params if n not in implied), args, strict=True))
        said = []
        for extent in implied:
            view = next((n for n, ty in f.params if ty.extent == extent and n in given), None)
            said.append(f"{extent} = {extent_of(given[view])}" if view else extent)
        at = cs[i + 1].start if cs[i + 1].s != ")" else t.end
        out.append(hint(doc, at, ", ".join(said) + ("," if len(args) > receiver else ""), kind=2, paddingRight=True))
    return out


def let_types(doc: Document, lo: int, hi: int) -> list[dict]:
    """After the name of a `let` that writes no type, the type the checker gave it in the last good analysis."""
    cs, out = doc.code, []
    if doc.good is None:
        return []
    types: dict[str, dict[str, str]] = {}  # function, as the checker names it -> local -> its type
    for s in doc.good.sites:
        for n, b in s["bindings"].items():
            types.setdefault(template(s["symbol"]), {}).setdefault(n, b["type"])
    modules = doc.modules()
    for i, t in enumerate(cs):
        j = i + 1 + (i + 1 < len(cs) and cs[i + 1].s == "mut")
        if t.s not in {"let", "reg"} or not lo <= t.start <= hi or j + 1 >= len(cs) or cs[j + 1].s != "=":
            continue
        d = enclosing(doc, t.start)[0]
        prefix = modules[i] + "." if modules[i] else ""
        mine = {} if d is None else types.get(prefix + d["name"]) or next(
            (v for k, v in types.items() if k.startswith(prefix) and k.endswith("." + d["name"])), {})  # fmt: skip
        if cs[j].s in mine:
            out.append(hint(doc, cs[j].end, ":" + QUALIFIER.sub("", mine[cs[j].s]), kind=1))
    return out


def inlay_hints(doc: Document, span: dict | None) -> list[dict]:
    """Every hint whose anchor lies in the requested range (the whole document when none is given)."""
    lo, hi = (doc.offset(span["start"]), doc.offset(span["end"])) if span else (0, len(doc.text))
    return sorted(rows(doc, lo, hi) + extents_left_out(doc, lo, hi) + let_types(doc, lo, hi),
                  key=lambda h: (h["position"]["line"], h["position"]["character"]))  # fmt: skip

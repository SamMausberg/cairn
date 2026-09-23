"""Comment-preserving, idempotent formatting of CAIRN sources.

The formatter rewrites the token stream, not the syntax tree: one scan that keeps
comments, one structural pass over brace depth, then canonical spacing and a
greedy wrap at 100 columns. Comments and syntax the parser has not learned yet
therefore survive unchanged. Every result is re-lexed and refused unless its
token stream and its comments are exactly the ones it was given.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from ..compiler.lexing import IDENT, RESERVED, TOKEN, lex
from ..compiler.syntax import PREC
from ..compiler.tree import Diagnostic

WIDTH = 100
STEP = "  "
NO_LEAD = {",", ";", ")", "]", "[", ".", "..", ":", "@"}
NO_TRAIL = {"(", "[", ".", "..", ":", "@"}
VALUES = {")", "]", "true", "false", "zeroed"}  # tokens that end a value, so `-` after them is binary
CALLEES = {")", "]", "fn", "effects"}  # tokens that take `(` with no space before it
OPENERS, CLOSERS = {"(", "[", "{"}, {")", "]", "}"}
GLUE = {"else", ")", "]", ",", ";"}  # a closing brace keeps these on its own line


@dataclass(slots=True)
class Item:
    """One comment or code token, with the newline count that preceded it."""

    s: str
    nl: int
    start: int
    end: int
    comment: bool = False
    # unary | angle_open | angle_close | lam_open | lam_close | lambda0 | inline | splice | fold | spaced
    role: str = ""
    pair: int = -1  # matching bracket, as an index into the code tokens


def scan(text: str) -> list[Item]:
    """Every comment and code token in order. Unlexable bytes become one-character tokens."""
    items: list[Item] = []
    p = nl = 0
    while p < len(text):
        m = TOKEN.match(text, p)
        if m is None:
            items.append(Item(text[p], nl, p, p + 1))
            p, nl = p + 1, 0
            continue
        s = m.group()
        if s.isspace():
            nl += s.count("\n")
        else:
            items.append(Item(s, nl, m.start(), m.end(), s.startswith("//")))
            nl = 0
        p = m.end()
    return items


def _value_end(t: Item) -> bool:
    return (
        t.role == "angle_close"
        or t.s in VALUES
        or t.s[0] in "\"'"
        or t.s[0].isdigit()
        or (IDENT.fullmatch(t.s) is not None and t.s not in RESERVED)
    )


def roles(cs: list[Item]) -> list[Item]:
    """The structural pass: bracket pairs, borrow angles, closure pipes, unary signs, inline blocks."""
    stack: list[int] = []
    for i, t in enumerate(cs):
        if t.s in OPENERS:
            stack.append(i)
        elif t.s in CLOSERS and stack:
            t.pair = stack.pop()
            cs[t.pair].pair = i
    angles: list[int] = []
    pipe = importing = False
    for i, t in enumerate(cs):
        if t.s == "<" and i and cs[i - 1].s in {"ro", "rw"}:
            t.role, angles = "angle_open", [*angles, i]
        elif t.s == ">" and angles:
            angles.pop()
            t.role = "angle_close"
        elif t.s in {"|", "||"} and not (i and _value_end(cs[i - 1])):
            t.role = "lam_open" if t.s == "|" else "lambda0"
            pipe = t.s == "|"
        elif t.s == "|" and pipe:
            t.role, pipe = "lam_close", False
        elif t.s in {"-", "!", "~"} and not (i and _value_end(cs[i - 1])):
            t.role = "unary"
        elif t.s in PREC and i and cs[i - 1].s == "fold":
            t.role = "fold"  # `fold + each ...`: the operator is an operand of fold, never a place to break
        elif t.s == "{" and i < t.pair and all(x.nl == 0 for x in cs[i + 1 : t.pair + 1]):
            body = cs[i + 1 : t.pair]  # One line without `;` is a list of expressions, `each f in R { a.$f, b.$f }`:
            t.role = "splice" if body and all(x.s != ";" for x in body) else "inline"  # a bracket, not a block.
        elif t.s == "(" and importing:
            t.role = "spaced"  # `import std.core (Option);`
        importing = t.s == "import" or (importing and t.s != ";")
    return cs


def space(a: Item | None, b: Item) -> bool:
    """Canonical spacing for one adjacent pair of code tokens."""
    if a is None or (a.s == "{" and b.s == "}"):
        return False
    if a.role in {"unary", "angle_open", "lam_open"} or b.role in {"angle_open", "angle_close", "lam_close"}:
        return False
    if a.s == "in" and b.s == "[":  # `tune K in [4, 8]` lists values; it indexes nothing
        return True
    if b.s in NO_LEAD or a.s in NO_TRAIL:
        return False
    if b.s == "(" and b.role != "spaced":
        return not (a.role == "angle_close" or a.s in CALLEES or _value_end(a))
    return True


# Rendering ---------------------------------------------------------------------------------------

Part = tuple[bool, str, bool]  # (space before, text, splittable binary operator)


def _join(parts: list[Part]) -> str:
    return "".join((" " if sp and i else "") + s for i, (sp, s, _) in enumerate(parts))


def _depths(parts: list[Part]) -> list[int]:
    out, d = [], 0
    for _, s, _ in parts:
        out.append(d)
        d += (s in OPENERS) - (s in CLOSERS)
    return out


def _close(parts: list[Part], i: int) -> int:
    d = 0
    for j in range(i, len(parts)):
        d += (parts[j][1] in OPENERS) - (parts[j][1] in CLOSERS)
        if d == 0:
            return j
    return -1


def _groups(parts: list[Part], cuts: list[int]) -> list[list[Part]]:
    edges = [0, *cuts, len(parts)]
    return [parts[a:b] for a, b in pairwise(edges) if b > a]


def _fill(groups: list[list[Part]], first: str, cont: str) -> list[str]:
    lines: list[str] = []
    cur: list[Part] = []
    pad = first
    for g in groups:
        if cur and len(pad) + len(_join(cur + g)) > WIDTH:
            lines.append(pad + _join(cur))
            cur, pad = [], cont
        cur += g
    return [*lines, pad + _join(cur)] if cur else lines


def wrap(parts: list[Part], indent: int) -> list[str]:
    """One line, or a greedy fill broken at top-level operators or the widest bracket group."""
    pad = STEP * indent
    if len(pad) + len(_join(parts)) <= WIDTH or len(parts) < 3:
        return [pad + _join(parts)]
    depth = _depths(parts)
    clause = [i for i in range(1, len(parts)) if depth[i] == 0 and parts[i][1] == "implements"]
    if clause:  # an implementation's signature, then what it implements on a line of its own
        return _fill(_groups(parts, clause[:1]), pad, pad + STEP)
    cuts = [i for i in range(1, len(parts)) if depth[i] == 0 and parts[i][2]]
    if cuts:
        return _fill(_groups(parts, cuts), pad, pad + STEP)
    best = (0, 0)
    for i, (_, s, _) in enumerate(parts):
        j = _close(parts, i) if depth[i] == 0 and s in {"(", "["} else -1
        if j > i + 1 and j - i > best[1] - best[0]:
            best = (i, j)
    if best == (0, 0):  # No bracket to open: a list at the top, such as a recipe's `where a = .., b = ..`.
        commas = [i + 1 for i in range(len(parts) - 1) if depth[i] == 0 and parts[i][1] == ","]
        return _fill(_groups(parts, commas), pad, pad + STEP)
    i, j = best
    body = parts[i + 1 : j]
    inner = _depths(body)
    cuts = [k + 1 for k in range(len(body) - 1) if inner[k] == 0 and body[k][1] == ","]
    head = [pad + _join(parts[: i + 1])]
    return head + _fill(_groups(body, cuts), pad + STEP, pad + STEP) + [pad + _join(parts[j:])]


class Writer:
    """Accumulates one logical line at a time and wraps it when it is flushed."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.parts: list[Part] = []
        self.indent = self.hang = 0

    def push(self, sp: bool, s: str, op: bool = False) -> None:
        self.parts.append((sp, s, op))

    def flush(self, hang: int = 0) -> None:
        if self.parts:
            self.lines += wrap(self.parts, self.indent + self.hang)
        self.parts, self.hang = [], hang

    def blank(self) -> None:
        if self.lines and self.lines[-1]:
            self.lines.append("")

    def comment(self, s: str, blank: bool) -> None:
        if self.parts:
            self.flush(1)  # a comment inside a statement forces a hanging break
        elif blank:
            self.blank()
        self.lines.append(STEP * (self.indent + self.hang) + s.rstrip())

    def trailing(self, s: str) -> None:
        if self.parts:
            self.push(True, s.rstrip())
            self.flush(1)
        elif self.lines and self.lines[-1]:
            self.lines[-1] += " " + s.rstrip()
        else:
            self.lines.append(STEP * self.indent + s.rstrip())

    def text(self) -> str:
        self.flush()
        lines = self.lines
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        return "".join(line + "\n" for line in lines)


def _fits(w: Writer, cs: list[Item], k: int) -> bool:
    """Would this one-line block, its else branches and what closes them end before column 100?"""
    end = cs[k].pair
    while end + 1 < len(cs) and cs[end + 1].s == "else":
        brace = next((j for j in range(end + 1, len(cs)) if cs[j].s == "{"), -1)
        if brace < 0 or cs[brace].pair < brace:
            break
        end = cs[brace].pair
    while end + 1 < len(cs) and cs[end + 1].s in GLUE:
        end += 1
    seg = [(space(cs[j - 1], cs[j]) if j > k else True, cs[j].s, False) for j in range(k, end + 1)]
    return len(STEP) * (w.indent + w.hang) + len(_join(w.parts)) + len(_join(seg)) + 1 <= WIDTH


def render(items: list[Item]) -> str:
    cs = roles([t for t in items if not t.comment])
    w, k, inline = Writer(), -1, 0
    for it in items:
        if it.comment:
            if it.nl:
                w.comment(it.s, it.nl >= 2 and not w.parts)
            else:
                w.trailing(it.s)
            continue
        k += 1
        prev = cs[k - 1] if k else None
        nxt = cs[k + 1] if k + 1 < len(cs) else None
        if not inline and not w.parts and it.nl >= 2 and it.s != "}" and not (prev and prev.s == "{"):
            w.blank()
        if it.s == "}" and inline:
            inline -= 1
            w.push(space(prev, it), it.s)
            if not inline and not (nxt and nxt.s in GLUE) and cs[it.pair].role != "splice":
                w.flush()
        elif it.s == "}":
            w.flush()
            w.indent = max(0, w.indent - 1)
            w.push(False, it.s)
            if not (nxt and nxt.s in GLUE):
                w.flush()
        elif it.s == "{":
            keep = bool(inline) or it.role == "splice" or (it.role == "inline" and _fits(w, cs, k))
            w.push(space(prev, it), it.s)
            if keep:
                inline += 1
            else:
                w.flush()
                w.indent += 1
        else:
            w.push(space(prev, it), it.s, it.s in PREC and not it.role)
            if it.s == ";" and not inline:
                w.flush()
    return w.text()


# Public interface --------------------------------------------------------------------------------


def comments(text: str) -> list[str]:
    return [t.s.rstrip() for t in scan(text) if t.comment]


def format_report(text: str) -> tuple[str, str]:
    """`(formatted, "")`, or `(text, reason)` when the result would not be faithful."""
    try:
        before = [t.s for t in lex(text)]
    except Diagnostic as e:
        return text, e.data["message"]
    out = render(scan(text))
    try:
        after = [t.s for t in lex(out)]
    except Diagnostic as e:  # pragma: no cover - a formatter bug, reported instead of written
        return text, "the formatted text does not lex: " + e.data["message"]
    if after != before:
        return text, "the formatted text has a different token stream"
    if comments(out) != comments(text):
        return text, "the formatted text has different comments"
    return out, ""


def format_source(text: str) -> str:
    """Format `text`, or return it unchanged when it does not lex or the result would differ."""
    return format_report(text)[0]


def sources(paths: list[Path]) -> list[Path]:
    return [q for p in paths for q in (sorted(p.rglob("*.cairn")) if p.is_dir() else [p])]


def format_paths(paths: list[Path], check: bool = False, diff: bool = False) -> int:
    """`cairn fmt`: rewrite in place, or report what would change. 1 when anything is wrong."""
    changed: list[str] = []
    failed: list[dict[str, str]] = []
    chunks: list[str] = []
    for path in sources(paths):
        text = path.read_text(encoding="utf-8")
        out, error = format_report(text)
        if error:
            failed.append({"file": str(path), "reason": error})
        elif out != text:
            changed.append(str(path))
            chunks += difflib.unified_diff(
                text.splitlines(True), out.splitlines(True), str(path), str(path) + " (formatted)"
            )
            if not (check or diff):
                path.write_text(out, encoding="utf-8")
    status = "formatted" if not (check or failed) else "would-change" if changed else "clean"
    report = {"status": status, "mode": "check" if check else "rewrite", "changed": changed, "not_formatted": failed}
    if diff:
        print("".join(chunks), end="")
    else:
        print(json.dumps(report, indent=2))
    return 1 if failed or (changed and (check or diff)) else 0

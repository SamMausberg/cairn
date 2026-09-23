"""Tokens: the one regular expression that cuts source into them, and the reserved words."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tree import MAX_SOURCE, fail


@dataclass(slots=True)
class Token:
    s: str
    line: int
    col: int
    start: int = -1
    end: int = -1


TOKEN = re.compile(  # No two alternatives match at the same first character, so the commonest go first.
    r"\s+|[A-Za-z_$][A-Za-z_0-9$]*|//[^\n]*|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)+'|0x[0-9A-Fa-f]+"
    r"|(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+(?:[eE][+-]?[0-9]+))|[0-9]+"
    r"|=>|->|\.\.|==|!=|<=|>=|&&|\|\||[-+*/%&|^]=|[{}()\[\],;:.@+*/%<>=!&|^~-]"
)
COMPOUND = {op + "=": op for op in "+-*/%&|^"}  # `x += e` is the checked `x = x + e`; wrapping stays by name
IDENT = re.compile(r"[A-Za-z_$][A-Za-z_0-9$]*\Z")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")
RESERVED = set(  # One readable paragraph of words beats a wall of quoted strings.
    "fn struct enum family let mut reg if else for each in while return true false ro rw nat "
    "effects pure extern unsafe defer match kernel module import compact where yield derive "
    "buffer stack zeroed break continue trait impl dyn const pub linear parallel reduce spawn try "
    "as type".split()  # Placements (@host @device @pinned @unified) are words only after `@`.
)
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}


def lex(text: str) -> list[Token]:
    if len(text.encode()) > MAX_SOURCE:
        fail("E-SOURCE-LIMIT", "Source exceeds the 2 MB bootstrap limit.")
    # Only a run of whitespace holds a newline: a comment, a string and a character literal all stop before one. So
    # only whitespace moves the line, and a token's column is its distance from where its line starts.
    out: list[Token] = []
    append, match = out.append, TOKEN.match
    p, end, line, start = 0, len(text), 1, 0
    while p < end:
        m = match(text, p)
        if not m:
            fail("E-LEX", f"Unexpected character {text[p]!r}.", Token("", line, p - start + 1))
        e = m.end()
        if text[p].isspace():
            if newlines := text.count("\n", p, e):
                line += newlines
                start = text.rindex("\n", p, e) + 1
        elif not text.startswith("//", p):
            append(Token(text[p:e], line, p - start + 1, p, e))
        p = e
    append(Token("<eof>", line, p - start + 1, p, p))
    return out


def unescape(token: Token) -> str:
    def replace(m: re.Match) -> str:
        body = m.group(1)
        if body[0] == "x" and len(body) == 3:
            return chr(int(body[1:], 16))
        if body not in ESCAPES:
            fail("E-LEX", f"Unknown escape \\{body}.", token)
        return ESCAPES[body]

    return re.sub(r"\\(x[0-9A-Fa-f]{2}|.)", replace, token.s[1:-1])


def comment_above(text: str, offset: int) -> list[str]:
    """The `//` lines directly above a declaration, as one paragraph (or nothing)."""
    lines = text[:offset].rstrip().split("\n") if offset > 0 else []
    found: list[str] = []
    while lines and lines[-1].lstrip().startswith("//"):
        found.insert(0, lines.pop().lstrip()[2:].strip())
    return [" ".join(found)] if found else []

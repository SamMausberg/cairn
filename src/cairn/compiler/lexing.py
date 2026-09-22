"""Tokens: the one regular expression that cuts source into them, and the reserved words."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tree import MAX_SOURCE, fail


@dataclass
class Token:
    s: str
    line: int
    col: int
    start: int = -1
    end: int = -1


TOKEN = re.compile(
    r"//[^\n]*|\s+|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)+'|0x[0-9A-Fa-f]+"
    r"|(?:[0-9]+\.[0-9]+(?:[eE][+-]?[0-9]+)?|[0-9]+(?:[eE][+-]?[0-9]+))|[0-9]+"
    r"|[A-Za-z_$][A-Za-z_0-9$]*|=>|->|\.\.|==|!=|<=|>=|&&|\|\||[{}()\[\],;:.@+*/%<>=!&|^~-]"
)
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
    out: list[Token] = []
    p, line, col = 0, 1, 1
    while p < len(text):
        m = TOKEN.match(text, p)
        if not m:
            fail("E-LEX", f"Unexpected character {text[p]!r}.", Token("", line, col))
        s = m.group()
        if not s.isspace() and not s.startswith("//"):
            out.append(Token(s, line, col, p, m.end()))
        if "\n" in s:
            line += s.count("\n")
            col = len(s.rsplit("\n", 1)[1]) + 1
        else:
            col += len(s)
        p = m.end()
    out.append(Token("<eof>", line, col, p, p))
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

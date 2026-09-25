"""What a field or a variant name refers to across the files of a project, and every token that writes it.

A field is written where its record declares it, where a declared extent or the `lends` clause of that record reads
it, and after the `.` of every access whose receiver the checker typed as that record. A variant is written where
its sum declares it, in `Sum.Variant`, in a bare `Variant` or `Variant(x)` the checker typed as that sum, and in a
match arm whose subject the checker typed as that sum. Every type here is one the checker's own sites recorded, so
nothing is guessed from spelling alone; the rename's recheck in `workspace.py` is what makes any token this search
missed or took in error refuse the rename instead of changing the program.
"""

from __future__ import annotations

from typing import Any

from ...compiler.syntax.parser import IDENT
from .document import Document, Item, dotted
from .names import named, qualified


def bodies(cs: list[Item]) -> list[tuple[str, int, int, int]]:
    """Each record or sum the tokens declare, as (struct or enum, its name's index, its `{`, its `}`); a record's
    declared extents and its `lends` clause sit inside the braces."""
    out = []
    for i, t in enumerate(cs):
        if t.s not in {"struct", "enum"} or i + 1 >= len(cs):
            continue
        j = i + 1
        while j < len(cs) and cs[j].s not in {"{", ";"}:
            j += 1
        if j < len(cs) and cs[j].s == "{":
            out.append((t.s, i + 1, j, cs[j].pair))
    return out


def declares(cs: list[Item], k: int, kind: str) -> bool:
    """Whether token `k`, inside a body, is the name a field or a variant is declared under."""
    later = cs[k + 1].s if k + 1 < len(cs) else ""
    starts = cs[k - 1].s in {"{", ";"}
    return bool(IDENT.fullmatch(cs[k].s)) and starts and (later == ":" or (kind == "enum" and later in {"(", ";", "}"}))


class Members:
    """The fields and variants of one analysed project, answered from its checked program and its sites."""

    def __init__(self, doc: Document):
        self.doc, self.cs, self.p = doc, doc.code, doc.program
        self.modules = doc.modules()
        self.decls = bodies(self.cs)
        self.ending: dict[int, list[dict]] = {}  # where a checked expression ends -> the expressions ending there
        self.starting: dict[int, list[dict]] = {}
        for s in doc.sites:
            self.ending.setdefault(s["end"], []).append(s)
            self.starting.setdefault(s["start"], []).append(s)

    def owner(self, decl: tuple[str, int, int, int]) -> str:
        at = decl[1]
        module = self.modules[at]
        return f"{module}.{self.cs[at].s}" if module else self.cs[at].s

    def sums(self) -> dict[str, Any]:
        assert self.p is not None
        return {**self.p.sums, **self.p.enums}

    def variants(self, full: str) -> set[str]:
        assert self.p is not None
        if full in self.p.enums:
            return set(self.p.enums[full])
        return {v for v, _ in self.p.sums.get(full, [])}

    def fields(self, full: str) -> set[str]:
        assert self.p is not None
        return {f for f, _ in self.p.records.get(full, [])}

    def typed(self, sites: list[dict]) -> str:
        """The record or sum the widest of these checked expressions has, as the checker named it."""
        return named(max(sites, key=lambda s: s["end"] - s["start"])["type"])[0] if sites else ""

    def receiver(self, dot: int) -> str:
        """The type of the expression before the `.` at `dot`."""
        return self.typed(self.ending.get(self.cs[dot - 1].end, [])) if dot else ""

    def subject(self, k: int) -> str:
        """The type of the subject of the `match` whose arm token `k` begins, or ""."""
        cs = self.cs
        opener = next((m for m in range(k - 1, -1, -1) if cs[m].s == "{" and cs[m].pair > k), -1)
        j = opener - 1
        while j >= 0 and cs[j].s not in {"match", ";", "{", "}"}:
            j -= 1
        if opener < 0 or j < 0 or cs[j].s != "match" or j + 1 >= opener:
            return ""
        whole = [s for s in self.starting.get(cs[j + 1].start, []) if s["end"] == cs[opener - 1].end]
        return self.typed(whole)

    def arm(self, k: int) -> bool:
        """Whether token `k` is the variant a match arm names: `V =>` or `V(x) =>`."""
        cs = self.cs
        after = k + 1
        if after < len(cs) and cs[after].s == "(":
            after = cs[after].pair + 1
        return after < len(cs) and cs[after].s == "=>"

    def at(self, k: int) -> tuple[str, str, str] | None:
        """What token `k` names, as (field or variant, its record or sum, its name), or None."""
        cs, t = self.cs, self.cs[k]
        for kind, name, opener, end in self.decls:
            if opener < k < end:
                full = self.owner((kind, name, opener, end))
                if kind == "enum" and declares(cs, k, kind):
                    return "variant", full, t.s
                if kind == "struct" and t.s in self.fields(full):
                    return "field", full, t.s  # declared here, or read by an extent or the lends clause
                return None
        path = dotted(cs, k)
        if "." in path:
            prefix = path.rsplit(".", 1)[0]
            sum_ = qualified(self.p, self.modules[k], prefix, self.sums()) if self.p else ""
            if sum_ and t.s in self.variants(sum_):
                return "variant", sum_, t.s
            record = self.receiver(k - 1)
            if self.p and record in self.p.records and t.s in self.fields(record):
                return "field", record, t.s
            return None
        if self.arm(k):
            sum_ = self.subject(k)
        else:  # a bare variant is checked as the qualified one it stands for: a field site of exactly its token
            here = self.starting.get(t.start, [])
            sum_ = self.typed(
                [s for s in here if s["tag"] == "call" or (s["tag"] in {"name", "field"} and s["end"] == t.end)]
            )
        if sum_ in self.sums() and t.s in self.variants(sum_):
            return "variant", sum_, t.s
        return None

    def tokens(self, kind: str, full: str, name: str) -> list[Item]:
        """Every token of the project that names this field or variant."""
        return [t for k, t in enumerate(self.cs) if t.s == name and self.at(k) == (kind, full, name)]

    def declaration(self, kind: str, full: str, name: str) -> Item | None:
        """The token its record or sum declares it under, when a file of the project writes that declaration."""
        for decl in self.decls:
            if self.owner(decl) == full and decl[0] == ("struct" if kind == "field" else "enum"):
                inside = range(decl[2] + 1, decl[3])
                return next(
                    (self.cs[k] for k in inside if self.cs[k].s == name and declares(self.cs, k, decl[0])), None
                )
        return None

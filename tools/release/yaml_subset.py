"""The subset of YAML the files under `.github/` are written in, read strictly and without a dependency.

It reads block mappings and block sequences nested by indentation, `- key: value` items, double-quoted scalars (read
as JSON strings, whose escapes YAML shares), `|` and `|-` literal blocks, `true` and `false`, and plain scalars that
YAML reads as the same string. It refuses everything else with the line it met it on: a flow collection, an anchor,
an alias, a tag, a single-quoted or folded scalar, a tab, trailing blanks, a duplicate key, a key without a value, and
a plain scalar YAML would read as a number, a null or a boolean. So a file this accepts means to GitHub what it means
to `sync_labels.py` and to the tests that read the issue forms.

    python3 tools/release/yaml_subset.py .github/labels.yml    # prints the file as JSON, or the refusal
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):(?: (.*))?$")
WORDS = {"y", "yes", "n", "no", "on", "off", "true", "false", "null"}  # what YAML 1.1 or 1.2 reads as no string


class YamlError(ValueError):
    pass


class Reader:
    def __init__(self, text: str):
        self.lines = text.split("\n")
        for number, line in enumerate(self.lines, 1):
            if "\t" in line or "\r" in line or line != line.rstrip(" "):
                raise YamlError(f"line {number}: a tab, a carriage return or trailing blanks")
        self.at = 0

    def fail(self, why: str) -> YamlError:
        return YamlError(f"line {self.at + 1}: {why}")

    def peek(self) -> tuple[int, str] | None:
        """The indentation and text of the next line that is neither blank nor a comment."""
        while self.at < len(self.lines) and (not self.lines[self.at].strip() or self.lines[self.at].lstrip()[0] == "#"):
            self.at += 1
        if self.at == len(self.lines):
            return None
        line = self.lines[self.at]
        return len(line) - len(line.lstrip(" ")), line.lstrip(" ")

    def block(self, indent: int) -> Any:
        """The mapping or sequence whose lines start at column `indent`."""
        here = self.peek()
        if here is None or here[0] != indent:
            raise self.fail("expected a mapping or a sequence here")
        return self.sequence(indent) if here[1] == "-" or here[1].startswith("- ") else self.mapping(indent)

    def sequence(self, indent: int) -> list[Any]:
        out = []
        while (here := self.peek()) and here[0] == indent and (here[1] == "-" or here[1].startswith("- ")):
            item = here[1][2:]
            if KEY.match(item):  # `- key: value` opens a mapping two columns in
                self.lines[self.at] = " " * (indent + 2) + item
                out.append(self.mapping(indent + 2))
            else:
                out.append(self.value(item, indent))
        return self.ended(indent, out)

    def mapping(self, indent: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while (here := self.peek()) and here[0] == indent and not here[1].startswith("-"):
            found = KEY.match(here[1])
            if not found:
                raise self.fail("expected `key: value`")
            if found[1] in out or found[1].lower() in WORDS:
                raise self.fail(f"{found[1]} is a second key of that name, or a key YAML may read as a boolean")
            out[found[1]] = self.value(found[2], indent)
        return self.ended(indent, out)

    def ended(self, indent: int, value: Any) -> Any:
        here = self.peek()
        if here is not None and here[0] > indent:
            raise self.fail("indented further than what it belongs to")
        return value

    def value(self, text: str | None, indent: int) -> Any:
        """The value after a key or a dash on the current line, and any lines under it."""
        if text and text not in ("|", "|-"):
            value = scalar(text, self.fail)
            self.at += 1
            return value
        self.at += 1
        if text:
            return self.literal(indent, keep=text == "|")
        here = self.peek()
        if here is None or here[0] <= indent:
            raise self.fail("a key with no value, which YAML reads as null")
        return self.block(here[0])

    def literal(self, indent: int, keep: bool) -> str:
        """A `|` block: every following line indented past `indent`, or blank, as written."""
        rows = []
        while self.at < len(self.lines):
            line = self.lines[self.at]
            if line and len(line) - len(line.lstrip(" ")) <= indent:
                break
            rows.append(line)
            self.at += 1
        while rows and not rows[-1]:
            rows.pop()
        if not rows:
            raise self.fail("an empty literal block")
        first = next(row for row in rows if row)  # the first line that is not blank sets the margin
        margin = len(first) - len(first.lstrip(" "))
        if any(row and len(row) - len(row.lstrip(" ")) < margin for row in rows):
            raise self.fail("a literal block line indented less than its first")
        return "\n".join(row[margin:] for row in rows) + ("\n" if keep else "")


def scalar(text: str, fail: Any) -> Any:
    if text.startswith('"'):
        end, i = None, 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                end = i
                break
            i += 1
        if end is None or (text[end + 1 :].strip() and not text[end + 1 :].startswith(" #")):
            raise fail("a double-quoted scalar ends on its own line, followed by nothing or a comment")
        try:
            return json.loads(text[: end + 1])
        except ValueError as error:
            raise fail(f"an escape YAML and JSON do not share ({error})") from None
    if text in ("true", "false"):
        return text == "true"
    if not text[0].isalpha() or text.lower() in WORDS or ": " in text or " #" in text or text.endswith(":"):
        raise fail(f"quote {text!r}: YAML may not read it as this string")
    return text


def load(text: str) -> Any:
    """The value of a document in the subset; YamlError, naming the line, for anything outside it."""
    reader = Reader(text)
    here = reader.peek()
    if here is None:
        raise YamlError("an empty document")
    value = reader.block(0)
    if reader.peek() is not None:
        raise reader.fail("expected the document to end")
    return value


def read(path: Path) -> Any:
    return load(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    try:
        print(json.dumps(read(Path(sys.argv[1])), indent=2))
    except (YamlError, OSError) as error:
        sys.exit(f"{sys.argv[1]}: {error}")

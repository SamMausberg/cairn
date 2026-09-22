"""What a person at a terminal reads: a diagnostic beside its source line, and a result in one line.

Piped output stays the JSON record, so scripts, tests and agents read exactly what they read before. A terminal
gets this rendering unless `--format json` or `CAIRN_FORMAT=json` asks for the record; `NO_COLOR` turns colour off.
"""

from __future__ import annotations

import difflib
import os
import re
import signal
import sys
from typing import TextIO

from ..compiler.lexing import TOKEN

SHOWN = {"protocol", "status", "code", "message", "line", "column", "trust", "file"}  # rendered in the header
CHOICES = ("available_names", "available_variants")  # a misspelling is answered with the nearest of these


def human(choice: str | None, stream: TextIO | None = None) -> bool:
    tty = (stream or sys.stdout).isatty()
    choice = choice or os.environ.get("CAIRN_FORMAT") or ("human" if tty else "json")
    return choice == "human"


def paint(stream: TextIO):
    on = stream.isatty() and "NO_COLOR" not in os.environ

    def style(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if on else text

    return style


def diagnostic(data: dict, source: str, stream: TextIO | None = None) -> None:
    """`error[E-CODE]: message`, the file position, and the line with the offending token underlined."""
    stream = stream or sys.stderr
    s = paint(stream)
    word = "unknown" if data.get("status") == "unknown" else "error"
    print(s(f"{word}[{data.get('code', 'E-UNKNOWN')}]", "1;31") + s(f": {data.get('message', '')}", "1"), file=stream)
    line, column = int(data.get("line") or 0), int(data.get("column") or 0)
    where = data.get("file", "")
    if where or line:
        print(f"  {s('-->', '1;34')} {where}{f':{line}:{column}' if line else ''}", file=stream)
    text = excerpt(source, int(data.get("source_line") or line))
    if text is not None and line:
        gutter = " " * len(str(line))
        mark = " " * max(column - 1, 0) + "^" * width(text, column)
        print(s(f"{gutter} |", "1;34"), file=stream)
        print(s(f"{line} |", "1;34") + f" {text}", file=stream)
        print(s(f"{gutter} |", "1;34") + " " + s(mark, "1;31"), file=stream)
    for key, value in data.items():
        if key in SHOWN or key == "source_line" or value in (None, "", []):
            continue
        if key in CHOICES and isinstance(value, list):
            asked = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", data.get("message", ""))
            near = [m for a in asked for m in difflib.get_close_matches(a, [str(v) for v in value], 1)]
            if near:
                print(f"  {s('= help', '1;36')}: did you mean {near[0]}?", file=stream)
            continue
        shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        if shown in data.get("message", ""):  # the message already says it
            continue
        print(f"  {s('= note', '1;36')}: {key.replace('_', ' ')}: {shown}", file=stream)


def excerpt(source: str, line: int) -> str | None:
    lines = source.splitlines()
    return lines[line - 1].rstrip() if 0 < line <= len(lines) else None


def width(text: str, column: int) -> int:
    """The length of the token that starts at `column`, so the underline covers the word the message names."""
    m = TOKEN.match(text, max(column - 1, 0))
    return max(1, len(m.group())) if m and not m.group().isspace() else 1


def summary(result: dict, stream: TextIO | None = None) -> None:
    """One line for a result a person asked for; the JSON record holds everything else."""
    stream = stream or sys.stdout
    s = paint(stream)
    status = str(result.get("status", ""))
    good = status in {"typed", "native-built", "passed-finite-tests", "created"}
    detail = {
        "typed": lambda: typed(result),
        "native-built": lambda: str(result.get("artifact", "")),
        "created": lambda: str(result.get("project", "")),
        "passed-finite-tests": lambda: cases(result),
        "tests-not-passed": lambda: cases(result),
    }.get(status, lambda: str(result.get("message") or result.get("stderr") or "").strip())()
    print(s(status, "1;32" if good else "1;31") + (f": {detail}" if detail else ""), file=stream)
    for test in result.get("tests", []):
        if test.get("status") != "passed-finite-tests":
            print(f"  {test.get('contract')}: {test.get('status')} {test.get('message', '')}".rstrip(), file=stream)
    generics = {n: v for n, v in result.get("generics", {}).items() if v != "ok"}
    for name, verdict in generics.items():
        print(f"  {name}: {verdict}", file=stream)


def typed(result: dict) -> str:
    """The program's own functions, and apart from them what its imports of the library brought in."""
    library = int(result.get("library_functions", 0))
    own = plural(int(result.get("functions", 0)) - library, "function")
    return own + (f", and {library} from the library" if library else "")


def cases(result: dict) -> str:
    tests = result.get("tests", [])
    return f"{plural(len(tests), 'contract')}, {plural(sum(t.get('cases', 0) for t in tests), 'case')}"


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' * (count != 1)}"


def ended(code: int) -> str:
    """How a child ended: a guard that fails aborts, so SIGABRT is the usual signal."""
    if code >= 0:
        return f"exited with status {code}"
    name = signal.Signals(-code).name if -code in signal.valid_signals() else f"signal {-code}"
    return f"was stopped by {name}" + (": a guard failed" if name == "SIGABRT" else "")

"""What a person at a terminal reads: a diagnostic beside its source line, and a result in one line.

Piped output stays the JSON record, so scripts, tests and agents read the same fields. A terminal gets this rendering
unless `--format json` or `CAIRN_FORMAT=json` asks for the record; `NO_COLOR` turns colour off.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from collections.abc import Callable
from typing import TextIO

from ..compiler.syntax.lexing import TOKEN

SHOWN = {"protocol", "status", "code", "message", "line", "column", "trust", "file", "module"}  # in the header
TALLIED = {"further", "further_omitted", "not_judged", "further_stopped"}  # said once, after a check's refusals
TAUGHT = {"repair_hint", "card", "source_line", "available_names", "available_variants", "available_fields"}  # below


def human(choice: str | None, stream: TextIO | None = None) -> bool:
    tty = (stream or sys.stdout).isatty()
    choice = choice or os.environ.get("CAIRN_FORMAT") or ("human" if tty else "json")
    return choice == "human"


def record(value: dict, lines: bool = False) -> str:
    """The JSON record as a command prints it: indented for a person at a terminal who asked for JSON, else one
    compact line. A piped record is read by a program or an agent, which reads its indentation too, and that was 8%
    to 37% of each record's tokens. `lines` asks for one line even at a terminal."""
    if sys.stdout.isatty() and not lines:
        return json.dumps(value, indent=2, allow_nan=False)
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


def paint(stream: TextIO):
    on = stream.isatty() and "NO_COLOR" not in os.environ

    def style(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if on else text

    return style


def diagnostic(data: dict, source: str, stream: TextIO | None = None) -> None:
    """`error[E-CODE]: message`, the file position, the line with the offending token underlined, then what else the
    record says, the fix as `= help` and the card that states the rule."""
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
        if key in SHOWN or key in TALLIED or key in TAUGHT or value in (None, "", []):
            continue
        shown = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        if shown in data.get("message", ""):  # the message already says it
            continue
        print(f"  {s('= note', '1;36')}: {key.replace('_', ' ')}: {shown}", file=stream)
    if hint := data.get("repair_hint"):  # a close name among the available ones, or the smallest fix
        print(f"  {s('= help', '1;36')}: {hint[:1].lower()}{hint[1:]}", file=stream)
    if card := data.get("card"):
        print(f"  {s('= note', '1;36')}: the {card} card states this rule: cairn rules {data['code']}", file=stream)


def refusals(located: dict, raw: dict, source: Callable[[dict], str], stream: TextIO | None = None) -> None:
    """Every refusal of one check, each beside its own line, then a line that counts them. `located` is the record at
    each file's own lines, `raw` the same at the lines of `source(d)`, the text a refusal `d` is in."""
    placed, given = [located, *located.get("further", [])], [raw, *raw.get("further", [])]
    for k, (d, at) in enumerate(zip(placed, given, strict=True)):
        if k:
            print(file=stream or sys.stderr)
        diagnostic({**d, "source_line": at["line"]}, source(d), stream)
    tally(located, stream)


def tally(data: dict, stream: TextIO | None = None) -> None:
    """After the refusals of one check: how many there were, how many functions they left without a verdict, and what
    ended the check early."""
    stream = stream or sys.stderr
    s = paint(stream)
    omitted, lost, stopped = data.get("further_omitted", 0), data.get("not_judged", 0), data.get("further_stopped")
    count = 1 + len(data.get("further", [])) + omitted
    if count == 1 and not lost and not stopped:
        return
    said = f"{count} refusal{'s' * (count > 1)}" + (f", {omitted} not shown" if omitted else "")
    said += f"; {lost} function{'s' * (lost > 1)} not judged because of {'them' if count > 1 else 'it'}" if lost else ""
    said += f"; the check stopped early on {stopped}, so later functions were not judged" if stopped else ""
    print(s("error", "1;31") + s(f": {said}", "1"), file=stream)


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
    if result.get("schema") == "cairn.validation/1":
        return validation(result, stream, s)
    good = status in {"typed", "native-built", "passed-finite-tests", "created"}
    detail = {
        "typed": lambda: typed(result),
        "native-built": lambda: str(result.get("artifact", "")),
        "created": lambda: str(result.get("project", "")),
        "passed-finite-tests": lambda: cases(result),
        "tests-not-passed": lambda: cases(result),
    }.get(status, lambda: str(result.get("message") or result.get("stderr") or "").strip())()
    print(s(status, "1;32" if good else "1;31") + (f": {detail}" if detail else ""), file=stream)
    if emulated := result.get("emulation") or result.get("blocks", {}).get("emulation"):
        print(f"  {emulated['claim']}", file=stream)
    for test in result.get("tests", []):
        if test.get("status") != "passed-finite-tests":
            print(f"  {test.get('contract')}: {test.get('status')} {test.get('message', '')}".rstrip(), file=stream)
    blocks = result.get("blocks", {})
    for test in blocks.get("tests", []):
        if test.get("status") != "passed":
            print(f"  test {test.get('name')}: {test.get('reason', '')}", file=stream)
    if blocks.get("status") in {"native-build-failed", "unknown"}:
        said = (blocks.get("build", {}).get("stderr") or blocks.get("build", {}).get("message") or "").strip()
        print(f"  the tests did not build: {said.splitlines()[0] if said else blocks['status']}", file=stream)
    generics = {n: v for n, v in result.get("generics", {}).items() if v != "ok"}
    for name, verdict in generics.items():
        print(f"  {name}: {verdict}", file=stream)


def rules(record: dict, stream: TextIO | None = None) -> None:
    """`cairn rules`: each card under a line naming it and its codes, one paragraph per line; the list is one line a
    card."""
    stream = stream or sys.stdout
    s = paint(stream)
    if "always" in record:
        print(f"Every program also gets {', '.join(record['always'])}; cairn rules NAME prints one.", file=stream)
    for card in record["cards"]:
        codes = ", ".join(card["codes"])
        if "text" not in card:
            print(s(card["name"].ljust(16), "1") + (codes or "(no codes)"), file=stream)
            continue
        said = (
            f"{record['code']} is a rule of the {card['name']} card"
            if record.get("code")
            else f"The {card['name']} card"
        )
        print("\n" + s(said, "1") + (f": {codes}" if codes else ""), file=stream)
        for paragraph in card["text"].splitlines():
            print(f"\n{paragraph}", file=stream)


def validation(result: dict, stream: TextIO, s) -> None:
    """`cairn validate`: the finite result, a failure's shrunk input, then what Z3 established apart from it and what
    its counterexample did when replayed."""
    finite, smt = result.get("finite", {}), result.get("smt", {})
    replay = smt.get("replay", {})
    status = str(result.get("status", ""))
    said = f"{result.get('implementation')} against {result.get('reference')}"
    if finite:
        emulated = result.get("emulation", {}).get("judged_against")
        tested = f"finite-tested on a host emulation of {emulated}, not on a device" if emulated else "finite-tested"
        said += f", {plural(finite.get('cases', 0), 'case')} ({finite.get('implementation_ran', 0)} ran it), {tested}"
    print(s(status, "1;32" if status == "passed" else "1;31") + f": {said}", file=stream)
    found = finite if finite.get("failed") else replay
    if failed := found.get("failed"):
        shown = ", ".join(f"{k} = {v}" for k, v in failed.get("inputs", {}).items())
        print(f"  fails at {shown}" + (f"; kept in {found['kept']}" if found.get("kept") else ""), file=stream)
    if reason := result.get("reason") or finite.get("reason"):
        print(f"  {reason}", file=stream)
    if smt:
        print(f"  smt: {smt.get('status')} where {smt.get('where')}", file=stream)
    if replay:
        print(f"  its counterexample, replayed: {replay['status']}: {replay.get('reason', '')}".rstrip(), file=stream)


def typed(result: dict) -> str:
    """The program's own functions, and apart from them what its imports of the library brought in."""
    library = int(result.get("library_functions", 0))
    own = plural(int(result.get("functions", 0)) - library, "function")
    return own + (f", and {library} from the library" if library else "")


def cases(result: dict) -> str:
    """`3 tests, 1 contract, 81 cases`, or `1 of 3 tests failed`; a count of nothing is left out."""
    tests, blocks = result.get("tests", []), result.get("blocks", {}).get("tests", [])
    failed = sum(test.get("status") != "passed" for test in blocks)
    said = []
    if blocks:
        said.append(f"{failed} of {plural(len(blocks), 'test')} failed" if failed else plural(len(blocks), "test"))
    if tests or not blocks:
        said += [plural(len(tests), "contract"), plural(sum(t.get("cases", 0) for t in tests), "case")]
    return ", ".join(said)


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'s' * (count != 1)}"


def ended(code: int) -> str:
    """How a child ended: a guard that fails aborts, so SIGABRT is the usual signal."""
    if code >= 0:
        return f"exited with status {code}"
    name = signal.Signals(-code).name if -code in signal.valid_signals() else f"signal {-code}"
    return f"was stopped by {name}" + (": a guard failed" if name == "SIGABRT" else "")

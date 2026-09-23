"""What the documentation names exists: every diagnostic code in docs/, README.md and the skill is one the compiler,
the library or a host emits, and every `cairn` command and option docs/tools.md names is one the command line
parses, which in turn names every command."""

import re
import shlex
from pathlib import Path

from cairn.commands import parser
from cairn.editor.shells import commands, options

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "cairn"
TOOLS = ROOT / "docs" / "tools.md"
NAMED = re.compile(r"\bE-[A-Z0-9]+(?:-[A-Z0-9]+)*(?:-\*)?")
PLACEHOLDER = {"E-CODE"}  # how docs/README.md writes the code a `cairn rejects` block states
RESTATES = {"agent/teaching.py", "agent/diagnostics.py", "agent/skill.py"}  # they explain codes and emit none
OTHER_TOOLS = set("python python3 npm npx code make c++ cc ln nm size qemu-system-aarch64".split())  # not cairn's flags


def documents() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "skills").rglob("*.md"))]


def emitted() -> set[str]:
    """Every code a string literal of the compiler, runtime, library or hosts starts with: `fail("E-PLAN", ...)`,
    `{"code": "E-SESSION"}`, or a library `require` message such as "E-DERIVE-FIELD: ..."."""
    literal = re.compile(r"""(?<=["'])E-[A-Z0-9]+(?:-[A-Z0-9]+)*(?=["':])""")
    found: set[str] = set()
    for path in SOURCE.rglob("*"):
        if path.suffix in {".py", ".hpp", ".cairn"} and path.relative_to(SOURCE).as_posix() not in RESTATES:
            found |= set(literal.findall(path.read_text()))
    return found


def test_every_named_diagnostic_code_is_emitted():
    known = emitted()
    assert {"E-PLAN", "E-SESSION", "E-DERIVE-FIELD", "E-COOP-BARRIER"} <= known
    unknown = []
    for path in documents():
        for code in sorted(set(NAMED.findall(path.read_text())) - PLACEHOLDER):
            if code.endswith("-*"):
                ok = any(k.startswith(code[:-1]) for k in known)
            else:
                ok = code in known
            if not ok:
                unknown.append(f"{path.relative_to(ROOT)}: {code}")
    assert not unknown, "codes nothing emits:\n" + "\n".join(unknown)


def spans(text: str):
    """What tools.md writes as command text: inline code, headings, and the lines of `sh` blocks and of `text`
    blocks that show a command."""
    fence = None
    for line in text.splitlines():
        if line.startswith("```"):
            fence = None if fence is not None else line[3:].strip()
            continue
        if fence == "sh":
            yield line.split(" #")[0]
        elif fence == "text":
            if line.startswith(("$ ", "cairn ")):
                yield line.removeprefix("$ ")
        elif fence is None:
            if line.startswith("#"):
                yield line.lstrip("#").strip()
            yield from re.findall(r"`([^`]+)`", line)


def words(span: str) -> list[str]:
    try:
        return shlex.split(span, comments=False)
    except ValueError:
        return span.split()


def flag(word: str) -> str:
    return word.split("=")[0]


def test_every_command_and_option_tools_md_names_is_parsed():
    table = {name: {o for a in options(sub) for o in a.option_strings} for name, (_, sub) in commands(parser()).items()}
    every = set().union(*table.values()) | {"--help", "--version"}
    wrong = []
    for span in spans(TOOLS.read_text()):
        tokens = words(span)
        if not tokens or tokens[0] in OTHER_TOOLS or tokens[0].startswith("./"):
            continue
        command = None
        for at, token in enumerate(tokens):
            if token in {"|", "&&", ";", ">", "2>&1"}:
                command = None
            elif (token == "cairn" or token.endswith("/cairn")) and at + 1 < len(tokens):
                name = tokens[at + 1]
                if re.fullmatch(r"[a-z][a-z-]*", name):
                    if name not in table:
                        wrong.append(f"`{span}`: no command {name}")
                    command = name
            elif token.startswith("--") and len(token) > 2:
                allowed = table.get(command, every) if command else every
                if flag(token) not in allowed:
                    wrong.append(f"`{span}`: {command or 'cairn'} takes no {flag(token)}")
    assert not wrong, "\n".join(wrong)


def test_tools_md_names_every_command():
    named = set()
    for span in spans(TOOLS.read_text()):
        named |= set(re.findall(r"\bcairn ([a-z][a-z-]*)", span))
    missing = sorted(set(commands(parser())) - named)
    assert not missing, f"docs/tools.md never names cairn {', cairn '.join(missing)}"

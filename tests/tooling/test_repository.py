"""The rules AGENTS.md states about the tree itself: file length, and how the documentation is written."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECORDS = ("evidence/", "training/", "docs/history/")  # what ran or was proposed then, kept as it was written
SOURCES = {".py", ".cairn", ".hpp", ".cpp", ".cu", ".lean", ".md", ".js", ".S", ".ld", ".toml", ".yml"}
EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿⭐✅❌]")


def tracked() -> list[str]:
    try:
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the tree rules read the tracked files, which needs a git checkout")
    return [name for name in listed.stdout.splitlines() if not name.startswith(RECORDS)]


def test_no_source_file_is_longer_than_800_lines():
    long = {}
    for name in tracked():
        path = ROOT / name
        if path.suffix in SOURCES and path.is_file():
            lines = path.read_text(encoding="utf-8").count("\n")
            if lines > 800:
                long[name] = lines
    assert not long, f"split these by responsibility: {long}"


def prose(text: str):
    """Each line outside a fenced block and the front matter of a template, with its number."""
    fenced, lines = False, text.splitlines()
    start = lines.index("---", 1) + 1 if lines[:1] == ["---"] else 0
    for number, line in enumerate(lines[start:], start + 1):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            yield number, line


def test_the_documentation_follows_the_writing_rules():
    broken = []
    for name in tracked():
        if not name.endswith(".md"):
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        previous = ""
        for number, line in prose(text):
            if "—" in line:
                broken.append(f"{name}:{number}: an em dash")
            if EMOJI.search(line):
                broken.append(f"{name}:{number}: an emoji")
            sentence = bool(re.match(r"[A-Za-z`*\[(]", line))
            if sentence and previous and not name.startswith("docs/std"):  # the generated reference pairs lines
                broken.append(f"{name}:{number}: a paragraph continues on a second line; one paragraph per line")
            previous = line if sentence else ""
    assert not broken, "\n".join(broken)

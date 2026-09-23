"""The rules AGENTS.md states about the tree itself: file length, and how the documentation is written."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECORDS = (
    "evidence/",
    "tools/corpus/lessons/",
    "tools/corpus/semantic/",
)  # what ran, and generated corpora, kept as written
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


def test_a_test_module_is_named_by_its_subject_not_by_a_release():
    """`test_review_1_4b.py` said when a file was written; `test_review_tools.py` says what it holds."""
    dated = [name for name in tracked() if re.match(r"tests/.*/test_\w*\d\w*\.py$", name)]
    assert not dated, f"rename these by what they test: {dated}"


def test_a_forked_child_that_may_trap_starts_no_crash_handler():
    """A C++ driver that forks a child per case makes the child non-dumpable. Where core dumps go through a pipe, as
    systemd-coredump takes them on CI, a crash handler started for each trapping child kept the soundness tests past
    their thirty minutes under clang 18."""
    here = str(Path(__file__).relative_to(ROOT))
    forked = [n for n in tracked() if n.endswith(".py") and n != here and "fork()" in (ROOT / n).read_text()]
    assert forked, "the drivers this rule is about have moved"
    dumped = [n for n in forked if "PR_SET_DUMPABLE" not in (ROOT / n).read_text()]
    assert not dumped, f"call prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) in each forked child of: {dumped}"


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


def anchors(path: Path) -> set[str]:
    """The fragments GitHub gives a Markdown file's headings: lowercased, punctuation dropped, spaces as hyphens."""
    found: set[str] = set()
    for _, line in prose(path.read_text(encoding="utf-8")):
        if heading := re.match(r"#+ (.*)", line):
            slug = re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-")
            again = [f"{slug}-{k}" for k in range(1, 100) if f"{slug}-{k}" not in found]
            found.add(slug if slug not in found else again[0])  # a repeated heading gets -1, -2, ...
    return found


def test_every_relative_link_names_a_file_and_heading_that_exist():
    """A renamed file or heading must not leave a link to it behind, in the docs or in a record's notes."""
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.md"], capture_output=True, text=True)
    dangling = []
    for name in listed.stdout.splitlines() or pytest.skip("the link check reads the tracked files"):
        text = "\n".join(line for _, line in prose((ROOT / name).read_text(encoding="utf-8")))
        for target in re.findall(r"\]\(([^)\s]+)\)", re.sub(r"`[^`\n]*`", "", text)):  # code is not a link
            path, _, fragment = target.partition("#")
            if re.match(r"[a-z]+:", path):
                continue
            file = (ROOT / name).parent.joinpath(path) if path else ROOT / name
            if not file.exists():
                dangling.append(f"{name}: {target}")
            elif fragment and file.suffix == ".md" and fragment not in anchors(file):
                dangling.append(f"{name}: {target} (no such heading)")
    assert not dangling, "\n".join(dangling)


def test_make_help_names_every_target():
    """`make help` is how a newcomer finds the gates, so every target the Makefile declares is in it."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    declared = set(re.search(r"^\.PHONY:(.*)$", makefile, re.M).group(1).split()) - {"help"}
    helped = set(re.findall(r"^\t@echo '([a-z-]+) ", makefile, re.M))
    assert declared == helped, f"undocumented: {sorted(declared - helped)}, stale: {sorted(helped - declared)}"

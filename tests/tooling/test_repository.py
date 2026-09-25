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


def test_every_example_project_is_indexed_and_every_bench_folder_has_a_readme():
    """A newcomer finds a program from examples/README.md and a measurement from its folder's README."""
    names = tracked()
    index = (ROOT / "examples/README.md").read_text(encoding="utf-8")
    linked = {target.rstrip("/") for target in re.findall(r"\]\(([^)#\s]+)\)", index)}
    projects = {str(Path(n).parent.relative_to("examples")) for n in names if re.match(r"examples/.+/cairn\.toml$", n)}
    missing = [p for p in sorted(projects) if not any(p == d or p.startswith(d + "/") for d in linked & projects)]
    assert not missing, f"list these in examples/README.md: {missing}"
    folders = {n.split("/")[1] for n in names if n.startswith("bench/") and n.count("/") >= 2}
    bare = sorted(f for f in folders if f"bench/{f}/README.md" not in names)
    assert not bare, f"give these bench folders a README.md: {bare}"


def test_every_record_is_indexed_and_from_1_1_on_says_what_ran():
    """evidence/README.md and a release's own README under it list each of its records, and from 1.1 on each record has
    a README that says what ran and what did not."""
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "evidence"], capture_output=True, text=True).stdout
    names = set(listed.splitlines())
    for release in sorted({n.split("/")[1] for n in names if n.count("/") >= 2}):
        index = ROOT / "evidence" / release / "README.md"
        if not index.is_file():
            continue  # a release before 1.0 is indexed by evidence/README.md itself
        text = index.read_text(encoding="utf-8")
        records = {n.split("/")[2] for n in names if n.startswith(f"evidence/{release}/") and n.count("/") >= 3}
        unlisted = sorted(r for r in records if f"`{r}/`" not in text)
        assert not unlisted, f"list these in evidence/{release}/README.md: {unlisted}"
        if tuple(map(int, release[1:].split("_"))) >= (1, 1):
            bare = sorted(r for r in records if f"evidence/{release}/{r}/README.md" not in names)
            assert not bare, f"give these records a README.md that says what ran: {bare}"


def test_every_module_of_the_package_has_an_owner_line():
    """AGENTS.md's ownership table, or a table of docs/internals.md, names every module and runtime header."""
    rows = [line for doc in ("AGENTS.md", "docs/internals.md") for line in (ROOT / doc).read_text().splitlines()
            if line.startswith("|")]  # fmt: skip
    cells = {piece for row in rows for cell in re.findall(r"`([^`]+)`", row) for piece in re.split(r"[\s,]+", cell)}
    package = [n.removeprefix("src/cairn/") for n in tracked() if re.match(r"src/cairn/.+\.(py|hpp)$", n)]
    unnamed = [n for n in package if Path(n).name not in {"__init__.py", "__main__.py"} and n not in cells
               and f"src/cairn/{n}" not in cells and Path(n).name not in cells]  # fmt: skip
    assert not unnamed, f"say what these own in AGENTS.md's table: {unnamed}"


def test_make_help_names_every_target():
    """`make help` is how a newcomer finds the gates, so every target the Makefile declares is in it."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    declared = set(re.search(r"^\.PHONY:(.*)$", makefile, re.M).group(1).split()) - {"help"}
    helped = set(re.findall(r"^\t@echo '([a-z-]+) ", makefile, re.M))
    assert declared == helped, f"undocumented: {sorted(declared - helped)}, stale: {sorted(helped - declared)}"

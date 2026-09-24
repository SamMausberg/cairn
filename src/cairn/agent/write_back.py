"""An admitted candidate written back to the files a session read.

A session opened on a path pins what it read: the manifest and every file of the project, by sha256. Its host judges
the combined source a project's files make (`projects/project.py`), so an admitted candidate is split back into files
by the layout that source has (`Project.split`). A candidate is written only when the files still hold exactly the
source the host judged it against, else the write is refused as stale (E-SESSION) and nothing is written; each file is
compared again as its new text is staged, so a save between the check and the write is caught too. Only the files the
candidate changes are written, never a vendored one, and all of them or none: each is written beside itself and
renamed into place, and a failure part way puts back every file already renamed.

A file is written as it was written before. A project reads a file's text after its byte-order mark and keeps its
carriage returns (`projects/project.py`); a write puts the mark back when the file had one, keeps each line it leaves
alone with the ending it had, and ends each line it adds or changes as most of the file's lines end, LF on a tie. So a
file whose lines all end in CRLF stays CRLF throughout, and in a file of mixed endings only the new lines choose. A
host's candidate may end its new lines in LF where the file ends them in CRLF, so what a session judged and what the
files hold are compared with their line endings aside; the pins still compare every byte.
"""

from __future__ import annotations

import os
from difflib import SequenceMatcher
from pathlib import Path

from ..compiler.cairnc import Diagnostic, fail
from ..projects.project import MARK, Project, ProjectError, decoded, load_project


def lines(text: str) -> list[tuple[str, str]]:
    """Each line of `text` and its ending: CRLF, LF, or nothing for a last line without one."""
    pieces = text.split("\n")
    ended = [(p[:-1], "\r\n") if p.endswith("\r") else (p, "\n") for p in pieces[:-1]]
    return ended + ([(pieces[-1], "")] if pieces[-1] else [])


def kept(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> dict[int, str]:
    """The ending each line of `new` that `old` already held had there, by the line's index in `new`: the lines both
    start and end with, and between them the lines a diff of the rest matches."""
    a, b = [text for text, _ in old], [text for text, _ in new]
    head, tail, most = 0, 0, min(len(a), len(b))
    while head < most and a[head] == b[head]:
        head += 1
    while tail < most - head and a[-1 - tail] == b[-1 - tail]:
        tail += 1
    found = {j: old[j][1] for j in range(head)} | {len(b) - 1 - k: old[len(a) - 1 - k][1] for k in range(tail)}
    middle = SequenceMatcher(None, a[head : len(a) - tail], b[head : len(b) - tail], autojunk=False)
    for i, j, n in middle.get_matching_blocks():
        found |= {head + j + k: old[head + i + k][1] for k in range(n)}
    return found


def faithful(before: bytes, text: str) -> bytes:
    """`text`, a new version of the file that held `before`, in that file's own form: its byte-order mark if it had
    one, each line it keeps with the ending it had, and each other line with the ending most of its lines have."""
    old, new = lines(decoded(before)), lines(text)
    crlf, lf = sum(e == "\r\n" for _, e in old), sum(e == "\n" for _, e in old)
    usual = "\r\n" if crlf > lf else "\n"
    had = kept(old, new) if crlf and lf else {}  # with one ending throughout, every line takes it
    body = "".join(line + (ending and (had.get(j) or usual)) for j, (line, ending) in enumerate(new))
    return (MARK if before.startswith(MARK.encode()) else "").encode() + body.encode("utf-8")


def unended(text: str) -> str:
    """`text` with every CRLF read as LF: what a session and the files it wrote agree on."""
    return text.replace("\r\n", "\n")


def replace(
    root: Path, texts: dict[str, str], suffix: str = ".cairn-write", expected: dict[str, str] | None = None
) -> None:
    """Every file of `texts` (a path under `root` -> its new text) written beside itself in the file's own form
    (`faithful`), then renamed into place; a failure puts back what was renamed, byte for byte. With `expected`, a file
    whose text is no longer its expected text when its new text is staged is stale (E-SESSION), and nothing is
    renamed."""
    staged, made = [], []
    try:
        for path, text in texts.items():
            target = root / path
            before = target.read_bytes()
            try:
                now = decoded(before)
            except UnicodeDecodeError:
                now = None
            if now is None or (expected is not None and now != expected[path]):
                fail("E-SESSION", f"{path} changed while it was being written; nothing was written.", files=[path])
            beside = target.with_name(target.name + suffix)
            beside.write_bytes(faithful(before, text))
            made.append(beside)
            staged.append((target, beside, before))
        done: list[tuple[Path, bytes]] = []
        try:
            for target, beside, before in staged:
                os.replace(beside, target)
                done.append((target, before))
        except OSError:
            for target, before in reversed(done):
                target.write_bytes(before)
            raise
    finally:
        for beside in made:
            beside.unlink(missing_ok=True)


def pins(project: Project) -> dict[str, str | None]:
    """What a session pins of a project: the sha256 of its manifest and of every file, by path."""
    return {"(manifest)": project.manifest_sha256, **{u.path: u.sha256 for u in project.units}}


class Files:
    """The files one session read at `path` (a .cairn file, a project directory or a manifest), and after each write
    the files as written."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.project = load_project(self.path)
        self.pinned = pins(self.project)

    def home(self, line: int) -> str:
        """The file, as the manifest names it, that holds a line of the combined source."""
        return self.project.site(line)[0]

    def current(self, base: str) -> Project:
        """The project as its files hold it now, refused as stale unless they hold what this session pinned and their
        combined source is `base`, the source the host judges a candidate against."""
        try:
            now = load_project(self.path)
        except (ProjectError, OSError, ValueError, Diagnostic) as error:
            fail("E-SESSION", f"The files this session read cannot be read again ({error}); nothing was written.")
        held = pins(now)
        if changed := sorted(p for p in {*held, *self.pinned} if held.get(p) != self.pinned.get(p)):
            named = ", ".join("the manifest" if p == "(manifest)" else p for p in changed)
            fail("E-SESSION", f"{named} changed since this session read it; nothing was written. Open a new session.",
                 files=changed)  # fmt: skip
        if unended(now.source) != unended(base):
            fail("E-SESSION", "The files hold this session's earlier admitted change, not the source this reply was "
                 "judged against; nothing was written. Open a new session to change them again.")  # fmt: skip
        return now

    def write(self, base: str, candidate: str) -> list[str]:
        """Write `candidate`, a change the host admitted to `base`, into the files it changes; the paths written."""
        now = self.current(base)
        before, after, held = now.split(base), now.split(candidate), now.split(now.source)
        changed = {path: text for path, text in after.items() if text != before[path]}
        if vendored := sorted(set(changed) & set(now.vendored_units)):
            raise ProjectError(f"The change reaches {vendored[0]}, a vendored file, which a session does not write; "
                               "nothing was written.")  # fmt: skip
        replace(now.root, changed, expected={path: held[path] for path in changed})
        self.project = load_project(self.path)
        self.pinned = pins(self.project)
        return sorted(changed)

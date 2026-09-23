"""An admitted candidate written back to the files a session read.

A session opened on a path pins what it read: the manifest and every file of the project, by sha256. Its host judges
the combined source a project's files make (`projects/project.py`), so an admitted candidate is split back into files
by the layout that source has (`Project.split`). A candidate is written only when the files still hold exactly the
source the host judged it against, else the write is refused as stale (E-SESSION) and nothing is written. Only the
files the candidate changes are written, never a vendored one, and all of them or none: each is written beside itself
and renamed into place, and a failure part way puts back every file already renamed.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..compiler.cairnc import Diagnostic, fail
from ..projects.project import Project, ProjectError, load_project


def replace(root: Path, texts: dict[str, str], suffix: str = ".cairn-write") -> None:
    """Every file of `texts` (a path under `root` -> its new text) written beside itself, then renamed into place; a
    failure puts back what was renamed."""
    staged = []
    try:
        for path, text in texts.items():
            target = root / path
            beside = target.with_name(target.name + suffix)
            beside.write_text(text, encoding="utf-8")
            staged.append((target, beside, target.read_text(encoding="utf-8")))
        done: list[tuple[Path, str]] = []
        try:
            for target, beside, before in staged:
                os.replace(beside, target)
                done.append((target, before))
        except OSError:
            for target, before in reversed(done):
                target.write_text(before, encoding="utf-8")
            raise
    finally:
        for _, beside, _ in staged:
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
        if now.source != base:
            fail("E-SESSION", "The files hold this session's earlier admitted change, not the source this reply was "
                 "judged against; nothing was written. Open a new session to change them again.")  # fmt: skip
        return now

    def write(self, base: str, candidate: str) -> list[str]:
        """Write `candidate`, a change the host admitted to `base`, into the files it changes; the paths written."""
        now = self.current(base)
        before, after = now.split(base), now.split(candidate)
        changed = {path: text for path, text in after.items() if text != before[path]}
        if vendored := sorted(set(changed) & set(now.vendored_units)):
            raise ProjectError(f"The change reaches {vendored[0]}, a vendored file, which a session does not write; "
                               "nothing was written.")  # fmt: skip
        replace(now.root, changed)
        self.project = load_project(self.path)
        self.pinned = pins(self.project)
        return sorted(changed)

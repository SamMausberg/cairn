"""A program as a path holds it, or as a git revision holds it.

A revision is read object by object (`git ls-tree`, then `git cat-file`) into a scratch directory; nothing is ever
checked out over the working tree, and the repository is only read. A path wins over a revision of the same name.
`--std` compares the packaged library itself: every `src/cairn/std/*.cairn` as that side has it, as one program that
defines each module, so the package's own copy is never linked in its place.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import ProjectError, load_project

MAX_FILES, MAX_BYTES = 4000, 32_000_000
LIBRARY = "src/cairn/std"


@dataclass
class Side:
    """One version: what the user named, how it was read, and the one program it is."""

    named: str
    kind: str  # "path" or "revision"
    commit: str
    source: str
    mapper: Any = None  # a project's own map from a line of `source` to its file and line

    def locate(self, error: Any) -> dict:
        """A refusal of this version, placed in the file it came from, and saying which version it was."""
        return {**(self.mapper(error) if self.mapper else error.data), "version": self.named}


def git(repo: Path, *args: str, raw: bool = False):
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60, check=False)
    if done.returncode:
        raise ProjectError(f"git {' '.join(args[:2])} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout if raw else done.stdout.decode()


def repository(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").strip())


def commit(repo: Path, name: str) -> str | None:
    done = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", name + "^{commit}"],
                          capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    return done.stdout.strip() if done.returncode == 0 else None


def extract(repo: Path, rev: str, within: str, into: Path) -> Path:
    """Write the files under `within` at `rev` into `into`, keeping their paths; refuse links and submodules."""
    listed = git(repo, "ls-tree", "-r", "-z", rev, "--", within or ".").split("\0")
    entries = [line.split("\t", 1) for line in listed if line]
    if not entries:
        raise ProjectError(f"{rev} holds nothing under {within or '.'}.")
    if len(entries) > MAX_FILES:
        raise ProjectError(f"{within or '.'} at {rev} holds more than {MAX_FILES} files.")
    total = 0
    for meta, name in entries:
        mode, kind, oid = meta.split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ProjectError(f"{name} at {rev} is a link or a submodule, which a project may not hold.")
        # A tree written by hand may name an entry `..`, which git itself never checks out; such a path would
        # land outside the scratch directory, so every part must be an ordinary name.
        if any(part in {"", ".", ".."} or "\\" in part for part in name.split("/")):
            raise ProjectError(f"{name!r} at {rev} is not a path inside the tree, which a project may not hold.")
        data = git(repo, "cat-file", "blob", oid, raw=True)
        total += len(data)
        if total > MAX_BYTES:
            raise ProjectError(f"{within or '.'} at {rev} exceeds {MAX_BYTES} bytes.")
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return into / within


def library(root: Path) -> str:
    """The packaged library under `root` as one program, its modules in file order."""
    directory = root / LIBRARY if (root / LIBRARY).is_dir() else root
    files = sorted(directory.glob("*.cairn"))
    if not files:
        raise ProjectError(f"No library modules under {directory}.")
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def read(named: str, within: str = ".", std: bool = False, cwd: Path | None = None) -> Side:
    """`named` as a path, or else as a revision of the repository around `cwd`, with `within` the project inside it."""
    here = (cwd or Path.cwd()).resolve()
    path = Path(named)
    path = path if path.is_absolute() else here / path
    if path.exists():
        if std:
            return Side(named, "path", "", library(path))
        project = load_project(path)
        return Side(named, "path", "", project.source, project.locate)
    repo = repository(here)
    rev = commit(repo, named)
    if rev is None:
        raise ProjectError(f"{named} is neither a path nor a revision of {repo}.")
    target = (here / within).resolve()
    if not std and not target.is_relative_to(repo):
        raise ProjectError(f"{within} is outside the repository {repo}.")
    inner = LIBRARY if std else "" if target == repo else str(target.relative_to(repo))
    with tempfile.TemporaryDirectory(prefix="cairn-diff-") as scratch:
        root = extract(repo, rev, inner, Path(scratch))
        if std:
            return Side(named, "revision", rev, library(root))
        project = load_project(root)
        return Side(named, "revision", rev, project.source, project.locate)

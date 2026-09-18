"""Data-only, ordered multi-file projects. No imports, hooks or network resolution."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .syntax import MAX_SOURCE, Diagnostic
from .toolchain import ARCHS, KINDS, ProjectError


def read_text(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ProjectError(f"{path.name} exceeds its {limit}-byte input limit.")
    return data.decode("utf-8")


def contained_file(root: Path, value: str, suffix: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectError("Paths must be nonempty relative POSIX strings.")
    raw = value.split("/")
    if any(x in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in raw):
        raise ProjectError(f"Noncanonical project path: {value!r}")
    p = PurePosixPath(value)
    if p.is_absolute() or p.suffix != suffix:
        raise ProjectError(f"Expected a relative {suffix} path: {value!r}")
    target = root
    for part in p.parts:
        target = target / part
        if target.is_symlink():
            raise ProjectError(f"Symbolic links are not project inputs: {value!r}")
    if not target.resolve().is_relative_to(root) or not target.is_file():
        raise ProjectError(f"Missing or out-of-root project input: {value!r}")
    return target


@dataclass(frozen=True)
class Unit:
    path: str
    first_line: int
    lines: int
    sha256: str


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    source: str
    units: tuple[Unit, ...]
    contracts: tuple[str, ...] = ()
    kind: str = "library"
    arch: str = "baseline"
    manifest_sha256: str | None = None

    def locate(self, error: Diagnostic) -> dict:
        result = dict(error.data)
        line = result.get("line", 0)
        for unit in self.units:
            if unit.first_line <= line < unit.first_line + unit.lines:
                result.update(file=unit.path, line=line - unit.first_line + 1)
                break
        return result

    def receipt(self) -> dict:
        return {
            "name": self.name,
            "manifest_sha256": self.manifest_sha256,
            "sources": [u.__dict__ for u in self.units],
            "composition": "ordered single module; no namespace or separate compilation",
            "source_sha256": hashlib.sha256(self.source.encode()).hexdigest(),
        }


def load_project(path: str | Path = ".") -> Project:
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "cairn.toml"
    target = target.resolve(strict=True)
    root = target.parent
    if target.suffix == ".cairn":
        body = read_text(target, MAX_SOURCE)
        return Project(
            root,
            target.stem,
            body,
            (Unit(target.name, 1, body.count("\n") + 1, hashlib.sha256(body.encode()).hexdigest()),),
        )
    if target.name != "cairn.toml":
        raise ProjectError("Pass a .cairn file, a project directory, or cairn.toml.")
    manifest = read_text(target, 65536)
    data = tomllib.loads(manifest)
    if set(data) - {"project", "build"}:
        raise ProjectError("Unknown manifest tables; hooks and dependencies are not supported.")
    project, build = data.get("project", {}), data.get("build", {})
    if not isinstance(project, dict) or not isinstance(build, dict):
        raise ProjectError("project and build must be tables.")
    if set(project) - {"name", "sources", "tests"} or set(build) - {"kind", "arch"}:
        raise ProjectError("Unknown manifest option.")
    name = project.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ProjectError("Project name must be an ASCII name of 1..64 characters.")
    sources, contracts = project.get("sources"), project.get("tests", [])
    for label, values, low, high in [("sources", sources, 1, 64), ("tests", contracts, 0, 128)]:
        if not isinstance(values, list) or not low <= len(values) <= high:
            raise ProjectError(f"{label} must contain {low}..{high} paths.")
        if not all(isinstance(v, str) for v in values) or len(values) != len(set(values)):
            raise ProjectError(f"{label} contains duplicate or non-string paths.")
    kind, arch = build.get("kind", "library"), build.get("arch", "baseline")
    if not isinstance(kind, str) or not isinstance(arch, str) or kind not in KINDS or arch not in ARCHS:
        raise ProjectError("Unsupported build kind or explicit CPU architecture.")
    units, text, line, byte_count = [], [], 1, 0
    for relative in sources:
        body = read_text(contained_file(root, relative, ".cairn"), MAX_SOURCE)
        header = "// source: " + relative + "\n"
        units.append(Unit(relative, line + 1, body.count("\n") + 1, hashlib.sha256(body.encode()).hexdigest()))
        fragment = header + body + "\n"
        byte_count += len(fragment.encode())
        if byte_count > MAX_SOURCE:
            raise ProjectError("Combined project exceeds the 2 MB native source limit.")
        text.append(fragment)
        line += fragment.count("\n")
    combined = "".join(text)
    if len(combined.encode()) > MAX_SOURCE:
        raise ProjectError("Combined project exceeds the 2 MB native source limit.")
    for relative in contracts:
        contained_file(root, relative, ".json")
    return Project(
        root, name, combined, tuple(units), tuple(contracts), kind, arch, hashlib.sha256(manifest.encode()).hexdigest()
    )

"""Data-only, ordered multi-file projects with vendored dependencies. No hooks, no network, nothing outside the root."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .syntax import MAX_SOURCE, Diagnostic, Parser
from .toolchain import ARCHS, KINDS, TARGETS, ProjectError


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
    target: str = "hosted"
    manifest_sha256: str | None = None
    dependencies: tuple[
        dict, ...
    ] = ()  # name, path, manifest and source hashes of every vendored project, in load order

    def origin(self, line: int) -> tuple[str, int]:
        """The authored file and line behind a line of the combined source."""
        for unit in self.units:
            if unit.first_line <= line < unit.first_line + unit.lines:
                return str(self.root / unit.path), line - unit.first_line + 1
        return self.name, line

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
            "dependencies": list(self.dependencies),
            "composition": "ordered sources, vendored dependencies first; modules are the only namespaces",
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
    if target.suffix != ".toml":  # A directory means its cairn.toml; a second configuration is named: app/gpu.toml.
        raise ProjectError("Pass a .cairn file, a project directory, or a manifest (cairn.toml).")
    manifest = read_text(target, 65536)
    data = tomllib.loads(manifest)
    if set(data) - {"project", "build", "dependencies"}:
        raise ProjectError("Unknown manifest tables; hooks are not supported.")
    project, build = data.get("project", {}), data.get("build", {})
    if not isinstance(project, dict) or not isinstance(build, dict):
        raise ProjectError("project and build must be tables.")
    vendored, fragments = dependencies(root, data.get("dependencies", {}), {root})
    if set(project) - {"name", "sources", "tests"} or set(build) - {"kind", "arch", "target"}:
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
    machine = build.get("target", "hosted")
    if not isinstance(machine, str) or machine not in TARGETS:
        raise ProjectError(f"Unsupported build target; known targets are {', '.join(sorted(TARGETS))}.")
    units, text, line, byte_count = [], [], 1, 0
    own = [(relative, read_text(contained_file(root, relative, ".cairn"), MAX_SOURCE)) for relative in sources]
    for relative, body in [*fragments, *own]:
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
    digest = hashlib.sha256(manifest.encode()).hexdigest()
    return Project(root, name, combined, tuple(units), tuple(contracts), kind, arch, machine, digest, tuple(vendored))


def dependencies(
    root: Path, table: object, seen: set[Path], depth: int = 0
) -> tuple[list[dict], list[tuple[str, str]]]:
    """`[dependencies] geometry = "deps/geometry"`: a project vendored inside this one's root. Its sources load
    before ours (its own dependencies first), it contributes modules only, and only what it marks `pub` is
    reachable. Nothing is fetched and nothing outside the root is read; the receipt pins what was used."""
    if not isinstance(table, dict) or len(table) > 16 or depth > 4:
        raise ProjectError("dependencies is a table of at most 16 entries, nested at most 4 deep.")
    found, fragments = [], []
    for name, where in table.items():
        if (
            not isinstance(where, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*", where)
            or ".." in where.split("/")
        ):
            raise ProjectError(f"Dependency {name} must be a relative directory inside the project: {where!r}")
        home = root / where
        if any(part.is_symlink() for part in [home, *home.parents][: len(where.split("/"))]) or not home.is_dir():
            raise ProjectError(f"Missing dependency directory (symbolic links are not followed): {where!r}")
        if home.resolve() in seen:
            continue  # A diamond loads once; the first mention fixes its place in the order.
        seen.add(home.resolve())
        manifest = read_text(contained_file(home.resolve(), "cairn.toml", ".toml"), 65536)
        data = tomllib.loads(manifest)
        project = data.get("project", {})
        if not isinstance(project, dict) or project.get("name") != name:
            raise ProjectError(f"Dependency {name} is a project of another name at {where!r}.")
        inner, inner_fragments = dependencies(home.resolve(), data.get("dependencies", {}), seen, depth + 1)
        sources = project.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 64 or not all(isinstance(v, str) for v in sources):
            raise ProjectError(f"Dependency {name} must list 1..64 sources.")
        bodies = [(f"{where}/{s}", read_text(contained_file(home.resolve(), s, ".cairn"), MAX_SOURCE)) for s in sources]
        rooted = [path for path, body in bodies if "" in set(Parser(body).parse().modules.values())]
        if rooted:
            raise ProjectError(
                f"Dependency {name} declares outside any module in {rooted[0]}; a library is modules only."
            )
        found += [*inner, {"name": name, "path": where, "manifest_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
                           "source_sha256": hashlib.sha256("".join(b for _, b in bodies).encode()).hexdigest()}]  # fmt: skip
        fragments += [*[(f"{where}/{path}", body) for path, body in inner_fragments], *bodies]
    return found, fragments

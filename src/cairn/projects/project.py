"""Data-only, ordered multi-file projects with vendored dependencies. No hooks, no network, nothing outside the root."""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from itertools import takewhile
from pathlib import Path, PurePosixPath

from ..compiler.lexing import lex
from ..compiler.syntax import Parser
from ..compiler.tree import MAX_SOURCE, Diagnostic
from .toolchain import ARCHS, KINDS, TARGETS, ProjectError

SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


def read_text(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ProjectError(f"{path.name} exceeds its {limit}-byte input limit.")
    return data.decode("utf-8")


def canonical(value: str) -> bool:
    """One spelling per file or directory: every segment is a plain name, never empty, `.` or `..`."""
    return all(SEGMENT.fullmatch(part) and part not in {".", ".."} for part in value.split("/"))


def contained_file(root: Path, value: str, suffix: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectError("Paths must be nonempty relative POSIX strings.")
    if not canonical(value):
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
    # name, path, manifest and source hashes of every vendored project, in load order
    dependencies: tuple[dict, ...] = ()
    vendored_units: tuple[str, ...] = ()  # the unit paths a dependency contributed, never the root project's own

    def unit_at(self, line: int) -> Unit | None:
        """The source file a line of the combined source comes from."""
        return next((u for u in self.units if u.first_line <= line < u.first_line + u.lines), None)

    def origin(self, line: int) -> tuple[str, int]:
        """The authored file and line behind a line of the combined source."""
        unit = self.unit_at(line)
        return (str(self.root / unit.path), line - unit.first_line + 1) if unit else (self.name, line)

    def wrote(self, line: int) -> bool:
        """True when the root project itself wrote this line of the combined source, not a vendored dependency."""
        unit = self.unit_at(line)
        return unit is None or unit.path not in self.vendored_units

    def locate(self, error: Diagnostic) -> dict:
        result = dict(error.data)
        unit = self.unit_at(result.get("line", 0))
        if unit:
            result.update(file=unit.path, line=result["line"] - unit.first_line + 1)
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


@dataclass(frozen=True)
class Manifest:
    name: str
    sources: tuple[str, ...]
    contracts: tuple[str, ...]
    kind: str
    arch: str
    target: str
    table: dict  # the `[dependencies]` entries, name -> path, checked when each one is loaded
    sha256: str


def read_manifest(target: Path) -> Manifest:
    """The one checker of every manifest in a build: a dependency's is read exactly as strictly as the root's, so
    an unknown table or option, a hook, a bad name or an unknown kind, architecture or target is refused wherever
    it is written. A dependency's `[build]` choices are ignored by the build but must still name something known."""
    text = read_text(target, 65536)
    data = tomllib.loads(text)
    if set(data) - {"project", "build", "dependencies"}:
        raise ProjectError("Unknown manifest tables; hooks are not supported.")
    project, build, table = data.get("project", {}), data.get("build", {}), data.get("dependencies", {})
    if not isinstance(project, dict) or not isinstance(build, dict) or not isinstance(table, dict):
        raise ProjectError("project, build and dependencies must be tables.")
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
    if len(table) > 16:
        raise ProjectError("dependencies is a table of at most 16 entries.")
    return Manifest(
        name, tuple(sources), tuple(contracts), kind, arch, machine, table, hashlib.sha256(text.encode()).hexdigest()
    )


def opened(body: str, current: str) -> list[str]:
    """Every module a source file declares into, given the one in effect where it starts: the files of a build are
    concatenated, so a file that does not open with a `module` header keeps declaring into the previous file's."""
    try:
        tokens = lex(body)
    except Diagnostic:
        return []  # A file that does not lex declares nothing; the checker reports it against the combined source.
    first = next((i for i, token in enumerate(tokens) if token.s == "module"), len(tokens) - 1)
    names = [] if all(token.s == "pub" for token in tokens[:first]) else [current]
    for i, token in enumerate(tokens):
        if token.s == "module":
            names.append("".join(t.s for t in takewhile(lambda t: t.s not in {";", "<eof>"}, tokens[i + 1 :])))
    return names


def claim(owners: dict[str, str], names: list[str], project: str, relative: str) -> None:
    """A module belongs to one project of a build, and never to the packaged library: no project source may declare
    a `std.*` module, and no project may reopen a module another project of the build declared."""
    for module in names:
        if module.split(".")[0] == "std":
            raise ProjectError(f"{relative} declares module {module}; std.* is the packaged library, not a project's.")
        if module and owners.setdefault(module, project) != project:
            raise ProjectError(
                f"Module {module} belongs to {owners[module]}; {project} may not reopen it in {relative}."
            )


def load_project(path: str | Path = ".") -> Project:
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "cairn.toml"
    target = target.resolve(strict=True)
    root = target.parent
    if target.suffix == ".cairn":
        body = read_text(target, MAX_SOURCE)
        claim({}, opened(body, ""), target.stem, target.name)  # One file is a project too, and declares no std.
        return Project(
            root,
            target.stem,
            body,
            (Unit(target.name, 1, body.count("\n") + 1, hashlib.sha256(body.encode()).hexdigest()),),
        )
    if target.suffix != ".toml":  # A directory means its cairn.toml; a second configuration is named: app/gpu.toml.
        raise ProjectError("Pass a .cairn file, a project directory, or a manifest (cairn.toml).")
    manifest = read_manifest(target)
    vendored, fragments = dependencies(root, manifest.table, {root: manifest.name})
    own = [(r, read_text(contained_file(root, r, ".cairn"), MAX_SOURCE), manifest.name) for r in manifest.sources]
    units, text, line, byte_count = [], [], 1, 0
    owners: dict[str, str] = {}
    current = ""
    for relative, body, project in [*fragments, *own]:
        names = opened(body, current)
        claim(owners, names, project, relative)
        current = names[-1] if names else current
        header = "// source: " + relative + "\n"
        units.append(Unit(relative, line + 1, body.count("\n") + 1, hashlib.sha256(body.encode()).hexdigest()))
        fragment = header + body + "\n"
        byte_count += len(fragment.encode())
        if byte_count > MAX_SOURCE:
            raise ProjectError("Combined project exceeds the 2 MB native source limit.")
        text.append(fragment)
        line += fragment.count("\n")
    combined = "".join(text)  # its size was held to the limit fragment by fragment
    for relative in manifest.contracts:
        contained_file(root, relative, ".json")
    return Project(root, manifest.name, combined, tuple(units), manifest.contracts, manifest.kind, manifest.arch,
                   manifest.target, manifest.sha256, tuple(vendored), tuple(p for p, _, _ in fragments))  # fmt: skip


def dependencies(
    root: Path, table: dict, seen: dict[Path, str], depth: int = 0
) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """`[dependencies] geometry = "deps/geometry"`: a project vendored inside this one's root. Its sources load
    before ours (its own dependencies first), it contributes modules only, and only what it marks `pub` is
    reachable. Nothing is fetched and nothing outside the root is read; the receipt pins what was used. One
    directory is one project under one name, and one name is one project of the build."""
    if depth > 4:
        raise ProjectError("A dependency is vendored at most 4 deep.")
    found, fragments = [], []
    for name, where in table.items():
        if not isinstance(where, str) or not where or "\\" in where or not canonical(where):
            raise ProjectError(f"Dependency {name} must be a relative directory inside the project: {where!r}")
        home = root / where
        if any(part.is_symlink() for part in [home, *home.parents][: len(where.split("/"))]) or not home.is_dir():
            raise ProjectError(f"Missing dependency directory (symbolic links are not followed): {where!r}")
        home = home.resolve()
        manifest = read_manifest(contained_file(home, "cairn.toml", ".toml"))
        if manifest.name != name:  # Read before the diamond below: nothing is pinned under a name of its own.
            raise ProjectError(f"Dependency {name} is a project of another name at {where!r}.")
        if home in seen:
            continue  # A diamond loads once; the first mention fixes its place in the order.
        if name in seen.values():
            raise ProjectError(f"Two projects of this build are named {name}; a name pins one project.")
        seen[home] = name
        inner, inner_fragments = dependencies(home, manifest.table, seen, depth + 1)
        bodies = [(f"{where}/{s}", read_text(contained_file(home, s, ".cairn"), MAX_SOURCE)) for s in manifest.sources]
        rooted = [path for path, body in bodies if "" in set(Parser(body).parse().modules.values())]
        if rooted:
            raise ProjectError(
                f"Dependency {name} declares outside any module in {rooted[0]}; a library is modules only."
            )
        for relative in manifest.contracts:
            contained_file(home, relative, ".json")
        found += [*inner, {"name": name, "path": where, "manifest_sha256": manifest.sha256,
                           "source_sha256": hashlib.sha256("".join(b for _, b in bodies).encode()).hexdigest()}]  # fmt: skip
        fragments += [*[(f"{where}/{path}", body, owner) for path, body, owner in inner_fragments],
                      *[(path, body, name) for path, body in bodies]]  # fmt: skip
    return found, fragments

"""Data-only, ordered multi-file projects with vendored dependencies. No hooks, no network, nothing outside the root."""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice, takewhile
from pathlib import Path, PurePosixPath

from ..compiler.lexing import lex
from ..compiler.modules import library_path
from ..compiler.syntax import Parser
from ..compiler.tree import MAX_SOURCE, Diagnostic
from .target import parse
from .toolchain import ARCHS, KINDS, LIBRARIES, TARGETS, ProjectError

SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")
MAX_SOURCES = 1024  # source files one manifest lists; the bytes they hold together are held to MAX_SOURCE
MAX_DEPENDENCIES = 64  # vendored projects one manifest names, each at most 4 deep
FOREIGN_SUFFIXES = (".cpp", ".cc", ".cu")  # what a [foreign] table vendors: C++ sources and CUDA sources
MARK = "\ufeff"  # a byte-order mark: a file may start with one, and its text is what follows


def decoded(data: bytes) -> str:
    """A file's text: its bytes as UTF-8, after the byte-order mark it may start with. `agent/write_back.py` puts the
    mark back, and the file's own line endings, when it writes a file this read."""
    return data.decode("utf-8").removeprefix(MARK)


def read_text(path: Path, limit: int) -> str:
    """The file's text, refused past `limit` bytes. The read is sized by the file, not by the limit, since a read
    of n bytes allocates n up front; one byte past what it held catches a file that grew meanwhile."""
    with path.open("rb") as stream:
        want = min(os.fstat(stream.fileno()).st_size, limit) + 1
        data = stream.read(want)
        if len(data) == want <= limit:  # It grew after fstat: read on to the limit.
            data += stream.read(limit + 1 - want)
    if len(data) > limit:
        raise ProjectError(f"{path.name} exceeds its {limit}-byte input limit.")
    return decoded(data)


def source_of(path: Path, given: Mapping[Path, str] | None) -> str:
    """A source file as an editor holds it when `given` has it, else as the disk does; either way under the limit."""
    held = given.get(path.resolve()) if given else None
    if held is not None and len(held.encode()) > MAX_SOURCE:
        raise ProjectError(f"{path.name} exceeds its {MAX_SOURCE}-byte input limit.")
    return held.removeprefix(MARK) if held is not None else read_text(path, MAX_SOURCE)


def agree(a: str, b: str, most: int) -> int:
    """How many leading characters `a` and `b` share, up to `most`: a binary search over slices compared in C."""
    lo, hi = 0, most
    while lo < hi:
        mid = (lo + hi + 1) // 2
        lo, hi = (mid, hi) if a[:mid] == b[:mid] else (lo, mid - 1)
    return lo


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
    libraries: tuple[str, ...] = ()  # the system libraries the root manifest names: rows of toolchain.LIBRARIES
    device_target: str | None = None  # `[build] device_target`, as written: projects/target.py resolves it
    # `[foreign]`: each vendored C++ or CUDA source and the symbols it defines (projects/foreign.py builds them)
    foreign: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def unit_at(self, line: int) -> Unit | None:
        """The source file a line of the combined source comes from."""
        return next((u for u in self.units if u.first_line <= line < u.first_line + u.lines), None)

    def origin(self, line: int) -> tuple[str, int]:
        """The authored file and line behind a line of the combined source."""
        unit = self.unit_at(line)
        return (str(self.root / unit.path), line - unit.first_line + 1) if unit else (self.name, line)

    def site(self, line: int) -> tuple[str, int]:
        """The file, as the manifest names it, and the line behind a line of the combined source."""
        unit = self.unit_at(line)
        return (unit.path, line - unit.first_line + 1) if unit else (self.name, line)

    def wrote(self, line: int) -> bool:
        """True when the root project itself wrote this line of the combined source, not a vendored dependency."""
        unit = self.unit_at(line)
        return unit is None or unit.path not in self.vendored_units

    def locate(self, error: Diagnostic) -> dict:
        """The record of `error` at the file and line it names, and so is each further refusal it carries."""
        result = self.place(error.data)
        if "further" in result:
            result["further"] = [self.place(d) for d in result["further"]]
        return result

    def place(self, data: dict) -> dict:
        result = dict(data)
        if result.get("module", "").startswith("std."):  # a line of a linked library module, in its own file
            return {**result, "file": str(library_path(result["module"]))}
        unit = self.unit_at(result.get("line", 0))
        if unit:
            result.update(file=unit.path, line=result["line"] - unit.first_line + 1)
        return result

    def layout(self) -> tuple[list[str], list[str]]:
        """What the combined source holds between files, and each file's text: the source is `between[0] + texts[0]
        + between[1] + ... + texts[-1] + between[-1]`. One file alone is its own text; each file of a manifest follows
        a `// source:` line and ends with a newline of its own."""
        lines = self.source.split("\n")
        texts = ["\n".join(lines[u.first_line - 1 : u.first_line - 1 + u.lines]) for u in self.units]
        if len(texts) == 1 and texts[0] == self.source:
            return ["", ""], texts
        paths = [u.path for u in self.units]
        return [f"// source: {paths[0]}\n", *(f"\n// source: {p}\n" for p in paths[1:]), "\n"], texts

    def split(self, candidate: str) -> dict[str, str]:
        """Each file's text in `candidate`, a combined source laid out as this one is, by the file's path.

        Each `// source:` line must occur as often in `candidate` as in this source, so text that names a file cannot
        move a boundary. What stands between two files keeps its place where `candidate` agrees with this source at the
        start or at the end; inside what differs, it is found by its text, which must occur there exactly once.
        ProjectError when `candidate` does not split into this project's files that way."""
        between, texts = self.layout()
        if between == ["", ""]:
            return {self.units[0].path: candidate}
        source, grown = self.source, len(candidate) - len(self.source)
        if named := next((b for b in between[:-1] if candidate.count(b) != source.count(b)), None):
            raise ProjectError(f"The candidate writes `{named.strip()}` where no file begins, so it does not split "
                               "into this project's files; nothing was written.")  # fmt: skip
        head = agree(source, candidate, min(len(source), len(candidate)))
        tail = agree(source[::-1], candidate[::-1], min(len(source), len(candidate)) - head)
        starts, at = [], 0
        for b, t in zip(between, [*texts, ""], strict=True):
            starts.append(at)
            at += len(b) + len(t)
        placed: list[int | None] = [s if s + len(b) <= head else s + grown if s >= len(source) - tail else None
                                    for s, b in zip(starts, between, strict=True)]  # fmt: skip
        after = 0  # where the text after the last placed separator begins
        for i, text in enumerate(between):
            if placed[i] is None:
                before = next((p for p in placed[i + 1 :] if p is not None), len(candidate))
                found = candidate.find(text, after, before)
                again = candidate.find(text, found + 1, before) if found >= 0 else -1
                if found < 0 or again >= 0:
                    raise ProjectError(f"The candidate does not split into this project's files: `{text.strip()}` is "
                                       "not where one file ends and the next begins; nothing was written.")  # fmt: skip
                placed[i] = found
            after = (placed[i] or 0) + len(text)
        cuts = [p for p in placed if p is not None]
        split = {u.path: candidate[cuts[i] + len(between[i]) : cuts[i + 1]] for i, u in enumerate(self.units)}
        if "".join(b + t for b, t in zip(between, [*split.values(), ""], strict=True)) != candidate:
            raise ProjectError("The candidate does not split into this project's files; nothing was written.")
        return split

    def receipt(self) -> dict:
        vendored = [{"path": p, "symbols": list(s), "sha256": digest(self.root / p)} for p, s in self.foreign]
        return {
            "name": self.name,
            "manifest_sha256": self.manifest_sha256,
            "sources": [u.__dict__ for u in self.units],
            "dependencies": list(self.dependencies),
            "composition": "ordered sources, vendored dependencies first; modules are the only namespaces",
            "source_sha256": hashlib.sha256(self.source.encode()).hexdigest(),
            **({"libraries": list(self.libraries)} if self.libraries else {}),
            **({"foreign": vendored} if vendored else {}),
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
    libraries: tuple[str, ...] = ()
    device_target: str | None = None
    foreign: tuple[tuple[str, tuple[str, ...]], ...] = ()


def read_manifest(target: Path) -> Manifest:
    """The one checker of every manifest in a build: a dependency's is read exactly as strictly as the root's, so
    an unknown table or option, a hook, a bad name or an unknown kind, architecture or target is refused wherever
    it is written. A dependency's `[build]` choices are ignored by the build but must still name something known."""
    text = read_text(target, 65536)
    data = tomllib.loads(text)
    if set(data) - {"project", "build", "dependencies", "foreign"}:
        raise ProjectError("Unknown manifest tables; hooks are not supported.")
    project, build, table = data.get("project", {}), data.get("build", {}), data.get("dependencies", {})
    if not isinstance(project, dict) or not isinstance(build, dict) or not isinstance(table, dict):
        raise ProjectError("project, build and dependencies must be tables.")
    if set(project) - {"name", "sources", "tests"} or set(build) - {
        "kind",
        "arch",
        "target",
        "libraries",
        "device_target",
    }:
        raise ProjectError("Unknown manifest option.")
    name = project.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
        raise ProjectError("Project name must be an ASCII name of 1..64 characters.")
    sources, contracts = project.get("sources"), project.get("tests", [])
    for label, values, low, high in [("sources", sources, 1, MAX_SOURCES), ("tests", contracts, 0, 128)]:
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
    if len(table) > MAX_DEPENDENCIES:
        raise ProjectError(f"dependencies is a table of at most {MAX_DEPENDENCIES} entries.")
    libraries = build.get("libraries", [])  # names of toolchain rows, never flags or paths
    if not isinstance(libraries, list) or len(libraries) > 8 or not all(isinstance(n, str) for n in libraries):
        raise ProjectError("libraries is a list of at most 8 names.")
    if len(set(libraries)) != len(libraries) or set(libraries) - set(LIBRARIES):
        raise ProjectError(f"libraries names each known library once; known: {', '.join(sorted(LIBRARIES))}.")
    if libraries and machine != "hosted":
        raise ProjectError("A freestanding image links no system library.")
    device = build.get("device_target")  # the GPU's compilation target, never the CPU's: sm_120, sm_120f, sm_120a
    if device is not None:
        parse(device, "manifest")
    foreign = vendored(data.get("foreign", {}))
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Manifest(
        name, tuple(sources), tuple(contracts), kind, arch, machine, table, digest, tuple(libraries), device, foreign
    )


def vendored(table: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """`[foreign] "vendor/x.cpp" = ["symbol"]`: a vendored C++ or CUDA source and the C symbols or kernel names it
    defines, which the project's externs declare. Data only: the build chooses every flag (projects/foreign.py)."""
    if not isinstance(table, dict) or len(table) > 64:
        raise ProjectError("foreign is a table of at most 64 vendored sources.")
    out = []
    for path, symbols in table.items():
        if PurePosixPath(path).suffix not in FOREIGN_SUFFIXES:
            raise ProjectError(f"A foreign source is C++ or CUDA ({', '.join(FOREIGN_SUFFIXES)}): {path!r}.")
        if not isinstance(symbols, list) or len(symbols) > 256 or len(set(map(str, symbols))) != len(symbols):
            raise ProjectError(f"{path} names each symbol it defines once, in a list.")
        if not all(isinstance(s, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", s) for s in symbols):
            raise ProjectError(f"{path} names its symbols as C identifiers.")
        out.append((path, tuple(symbols)))
    return tuple(out)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def opened(body: str, current: str) -> list[str]:
    """Every module a source file declares into, given the one in effect where it starts: the files of a build are
    concatenated, so a file that does not open with a `module` header keeps declaring into the previous file's."""
    found = headers(body)
    if found is None:
        return []  # A file that does not lex declares nothing; the checker reports it against the combined source.
    before, names = found
    return [current, *names] if before else list(names)


@lru_cache(maxsize=2048)
def headers(body: str) -> tuple[bool, tuple[str, ...]] | None:
    """Whether a file declares anything but `pub` before its first `module` header, and every header's name; None
    when it does not lex. Kept by the text, so an editor's refresh lexes again only the files that changed."""
    try:
        tokens = lex(body)
    except Diagnostic:
        return None
    first = next((i for i, token in enumerate(tokens) if token.s == "module"), len(tokens) - 1)
    ends = {";", "<eof>"}
    names = tuple("".join(t.s for t in takewhile(lambda t: t.s not in ends, islice(tokens, i + 1, None)))
                  for i, token in enumerate(tokens) if token.s == "module")  # fmt: skip
    return not all(token.s == "pub" for token in tokens[:first]), names


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


def load_project(path: str | Path = ".", given: Mapping[Path, str] | None = None) -> Project:
    """The project at `path`, its sources read through `given` (resolved path -> text) where an editor holds them."""
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "cairn.toml"
    target = target.resolve(strict=True)
    root = target.parent
    if target.suffix == ".cairn":
        body = source_of(target, given)
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
    vendored, fragments = dependencies(root, manifest.table, {root: manifest.name}, given=given)
    own = [(r, source_of(contained_file(root, r, ".cairn"), given), manifest.name) for r in manifest.sources]
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
            raise ProjectError(f"The project's sources together exceed the {MAX_SOURCE}-byte limit.")
        text.append(fragment)
        line += fragment.count("\n")
    combined = "".join(text)  # its size was held to the limit fragment by fragment
    for relative in manifest.contracts:
        contained_file(root, relative, ".json")
    for relative, _ in manifest.foreign:
        contained_file(root, relative, PurePosixPath(relative).suffix)
    return Project(root, manifest.name, combined, tuple(units), manifest.contracts, manifest.kind, manifest.arch,
                   manifest.target, manifest.sha256, tuple(vendored), tuple(p for p, _, _ in fragments),
                   manifest.libraries, manifest.device_target, manifest.foreign)  # fmt: skip


def dependencies(root: Path, table: dict, seen: dict[Path, str], depth: int = 0,
                 given: Mapping[Path, str] | None = None) -> tuple[list[dict], list[tuple[str, str, str]]]:  # fmt: skip
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
        if manifest.foreign:  # one build compiles one project's vendored C++ and CUDA, by its own externs
            raise ProjectError(f"Dependency {name} vendors foreign sources; the root project lists and builds them.")
        if home in seen:
            continue  # A diamond loads once; the first mention fixes its place in the order.
        if name in seen.values():
            raise ProjectError(f"Two projects of this build are named {name}; a name pins one project.")
        seen[home] = name
        inner, inner_fragments = dependencies(home, manifest.table, seen, depth + 1, given)
        bodies = [(f"{where}/{s}", source_of(contained_file(home, s, ".cairn"), given)) for s in manifest.sources]
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

"""nvcc accepts the C++ CAIRN generates for every program the repository shows, as g++ and clang++ do: each example,
each `cairn` block of README.md and docs/, and every std module, compiled for sm_120 by the project's own device
command line and never run. A device program's host code may be any code, and nvcc's front end refuses C++ the host
compilers accept (a local declared and never read, #177-D; a host region's runtime missing from its device pass), so
each lowering meets nvcc here before a user's program does. Skipped without nvcc.

One nvcc run costs seconds, so a group is compiled as one program that holds each member as a module of its own, and
the #line directives of a debug build name each member's own file and line in what nvcc says. A member that cannot be
such a module is compiled alone: one that declares a module the shared program already holds, and one that a named
module refuses, as it refuses a private type of its own in a std generic (E-PRIVATE) where the root module does not.
A member that declares more modules joins first, so the shared program holds the larger of two that share modules,
such as the device configuration of examples/apps/analytics beside its host one.
"""

import re
import shutil
from bisect import bisect_right
from collections.abc import Callable
from pathlib import Path

import pytest

from cairn.compiler.cairnc import compile_source
from cairn.compiler.primitives.machine import unbuildable
from cairn.compiler.syntax.tree import Diagnostic
from cairn.projects.project import load_project
from cairn.projects.toolchain import host_family
from emitted import device_build
from sources import cairn_sources

ROOT = Path(__file__).resolve().parents[2]
Origin = Callable[[int], tuple[str, int]]  # a line of a member's source -> the file and line it was written at
Member = tuple[str, str, Origin]  # a name, the source, and where each line of it was written


def builds(source: str) -> bool:
    """Whether `source` checks and its assembly is this machine's: the string form of `asm` names no target, so a
    program holding it builds only for the machine it was written for."""
    try:
        receipt = compile_source(source)[1]
    except Diagnostic:
        return False
    return not re.search(r"\basm\(", source) and not unbuildable(receipt["requires"], host_family(), "sm_120")


def examples() -> list[Member]:
    """Every example program: each manifest's, and each file no manifest holds."""
    found: list[Member] = []
    for manifest in sorted((ROOT / "examples").rglob("*.toml")):
        if "[project]" in manifest.read_text(encoding="utf-8"):  # a harness.toml maps arguments; it is no manifest
            project = load_project(manifest)
            found.append((str(manifest.relative_to(ROOT)), project.source, project.origin))
    owned = {manifest.parent for manifest in (ROOT / "examples").rglob("cairn.toml")}
    for single in cairn_sources(ROOT / "examples"):
        if not owned & set(single.parents):
            name = str(single.relative_to(ROOT))
            found.append((name, single.read_text(encoding="utf-8"), lambda line, name=name: (name, line)))
    return found


def documented() -> list[Member]:
    """Every accepted `cairn` block of README.md and docs/ but the generated std reference, as
    tests/language/test_docs_examples.py reads them."""
    found: list[Member] = []
    for doc in [ROOT / "README.md", *sorted(p for p in (ROOT / "docs").glob("*.md") if p.name != "std_api.md")]:
        name, text = str(doc.relative_to(ROOT)), doc.read_text(encoding="utf-8")
        for block in re.finditer(r"^```cairn\n(.*?)^```", text, flags=re.S | re.M):
            first = text[: block.start()].count("\n") + 2  # the block's first line in the document
            found.append(
                (f"{name}:{first - 1}", block[1], lambda line, name=name, first=first: (name, first + line - 1))
            )
    return found


def library() -> list[Member]:
    """One program importing every std module: a library build emits each function std declares."""
    modules = sorted("std." + path.stem for path in (ROOT / "src/cairn/std").glob("*.cairn"))
    return [("std", "".join(f"import {module};\n" for module in modules), lambda line: ("std", line))]


def together(members: list[Member]) -> Member:
    """One program holding each member as the module `mJ`, with the origin of each of its lines."""
    parts, starts, line = [], [], 1
    for j, (_, source, _) in enumerate(members):
        parts.append(f"module m{j};\n{source}\n")
        starts.append(line + 1)
        line += source.count("\n") + 2

    def origin(line: int) -> tuple[str, int]:
        j = max(bisect_right(starts, line) - 1, 0)
        return members[j][2](line - starts[j] + 1)

    return f"{len(members)} programs together", "".join(parts), origin


def programs(members: list[Member]) -> list[Member]:
    """The programs that hold every member that builds here: as few as can, as the module docstring says."""
    declared: set[str] = set()
    shared, alone = [], []
    modules = {name: set(re.findall(r"^module ([\w.]+);", source, flags=re.M)) for name, source, _ in members}
    for member in sorted(members, key=lambda m: -len(modules[m[0]])):
        if not modules[member[0]] & declared and builds(f"module m;\n{member[1]}"):
            shared.append(member)
            declared |= modules[member[0]]
        elif builds(member[1]):
            alone.append(member)
    return [together(shared), *alone] if shared else alone


@pytest.mark.parametrize("group", [examples, documented, library], ids=["examples", "documentation", "std"])
def test_nvcc_accepts_what_cairn_generates(tmp_path, group):
    """Compiled for sm_120 by the project's own device command line; nothing runs on a device."""
    if not shutil.which("nvcc"):
        pytest.skip("nvcc is not installed")
    held = programs(group())
    for i, (name, source, origin) in enumerate(held):
        folder = tmp_path / str(i)
        folder.mkdir()
        try:
            device_build(folder, compile_source(source, origin)[0])
        except AssertionError as refused:
            raise AssertionError(f"nvcc refused {name}:\n{refused}") from None

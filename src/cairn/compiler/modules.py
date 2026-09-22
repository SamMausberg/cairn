"""Link imported library modules into one program. Only the packaged std tree is searched."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from .syntax import Parser
from .tree import Program, fail

STD = Path(__file__).parents[1] / "std"


def library_path(module: str) -> Path | None:
    """`std.io.file` lives at std/io/file.cairn; nothing outside the package is ever read."""
    parts = module.split(".")
    if not parts or parts[0] != "std" or not all(part.isidentifier() for part in parts):
        return None
    path = STD.joinpath(*parts[1:]).with_suffix(".cairn")
    return path if path.is_file() else None


def library_source(module: str) -> str | None:
    path = library_path(module)
    return path.read_text(encoding="utf-8") if path else None


def link(p: Program) -> Program:
    """Merge every imported module that the program does not define itself, transitively."""
    defined = set(p.modules.values())
    pending = [target for _, target, _ in p.imports]
    for m, r, _, _, _ in p.derivations:  # A bare recipe the deriving module cannot see is a packaged one.
        if "." not in r and not {f"{m}.{r}", r} & set(p.recipes):
            pending.append(f"std.{r}" if library_source(f"std.{r}") else "std.derived")
    while pending:
        module = pending.pop()
        if module in defined:
            continue
        source = library_source(module)
        if source is None:
            fail("E-IMPORT", f"Unknown module {module}; only project modules and std.* can be imported.")
        library = Parser(source).parse()
        if set(library.modules.values()) != {module}:
            fail("E-IMPORT", f"{module} must declare exactly `module {module};`.")
        if set(library.modules) & set(p.modules):
            fail("E-DUPLICATE", f"{module} redeclares a name of the program.")
        for slot in fields(Program):
            mine, theirs = getattr(p, slot.name), getattr(library, slot.name)
            mine.extend(theirs) if isinstance(mine, list) else mine.update(theirs)
        p.sources[module] = source
        defined.add(module)
        pending += [target for _, target, _ in library.imports]
    return p

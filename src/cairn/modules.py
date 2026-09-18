"""Link imported library modules into one program. Only the packaged std tree is searched."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from .syntax import Parser, Program, fail

STD = Path(__file__).parent / "std"


def library_source(module: str) -> str | None:
    """`std.io.file` lives at std/io/file.cairn; nothing outside the package is ever read."""
    parts = module.split(".")
    if parts[0] != "std" or not all(part.isidentifier() for part in parts):
        return None
    path = STD.joinpath(*parts[1:]).with_suffix(".cairn")
    return path.read_text(encoding="utf-8") if path.is_file() else None


def link(p: Program) -> Program:
    """Merge every imported module that the program does not define itself, transitively."""
    defined = set(p.modules.values())
    pending = [target for _, target, _ in p.imports]
    pending += [f"std.{r}" for _, r, _, _, _ in p.derivations if "." not in r and library_source(f"std.{r}")
                and not any(name.rsplit(".", 1)[-1] == r for name in p.recipes)]  # fmt: skip
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

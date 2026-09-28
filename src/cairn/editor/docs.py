"""`cairn doc`: an API reference taken from the checked program, so it cannot drift from the code.

For every public declaration of a module: its signature with bounds, the comment written above it, and
for a function the effect row the checker inferred. A template's row is the one at its witnesses: what it
may do for any arguments within its bounds, besides what their own trait members do. A module is one
heading, its own comment, and one block of declarations, each under its comment and its row as `//` lines,
so the reference reads like the source it came from.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

from ..agent.projection import generics, local, signature, type_declarations
from ..compiler.cairnc import Checker, Parser, Program, derive, link, specialize
from ..compiler.check.traits import described
from ..compiler.syntax.lexing import comment_above

TABLES = (("records", "struct"), ("sums", "enum"), ("enums", "enum"), ("traits", "trait"), ("consts", "const"))


WIDTH = 120


def commented(lines: list[str]) -> list[str]:
    """Prose as `//` lines of at most WIDTH columns."""
    return [f"// {line}" for text in lines for line in textwrap.wrap(text, WIDTH - 3)]


NOTE = "A generic function's effects are what it may do for any arguments within its bounds, besides what their own"
NOTE += " trait members do."


def document(source: str, modules: list[str] | None = None) -> str:
    """Every module's section in one document, under the note on generic rows when a template is among them."""
    chapters, generic = sections(source, modules)
    return "\n\n".join([NOTE, *chapters] if generic else chapters) + "\n"


def shared_memory(c: Any, f: Any) -> list[str]:
    """The shared memory each block of f's cooperative regions holds, by instance for a template, whose naturals (a
    pipeline's depth among them) may change it: what a device inspector will find in the kernel."""
    instances = [g for g in c.fs.values() if g.source_name == f.name and g.bindings] if f.generics else [f]
    held = []
    for g in instances:
        sizes = [str(x["shared_bytes"]) for x in c.resources.get(g.name, []) if x.get("kind") == "blocks"]
        if sizes:
            held.append(f"{' and '.join(sizes)} bytes" + (f" in {g.name}" if f.generics else ""))
    return [f"shared memory a block: {', '.join(held)}"] if held else []


def sections(source: str, modules: list[str] | None = None) -> tuple[list[str], bool]:
    """One Markdown section per module, and whether any function documented is a template."""
    c, verdicts, rows = described(lambda: Checker(specialize(derive(link(Parser(source).parse())))))
    p = c.p
    chapters: list[str] = []
    for module in modules or sorted(m for m in set(p.modules.values()) if m not in p.sources):
        text = p.sources.get(module, source)

        def shown(name: str, module: str = module) -> bool:
            return p.modules.get(name) == module and (name in p.public or not module)

        def above(pattern: str, text: str = text) -> list[str]:
            at = re.search(pattern, text, re.M)
            return comment_above(text, at.start()) if at else []

        entries: list[list[str]] = []
        for table, kind in TABLES:
            for name in filter(shown, getattr(p, table)):
                one = Program(
                    generics=p.generics,
                    attributes=p.attributes,
                    public=p.public,
                    field_extents=p.field_extents,
                    lends=p.lends,
                )
                setattr(one, table, {name: getattr(p, table)[name]})
                doc = above(rf"^(?:pub )?(?:linear )?{kind} {re.escape(local(name))}\b")
                entries.append([*commented(doc), type_declarations(one)])
        for name, recipe in p.recipes.items():
            if shown(name):
                head = f"pub recipe {local(name)}{generics(recipe.statics)}" + (
                    f" for {recipe.param}" if recipe.param else ""
                )
                entries.append([*commented(comment_above(text, recipe.start)), head])
        for f in [f for f in c.fs.values() if f.module == module and not f.bindings and (f.owner or shown(f.name))]:
            impl = f"impl {f.owner[0]} for {f.owner[1].display()}: " if f.owner else "pub " if module else ""
            row = ", ".join(sorted(rows.get(f.name, ()))) or "none"
            certified = verdicts.get(f.name, "ok") == "ok"
            effects = f"effects: {row}" if certified else f"checked per instance: {verdicts[f.name]}"
            declared = f"{impl}{signature(f)}"
            told = [*comment_above(text, f.start), *shared_memory(c, f)]
            if len(declared) + len(effects) + 5 <= WIDTH:  # the row at the end of the line it describes
                entries.append([*commented(told), f"{declared}  // {effects}"])
            else:
                entries.append([*commented([*told, effects]), declared])
        block = ""  # One-line declarations stack; a blank line sets off every declaration that carries a comment.
        for k, entry in enumerate(entries):
            apart = k > 0 and (len(entry) > 1 or len(entries[k - 1]) > 1)
            block += ("\n\n" if apart else "\n" if k else "") + "\n".join(entry)
        told = above(rf"^module {re.escape(module)};")
        out = [f"# {module or 'root module'}", *(["", *told] if told else [])]
        chapters.append("\n".join(out + ([f"\n```cairn\n{block}\n```"] if entries else [])))
    return chapters, any(f.generics for f in c.fs.values())


GENERATED = "<!-- Generated by `cairn doc --std`; edit the comments in src/cairn/std/*.cairn instead. -->\n\n"


def library_names() -> list[str]:
    """Every packaged module, as `std.text`."""
    from ..compiler.syntax.modules import STD

    return sorted("std." + path.stem for path in STD.glob("*.cairn"))


def library_sections(modules: list[str] | None = None) -> tuple[list[str], list[str]]:
    """The packaged modules named, every one when none is, and the reference section of each. A module's rows are
    its own, so its section reads the same whichever others are compiled beside it."""
    names = modules or library_names()
    return names, sections("".join(f"import {m};\n" for m in names) + "fn main() -> i32 { return 0; }\n", names)[0]


def standard_library(modules: list[str] | None = None) -> str:
    """The reference of the packaged modules named, every one when none is, in one document. It is read where it is
    printed, so it does not say where a generated page is edited."""
    return "\n\n".join([NOTE, *library_sections(modules)[1]]) + "\n"


def standard_library_pages() -> dict[str, str]:
    """The same reference as files: `std_api.md` indexes the modules and `std/<name>.md` is one module's page, so
    no generated file outgrows what a reader takes in, or the 800-line rule, as the library grows."""
    names, chapters = library_sections()
    pages, rows = {}, []
    for module, chapter in zip(names, chapters, strict=True):
        page = f"std/{module.removeprefix('std.')}.md"
        pages[page] = GENERATED + chapter + "\n\n" + NOTE + "\n"
        told = (
            chapter.split("\n\n")[1] if chapter.count("\n\n") and not chapter.split("\n\n")[1].startswith("`") else ""
        )
        what = re.split(r"(?<=\.)\s", told, maxsplit=1)[0] if told else ""
        rows.append(f"| [{module}]({page}) | {what.replace('|', '/')} |")
    head = "Every public declaration of the packaged library, with its bounds, the comment above it and the effect row"
    head += " the checker infers, one page per module. [library.md](library.md) is the guide to what each is for."
    pages["std_api.md"] = GENERATED + head + "\n\n| module | what it holds |\n|---|---|\n" + "\n".join(rows) + "\n"
    return pages

"""References and rename across every file of a project: the files read as the editor holds them, the program
analysed whole, and a rename applied only when the whole linked program says it changed nothing but the name.

A document on disk under a `cairn.toml` that lists it belongs to that project, and the combined source the build
compiles is analysed once (`Document` over it), with every open buffer in place of its file. A position in the
combined source maps back to one file by the unit table `load_project` keeps. A document in no project keeps the
one-document rules of `edits.py`.

A rename is a checked transaction. The new name must be an identifier that no file of the project writes at all,
and not a reserved word, a builtin or a type, so no occurrence it adds can mean something already there. The edit is
applied to the combined source, which must still compile, and every function's receipt entry (its effect row, its
callees, its guard sites and its allocations) must be what it was, under the new name where the name changed: in
the reference an implementation names, and in a reference's implementations and the one its plan runs. An
implementation's identity digests the declarations' tokens, which a rename changes by design, so it is the one part
of an entry left out. Any other answer refuses the rename whole, with the reason, and nothing is applied in part. A field or a variant is
renamed the same way (`members.py` finds its tokens), and since a receipt names no field or variant, every entry
must be exactly what it was.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...agent.projection import local
from ...compiler import compilations
from ...compiler.cairnc import Diagnostic
from ...compiler.primitives.builtins import TABLE
from ...compiler.syntax.parser import IDENT, RESERVED
from ...projects.project import Project, ProjectError, contained_file, load_project
from .document import Document, Item, binders, dotted, enclosing, word_at
from .edits import occurrences as local_occurrences
from .members import Members, bodies, declares
from .names import TYPES, callee, declared, qualified

MANIFEST = "cairn.toml"
LEVELS = 8  # how far above a document a manifest is looked for
MEMBERS = {"field", "variant"}  # what a record or a sum declares, renamed through members.py


class Refused(ValueError):
    """A reference or rename this project cannot answer soundly; the message says why."""


def path_of(uri: str) -> Path | None:
    parsed = urlparse(uri)
    return Path(unquote(parsed.path)) if parsed.scheme == "file" and parsed.path else None


@dataclass
class File:
    uri: str
    text: str
    start: int  # where its first character sits in the combined source
    whole: str = ""  # the combined source of its project, which says the module its first line sits in


@dataclass
class Workspace:
    project: Project
    whole: Document
    files: list[File]

    def file_at(self, offset: int) -> File | None:
        inside = [f for f in self.files if f.start <= offset <= f.start + len(f.text)]
        return inside[-1] if inside else None

    def offset(self, uri: str, offset: int) -> int:
        return next(f.start + offset for f in self.files if f.uri == uri)

    def location(self, t: Item) -> dict | None:
        f = self.file_at(t.start)
        if f is None:
            return None
        here = Document(f.text, analyse=False)
        return {"uri": f.uri, "range": here.span(t.start - f.start, t.end - f.start)}


def on_disk(buffers: dict[str, str]) -> dict[Path, str]:
    """The open buffers of files on disk, by resolved path: what a project is loaded with in place of those files."""
    return {p.resolve(): text for u, text in buffers.items() if (p := path_of(u)) and p.is_file()}


def context(uri: str, buffers: dict[str, str]) -> tuple[Project, list[File]] | None:
    """The project holding the document at `uri` and its files, with `buffers` (uri -> text) in place of the files
    they hold: the nearest `cairn.toml` above it whose project lists it. None for a document in no project."""
    path = path_of(uri)
    if path is None or not path.is_file():
        return None
    given = on_disk(buffers)
    for home in [path.parent, *path.parents][:LEVELS]:
        if not (home / MANIFEST).is_file():
            continue
        try:
            project = load_project(home / MANIFEST, given)
        except (ProjectError, OSError, ValueError, Diagnostic):
            return None
        files = files_of(project)
        if path.resolve().as_uri() in {f.uri for f in files}:
            return project, files
    return None


def files_of(project: Project) -> list[File]:
    """Every file of a loaded project, as the editor holds it, with where it starts in the combined source. Each text
    is the one the project read, from the editor or the disk, so an offset into the combined source is an offset into
    it: a file's carriage returns are kept and its byte-order mark is not part of it."""
    return [
        File((project.root / u.path).resolve().as_uri(), text, at, project.source) for u, at, text in project.files()
    ]


def workspace_symbols(query: str, buffers: dict[str, str], roots: list[str]) -> list[dict]:
    """Every declaration whose name holds `query`, case aside, in the open documents, the projects they belong
    to and the projects at the workspace's roots; each named with its module as its container."""
    given = on_disk(buffers)
    files: dict[str, File] = {}
    for root in roots:
        home = path_of(root)
        if home is not None and (home / MANIFEST).is_file():
            with suppress(ProjectError, OSError, ValueError, Diagnostic):  # a broken manifest has no symbols
                files |= {f.uri: f for f in files_of(load_project(home / MANIFEST, given))}
    for uri, text in buffers.items():
        held = context(uri, buffers)
        files |= {f.uri: f for f in held[1]} if held else {uri: File(uri, text, 0)}
    out = []
    for f in files.values():
        doc = Document(f.text, analyse=False, within=(f.whole, f.start, None) if f.whole else None)
        for d in doc.declarations:
            if query.lower() in d["name"].lower():
                where = {"uri": f.uri, "range": doc.span(*d["mark"])}
                out.append({"name": d["name"], "kind": d["kind"], "location": where,
                            "containerName": doc.module_at(d["head"])})  # fmt: skip
    return out[:1000]


def within(project: Project, files: list[File], uri: str) -> tuple[str, int, Any] | None:
    """What `Document(within=...)` takes for the file at `uri` of this project."""
    return next(((project.source, f.start, project.site) for f in files if f.uri == uri), None)


def workspace(uri: str, buffers: dict[str, str]) -> Workspace | None:
    held = context(uri, buffers)
    return Workspace(held[0], Document(held[0].source), held[1]) if held else None


# What a name refers to --------------------------------------------------------------------------


def target(ws: Workspace, offset: int) -> tuple[str, str, list[Item]] | None:
    """The declaration the name under the cursor refers to, as (kind, full name, every token that names it), or
    None for a local, which the one-document rule answers. Refuses what a project cannot rename soundly."""
    doc, p = ws.whole, ws.whole.program
    at = word_at(doc, offset)
    if at is None or p is None:
        raise Refused("There is no name here, or the project does not compile.")
    cs, (i, word) = doc.code, at
    modules, members = doc.modules(), Members(doc)
    member = members.at(i)  # a field or a variant: checked before locals, since a local may share its spelling
    if member is not None:
        kind, owner, name = member
        if p.modules.get(owner, "") in p.sources:
            raise Refused(f"{owner}.{name} is declared in the library, which a project does not rename.")
        if members.declaration(kind, owner, name) is None:
            raise Refused(f"{owner}.{name} has no declaration in the project's files to rename: a recipe wrote it.")
        return kind, f"{owner}.{name}", members.tokens(kind, owner, name)
    if any(cs[j].s == word.s for j in binders(cs, 0, len(cs))):
        return None
    tables: dict[str, dict[str, Any]] = {"fn": declared(p), "struct": p.records, "enum": {**p.sums, **p.enums},
                                         "trait": p.traits, "const": p.consts}  # fmt: skip
    path = dotted(cs, i)
    found = [(kind, n) for kind, table in tables.items() if (n := qualified(p, modules[i], path, table))]
    if not found and i and cs[i - 1].s == "." and i + 1 < len(cs) and cs[i + 1].s in {"(", "["}:
        f = callee(doc, modules[i], path, word.start)[0]
        found = [("fn", f.name)] if f is not None and f.name in tables["fn"] else []
    if not found:
        raise Refused(f"{word.s} is not a declaration this project can rename: a trait member, or a name of a recipe.")
    kind, full = found[0]
    home = p.modules.get(full, "")
    if home in p.sources:
        raise Refused(f"{full} is declared in the library, which a project does not rename.")
    bare, table = local(full), tables[kind]
    fields = field_names(cs)
    tokens = []
    for j, t in enumerate(cs):
        if t.s != bare or j in fields:
            continue
        if qualified(p, modules[j], dotted(cs, j), table) == full:
            tokens.append(t)
        elif kind == "fn" and j and cs[j - 1].s == "." and j + 1 < len(cs) and cs[j + 1].s in {"(", "["}:
            f = callee(doc, modules[j], dotted(cs, j), t.start)[0]  # `x.name(...)`: a method call can reach it
            tokens += [t] if f is not None and f.name == full else []
    declaring = [d for d in doc.declarations if d["name"] == bare]
    if not any(t.start == d["mark"][0] for t in tokens for d in declaring):
        raise Refused(f"{full} has no declaration in the project's files to rename: a recipe may have written it.")
    return kind, full, tokens


def field_names(cs: list[Item]) -> set[int]:
    """The tokens that name a field or a variant where a record or a sum declares it: `head:Header`, `Some(T);`."""
    return {k for kind, _, opener, end in bodies(cs) for k in range(opener + 1, end) if declares(cs, k, kind)}


# The requests -------------------------------------------------------------------------------------


def references(ws: Workspace, uri: str, offset: int) -> list[dict]:
    at = ws.offset(uri, offset)
    try:
        named = target(ws, at)
    except Refused:
        return []
    tokens = named[2] if named else local_occurrences(ws.whole, at)
    return [where for t in tokens if (where := ws.location(t))]


def definition(ws: Workspace, uri: str, offset: int) -> dict | None:
    """Where the declaration the name under the cursor refers to is written, in whichever file of the project holds
    it; None for a local, a library name or a member, and on the declaration itself, which one document answers."""
    at = ws.offset(uri, offset)
    try:
        named = target(ws, at)
    except Refused:
        return None
    if named is None:
        return None
    bare = local(named[1])
    if named[0] in MEMBERS:  # a field or a variant: where its record or sum declares it
        owner, name = named[1].rsplit(".", 1)
        marks = {t.start for t in [Members(ws.whole).declaration(named[0], owner, name)] if t is not None}
    else:
        marks = {d["mark"][0] for d in ws.whole.declarations if d["name"] == bare}
    declaring = [t for t in named[2] if t.start in marks and not t.start <= at < t.end]
    return ws.location(declaring[0]) if declaring else None


def renameable(ws: Workspace, at: int) -> tuple[tuple[str, str, list[Item]] | None, list[Item]]:
    """What a rename at `at` would change: the declaration it names (None for a local) and every token of it."""
    named = target(ws, at)
    tokens = named[2] if named else local_occurrences(ws.whole, at)
    fixed = named and named[0] == "fn" and (declared(ws.whole.program)[named[1]].extern or local(named[1]) == "main")
    if fixed:
        raise Refused(f"{named[1]} is foreign or the entry point: the name is its symbol, which other code links to.")
    if not tokens:
        raise Refused("This name cannot be renamed soundly: it is bound twice, shadowed or reached another way.")
    return named, tokens


def prepare_rename(ws: Workspace, uri: str, offset: int) -> dict | None:
    at = ws.offset(uri, offset)
    try:
        tokens = renameable(ws, at)[1]
    except Refused:
        return None
    under = [t for t in tokens if t.start <= at < t.end]
    where = ws.location(under[0]) if under else None
    return {"range": where["range"], "placeholder": under[0].s} if where else None


def rename(ws: Workspace, uri: str, offset: int, fresh: str) -> dict:
    """Every file's edits, or a refusal that applies nothing."""
    at = ws.offset(uri, offset)
    named, tokens = renameable(ws, at)
    if not IDENT.fullmatch(fresh) or fresh in RESERVED or fresh in TABLE or fresh in TYPES:
        raise Refused(f"{fresh} is a reserved word, a builtin or a type, or not an identifier.")
    if any(t.s == fresh for t in ws.whole.code):
        raise Refused(f"{fresh} is already written in the project; a rename to it could change what a name means.")
    before, after = ws.project.source, ws.project.source
    for t in sorted(tokens, key=lambda t: t.start, reverse=True):
        after = after[: t.start] + fresh + after[t.end :]
    # A declaration's receipt names change with it; a field or a variant names no function, so every entry stays.
    old, new = (named[1], renamed(named[1], fresh)) if named and named[0] not in MEMBERS else ("", "")
    try:
        was, now = (unidentified(compilations.emitted(s)[1]["functions"]) for s in (before, after))
    except Diagnostic as error:
        raise Refused(f"After the rename the project would not compile: {error.data['code']}: {error}") from None
    if old:
        was = {rewrite(n, old, new): renaming(e, old, new) for n, e in was.items()}
    if named is None:  # a parameter's row names it: `read:p` of its own function, and of that function's instances
        home, name = owner(ws, at), tokens[0].s
        was = {n: {**e, "effects": [re.sub(rf":{re.escape(name)}\Z", ":" + fresh, x) for x in e["effects"]]}
               if n == home or n.startswith(home + "[") else e for n, e in was.items()}  # fmt: skip
    if was != now:
        changed = sorted(set(was) ^ set(now) or {n for n in was if was[n] != now.get(n)})
        raise Refused(f"The rename would change what the program does, not only a name: {', '.join(changed[:3])}.")
    edits: dict[str, list[dict]] = {}
    for t in tokens:
        where = ws.location(t)
        if where is None:
            raise Refused("A name to rename lies outside the project's files.")
        edits.setdefault(where["uri"], []).append({"range": where["range"], "newText": fresh})
    for uri_, contract in contracts(ws.project, old, new):  # a task contract names its function by symbol
        edits.setdefault(uri_, []).append(contract)
    return {"changes": edits}


def contracts(project: Project, old: str, new: str) -> list[tuple[str, dict]]:
    """The edit that renames `old` in each task contract the manifest lists whose symbol it is, so a rename never
    leaves `cairn test` a contract naming a function that no longer exists."""
    found = []
    for listed in project.contracts if old else ():
        path = contained_file(project.root, listed, ".json")
        text = path.read_text(encoding="utf-8")
        named = None
        with suppress(ValueError, AttributeError):  # a file that is no contract is `cairn test`'s to refuse
            named = json.loads(text).get("symbol")
        if named != old:
            continue
        at = re.search(rf'"symbol"\s*:\s*"({re.escape(old)})"', text)
        if at is None:
            raise Refused(f"{listed} names {old} in a form this rename cannot rewrite; rename it there first.")
        span = Document(text, analyse=False).span(at.start(1), at.end(1))
        found.append((path.resolve().as_uri(), {"range": span, "newText": new}))
    return found


def owner(ws: Workspace, offset: int) -> str:
    """The function a local belongs to, as the receipt names it."""
    d = enclosing(ws.whole, offset)[0]
    module = ws.whole.module_at(d["head"] + 1) if d else ""
    return f"{module}.{d['name']}" if module and d else d["name"] if d else ""


def renaming(entry: dict, old: str, new: str) -> dict:
    """A receipt entry with `old` renamed `new` wherever it names a function: its callees, the reference it implements,
    its implementations, the one a plan runs, and the template an implementation's instance comes from."""
    out = {**entry, "calls": sorted(rewrite(c, old, new) for c in entry["calls"])}
    for key in ("implements", "runs", "instance_of"):
        if key in out:
            out[key] = rewrite(out[key], old, new)
    if "implementations" in out:
        out["implementations"] = {
            rewrite(n, old, new): {**v, "instance_of": rewrite(v["instance_of"], old, new)} if "instance_of" in v else v
            for n, v in out["implementations"].items()
        }
    return out


def unidentified(entries: dict) -> dict:
    """Receipt entries without their implementations' identities. An identity digests the declarations' tokens as
    written, which a rename changes by design; what an implementation does and needs is the rest of its entry."""
    return {n: {**e, "implementations": {i: {k: v for k, v in x.items() if k != "identity"}
                                         for i, x in e["implementations"].items()}} if "implementations" in e else e
            for n, e in entries.items()}  # fmt: skip


def renamed(full: str, fresh: str) -> str:
    return full.rsplit(".", 1)[0] + "." + fresh if "." in full else fresh


def rewrite(symbol: str, old: str, new: str) -> str:
    """A receipt's name with the renamed declaration renamed, in an instance's arguments too: `f[geo.Pair]`."""
    return re.sub(rf"(?<![\w.]){re.escape(old)}(?![\w])", new, symbol)

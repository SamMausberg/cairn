"""Checked interface migrations: one host-authorized change to a function's interface, carried through every
caller of it across the project's files, applied to all of them or to none.

A one-function edit (`cairn.edit/1`, `cairn.edit/2`) can never change a signature; this is a separate
authorization class, and nothing in an edit session can reach it. The host names the function, its new
signature, any further functions whose signatures may change with it (`also`) and the effects the rows may gain.
The authorization binds those to the sha256 of every file of the project and to the compiler, so a reply written
against an older tree is stale and changes nothing. A reply replaces whole function declarations, only of the
authorized functions and their callers. The whole linked program is rechecked with every replacement in place;
every other signature must stay as it was, no declaration may appear or vanish, and no row may gain an effect the
host did not allow. Only then are the files written, each beside itself first and then renamed into place, and a
failure part way puts back every file already renamed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..compiler import compilations
from ..compiler.cairnc import Diagnostic, Parser, fail
from ..compiler.effects import EFFECT_FAMILIES, EFFECTS
from ..projects.project import Project, load_project
from .agent_tools import digest, implementation, load_json_strict, stable_json
from .projection import declarations, related, signature
from .teaching import select_cards
from .write_back import replace

PROTOCOL = "cairn.migration/1"
MAX_FUNCTIONS = 64  # authorized functions and callers one migration may carry


# Every kind of top-level declaration besides functions. A replacement declares none of them, and a migration leaves
# the program's set of them as it found it.
DECLARATIONS = ("records", "enums", "sums", "traits", "generics", "consts", "plans", "selections", "derivations",
                "families", "recipes", "imports")  # fmt: skip


def declared(text: str) -> Any:
    """The one function a signature or a replacement declares, and nothing else beside it."""
    parsed = Parser(text).parse()
    if len(parsed.functions) != 1 or any(getattr(parsed, kind) for kind in DECLARATIONS):
        fail("E-MIGRATION", "Write exactly one function declaration.")
    return parsed.functions[0]


def inventory(program: Any) -> set[tuple[str, str]]:
    """Each declaration other than a function, by kind and by what names it, schedules included."""
    named = {(kind, name) for kind in ("records", "enums", "sums", "traits", "generics", "consts", "recipes")
             for name in getattr(program, kind)}  # fmt: skip
    named |= {("plans", f"{p[0]}.{p[1]} {p[2]} {p[3]}") for p in program.plans}
    named |= {("selections", f"{s[0]}.{s[1]} use {s[2]}") for s in program.selections}
    named |= {("derivations", f"{d[0]}.{d[1]} {d[2]} {d[3]}") for d in program.derivations}
    named |= {("families", repr(family)) for family in program.families}
    named |= {("imports", repr(item)) for item in program.imports}
    return named


def spans(project: Project) -> dict[str, tuple[int, str]]:
    """Each unit's first offset in the combined source and its text, for the units the root project wrote."""
    lines = project.source.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    out = {}
    for u in project.units:
        at = starts[u.first_line - 1]
        out[u.path] = (at, "\n".join(lines[u.first_line - 1 : u.first_line - 1 + u.lines]))
    return out


def combined(project: Project, bodies: dict[str, str]) -> str:
    """The combined source with some units' text replaced, laid out exactly as `load_project` lays it out: one
    file alone is its own text, and a manifest's files each follow a `// source:` line."""
    old = spans(project)
    if len(project.units) == 1 and old[project.units[0].path][1] == project.source:
        return bodies.get(project.units[0].path, project.source)
    return "".join(f"// source: {u.path}\n{bodies.get(u.path, old[u.path][1])}\n" for u in project.units)


class Migration:
    """One authorized interface change of a project, the packet an agent reads for it, and the checked apply."""

    def __init__(self, path: str | Path, symbol: str, to: str, also: dict[str, str] | None = None,
                 effects: tuple[str, ...] = ()):  # fmt: skip
        self.path, self.project = Path(path), load_project(path)
        self.symbol, self.to, self.also = symbol, to, dict(also or {})
        self.effects = tuple(sorted(effects))
        if unknown := [e for e in self.effects if e not in EFFECTS and not e.startswith(EFFECT_FAMILIES)]:
            fail("E-MIGRATION", "The effects a migration allows are effect names.", effects=unknown)
        self.parsed = compilations.parsed(self.project.source)
        self.program, _, self.receipts = compilations.program(self.project.source)
        authored = {f.name: f for f in self.parsed.functions}
        changing = {symbol: to, **self.also}
        if missing := sorted(set(changing) - set(authored)):
            fail("E-SYMBOL", "Migrate an authored function of this project.", symbols=missing)
        if generic := sorted(n for n in changing if authored[n].static or authored[n].generics):
            fail("E-EDIT-PROFILE", "A migration changes ordinary functions, not templates.", symbols=generic)
        self.signatures = {}
        for name, text in changing.items():
            new = declared(text + " { }")
            if new.name != authored[name].name.rsplit(".", 1)[-1]:
                fail("E-MIGRATION", "A new signature keeps the function's name.", symbol=name)
            self.signatures[name] = signature(new)
        checked = {f.name: f.source_name or f.name for f in self.program.functions}  # an instance's template
        self.callers = sorted({checked.get(n, n) for n, r in self.receipts.items() if set(r["calls"]) & set(changing)}
                              & set(authored) - set(changing))  # fmt: skip
        self.names = [*changing, *self.callers]
        if len(self.names) > MAX_FUNCTIONS:
            fail("E-MIGRATION", f"A migration carries at most {MAX_FUNCTIONS} functions.")
        self.homes = {n: self.home(authored[n]) for n in self.names}
        self.authorization = digest(stable_json(self.bound()))

    def home(self, f: Any) -> tuple[str, int, int]:
        """(file, start, end) of a function's declaration in the file that holds it; a vendored one is refused."""
        for path, (at, text) in spans(self.project).items():
            if at <= f.start and f.end <= at + len(text):
                if path in self.project.vendored_units:
                    fail("E-MIGRATION", "A migration changes the project's own files, not a vendored one.",
                         symbol=f.name, file=path)  # fmt: skip
                return path, f.start - at, f.end - at
        fail("E-MIGRATION", "The function is not in one of the project's files.", symbol=f.name)

    def bound(self) -> dict[str, Any]:
        """Everything the authorization binds: the change, the tree it was made against, and the compiler."""
        return {"protocol": PROTOCOL, "project": self.project.name, "symbol": self.symbol, "to": self.to,
                "also": self.also, "effects": list(self.effects), "implementation": implementation(),
                "files": {u.path: u.sha256 for u in self.project.units}}  # fmt: skip

    def packet(self) -> dict[str, Any]:
        """What the agent reads: the change, and the declaration of every function it may rewrite, by file."""
        functions = {}
        for n in self.names:
            path, start, end = self.homes[n]
            functions[n] = {"file": path, "source": spans(self.project)[path][1][start:end],
                            "role": "migrated" if n == self.symbol or n in self.also else "caller"}  # fmt: skip
        text = "\n".join(f["source"] for f in functions.values())
        return {
            "protocol": PROTOCOL,
            "authorization": self.authorization,
            "change": {n: {"from": signature(self.function(n)), "to": s} for n, s in self.signatures.items()},
            "effects_allowed": list(self.effects),
            "functions": functions,
            "types": "\n".join(related(declarations(self.program), text).values()),
            "rule_cards": select_cards(text, True, bool(self.program.records), bool(self.program.sums)),
            "reply": {
                "protocol": PROTOCOL,
                "authorization": self.authorization,
                "functions": dict.fromkeys(self.names, "fn ... { ... }"),
            },
            "rules": [
                "Replace whole declarations as shown, pub included, only of the functions listed; leave out the rest.",
                "A migrated function takes exactly the new signature; every other signature stays as written.",
                "The whole program is rechecked; nothing is written unless every file checks, and then all are.",
            ],
        }

    def function(self, name: str) -> Any:
        return next(f for f in self.parsed.functions if f.name == name)

    def apply(self, reply: Any, write: bool = True) -> dict[str, Any]:
        """Check a reply against this authorization and the tree as it is now; then write every file, or none."""
        request = load_json_strict(reply) if isinstance(reply, str) else reply
        if not isinstance(request, dict) or set(request) != {"protocol", "authorization", "functions"}:
            fail("E-REQUEST", "A migration reply holds protocol, authorization and functions.")
        if request["protocol"] != PROTOCOL or request["authorization"] != self.authorization:
            fail("E-SESSION", "This reply answers another migration.")
        given = request["functions"]
        if not isinstance(given, dict) or not given or not all(isinstance(v, str) for v in given.values()):
            fail("E-REQUEST", "functions maps a function name to its whole new declaration.")
        if widened := sorted(set(given) - set(self.names)):
            fail("E-MIGRATION-SCOPE", "The reply rewrites functions this migration did not authorize.", symbols=widened)
        now = load_project(self.path)
        if {u.path: u.sha256 for u in now.units} != self.bound()["files"] or implementation() != self.bound()[
            "implementation"
        ]:
            fail(
                "E-SESSION",
                "The project or the compiler changed since this migration was authorized; nothing was applied.",
            )
        edits: dict[str, list[tuple[int, int, str]]] = {}
        for name, text in given.items():
            f, old = declared(text), self.function(name)
            if f.name != old.name.rsplit(".", 1)[-1]:
                fail("E-MIGRATION", "A replacement declares the function it replaces.", symbol=name)
            if f.public != old.public:
                fail("E-MIGRATION", "A replacement keeps the declaration's visibility, pub or not.", symbol=name)
            path, start, end = self.homes[name]
            edits.setdefault(path, []).append((start, end, text))
        bodies = {}
        for path, cuts in edits.items():
            text = spans(self.project)[path][1]
            for start, end, new in sorted(cuts, reverse=True):
                text = text[:start] + new + text[end:]
            bodies[path] = text
        candidate = combined(self.project, bodies)
        self.recheck(candidate, bodies)
        files = {
            path: {"before": digest(spans(self.project)[path][1]), "after": digest(text)}
            for path, text in bodies.items()
        }
        if write:
            self.write(bodies)
        return {"protocol": PROTOCOL, "status": "applied" if write else "checked", "authorization": self.authorization,
                "files": files, "functions": sorted(given), "whole_program_rechecked": True,
                "native_build": "not-run", "behavioral_tests": "not-run", "formal_status": "not-verified"}  # fmt: skip

    def recheck(self, candidate: str, bodies: dict[str, str]) -> None:
        """The whole linked program with every replacement in place: signatures first, then types, declarations
        and rows. A refusal names the file and line of the new text, where the agent wrote it."""
        try:
            whole = compilations.parsed(candidate)
            if inventory(whole) != inventory(self.parsed):  # a comment ending a replacement would hide the line's rest
                fail("E-DECLARATION", "A migration adds or removes no declaration.")
            parsed = {f.name: f for f in whole.functions}
            for f in self.parsed.functions:
                wanted = self.signatures.get(f.name, signature(f))
                if f.name not in parsed:
                    fail("E-DECLARATION", "A migration adds or removes no declaration.", symbol=f.name)
                if signature(parsed[f.name]) != wanted:
                    fail("E-SIGNATURE", f"{f.name} must have the signature {wanted}.", parsed[f.name], symbol=f.name)
            _, _, after = compilations.program(candidate)
        except Diagnostic as e:
            line, single = 1, len(self.project.units) == 1 and candidate == bodies.get(self.project.units[0].path)
            for u in self.project.units:
                text = bodies.get(u.path, spans(self.project)[u.path][1])
                first = line if single else line + 1
                if first <= e.data.get("line", 0) < first + text.count("\n") + 1:
                    e.data.update(file=u.path, line=e.data["line"] - first + 1)
                    break
                line = first + text.count("\n") + 1
            raise
        if set(after) != set(self.receipts):
            fail("E-DECLARATION", "A migration adds or removes no declaration.")
        allowed = set(self.effects)
        for name, r in after.items():
            if gained := sorted(set(r["effects"]) - set(self.receipts[name]["effects"]) - allowed):
                fail("E-CALLER-EFFECT", "The migration gives a row an effect the host did not allow.", symbol=name,
                     added_effects=gained)  # fmt: skip

    def write(self, bodies: dict[str, str]) -> None:
        """Every file written beside itself, then renamed into place; a failure puts back what was renamed."""
        replace(self.project.root, bodies, ".migration")


def migrate(path: str | Path, symbol: str, to: str, also: dict[str, str] | None = None, effects: tuple[str, ...] = (),
            reply: Any = None) -> dict[str, Any]:  # fmt: skip
    """The packet for a migration, or with `reply`, the result of applying it; a refusal is a Diagnostic."""
    m = Migration(path, symbol, to, also, effects)
    return m.packet() if reply is None else m.apply(reply)

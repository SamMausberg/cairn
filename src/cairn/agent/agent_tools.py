"""Compiler-backed edit sessions, and the projections they show an editing agent.

A typed edit is not an equivalence proof or a task verdict. The host owns the original source and the task
contract, and no edit request can change either. Session digests and handles keep one local host consistent;
they are not authentication.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ..compiler.cairnc import (
    VERSION,
    Diagnostic,
    Expr,
    Function,
    Parser,
    Program,
    Stmt,
    Type,
    compile_program,
    compile_source,
    fail,
)
from ..compiler.effects import EFFECT_FAMILIES, EFFECTS
from ..compiler.expansion import declared, derive
from ..compiler.lexing import lex
from ..compiler.modules import link
from .teaching import select_cards

PROTOCOL = "cairn.edit/1"  # A request bound by the session digest.
HANDLES = "cairn.edit/2"  # A request bound by a host handle; it may also ask to expand the context.
MAX_REPLACEMENT = 64_000
MAX_EXPAND = 32
SCOPES = ("focused", "component")


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def generics(params: list[tuple[str, str]]) -> str:
    shown = ", ".join(n if kind == "type" else f"{n}:{kind.replace('+', ' + ')}" for n, kind in params)
    return f"[{shown}]" if params else ""


def local(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def signature(f: Function) -> str:
    """The declared interface, exactly as an edit must preserve it."""
    ps = ", ".join(n + ":" + t.display() for n, t in f.params)
    ret = "" if f.ret.name == "void" else " -> " + f.ret.display()
    ceiling = "" if f.effects is None else " pure" if f.effects == ("pure",) else f" effects({', '.join(f.effects)})"
    written = f.name if f.source_name.startswith("derive ") else f.source_name  # A derived impl keeps its origin there.
    name = written.rsplit(".", 1)[-1] if f.owner else f.name.rsplit(".", 1)[-1] if f.module else f.name
    link = f'"{f.symbol}" ' if f.symbol else ""
    return f"{'extern ' * f.extern}{link}{'kernel ' * f.kernel}fn {name}{generics(f.generics if not f.bindings else [])}({ps}){ret}{ceiling}"


ESCAPES = {"\n": "\\n", "\t": "\\t", "\r": "\\r", "\0": "\\0", "\\": "\\\\", '"': '\\"'}


def format_expr(e: Expr) -> str:
    args = [format_expr(a) for a in e.args] if e.tag != "lambda" else []
    if e.tag in {"name", "int", "float", "bool", "function"}:
        return e.val
    if e.tag == "str":
        return '"' + "".join(ESCAPES.get(c, c if 32 <= ord(c) < 127 else f"\\x{ord(c):02x}") for c in e.val) + '"'
    if e.tag == "call":
        targs = e.ref if isinstance(e.ref, tuple) and all(isinstance(t, Type | int) for t in e.ref) else ()
        shown = "[" + ", ".join(t.display() if isinstance(t, Type) else str(t) for t in targs) + "]" if targs else ""
        if e.val.startswith("."):
            return f"{args[0]}{e.val}{shown}({', '.join(args[1:])})"
        return f"{e.val}{shown}({', '.join(args)})"
    if e.tag == "index":
        return f"{args[0]}[{', '.join(args[1:])}]"
    if e.tag == "slice":
        return f"{args[0]}[{args[1]}..{args[2]}]"
    if e.tag == "field":
        return f"{args[0]}.{e.val}"
    if e.tag == "spawn" and isinstance(e.ref, Stmt):  # Queued device work: the region is the operand.
        inner = format_block([e.ref], -1).split("\n")[1:-1]  # The region's own lines, without a block around them.
        return "spawn " + "\n".join(inner).strip()
    if e.tag in {"try", "spawn"}:
        return f"{e.tag} {args[0]}" + (" after " + ", ".join(args[1:]) if args[1:] else "")
    if e.tag == "coerce":
        return args[0]
    if e.tag == "unary":
        return f"({e.val}{args[0]})"
    if e.tag == "binary":
        return f"({args[0]} {e.val} {args[1]})"
    if e.tag == "lambda":
        f = e.ref
        ps = ", ".join(n + ":" + t.display() for n, t in f.params)
        return f"|{ps}|" + ("" if f.ret.name == "void" else " -> " + f.ret.display()) + " " + format_block(f.body)
    fail("E-PROJECTION", "Cannot project unknown expression kind.")


def format_block(ss: list[Stmt], indent: int = 0) -> str:
    lines = ["{"]
    pad = "  " * (indent + 1)

    def nested(body: list[Stmt]) -> str:
        return format_block(body, indent + 1)

    for s in ss:
        es = [format_expr(e) for e in s.exprs]
        typed = ":" + s.ty.display() if s.tag in {"let", "reg", "reduce"} and s.ty else ""
        if s.tag in {"buffer", "stack"}:
            place = "" if s.ty.place == "host" else "@" + s.ty.place
            line = f"{s.tag} {s.name}:{s.ty.value.display()}[{es[0]}]{place} = zeroed;"
        elif s.tag in {"let", "reg"}:
            line = f"{'let mut' if s.tag == 'reg' else 'let'} {s.name}{typed} = {es[0]};"
        elif s.tag == "unpack":
            line = f"{'let mut' if s.op == 'reg' else 'let'} {s.name}({', '.join(n.val for n in s.other_names)}) = {es[0]};"
        elif s.tag == "compact":
            line = f"let {s.name} = compact {es[0]} for {s.binder} in {es[1]} where {es[2]} yield {es[3]};"
        elif s.tag == "reduce":
            line = f"let {s.name}{typed} = reduce {s.op} for {s.binder} in {es[0]} yield {es[1]};"
        elif s.tag == "assign":
            line = f"{es[0]} = {es[1]};"
        elif s.tag in {"break", "continue"}:
            line = s.tag + ";"
        elif s.tag == "return":
            line = "return" + (" " + es[0] if es else "") + ";"
        elif s.tag == "expr":
            line = es[0] + ";"
        elif s.tag == "submit":
            line = f"{es[0]} into {s.name};"
        elif s.tag == "match":
            arms = [
                f"{pad}  {a.variant}{f'({a.binder})' if a.binder else ''} => {format_block(a.body, indent + 2)}"
                for a in s.arms
            ]
            line = "\n".join([f"match {es[0]} {{", *arms, pad + "}"])
        elif s.tag in {"if", "while"}:
            line = f"{s.tag} {es[0]} {nested(s.body)}" + (f"\n{pad}else {nested(s.other)}" if s.other else "")
        elif s.tag == "for":
            line = f"for {s.name} in {es[0]}..{es[1]} {nested(s.body)}"
        elif s.tag == "parallel":
            order = " after " + ", ".join(t.val for t in s.other_names) if s.other_names else ""
            line = f"parallel {s.name} in {es[0]}{order} {nested(s.body)}"
        elif s.tag == "defer":
            line = "defer " + format_block(s.body, indent).split("\n", 2)[1].strip()
        elif s.tag in {"unsafe", "block"}:
            line = ("unsafe " if s.tag == "unsafe" else "") + nested(s.body)
        else:
            fail("E-PROJECTION", "Cannot project unknown statement kind.")
        lines.append(pad + line)
    lines.append("  " * indent + "}")
    return "\n".join(lines)


def declarations(p: Program) -> dict[str, str]:
    """Every record, enum, sum, trait and constant declaration, keyed by its full name."""
    out: dict[str, str] = {}

    def pub(name: str) -> str:
        return "pub " if name in p.public else ""

    for n, fs in p.records.items():
        marks = p.attributes.get(n, set())
        layout = "".join(" " + a for a in sorted(marks - {"linear"}))
        carried = p.field_extents.get(n, {})
        fields = " ".join(f"{k}:{t.display()}{'[' + carried[k] + ']' if k in carried else ''};" for k, t in fs)
        linear = "linear " * ("linear" in marks)
        out[n] = f"{pub(n)}{linear}struct {local(n)}{generics(p.generics.get(n, []))}{layout} {{ {fields} }}"
    for n, vs in p.enums.items():
        out[n] = f"{pub(n)}enum {local(n)} {{ {' '.join(v + ';' for v in vs)} }}"
    for n, payloads in p.sums.items():
        variants = " ".join(v + (f"({t.display()})" if t else "") + ";" for v, t in payloads)
        out[n] = f"{pub(n)}enum {local(n)}{generics(p.generics.get(n, []))} {{ {variants} }}"
    for n, members in p.traits.items():
        out[n] = f"{pub(n)}trait {local(n)} {{ {' '.join(signature(m) + ';' for m in members)} }}"
    out.update({n: f"{pub(n)}const {local(n)}:{t.display()} = {format_expr(e)};" for n, (t, e) in p.consts.items()})
    return out


def type_declarations(p: Program) -> str:
    return "\n".join(declarations(p).values())


def related(table: dict[str, str], text: str) -> dict[str, str]:
    """The declarations `text` names, closed over the names those declarations use in turn."""
    words = {t.s for t in lex(text)}
    chosen: dict[str, str] = {}
    while fresh := {n: d for n, d in table.items() if n not in chosen and local(n) in words}:
        chosen.update(fresh)
        words |= {t.s for d in fresh.values() for t in lex(d)}
    return {n: d for n, d in table.items() if n in chosen}


def function_source(f: Function) -> str:
    if f.extern:
        return signature(f) + ";"
    return signature(f) + " " + format_block(f.body, 1 if f.owner else 0)


def derivation(recipe: str, naturals: tuple, target: str) -> str:
    arguments = f"[{', '.join(map(str, naturals))}]" if naturals else ""
    return f"derive {recipe}{arguments}" + (f" for {target}" if target else "") + ";"


def canonical_source(source: str) -> str:
    """An inspectable AST projection. Comments are not copied. Not an in-place edit."""
    return projection(Parser(source).parse(), source)


def expanded_source(source: str) -> str:
    """What the program's derivations generated, in the same projection: generated code reads as ordinary code."""
    p = link(Parser(source).parse())
    written = declared(p)
    p = derive(p)
    records = {n: fields for n, fields in p.records.items() if n not in written}
    made = [f for f in p.functions if f.name not in written]
    modules = {n: p.modules[n] for n in [*records, *(f.name for f in made)]}
    return projection(
        Program(records, functions=made, generics=p.generics, public=p.public, modules=modules,
                field_extents=p.field_extents),
        source,
    )  # fmt: skip


def projection(p: Program, source: str) -> str:
    out = []
    for module in dict.fromkeys(p.modules.values()):
        tables: dict[str, Any] = {k: {n: v for n, v in getattr(p, k).items() if p.modules.get(n, "") == module}
                  for k in ("records", "enums", "sums", "traits", "consts")}  # fmt: skip
        out += [f"module {module};"] if module else []
        for importer, target, alias in p.imports:
            if importer != module:
                continue
            names = [n for (m, n), full in p.uses.items() if m == module and full == f"{target}.{n}"]
            renamed = f" as {alias}" if alias != local(target) else ""
            out.append(f"import {target}{renamed}{' (' + ', '.join(names) + ')' if names else ''};")
        shown = type_declarations(
            Program(**tables, generics=p.generics, attributes=p.attributes, public=p.public,
                    field_extents=p.field_extents)
        )  # fmt: skip
        out += [shown] if shown else []
        members: list[Function] = []
        for f in [*(f for f in p.functions if f.module == module), None]:
            if members and (f is None or f.owner != members[0].owner):  # Close the impl block in its place.
                shared = [g for g in members[0].generics if all(g in m.generics for m in members)]
                bodies = ["  " + function_source(replace(m, generics=m.generics[len(shared) :])) for m in members]
                trait, self_type = members[0].owner
                out.append(f"impl{generics(shared)} {trait} for {self_type.display()} {{\n" + "\n".join(bodies) + "\n}")
                members = []
            if f is not None and f.owner:
                members.append(f)
            elif f is not None:
                out.append(("pub " if f.public and module else "") + function_source(f))
        out += [f"{'pub ' * (pre in p.public)}family {local(pre)} = {name}[{lo}..{hi}];"
                for pre, name, lo, hi in p.families if p.modules[pre] == module]  # fmt: skip
        out += [source[r.start : r.end] for r in p.recipes.values() if r.module == module]  # A recipe is its own text.
        out += [derivation(r, naturals, target) for m, r, naturals, target, _ in p.derivations if m == module]
    return "\n\n".join(out) + "\n"


def semantic_ast(p: Program) -> Any:
    """Strip annotations/source locations, preserving all authored AST decisions."""
    erased = {"line", "col", "start", "end", "body_start", "block", "cpp", "ty_inferred"}

    def erase(v):
        if isinstance(v, dict):
            return {k: erase(x) for k, x in v.items() if k not in erased}
        return [erase(x) for x in v] if isinstance(v, list | tuple) else v

    return erase(asdict(p))


HINTS = {
    "E-MATCH-COVERAGE": "Handle the listed missing variants exactly once. Do not delete variants or weaken the task to silence coverage.",
    "E-MATCH-BINDING": "Bind one fresh immutable value only for an arm that has a declared payload.",
    "E-OWNER-EXTENT": "Bind a computed capacity to an immutable usize before declaring the buffer.",
    "E-STACK-LIMIT": "Reduce explicit stack storage, or request an authorized heap-allocation effect. Do not hide the cost.",
    "E-LOOP-CONTROL": "break and continue require an enclosing for or while loop.",
    "E-TYPE-MISMATCH": "Use the expected type. An explicit conversion may trap; do not change the function signature to hide a mismatch.",
    "E-UNBOUND": "Choose a name from the supplied lexical environment, or introduce a local before this use.",
    "E-CALLEE": "Only declared callables and listed primitives are legal. Ask the host to expand a dependency instead of inventing an API.",
    "E-IMMUTABLE": "Parameters and let bindings are immutable. Use a separate let mut local when mutation is required.",
    "E-WRITE-LEASE": "This location is not writable. Do not change ro to rw inside a repair; the host owns that contract.",
    "E-SHADOW": "Choose a fresh descriptive local name; this profile forbids shadowing.",
    "E-EFFECT-ORDER": "Bind a writing call at statement level before using its result.",
    "E-COLLECT-CAPACITY": "The collector extent must match the declared output capacity exactly in this profile.",
    "E-RETURN": "Write an explicit return on every required path. Rust-style implicit tail returns are not supported.",
    "E-PARSE": "Use braces, semicolons and the implemented grammar. This is not arbitrary Rust or Python.",
    "E-SESSION": "Refresh the packet from the host. Never guess a session digest or a handle.",
    "E-EFFECT-EXPANSION": "The candidate exceeds the host-owned effect ceiling. Change the implementation, not the contract.",
    "E-CONTEXT-CLOSURE": "The candidate calls a function the packet does not disclose. Ask the host to expand it first.",
    "E-SYMBOL": "Name a function or type of this program exactly as a packet or a body shows it.",
}


def explain(error: Diagnostic, source: str = "") -> dict[str, Any]:
    d = dict(error.data)
    d["repair_hint"] = HINTS.get(d["code"], "Repair the stated obligation without weakening the host-owned contract.")
    d["automatic_edit"] = False
    line = d.get("line", 0)
    if 1 <= line <= len(source.splitlines()):
        d["source_line"] = source.splitlines()[line - 1][:500]
    d["acceptance_boundary"] = "frontend only; not a semantic or machine proof"
    return d


def load_json_strict(text: str) -> Any:
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                fail("E-REQUEST", "Duplicate JSON field: " + k)
            d[k] = v
        return d

    try:
        return json.loads(
            text, object_pairs_hook=pairs, parse_constant=lambda s: fail("E-REQUEST", "Nonfinite JSON value")
        )
    except (json.JSONDecodeError, RecursionError) as e:
        fail("E-REQUEST", str(e))


def comment_above(text: str, offset: int) -> list[str]:
    """The `//` lines directly above a declaration, as one paragraph (or nothing)."""
    lines = text[:offset].rstrip().split("\n") if offset > 0 else []
    found: list[str] = []
    while lines and lines[-1].lstrip().startswith("//"):
        found.insert(0, lines.pop().lstrip()[2:].strip())
    return [" ".join(found)] if found else []


BOUNDARIES = [
    "No permission to change parameters, imports, target flags, tests or task contract.",
    "Typed admission does not imply the requested behavior, termination, equivalence, or performance.",
    "No fresh-model success rate has been measured.",
]
FOCUSED = (
    "The target's source; the signature, effect row and host contract of everything it may call and everything"
    " that calls it; the types those name. Expand any other function or type by name. The whole module is rechecked."
)
COMPONENT = "Entire static call-graph component plus all record/enum/sum definitions; full module rechecked."


class EditSession:
    """One authored function, the host's contract for it, and what an edit of it may see and call.

    `focused` (the default) discloses the target, its direct callees and callers by interface, and grows by
    `expand`; `component` discloses the whole connected call graph. Both recheck the whole linked module, and
    a candidate may call only what has been disclosed, so the focused boundary is never wider.
    """

    def __init__(
        self,
        source: str,
        symbol: str,
        contract: dict[str, Any] | None = None,
        include: tuple[str, ...] = (),
        scope: str = "focused",
    ):
        self.source, self.symbol, self.scope = source, symbol, scope
        self.contract = {} if contract is None else copy.deepcopy(contract)
        if scope not in SCOPES:
            fail("E-REQUEST", "An edit scope is focused or component.")
        if not isinstance(self.contract, dict):
            fail("E-CONTRACT", "Contract must be a host-owned JSON object.")
        if self.contract.get("symbol", symbol) != symbol:
            fail("E-CONTRACT", "Contract names another function.")
        self.parsed = Parser(source).parse()
        authored = {f.name: f for f in self.parsed.functions}
        if symbol not in authored:
            fail("E-SYMBOL", "Edit an authored function, not a generated entry.")
        self.f = authored[symbol]
        if self.f.static:
            fail("E-EDIT-PROFILE", "Template-body editing is not implemented; edit ordinary functions.")
        self.cpp, self.receipt = compile_source(source)
        self.program, checker, _ = compile_program(source, capture_sites=True)
        fs = self.receipt["functions"]
        effects = fs[symbol]["effects"]
        allowed = self.contract.get("allowed_effects", effects)
        if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
            fail("E-CONTRACT", "allowed_effects must be a list of effect strings.")
        borrows = {n: t.mode for n, t in self.f.params if t.mode != "value"}
        permitted = {"read:" + n for n in borrows} | {"write:" + n for n, mode in borrows.items() if mode == "rw"}
        legal = {x for x in allowed if x in EFFECTS or x.startswith(EFFECT_FAMILIES)} | permitted
        if set(allowed) - legal:
            fail("E-CONTRACT", "Unknown effect or inaccessible memory permission in contract.")
        if set(effects) - set(allowed):
            fail("E-CONTRACT", "Baseline itself exceeds the supplied effect ceiling.")
        self.allowed_effects = set(allowed)
        self.contracts = self.contract.get("contracts", {})
        if not isinstance(self.contracts, dict) or not all(isinstance(v, str) for v in self.contracts.values()):
            fail("E-CONTRACT", "contracts maps a function name to the host's text for it.")
        if set(self.contracts) - set(fs) or set(include) - set(fs):
            fail("E-SYMBOL", "An included or contracted symbol is not in this module.")
        self.callers = sorted(n for n, r in fs.items() if symbol in r["calls"] and n != symbol)
        selected = {symbol, *include, *fs[symbol]["calls"], *self.callers}
        while scope == "component":  # Both call directions: a connected component, not a minimum sufficient context.
            grown = {m for n, r in fs.items() if n in selected or set(r["calls"]) & selected for m in (n, *r["calls"])}
            if grown <= selected:
                break
            selected |= grown
        self.visible = selected
        self.shown: list[str] = []  # What `expand` has disclosed as source, in order.
        self.sites: dict[str, dict[str, Any]] = {}
        for site in checker.sites:
            if site["symbol"] == symbol and site["end"] > site["start"]:
                # Expressions inferred without an expected type are still checked in their original context.
                key = digest(stable_json([digest(source), symbol, site["start"], site["end"]]))
                self.sites[key] = {**site, "site": key, "source": source[site["start"] : site["end"]]}
        ordered = sorted(self.sites, key=lambda k: (self.sites[k]["start"], self.sites[k]["end"]))
        self.site_names = {f"x{i}": key for i, key in enumerate(ordered)}  # Short names for handle requests.
        files = sorted(
            p
            for p in Path(__file__).parents[1].rglob("*")
            if p.suffix in {".py", ".hpp", ".cairn"} and "__pycache__" not in p.parts
        )
        self.implementation_hash = digest(b"".join(f.name.encode() + b"\0" + f.read_bytes() + b"\0" for f in files))
        self.seal()

    def seal(self) -> None:
        """Bind the digest to everything an edit is judged against, including what it may call."""
        bound = {"source": digest(self.source), "symbol": self.symbol, "contract": self.contract,
                 "visible": sorted(self.visible), "implementation": self.implementation_hash}  # fmt: skip
        self.session = digest(stable_json(bound if self.scope == "component" else {**bound, "scope": self.scope}))

    def interface(self, name: str) -> dict[str, Any]:
        """What a caller may rely on: signature, effect row, the host's contract (None when it gave none), and the
        comment written above the declaration, which nothing checks."""
        f = next(g for g in self.program.functions if g.name == name)
        origin = next((g for g in self.parsed.functions if g.name in {f.source_name, name}), None)
        text, start = (self.source, origin.start) if origin else (self.program.sources.get(f.module, ""), f.start)
        entry = {"signature": signature(f), "effects": self.receipt["functions"][name]["effects"]}
        comment = comment_above(text, start)
        return {**entry, "contract": self.contracts.get(name), **({"comment": comment[0]} if comment else {})}

    def cards(self, text: str, names: set[str], types: dict[str, str]) -> dict[str, str]:
        functions = {f.name: f for f in self.program.functions}
        views = any(t.mode != "value" for n in names for _, t in functions[n].params)
        if self.scope == "component":
            return select_cards(text, views, bool(self.parsed.records or self.parsed.enums), bool(self.parsed.sums))
        records = any(n in self.program.records or n in self.program.enums for n in types)
        return select_cards(text, views, records, any(n in self.program.sums for n in types))

    def packet(self, site: str | None = None) -> dict[str, Any]:
        p = self.component() if self.scope == "component" else self.focused()
        p["draft_protocol"] = {"protocol": PROTOCOL, "session": self.session, "kind": "body", "replacement": "{ ... }"}
        if site is not None:
            if site not in self.sites:
                fail("E-SITE", "Unknown expression site.")
            p["focus"] = self.sites[site]
            p["draft_protocol"].update(kind="expr", site=site, replacement=p["focus"]["source"])
        return p

    def header(self, protocol: str) -> dict[str, Any]:
        return {
            "protocol": protocol,
            "session": self.session,
            "symbol": self.symbol,
            "signature": signature(self.f),
            "profile": VERSION,
            "task": self.contract.get("task", "No behavioral task contract supplied; do not infer one."),
            "contract_sha256": digest(stable_json(self.contract)),
            "source_sha256": digest(self.source),
            "implementation_sha256": self.implementation_hash,
            "allowed_effects": sorted(self.allowed_effects),
        }

    def component(self) -> dict[str, Any]:
        names = {f.name: f for f in self.program.functions}
        origins = {names[n].source_name for n in self.visible}
        context = [{"symbol": f.name, "source": self.source[f.start : f.end]}
                   for f in self.parsed.functions if f.name in origins or f.name in self.visible]  # fmt: skip
        context += [{"family": prefix, "source": f"family {prefix} = {base}[{lo}..{hi}];"}
                    for prefix, base, lo, hi in self.parsed.families if base in origins]  # fmt: skip
        for module, recipe, naturals, target, _ in self.parsed.derivations:
            full = f"{module}.{target}" if module and target and "." not in target else target
            if f"derive {recipe}" + (f" for {full}" if target else "") in origins:
                context.append({"wire" if recipe == "wire" else "derive": full,
                                "source": derivation(recipe, naturals, target)})  # fmt: skip
        return {
            **self.header("cairn.packet/1"),
            "types": type_declarations(self.parsed),
            "context": context,
            "rule_cards": self.cards("\n".join(x["source"] for x in context), self.visible, {}),
            "dependencies": {
                n: {"signature": signature(names[n]), "effects": self.receipt["functions"][n]["effects"]}
                for n in sorted(self.visible)
            },
            "limits": {"replacement_bytes": MAX_REPLACEMENT, "one_authored_function": True},
            "scope": COMPONENT,
            "boundaries": BOUNDARIES,
        }

    def focused(self) -> dict[str, Any]:
        others = sorted(self.visible - {self.symbol})
        dependencies = {n: self.interface(n) for n in others}
        context = [{"symbol": self.symbol, "source": self.source[self.f.start : self.f.end]}, *self.expansions()]
        text = "\n".join([*(x["source"] for x in context), *(d["signature"] for d in dependencies.values())])
        types = related(declarations(self.program), text)
        written = sorted(n for n in self.receipt["functions"] if n not in self.visible and not n.startswith("std."))
        effects = self.receipt["functions"][self.symbol]["effects"]
        return {
            **self.header("cairn.packet/2"),
            **(
                {"effects": effects} if set(effects) != self.allowed_effects else {}
            ),  # The row now, when below the ceiling.
            "types": "\n".join(types.values()),
            "context": context,
            "rule_cards": self.cards(text, {self.symbol, *others}, types),
            "dependencies": dependencies,
            "callers": self.callers,
            "not_shown": written,
            "limits": {"replacement_bytes": MAX_REPLACEMENT, "one_authored_function": True, "expand": MAX_EXPAND},
            "scope": FOCUSED,
            "boundaries": BOUNDARIES,
        }

    def source_of(self, name: str) -> str:
        """A function as it was written, or, for one a recipe or family generated, as the projection shows it."""
        f = next(g for g in self.program.functions if g.name == name)
        origin = next((g for g in self.parsed.functions if g.name in {f.source_name, name}), None)
        if origin is None:
            return function_source(f)
        family = [
            f"family {pre} = {base}[{lo}..{hi}];" for pre, base, lo, hi in self.parsed.families if base == origin.name
        ]
        return "\n".join([self.source[origin.start : origin.end], *family])

    def expansions(self) -> list[dict[str, str]]:
        return [{"symbol": n, "source": self.source_of(n)} for n in self.shown]

    def expand(self, names: Any) -> dict[str, Any]:
        """Disclose more of the pinned program: a function's source, which the candidate may then call, or a type.

        Disclosure changes the session digest, so a request bound to the old one is stale.
        """
        if not (isinstance(names, list) and 0 < len(names) <= MAX_EXPAND and all(isinstance(n, str) for n in names)):
            fail("E-REQUEST", f"Expand one to {MAX_EXPAND} names.")
        table = declarations(self.program)
        functions, types = [], {}
        for name in names:
            full = [n for n in table if name in {n, local(n)}]
            if len(full) == 1:
                types[full[0]] = table[full[0]]
            elif name in self.receipt["functions"] and name != self.symbol:
                functions.append(name)
            else:
                fail("E-SYMBOL", "Nothing of that name to expand, or more than one thing.", symbol=name)
        fresh, cards = sorted(set(functions) - self.visible), self.focused()["rule_cards"]
        self.visible |= set(functions)
        self.shown += [n for n in dict.fromkeys(functions) if n not in self.shown]
        self.seal()
        bodies = [{"symbol": n, "source": self.source_of(n)} for n in dict.fromkeys(functions)]
        mentioned = related(table, "\n".join(b["source"] for b in bodies))
        return {
            "protocol": "cairn.expansion/1",
            "session": self.session,
            "context": bodies,
            "types": "\n".join({**mentioned, **types}.values()),
            "dependencies": {n: self.interface(n) for n in fresh},
            "rule_cards": {n: text for n, text in self.focused()["rule_cards"].items() if n not in cards},
        }

    def check(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """An edit/1 request: the session digest binds it to this source, contract, context and compiler."""
        if not isinstance(request, dict):
            fail("E-REQUEST", "Edit request must be an object.")
        kind = request.get("kind")
        keys = {"protocol", "session", "kind", "replacement"} | ({"site"} if kind == "expr" else set())
        if set(request) != keys:
            fail("E-REQUEST", "Missing or unknown edit fields.", fields=sorted(set(request) ^ keys))
        if request.get("protocol") != PROTOCOL:
            fail("E-REQUEST", "Unsupported edit protocol.")
        if request.get("session") != self.session:
            fail("E-SESSION", "Stale or mismatched source, policy, context, or toolchain.")
        return self.admit(kind, request.get("replacement"), request.get("site"))

    def admit(self, kind: Any, text: Any, site: Any = None) -> tuple[str, dict[str, Any]]:
        if not isinstance(text, str) or len(text.encode()) > MAX_REPLACEMENT:
            fail("E-REQUEST", "Replacement must be bounded UTF-8 source.")
        parser = Parser(text)
        if kind == "body":
            if parser.eat("="):
                if self.f.ret.name == "void":
                    fail("E-EXPRESSION-BODY", "Expression bodies cannot return void.")
                parser.expr()
                parser.need(";")
            else:
                parser.block()
            parser.need("<eof>")
            start, end = self.f.body_start, self.f.end
        elif kind == "expr":
            if not isinstance(site, str) or site not in self.sites:
                fail("E-SITE", "Unknown or stale expression site.")
            parser.expr()
            parser.need("<eof>")
            start, end = self.sites[site]["start"], self.sites[site]["end"]
            text = "(" + text + ")"  # Operator binding at the insertion site must not change the tree around it.
        else:
            fail("E-REQUEST", "Expected body or expr edit kind.")
        candidate = self.source[:start] + text + self.source[end:]  # Every byte outside the span is preserved.
        receipt = compile_source(candidate)[1]
        f = next(f for f in Parser(candidate).parse().functions if f.name == self.symbol)
        if signature(f) != signature(self.f):
            fail("E-SIGNATURE", "Function signature changed.")
        before, after = self.receipt["functions"], receipt["functions"]
        if set(after) != set(before):
            fail("E-DECLARATION", "Declaration set changed.")
        if additions := set(after[self.symbol]["effects"]) - self.allowed_effects:
            fail("E-EFFECT-EXPANSION", "Candidate exceeds its effect ceiling.", added_effects=sorted(additions))
        if unknown := set(after[self.symbol]["calls"]) - self.visible:
            fail("E-CONTEXT-CLOSURE", "Candidate introduces an undisclosed callee.", symbols=sorted(unknown))
        for name, r in after.items():
            if name != self.symbol and (delta := set(r["effects"]) - set(before[name]["effects"])):
                fail("E-CALLER-EFFECT", "Candidate expands an unchanged caller footprint.", symbol=name,
                     added_effects=sorted(delta))  # fmt: skip
        return candidate, {
            "protocol": "cairn.admission/1",
            "status": "typed",
            "session": self.session,
            "symbol": self.symbol,
            "candidate_sha256": digest(candidate),
            "effects": after[self.symbol]["effects"],
            "check_sites": after[self.symbol]["syntactic_check_sites"],
            "check_sites_before": before[self.symbol]["syntactic_check_sites"],
            "runtime_cost": "unmeasured; guard-site counts are static, not execution costs",
            "source_outside_edit_unchanged": True,
            "contract_unchanged": True,
            "full_module_rechecked": True,
            "native_build": "not-run",
            "behavioral_tests": "not-run",
            "equivalence": "not-proved",
            "formal_status": "not-verified",
        }

    def candidates(self, site: str, expressions: list[str]) -> list[dict[str, Any]]:
        if len(expressions) > 128:
            fail("E-REQUEST", "At most 128 candidates per query.")
        results = []
        for expr in expressions:
            r = {"protocol": PROTOCOL, "session": self.session, "kind": "expr", "site": site, "replacement": expr}
            try:
                _, receipt = self.check(r)
                results.append({"expression": expr, "status": "typed", "effects": receipt["effects"]})
            except Diagnostic as e:
                results.append({"expression": expr, **explain(e)})
        return results


HOST_KEPT = ("session", "contract_sha256", "source_sha256", "implementation_sha256")


class EditHost:
    """The sessions of one conversation with a model, named by short handles.

    The host keeps every digest, the pinned sources and each admitted candidate, so a request names a session
    as `e1` and a site as `x3` and never copies a hash. A handle resolves to the same session object that an
    edit/1 digest names, and admission runs the same checks. Each rule card and the boundary text are sent once
    per host; a later packet lists them under `sent_before`.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, EditSession] = {}
        self.sent: set[str] = set()
        self.admitted: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    def open(self, source: str, symbol: str, contract: dict[str, Any] | None = None, include: tuple[str, ...] = (),
             scope: str = "focused", site: str | None = None) -> dict[str, Any]:  # fmt: skip
        handle = f"e{len(self.sessions) + 1}"
        self.sessions[handle] = EditSession(source, symbol, contract, include, scope)
        return self.packet(handle, site)

    def packet(self, handle: str, site: str | None = None) -> dict[str, Any]:
        s = self.session(handle)
        key = None if site is None else s.site_names.get(site, site)
        p = {k: v for k, v in s.packet(key).items() if k not in HOST_KEPT}
        p["handle"] = handle
        draft = p.pop("draft_protocol")
        p["draft_protocol"] = {"protocol": HANDLES, "handle": handle, "kind": draft["kind"],
                               "replacement": draft["replacement"]}  # fmt: skip
        if "focus" in p:
            name = next(n for n, k in s.site_names.items() if k == p["focus"]["site"])
            p["focus"] = {**{k: v for k, v in p["focus"].items() if k != "site"}, "site": name}
            p["draft_protocol"]["site"] = name
        if s.scope == "focused":
            p["expand_protocol"] = {"protocol": HANDLES, "handle": handle, "kind": "expand", "symbols": ["name"]}
        earlier = [n for n in [*p["rule_cards"], "boundaries"] if n in self.sent]
        self.sent |= {*p["rule_cards"], "boundaries"}
        p["rule_cards"] = {n: text for n, text in p["rule_cards"].items() if n not in earlier}
        p.pop("boundaries") if "boundaries" in earlier else None
        if earlier:
            p["sent_before"] = earlier
        return p

    def session(self, handle: Any) -> EditSession:
        if not isinstance(handle, str) or handle not in self.sessions:
            fail("E-SESSION", "Unknown handle; the host opens sessions.")
        return self.sessions[handle]

    def respond(self, request: Any) -> dict[str, Any]:
        """An edit/2 request: `body`, `expr` (with a short site name) or `expand` (with symbols)."""
        if not isinstance(request, dict):
            fail("E-REQUEST", "Edit request must be an object.")
        kind = request.get("kind")
        extra = {"expr": {"site", "replacement"}, "body": {"replacement"}, "expand": {"symbols"}}.get(kind)
        if extra is None:
            fail("E-REQUEST", "Expected body, expr or expand.")
        keys = {"protocol", "handle", "kind", *extra}
        if set(request) != keys:
            fail("E-REQUEST", "Missing or unknown edit fields.", fields=sorted(set(request) ^ keys))
        if request["protocol"] != HANDLES:
            fail("E-REQUEST", "Unsupported edit protocol.")
        s = self.session(request["handle"])
        if kind == "expand":
            if s.scope != "focused":
                fail("E-REQUEST", "A component packet already shows everything it may call.")
            grown = {k: v for k, v in s.expand(request["symbols"]).items() if k != "session"}
            grown["rule_cards"] = {n: text for n, text in grown["rule_cards"].items() if n not in self.sent}
            self.sent |= set(grown["rule_cards"])
            return grown
        site = s.site_names.get(request["site"]) if kind == "expr" and isinstance(request["site"], str) else None
        candidate, receipt = s.admit(kind, request["replacement"], site)
        self.admitted.setdefault(request["handle"], []).append((candidate, receipt))
        return {k: v for k, v in receipt.items() if k not in {"session", "candidate_sha256"}}

    def reply(self, text: str) -> dict[str, Any]:
        """A model's raw reply, parsed strictly; a refusal comes back as the diagnostic it would read."""
        request = None
        try:
            request = load_json_strict(text)
            return self.respond(request)
        except Diagnostic as e:
            handle = request.get("handle") if isinstance(request, dict) else None
            s = self.sessions.get(handle) if isinstance(handle, str) else None
            return explain(e, s.source if s else "")

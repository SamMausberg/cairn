"""CAIRN 0.3: compiler-backed edit sessions and conservative context projections.

A typed edit is NOT an equivalence proof or a complete task-correctness verdict.
The host owns the original source and task contract; neither is accepted from
an edit request. Sessions are consistency controls, not authentication tokens.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
from typing import Any

from .cairnc import (
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
from .checking import EFFECT_FAMILIES, EFFECTS
from .teaching import select_cards

PROTOCOL = "cairn.edit/1"
MAX_REPLACEMENT = 64_000


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def generics(params: list[tuple[str, str]]) -> str:
    shown = ", ".join(n if kind == "type" else f"{n}:{kind}" for n, kind in params)
    return f"[{shown}]" if params else ""


def signature(f: Function) -> str:
    """The declared interface, exactly as an edit must preserve it."""
    ps = ", ".join(n + ":" + t.display() for n, t in f.params)
    ret = "" if f.ret.name == "void" else " -> " + f.ret.display()
    ceiling = "" if f.effects is None else " pure" if f.effects == ("pure",) else f" effects({', '.join(f.effects)})"
    name = f.source_name.rsplit(".", 1)[-1] if f.owner else f.name.rsplit(".", 1)[-1] if f.module else f.name
    return f"{'extern ' * f.extern}{'kernel ' * f.kernel}fn {name}{generics(f.generics if not f.bindings else [])}({ps}){ret}{ceiling}"


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
    if e.tag in {"try", "spawn"}:
        return f"{e.tag} {args[0]}"
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
        if s.tag in {"buffer", "stack"}:
            place = "" if s.ty.place == "host" else "@" + s.ty.place
            line = f"{s.tag} {s.name}:{s.ty.value.display()}[{es[0]}]{place} = zeroed;"
        elif s.tag in {"let", "reg"}:
            declared = ":" + s.ty.display() if s.ty else ""
            line = f"{'let mut' if s.tag == 'reg' else 'let'} {s.name}{declared} = {es[0]};"
        elif s.tag == "compact":
            line = f"let {s.name} = compact {es[0]} for {s.binder} in {es[1]} where {es[2]} yield {es[3]};"
        elif s.tag == "reduce":
            declared = ":" + s.ty.display() if s.ty else ""
            line = f"let {s.name}{declared} = reduce {s.op} for {s.binder} in {es[0]} yield {es[1]};"
        elif s.tag == "assign":
            line = f"{es[0]} = {es[1]};"
        elif s.tag in {"break", "continue"}:
            line = s.tag + ";"
        elif s.tag == "return":
            line = "return" + (" " + es[0] if es else "") + ";"
        elif s.tag == "expr":
            line = es[0] + ";"
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
            line = f"parallel {s.name} in {es[0]} {nested(s.body)}"
        elif s.tag == "defer":
            line = "defer " + format_block(s.body, indent).split("\n", 2)[1].strip()
        elif s.tag in {"unsafe", "block"}:
            line = ("unsafe " if s.tag == "unsafe" else "") + nested(s.body)
        else:
            fail("E-PROJECTION", "Cannot project unknown statement kind.")
        lines.append(pad + line)
    lines.append("  " * indent + "}")
    return "\n".join(lines)


def type_declarations(p: Program) -> str:
    out = []
    for n, fs in p.records.items():
        marks = p.attributes.get(n, set())
        layout = "".join(" " + a for a in sorted(marks - {"linear"}))
        fields = " ".join(f"{k}:{t.display()};" for k, t in fs)
        out.append(
            f"{'linear ' * ('linear' in marks)}struct {local(n)}{generics(p.generics.get(n, []))}{layout} {{ {fields} }}"
        )
    for n, vs in p.enums.items():
        out.append(f"enum {local(n)} {{ {' '.join(v + ';' for v in vs)} }}")
    for n, vs in p.sums.items():
        variants = " ".join(v + (f"({t.display()})" if t else "") + ";" for v, t in vs)
        out.append(f"enum {local(n)}{generics(p.generics.get(n, []))} {{ {variants} }}")
    for n, members in p.traits.items():
        out.append(f"trait {local(n)} {{ {' '.join(signature(m) + ';' for m in members)} }}")
    out += [f"const {local(n)}:{t.display()} = {format_expr(e)};" for n, (t, e) in p.consts.items()]
    return "\n".join(out)


def local(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def function_source(f: Function) -> str:
    if f.extern:
        return signature(f) + ";"
    return signature(f) + " " + format_block(f.body, 1 if f.owner else 0)


def canonical_source(source: str) -> str:
    """An inspectable AST projection. Comments are not copied. Not an in-place edit."""
    p = Parser(source).parse()
    out = []
    for module in dict.fromkeys(p.modules.values()):
        tables = {k: {n: v for n, v in getattr(p, k).items() if p.modules.get(n, "") == module}
                  for k in ("records", "enums", "sums", "traits", "consts")}  # fmt: skip
        out += [f"module {module};"] if module else []
        for importer, target, alias in p.imports:
            names = [n for (m, n), full in p.uses.items() if m == module and full == f"{target}.{n}"]
            renamed = f" as {alias}" if alias != local(target) else ""
            out += (
                [f"import {target}{renamed}{' (' + ', '.join(names) + ')' if names else ''};"]
                if importer == module
                else []
            )
        declared = type_declarations(Program(**tables, generics=p.generics, attributes=p.attributes))
        out += [declared] if declared else []
        members: list[Function] = []
        for f in [*(f for f in p.functions if f.module == module), None]:
            if members and (f is None or f.owner != members[0].owner):  # Close the impl block in its place.
                shared = [g for g in members[0].generics if all(g in m.generics for m in members)]
                bodies = ["  " + function_source(replace(m, generics=m.generics[len(shared) :])) for m in members]
                trait, target = members[0].owner
                out.append(f"impl{generics(shared)} {trait} for {target.display()} {{\n" + "\n".join(bodies) + "\n}")
                members = []
            if f is not None and f.owner:
                members.append(f)
            elif f is not None:
                out.append(("pub " if f.public and module else "") + function_source(f))
    out += [f"family {pre} = {name}[{lo}..{hi}];" for pre, name, lo, hi in p.families]
    out += [f"derive wire for {name};" for name in p.derivations]
    return "\n\n".join(out) + "\n"


def semantic_ast(p: Program) -> Any:
    """Strip annotations/source locations, preserving all authored AST decisions."""

    def erase(v):
        if isinstance(v, dict):
            return {
                k: erase(x)
                for k, x in v.items()
                if k not in {"line", "col", "start", "end", "body_start", "cpp", "ty_inferred"}
            }
        if isinstance(v, list):
            return [erase(x) for x in v]
        if isinstance(v, tuple):
            return [erase(x) for x in v]
        return v

    return erase(asdict(p))


HINTS = {
    "E-MATCH-COVERAGE": "Handle the listed missing variants exactly once. Do not delete variants or weaken the task to silence coverage.",
    "E-MATCH-BINDING": "Bind one fresh immutable value only for an arm that has a declared payload.",
    "E-OWNER-EXTENT": "Bind a computed capacity to an immutable usize before declaring the buffer.",
    "E-STACK-LIMIT": "Reduce explicit stack storage, or request an authorized heap-allocation effect. Do not hide the cost.",
    "E-LOOP-CONTROL": "break and continue require an enclosing for or while loop.",
    "E-TYPE-MISMATCH": "Use the expected type. An explicit conversion may trap; do not change the function signature to hide a mismatch.",
    "E-UNBOUND": "Choose a name from the supplied lexical environment, or introduce a local before this use.",
    "E-CALLEE": "Only declared callables and listed primitives are legal. Request context for a dependency instead of inventing an API.",
    "E-IMMUTABLE": "Parameters and let bindings are immutable. Use a separate let mut local when mutation is required.",
    "E-WRITE-LEASE": "This location is not writable. Do not change ro to rw inside a repair; the host owns that contract.",
    "E-SHADOW": "Choose a fresh descriptive local name; this profile forbids shadowing.",
    "E-EFFECT-ORDER": "Bind a writing call at statement level before using its result.",
    "E-COLLECT-CAPACITY": "The collector extent must match the declared output capacity exactly in this profile.",
    "E-RETURN": "Write an explicit return on every required path. Rust-style implicit tail returns are not supported.",
    "E-PARSE": "Use braces, semicolons and the implemented grammar. This is not arbitrary Rust or Python.",
    "E-SESSION": "Refresh the packet from the host. Never guess a new session identifier.",
    "E-EFFECT-EXPANSION": "The candidate exceeds the host-owned effect ceiling. Change the implementation, not the contract.",
    "E-CONTEXT-CLOSURE": "The candidate calls an undisclosed dependency. Request a new packet including that symbol.",
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


class EditSession:
    def __init__(self, source: str, symbol: str, contract: dict[str, Any] | None = None, include: tuple[str, ...] = ()):
        self.source = source
        self.symbol = symbol
        self.contract = {} if contract is None else copy.deepcopy(contract)
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
        self.expanded, checker, _ = compile_program(source, capture_sites=True)
        effects = self.receipt["functions"][symbol]["effects"]
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
        # Include both call directions conservatively. This is a connected
        # component, not a claim of minimum sufficient semantic context.
        fs = self.receipt["functions"]
        selected = {symbol, *include}
        if selected - set(fs):
            fail("E-SYMBOL", "An included symbol is not in this module.")
        changed = True
        while changed:
            old = set(selected)
            for n, r in fs.items():
                if n in selected or set(r["calls"]) & selected:
                    selected.add(n)
                    selected.update(r["calls"])
            changed = old != selected
        self.visible = selected
        self.sites = {}
        for site in checker.sites:
            if site["symbol"] != symbol or site["end"] <= site["start"]:
                continue
            # Expressions inferred without an expected type are still checked
            # in their original syntactic context after every substitution.
            key = digest(stable_json([digest(source), symbol, site["start"], site["end"]]))
            self.sites[key] = {**site, "site": key, "source": source[site["start"] : site["end"]]}
        from pathlib import Path

        files = sorted(
            p
            for p in Path(__file__).parent.rglob("*")
            if p.suffix in {".py", ".hpp", ".cairn"} and "__pycache__" not in p.parts
        )
        self.implementation_hash = digest(b"".join(f.name.encode() + b"\0" + f.read_bytes() + b"\0" for f in files))
        self.session = digest(
            stable_json(
                {
                    "source": digest(source),
                    "symbol": symbol,
                    "contract": self.contract,
                    "visible": sorted(selected),
                    "implementation": self.implementation_hash,
                }
            )
        )

    def packet(self, site: str | None = None) -> dict[str, Any]:
        names = {f.name: f for f in self.expanded.functions}
        origins = {names[n].source_name for n in self.visible}
        context = []
        for f in self.parsed.functions:
            if f.name in origins or f.name in self.visible:
                context.append({"symbol": f.name, "source": self.source[f.start : f.end]})
        for prefix, base, lo, hi in self.parsed.families:
            if base in origins:
                context.append({"family": prefix, "source": f"family {prefix} = {base}[{lo}..{hi}];"})
        for record in self.parsed.derivations:
            if "derive wire for " + record in origins:
                context.append({"wire": record, "source": f"derive wire for {record};"})
        p = {
            "protocol": "cairn.packet/1",
            "session": self.session,
            "symbol": self.symbol,
            "signature": signature(self.f),
            "profile": VERSION,
            "task": self.contract.get("task", "No behavioral task contract supplied; do not infer one."),
            "contract_sha256": digest(stable_json(self.contract)),
            "source_sha256": digest(self.source),
            "implementation_sha256": self.implementation_hash,
            "allowed_effects": sorted(self.allowed_effects),
            "types": type_declarations(self.parsed),
            "context": context,
            "rule_cards": select_cards(
                "\n".join(x["source"] for x in context),
                any(t.mode != "value" for n in self.visible for _, t in names[n].params),
                bool(self.parsed.records or self.parsed.enums),
                bool(self.parsed.sums),
            ),
            "dependencies": {
                n: {"signature": signature(names[n]), "effects": self.receipt["functions"][n]["effects"]}
                for n in sorted(self.visible)
            },
            "draft_protocol": {"protocol": PROTOCOL, "session": self.session, "kind": "body", "replacement": "{ ... }"},
            "limits": {"replacement_bytes": MAX_REPLACEMENT, "one_authored_function": True},
            "scope": "Entire static call-graph component plus all record/enum/sum definitions; full module rechecked.",
            "boundaries": [
                "No permission to change parameters, imports, target flags, tests or task contract.",
                "Typed admission does not imply the requested behavior, termination, equivalence, or performance.",
                "No fresh-model success rate has been measured.",
            ],
        }
        if site is not None:
            if site not in self.sites:
                fail("E-SITE", "Unknown expression site.")
            focus = self.sites[site]
            p["focus"] = focus
            p["draft_protocol"] = {
                "protocol": PROTOCOL,
                "session": self.session,
                "kind": "expr",
                "site": site,
                "replacement": focus["source"],
            }
        return p

    def check(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
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
        text = request.get("replacement")
        if not isinstance(text, str) or len(text.encode()) > MAX_REPLACEMENT:
            fail("E-REQUEST", "Replacement must be bounded UTF-8 source.")
        if kind == "body":
            parser = Parser(text)
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
            key = request.get("site")
            if not isinstance(key, str) or key not in self.sites:
                fail("E-SITE", "Unknown or stale expression site.")
            parser = Parser(text)
            parser.expr()
            parser.need("<eof>")
            start, end = self.sites[key]["start"], self.sites[key]["end"]
            # Parenthesize substitution: operator binding at the insertion site
            # must not accidentally change the surrounding expression tree.
            text = "(" + text + ")"
        else:
            fail("E-REQUEST", "Expected body or expr edit kind.")
        candidate = self.source[:start] + text + self.source[end:]
        # Exact source surgery preserves every byte outside the authorized span.
        receipt = compile_source(candidate)[1]
        parsed = Parser(candidate).parse()
        f = next(f for f in parsed.functions if f.name == self.symbol)
        if signature(f) != signature(self.f):
            fail("E-SIGNATURE", "Function signature changed.")
        if set(receipt["functions"]) != set(self.receipt["functions"]):
            fail("E-DECLARATION", "Declaration set changed.")
        additions = set(receipt["functions"][self.symbol]["effects"]) - self.allowed_effects
        if additions:
            fail("E-EFFECT-EXPANSION", "Candidate exceeds its effect ceiling.", added_effects=sorted(additions))
        unknown = set(receipt["functions"][self.symbol]["calls"]) - self.visible
        if unknown:
            fail("E-CONTEXT-CLOSURE", "Candidate introduces an undisclosed callee.", symbols=sorted(unknown))
        for name, r in receipt["functions"].items():
            if name != self.symbol:
                delta = set(r["effects"]) - set(self.receipt["functions"][name]["effects"])
                if delta:
                    fail(
                        "E-CALLER-EFFECT",
                        "Candidate expands an unchanged caller footprint.",
                        symbol=name,
                        added_effects=sorted(delta),
                    )
        return candidate, {
            "protocol": "cairn.admission/1",
            "status": "typed",
            "session": self.session,
            "symbol": self.symbol,
            "candidate_sha256": digest(candidate),
            "effects": receipt["functions"][self.symbol]["effects"],
            "check_sites": receipt["functions"][self.symbol]["syntactic_check_sites"],
            "check_sites_before": self.receipt["functions"][self.symbol]["syntactic_check_sites"],
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

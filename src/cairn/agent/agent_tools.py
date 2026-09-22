"""Compiler-backed edit sessions, and the projections they show an editing agent.

A typed edit is not an equivalence proof or a task verdict. The host owns the original source and the task
contract, and no edit request can change either. Session digests and handles keep one local host consistent;
they are not authentication.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
from pathlib import Path
from typing import Any

from ..compiler.cairnc import VERSION, Diagnostic, Function, Parser, compile_program, compile_source, fail
from ..compiler.effects import EFFECT_FAMILIES, EFFECTS
from .diagnostics import explain, located
from .evidence import MAX_EXPAND, MAX_REPLACEMENT, TERMS, classes, establish
from .projection import (
    comment_above,
    declarations,
    derivation,
    function_source,
    local,
    related,
    signature,
    type_declarations,
)
from .state import delta, state, written
from .teaching import select_cards

PROTOCOL = "cairn.edit/1"  # A request bound by the session digest.
HANDLES = "cairn.edit/2"  # A request bound by a host handle; it may also ask to expand the context.
SCOPES = ("focused", "component")
NO_TASK = "No behavioral task contract supplied; do not infer one."


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_json_strict(text: str) -> Any:
    def pairs(items):
        if len(keys := [k for k, _ in items]) != len(set(keys)):
            fail("E-REQUEST", "Duplicate JSON field: " + next(k for k in keys if keys.count(k) > 1))
        return dict(items)

    try:
        return json.loads(
            text, object_pairs_hook=pairs, parse_constant=lambda _: fail("E-REQUEST", "Nonfinite JSON value")
        )
    except (json.JSONDecodeError, RecursionError) as e:
        fail("E-REQUEST", str(e))


def shaped(request: Any, protocol: str, keys: set[str]) -> None:
    """A request is an object of exactly `keys` under `protocol`; `kind` has already chosen the keys."""
    if not isinstance(request, dict):
        fail("E-REQUEST", "Edit request must be an object.")
    if set(request) != keys:
        fail("E-REQUEST", "Missing or unknown edit fields.", fields=sorted(set(request) ^ keys))
    if request["protocol"] != protocol:
        fail("E-REQUEST", "Unsupported edit protocol.")


@functools.cache
def implementation() -> str:
    """The digest of every compiler, runtime and library file of the package, read once per process: the compiler
    that judges an edit is the one that was loaded."""
    files = sorted(p for p in Path(__file__).parents[1].rglob("*")
                   if p.suffix in {".py", ".hpp", ".cairn"} and "__pycache__" not in p.parts)  # fmt: skip
    return digest(b"".join(f.name.encode() + b"\0" + f.read_bytes() + b"\0" for f in files))


class EditSession:
    """One authored function, the host's contract for it, and what an edit of it may see and call.

    `focused` (the default) discloses the target, its direct callees and callers by interface, and grows by
    `expand`; `component` discloses the whole connected call graph. Both recheck the whole linked module, and
    a candidate may call only what has been disclosed, so the focused boundary is never wider. The contract may
    ask for evidence about other functions (`contracts`, `references`, `tests`), which is established when the
    session opens and shown with each interface (`agent/evidence.py`).
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
        self.functions = {f.name: f for f in self.program.functions}
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
        requested = classes(self.contract, set(fs))
        if set(include) - set(fs):
            fail("E-SYMBOL", "An included or contracted symbol is not in this module.")
        self.evidence = establish(source, requested)
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
        self.implementation_hash = implementation()
        self.seal()

    def seal(self) -> None:
        """Bind the digest to everything an edit is judged against, including what it may call."""
        bound = {"source": digest(self.source), "symbol": self.symbol, "contract": self.contract,
                 "visible": sorted(self.visible), "implementation": self.implementation_hash}  # fmt: skip
        self.session = digest(stable_json(bound if self.scope == "component" else {**bound, "scope": self.scope}))

    def written(self, name: str) -> tuple[Function, Function | None]:
        """A function of the program, and the declaration this source wrote for it (a template for an instance),
        or None when a library, recipe or family wrote it."""
        f = self.functions[name]
        return f, next((g for g in self.parsed.functions if g.name in {f.source_name, name}), None)

    def row(self, name: str) -> dict[str, Any]:
        return {"signature": signature(self.functions[name]), "effects": self.receipt["functions"][name]["effects"]}

    def interface(self, name: str) -> dict[str, Any]:
        """What a caller may rely on: signature and effect row, the evidence class the session established with the
        host's contract behind it, and the comment written above the declaration, which nothing checks."""
        f, origin = self.written(name)
        text, start = (self.source, origin.start) if origin else (self.program.sources.get(f.module, ""), f.start)
        comment = comment_above(text, start)
        shown = self.evidence.get(name, {"evidence": "interface"})
        return {**self.row(name), **shown, **({"comment": comment[0]} if comment else {})}

    def cards(self, text: str, names: set[str], types: dict[str, str]) -> dict[str, str]:
        views = any(t.mode != "value" for n in names for _, t in self.functions[n].params)
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
        """What names this session and its contract. The target's signature is the first line of its source, and
        the profile and the meaning of a missing task are in the terms, so neither is repeated here."""
        return {
            "protocol": protocol,
            "session": self.session,
            "symbol": self.symbol,
            **({"task": self.contract["task"]} if "task" in self.contract else {}),
            "contract_sha256": digest(stable_json(self.contract)),
            "source_sha256": digest(self.source),
            "implementation_sha256": self.implementation_hash,
            "allowed_effects": sorted(self.allowed_effects),
        }

    def terms(self, dependencies: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """The terms this packet is read under: its own scope, the evidence classes it shows, and the constant rest."""
        shown = {d["evidence"] for d in dependencies.values() if "evidence" in d}
        shown |= {"comment"} if any("comment" in d for d in dependencies.values()) else set()
        evidence = {c: text for c, text in TERMS["evidence"].items() if c in shown}
        kept = ("limits", "boundaries", "refusal", "admission", "state")
        return {"profile": VERSION, "scope": TERMS["scopes"][self.scope], **{k: TERMS[k] for k in kept},
                **({"evidence": evidence} if evidence else {}), **({} if "task" in self.contract else {"task": TERMS["task"]})}  # fmt: skip

    def component(self) -> dict[str, Any]:
        origins = {self.functions[n].source_name for n in self.visible}
        context = [{"symbol": f.name, "source": self.source[f.start : f.end]}
                   for f in self.parsed.functions if f.name in origins or f.name in self.visible]  # fmt: skip
        context += [{"family": prefix, "source": f"family {prefix} = {base}[{lo}..{hi}];"}
                    for prefix, base, lo, hi in self.parsed.families if base in origins]  # fmt: skip
        for module, recipe, naturals, target, _ in self.parsed.derivations:
            full = f"{module}.{target}" if module and target and "." not in target else target
            if f"derive {recipe}" + (f" for {full}" if target else "") in origins:
                context.append({"wire" if recipe == "wire" else "derive": full,
                                "source": derivation(recipe, naturals, target)})  # fmt: skip
        dependencies = {n: {**self.row(n), **self.evidence.get(n, {})} for n in sorted(self.visible)}
        return {
            **self.header("cairn.packet/1"),
            "types": type_declarations(self.parsed),
            "context": context,
            "rule_cards": self.cards("\n".join(x["source"] for x in context), self.visible, {}),
            "dependencies": dependencies,
            "scope": "component",
            "terms": self.terms(dependencies),
        }

    def focused(self) -> dict[str, Any]:
        others = sorted(self.visible - {self.symbol})
        dependencies = {n: self.interface(n) for n in others}
        context = [{"symbol": self.symbol, "source": self.source[self.f.start : self.f.end]}, *self.expansions()]
        text = "\n".join([*(x["source"] for x in context), *(d["signature"] for d in dependencies.values())])
        types = related(declarations(self.program), text)
        written: dict[str, list[str]] = {}  # By module, each name as its module writes it; expand takes either form.
        for n in sorted(n for n in self.receipt["functions"] if n not in self.visible and not n.startswith("std.")):
            module, short = self.written_name(n)
            written.setdefault(module, []).append(short)
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
            "scope": "focused",
            "terms": self.terms(dependencies),
        }

    def source_of(self, name: str) -> str:
        """A function as it was written, or, for one a recipe or family generated, as the projection shows it."""
        f, origin = self.written(name)
        if origin is None:
            return function_source(f)
        family = [
            f"family {pre} = {base}[{lo}..{hi}];" for pre, base, lo, hi in self.parsed.families if base == origin.name
        ]
        return "\n".join([self.source[origin.start : origin.end], *family])

    def expansions(self) -> list[dict[str, str]]:
        return [{"symbol": n, "source": self.source_of(n)} for n in self.shown]

    def written_name(self, name: str) -> tuple[str, str]:
        """(module, the name as that module writes it): an impl method is Trait.Type.method, where the compiler's
        own name repeats the module three times."""
        return written(self.functions[name])

    @functools.cache  # noqa: B019 (a session lives as long as its host, and its program never changes)
    def aliases(self) -> dict[str, str]:
        """Each written name, bare and module-qualified, for the one function it names; an ambiguous one is left out."""
        seen: dict[str, set[str]] = {}
        for n in self.receipt["functions"]:
            module, short = self.written_name(n)
            for alias in {short, f"{module}.{short}" if module else short}:
                seen.setdefault(alias, set()).add(n)
        return {alias: next(iter(full)) for alias, full in seen.items() if len(full) == 1}

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
            called = name if name in self.receipt["functions"] else self.aliases().get(name)
            if len(full) == 1:
                types[full[0]] = table[full[0]]
            elif called and called != self.symbol:
                functions.append(called)
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

    def explain(self, candidate: str | None = None) -> dict[str, Any]:
        """`cairn explain` of the disclosed functions, in the original or in an admitted candidate."""
        from .explain import explain

        return explain(self.source if candidate is None else candidate, "program.cairn", set(self.visible))

    def predict(self, sizes: Any, candidate: str | None = None) -> dict[str, Any]:
        """`cairn predict` of the disclosed functions: the original priced, or what an admitted candidate changes."""
        from ..perf.report import delta, report

        if not isinstance(sizes, list) or not all(
            isinstance(s, dict) and all(isinstance(k, str) and isinstance(v, int | float) for k, v in s.items())
            for s in sizes
        ):
            fail("E-REQUEST", 'sizes is a list of objects from an extent\'s name to a number, such as [{"n": 1e6}].')
        given = [{k: float(v) for k, v in s.items()} for s in sizes]
        answer = report(self.source, given) if candidate is None else delta(self.source, candidate, given)
        answer["functions"] = {n: v for n, v in answer["functions"].items() if n in self.visible}
        return answer

    def check(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """An edit/1 request: the session digest binds it to this source, contract, context and compiler."""
        kind = request.get("kind") if isinstance(request, dict) else None
        shaped(
            request, PROTOCOL, {"protocol", "session", "kind", "replacement"} | ({"site"} if kind == "expr" else set())
        )
        if request["session"] != self.session:
            fail("E-SESSION", "Stale or mismatched source, policy, context, or toolchain.")
        return self.admit(kind, request["replacement"], request.get("site"))

    def admit(self, kind: Any, text: Any, site: Any = None) -> tuple[str, dict[str, Any]]:
        if not isinstance(text, str) or len(text.encode()) > MAX_REPLACEMENT:
            fail("E-REQUEST", "Replacement must be bounded UTF-8 source.")
        if kind not in {"body", "expr"}:
            fail("E-REQUEST", "Expected body or expr edit kind.")
        if kind == "expr" and (not isinstance(site, str) or site not in self.sites):
            fail("E-SITE", "Unknown or stale expression site.")
        reply = text
        try:
            parser = Parser(text)
            if kind == "body" and parser.eat("="):
                if self.f.ret.name == "void":
                    fail("E-EXPRESSION-BODY", "Expression bodies cannot return void.")
                parser.expr()
                parser.need(";")
            elif kind == "body":
                parser.block()
            else:
                parser.expr()
            parser.need("<eof>")
        except Diagnostic as e:
            raise located(e, text, 0, text) from None
        start, end = (
            (self.f.body_start, self.f.end) if kind == "body" else (self.sites[site]["start"], self.sites[site]["end"])
        )
        if kind == "expr":
            text = "(" + text + ")"  # Operator binding at the insertion site must not change the tree around it.
        candidate = self.source[:start] + text + self.source[end:]  # Every byte outside the span is preserved.
        try:
            receipt = compile_source(candidate)[1]
        except Diagnostic as e:  # Point into the reply the model wrote, not into the spliced whole.
            raise located(e, candidate, start + (kind == "expr"), reply) from None
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
# What every admission and every refusal says; a host that sent the terms says it there once.
ADMISSION_TERMS = {"protocol", "session", "candidate_sha256", "runtime_cost", "source_outside_edit_unchanged",
                   "contract_unchanged", "full_module_rechecked", "native_build", "behavioral_tests", "equivalence",
                   "formal_status"}  # fmt: skip
REFUSAL_TERMS = {"protocol", "trust", "automatic_edit", "acceptance_boundary"}
REQUESTS = {"body": {"replacement"}, "expr": {"site", "replacement"}, "expand": {"symbols"}, "explain": set(),
            "predict": {"sizes"},
            "state": set(), "delta": {"since"}}  # fmt: skip


class EditHost:
    """The sessions of one conversation with a model, named by short handles.

    The host keeps every digest, the pinned sources and each admitted candidate, so a request names a session
    as `e1` and a site as `x3` and never copies a hash. A handle resolves to the same session object that an
    edit/1 digest names, and admission runs the same checks. Each rule card and the terms are sent once per
    host; a later packet lists them under `sent_before`. A `state` request gives the session's program as it now
    stands (`agent/state.py`), and `delta` what changed since a state this host sent.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, EditSession] = {}
        self.sent: set[str] = set()
        self.admitted: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.states: dict[str, dict[str, Any]] = {}  # Every state this host sent, by digest, for a later delta.

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
        p["explain_protocol"] = {"protocol": HANDLES, "handle": handle, "kind": "explain"}  # Costs, after an edit too.
        p["state_protocol"] = {"protocol": HANDLES, "handle": handle, "kind": "state"}  # The program as it now stands.
        earlier = [n for n in p["rule_cards"] if n in self.sent]
        self.sent |= set(p["rule_cards"])
        p["rule_cards"] = {n: text for n, text in p["rule_cards"].items() if n not in earlier}
        fresh, repeated = self.unsent(p.pop("terms"))
        if fresh:
            p["terms"] = fresh
        if repeated:
            earlier.append("terms")
        if earlier:
            p["sent_before"] = earlier
        return p

    def unsent(self, terms: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The entries of `terms` this host has not sent, each evidence class on its own, and whether any it has.

        A packet's terms depend on its scope and on the evidence it shows, so a later packet carries only what is
        new to this host: the component scope after a focused packet, or an evidence class seen for the first time.
        """
        fresh: dict[str, Any] = {}
        repeated = False
        for key, value in terms.items():
            parts = value.items() if key == "evidence" else [(None, value)]
            for name, text in parts:
                mark = f"terms.{key}.{name}:{stable_json(text)}"
                if mark in self.sent:
                    repeated = True
                    continue
                self.sent.add(mark)
                if name is None:
                    fresh[key] = text
                else:
                    fresh.setdefault(key, {})[name] = text
        self.sent.add("terms")  # From here on, admissions and refusals leave out what the terms said.
        return fresh, repeated

    def session(self, handle: Any) -> EditSession:
        if not isinstance(handle, str) or handle not in self.sessions:
            fail("E-SESSION", "Unknown handle; the host opens sessions.")
        return self.sessions[handle]

    def respond(self, request: Any) -> dict[str, Any]:
        """An edit/2 request: `body`, `expr` (with a short site name), `expand` (with symbols), `explain`, `predict`
        (with sizes), `state` or `delta` (with the digest of a state this host sent)."""
        kind = request.get("kind") if isinstance(request, dict) else None
        if isinstance(request, dict) and not (isinstance(kind, str) and kind in REQUESTS):
            fail("E-REQUEST", "Expected body, expr, expand, explain, predict, state or delta.")
        shaped(request, HANDLES, {"protocol", "handle", "kind", *REQUESTS.get(kind, ())})
        s = self.session(request["handle"])
        admitted = self.admitted.get(request["handle"], [])
        if kind == "explain":  # The latest admitted candidate of this session, else the original.
            return s.explain(admitted[-1][0] if admitted else None)
        if kind == "predict":  # What the latest admitted candidate is predicted to change; nothing is built.
            return s.predict(request["sizes"], admitted[-1][0] if admitted else None)
        if kind in {"state", "delta"}:
            evidence = [{k: r[k] for k in ("symbol", "status", "effects", "check_sites")} for _, r in admitted]
            now = state(admitted[-1][0] if admitted else s.source, evidence)
            earlier = self.states.get(request["since"]) if kind == "delta" else None
            if kind == "delta" and earlier is None:
                fail("E-SESSION", "This host sent no state with that digest; ask for the state.")
            self.states[now["digest"]] = now
            return delta(earlier, now) if earlier else now
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
        said = ADMISSION_TERMS if "terms" in self.sent else {"session", "candidate_sha256"}
        told = {k: v for k, v in receipt.items() if k not in said}
        if told.get("check_sites_before") == told["check_sites"]:
            told.pop("check_sites_before")
        return told

    def reply(self, text: str) -> dict[str, Any]:
        """A model's raw reply, parsed strictly; a refusal comes back as the diagnostic it would read, without
        what the terms already said of every refusal."""
        request = None
        try:
            request = load_json_strict(text)
            return self.respond(request)
        except Diagnostic as e:
            handle = request.get("handle") if isinstance(request, dict) else None
            s = self.sessions.get(handle) if isinstance(handle, str) else None
            d = explain(e, s.source if s else "", tuple(s.visible) if s else ())
            return {k: v for k, v in d.items() if k not in REFUSAL_TERMS} if "terms" in self.sent else d

"""The implementation session: an agent writes a new implementation of a pinned reference, and the host validates it.

A body edit (`agent_tools.py`) rewrites a function inside its signature; a plan edit (`plans.py`) changes only a
schedule. This session is wider in what the agent writes and narrower in what it may touch. The host pins the
reference (its declaration's tokens, signature, row, ceiling and roundings), the tolerance on float results, the test
policy (how many cases, the seed, the shrinking budget, the time a call may take) and the permitted inputs, each by
digest. A submission is one implementation of that reference, new or replacing one of the same name, with any
helpers it needs; the host splices it in beside the reference, rechecks the whole program, and validates it against
the reference on boundary inputs (verify/validation.py). Only a validated implementation advances the source, and no
submission selects one: `plan f use g;` stays the host's decision.

A submission that tries to change the reference (E-REFERENCE), a tolerance (E-TOLERANCE), the test policy
(E-TEST-POLICY) or the permitted inputs (E-DOMAIN) is refused before anything is compiled. A failing implementation
is refused as E-VALIDATION with its shrunk input, which the host keeps as a regression. Every submission, admitted
or refused, is kept in the candidate history (agent/history.py) when the host names a history directory, and passed
to the host's `history` callback: a validation record for one that validated, a failure record with its stage and
why for one that did not.

    host = ImplementationHost()
    packet = host.open(source, "total", {"tolerance": {"absolute": 0, "relative": 0}, "domain": {"largest_extent": 64}})
    host.respond({"protocol": "cairn.implementation/1", "handle": "i1", "kind": "submit", "source": "fn ..."})
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, Parser, compile_source, fail
from ..compiler.lexing import lex
from ..verify.validation import FINITE, Policy, validate
from .agent_tools import digest, load_json_strict, stable_json
from .diagnostics import explain, located
from .projection import local, signature
from .teaching import select_cards

PROTOCOL = "cairn.implementation/1"
MAX_SOURCE = 64_000  # bytes of one submission
# A key a submission may not carry, and the code that says which pinned part it reached for.
REACHES = {
    "tolerance": "E-TOLERANCE", "tolerances": "E-TOLERANCE", "absolute": "E-TOLERANCE", "relative": "E-TOLERANCE",
    "tests": "E-TEST-POLICY", "test_policy": "E-TEST-POLICY", "budget": "E-TEST-POLICY", "seed": "E-TEST-POLICY",
    "cases": "E-TEST-POLICY", "probes": "E-TEST-POLICY", "seconds": "E-TEST-POLICY",
    "domain": "E-DOMAIN", "inputs": "E-DOMAIN", "assume": "E-DOMAIN", "precondition": "E-DOMAIN",
    "largest_extent": "E-DOMAIN", "reference": "E-REFERENCE", "policy": "E-TEST-POLICY",
}  # fmt: skip
OTHER = ("records", "enums", "sums", "traits", "consts", "plans", "selections", "derivations", "families", "recipes",
         "imports")  # fmt: skip
TESTS = ("budget", "seed", "probes", "seconds")  # the policy's test part; tolerance and domain are the others
CONTRACT = ("reference_sha256", "tolerance_sha256", "tests_sha256", "domain_sha256")
PINNED = ("the reference's declaration, signature, row, ceiling and roundings", "the tolerance on float results",
          "the test policy: cases, seed, shrinking and time", "the permitted inputs", "which implementation runs")  # fmt: skip


def pinned(declaration: str, policy: dict[str, Any]) -> dict[str, str]:
    """The digests of what a validation is held to: the reference's tokens, the tolerance, the tests and the inputs."""
    return {
        "reference_sha256": digest(" ".join(t.s for t in lex(declaration))),
        "tolerance_sha256": digest(stable_json(policy["tolerance"])),
        "tests_sha256": digest(stable_json({k: policy[k] for k in TESTS})),
        "domain_sha256": digest(stable_json(policy["domain"])),
    }


def remember(where: Path, base: str, reference: str, entry: dict[str, Any]) -> dict[str, Any]:
    """One submission or validation as a candidate-history record (agent/history.py): its identity is the reference
    as written (`base`, history.as_written), the implementation's own identity with everything it calls
    (`variant`, history.selectable), or the submission's digest when it has none, the pinned contract and the host,
    where validation ran. The candidate is named `plan f use g;`, as
    `cairn tune` names the same selection, so one implementation has one name in the history."""
    from ..perf.plan_source import selecting
    from . import history

    contract = {k: entry[k] for k in CONTRACT}
    variant = entry.get("variant") or entry.get("identity")
    who = history.identity(base, variant or entry["submission_sha256"], contract, "host")
    if entry["status"] == "validated":
        kind, detail = "validation", {"evidence": "finite-tested", "finite": entry["finite"], "smt": entry["smt"]}
    else:
        stage = "validation" if entry["code"] == "E-VALIDATION" else "check"
        detail = {"stage": stage, "why": f"{entry['code']}: {entry['why']}",
                  **({"inputs": entry["inputs"]} if entry.get("inputs") else {})}  # fmt: skip
        kind = "failure"
    named = selecting(reference, entry["implementation"]) if entry.get("implementation") else "submission"
    return history.record(where, kind, reference, named, who, detail, variant=variant)


class ImplementationSession:
    """One reference of one program, open to new implementations of it and to nothing else."""

    def __init__(self, source: str, reference: str, policy: dict[str, Any] | None = None,
                 regressions: Path | None = None, cxx: str = "clang++"):  # fmt: skip
        self.source, self.policy, self.regressions, self.cxx = source, Policy.of(policy), regressions, cxx
        self.parsed = Parser(source).parse()
        found = [f for f in self.parsed.functions if reference in (f.name, local(f.name)) and not f.implements]
        if len(found) != 1:
            fail("E-SYMBOL", f"No single function {reference} to implement.")
        self.f = found[0]
        self.reference = self.f.name
        self.receipt = compile_source(source)[1]["functions"]
        mine = self.receipt[self.reference]
        declaration = source[self.f.start : self.f.end]
        record = self.policy.record()
        self.pinned = {
            "tolerance": record["tolerance"],
            "tests": {k: record[k] for k in TESTS},
            "domain": record["domain"],
            **pinned(declaration, record),
        }
        self.reference_view = {
            "symbol": self.reference,
            "source": declaration,
            "effects": mine["effects"],
            "ceiling": list(self.f.effects) if self.f.effects is not None else "its own row: it declares none",
            **({"numerics": [{k: v for k, v in r.items() if k != "line"} for r in mine["numerics"]]}
               if mine.get("numerics") else {}),
        }  # fmt: skip
        self.digest = digest(stable_json([PROTOCOL, digest(source), self.reference, self.pinned]))

    def packet(self) -> dict[str, Any]:
        head = signature(self.f).removeprefix("fn " + local(self.reference))
        existing = self.receipt[self.reference].get("implementations", {})
        return {
            "protocol": PROTOCOL,
            "reference": self.reference_view,
            "implementations": {n: {k: r[k] for k in ("when", "identity")} for n, r in existing.items()},
            "pinned": self.pinned,
            "rule_cards": select_cards(self.reference_view["source"] + " implements", has_views=True),
            "reply": {
                "protocol": PROTOCOL,
                "kind": "submit",
                "source": f"fn NAME{head} implements {local(self.reference)} when CONDITION {{ ... }}",
            },
            "note": "Submit one implementation of the reference, and any helpers it calls; the host rechecks the "
            "program, validates it against the reference on boundary inputs, and decides whether it runs.",
            "boundaries": list(PINNED),
        }

    def submit(self, text: Any) -> tuple[str, dict[str, Any], str]:
        """Check one submission: the candidate program, its receipt and the implementation's name, or a refusal."""
        if not isinstance(text, str) or len(text.encode()) > MAX_SOURCE:
            fail("E-REQUEST", f"source is at most {MAX_SOURCE} bytes of CAIRN declarations.")
        try:
            parsed = Parser(text).parse()
        except Diagnostic as e:
            raise located(e, text, 0, text) from None
        if any(f.test for f in parsed.functions):
            fail("E-TEST-POLICY", "A submission holds no test block: the test policy is the host's.")
        if kinds := [k for k in OTHER if getattr(parsed, k)]:
            fail("E-DECLARATION", f"A submission declares functions only; it holds {', '.join(kinds)}.")
        ours = {local(f.name) for f in self.parsed.functions if not f.module or f.module == self.f.module}
        implementations = [f for f in parsed.functions if f.implements is not None]
        if local(self.reference) in {f.name for f in parsed.functions}:
            fail("E-REFERENCE", f"{self.reference} is pinned; submit an implementation of it, not a new definition.")
        if len(implementations) != 1:
            fail("E-DECLARATION", "A submission holds exactly one implementation, `fn g(...) implements "
                 f"{local(self.reference)} ...`, and any helpers it calls.")  # fmt: skip
        mine = implementations[0]
        if mine.implements is not None and mine.implements.reference != local(self.reference):
            fail("E-REFERENCE", f"This session implements {local(self.reference)}; {mine.name} implements "
                 f"{mine.implements.reference}.")  # fmt: skip
        table = self.receipt[self.reference].get("implementations", {})
        existing = set(table) | {r["instance_of"] for r in table.values() if "instance_of" in r}  # and templates
        full = f"{self.f.module}.{mine.name}" if self.f.module else mine.name
        if mine.name in ours and full not in existing:
            fail("E-DECLARATION", f"{mine.name} is already a function of the program, not an implementation of "
                 f"{local(self.reference)}; choose another name.")  # fmt: skip
        if clash := sorted(f.name for f in parsed.functions if f is not mine and f.name in ours):
            fail("E-DECLARATION", f"A helper may not replace a function of the program: {', '.join(clash)}.")
        base = self.source
        if full in existing:  # a changed implementation: the old declaration goes, the new one takes its place
            old = next(f for f in self.parsed.functions if f.name == full)
            base = base[: old.start] + base[old.end :]
        at = next(f for f in Parser(base).parse().functions if f.name == self.reference).end
        candidate = base[:at] + "\n\n" + text.strip("\n") + "\n" + base[at:]
        try:
            receipt = compile_source(candidate)[1]["functions"]
        except Diagnostic as e:
            raise located(e, candidate, at + 2, text.strip("\n")) from None
        added = {n for n in receipt if n not in self.receipt}
        for name, r in receipt.items():
            if name in added or name == full or name == self.reference:
                continue
            if name in self.receipt and (grown := sorted(set(r["effects"]) - set(self.receipt[name]["effects"]))):
                fail("E-CALLER-EFFECT", f"The implementation widens {name}'s row.", symbol=name, added_effects=grown)
        return candidate, receipt, full


class ImplementationHost:
    """The implementation sessions of one conversation, named by handles, and every submission they took."""

    def __init__(self, regressions: Path | None = None, history: Callable[[dict[str, Any]], None] | None = None,
                 cxx: str = "clang++", records: Path | None = None):  # fmt: skip
        self.sessions: dict[str, ImplementationSession] = {}
        self.regressions, self.history, self.cxx, self.records = regressions, history, cxx, records
        self.submissions: list[dict[str, Any]] = []
        self.bases: dict[str, str] = {}  # a session's source digest -> its reference as written (history.as_written)

    def open(self, source: str, reference: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        handle = f"i{len(self.sessions) + 1}"
        self.sessions[handle] = ImplementationSession(source, reference, policy, self.regressions, self.cxx)
        return {**self.sessions[handle].packet(), "handle": handle,
                "reply": {**self.sessions[handle].packet()["reply"], "handle": handle}}  # fmt: skip

    def source(self, handle: str) -> str:
        return self.session(handle).source

    def session(self, handle: Any) -> ImplementationSession:
        if not isinstance(handle, str) or handle not in self.sessions:
            fail("E-SESSION", "Unknown handle; the host opens implementation sessions.")
        return self.sessions[handle]

    def respond(self, request: Any) -> dict[str, Any]:
        if isinstance(request, str):
            request = load_json_strict(request)
        if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
            fail("E-REQUEST", f"An implementation reply is a {PROTOCOL} object.")
        s = self.session(request.get("handle"))
        entry: dict[str, Any] = {"protocol": PROTOCOL, "reference": s.reference, "source_sha256": digest(s.source),
                                 "submission_sha256": digest(stable_json(request)),
                                 **{k: v for k, v in s.pinned.items() if k.endswith("_sha256")}}  # fmt: skip
        try:
            for key in sorted(set(request) - {"protocol", "handle", "kind", "source"}):
                fail(REACHES.get(key, "E-REQUEST"), f"{key} is the host's: a submission carries protocol, handle, "
                     "kind and source, and nothing it carries changes the reference, the tolerance, the test policy "
                     "or the permitted inputs.", field=key)  # fmt: skip
            if set(request) != {"protocol", "handle", "kind", "source"} or request["kind"] != "submit":
                fail("E-REQUEST", "A submission is {protocol, handle, kind: submit, source}.")
            candidate, receipt, name = s.submit(request["source"])
        except Diagnostic as e:
            self.log(s, {**entry, "status": "refused", "code": e.data["code"], "why": e.data["message"]})
            raise
        table = receipt[s.reference]["implementations"]
        instances = [n for n, r in table.items() if r.get("instance_of") == name]  # each value its own validation
        found = {one: self.validated(s, entry, candidate, receipt, one) for one in instances or [name]}
        successor = ImplementationSession(candidate, s.reference, s.policy.record(), s.regressions, self.cxx)
        self.sessions[request["handle"]] = successor
        done = {"protocol": PROTOCOL, "status": "validated", "implementation": name}
        tail = {"claim": FINITE, "candidate_sha256": digest(candidate), "selected": False}
        if not instances:
            return {**done, **found[name], **tail}
        return {**done, "instances": found, **tail}

    def validated(self, s: ImplementationSession, entry: dict[str, Any], candidate: str, receipt: dict[str, Any],
                  name: str) -> dict[str, Any]:  # fmt: skip
        """Validate the implementation `name`, or one instance of a parameterized one, against the reference under
        the pinned policy, and keep the result; what the answer says of it, or E-VALIDATION."""
        record = validate(candidate, s.reference, name, s.policy, self.cxx, s.regressions)
        info = receipt[s.reference]["implementations"][name]
        entry = {**entry, "implementation": name, "identity": info["identity"], "finite": record.get(
            "finite", {}).get("status", record["status"]), "smt": record.get("smt", {}).get("status")}  # fmt: skip
        if self.records is not None:  # kept under what the implementation calls too, so an edited helper is stale
            from . import history

            entry["variant"] = history.selectable(candidate, {name: info})[name]["identity"]
        if record["status"] != "passed":
            finite = record.get("finite", {})
            self.log(s, {**entry, "status": "refused", "code": "E-VALIDATION", "why": finite.get("status", "unknown"),
                         "inputs": finite.get("failed", {}).get("inputs")})  # fmt: skip
            fail("E-VALIDATION", f"{name} is not validated: {finite.get('status', record['status'])} against "
                 f"{s.reference}.", finite=finite or {"reason": record.get("reason")}, smt=record.get("smt"),
                 identity=info["identity"], implementation=name)  # fmt: skip
        self.log(s, {**entry, "status": "validated"})
        finite = record["finite"]
        return {
            "identity": info["identity"],
            "when": info["when"],
            "effects": receipt[name]["effects"],
            "requires": info["requires"],
            "finite": {k: finite[k] for k in ("status", "cases", "implementation_ran", "kept_cases")},
            "smt": record["smt"],
            "select_with": f"plan {local(s.reference)} use {local(name)};",
        }

    def log(self, s: ImplementationSession, entry: dict[str, Any]) -> None:
        self.submissions.append(entry)
        if self.records is not None:
            self.remember(s, entry)
        if self.history is not None:
            self.history(entry)

    def remember(self, s: ImplementationSession, entry: dict[str, Any]) -> dict[str, Any]:
        from . import history

        key = digest(s.source)
        if key not in self.bases:
            self.bases[key] = history.as_written(s.source, s.reference)
        assert self.records is not None
        return remember(self.records, self.bases[key], s.reference, entry)

    def reply(self, text: str) -> dict[str, Any]:
        """A model's raw reply: the result, or the refusal it would read."""
        request = None
        try:
            request = load_json_strict(text)
            return self.respond(request)
        except Diagnostic as e:
            handle = request.get("handle") if isinstance(request, dict) else None
            s = self.sessions.get(handle) if isinstance(handle, str) else None
            return explain(e, s.source if s else "")

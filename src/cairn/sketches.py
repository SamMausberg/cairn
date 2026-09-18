"""Host-bound named expression sketches and finite counterexample-guided search.

Ordinary Python code can construct/search candidates without inventing CAIRN
syntax or editing hashes. Python itself is not a sandbox: execute only trusted
host scripts. Models receive a data-only choice map, not authority to alter the
host task, original source, slot map, reference, or compiler configuration.
"""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass
from typing import Any

from .agent_tools import PROTOCOL, EditSession, digest, explain, load_json_strict, stable_json
from .cairnc import IDENT, RESERVED, Diagnostic, Parser, fail
from .scalar_semantics import Concrete, Unsupported, equivalent, outcome_key, prepared

MAX_HOLES = 16
MAX_CHOICES = 4096


@dataclass(frozen=True)
class ScalarContract:
    reference: str
    symbol: str
    assume: str = "true"
    allow_reference_traps: bool = False


@dataclass
class Candidate:
    source: str
    receipt: dict[str, Any]
    choices: dict[str, str]


class Sketch:
    """A fixed function, fixed policy, and a few nonoverlapping expression slots."""

    def __init__(
        self,
        source: str,
        symbol: str,
        *,
        task: dict | None = None,
        semantic: ScalarContract | None = None,
        include: tuple[str, ...] = (),
    ):
        self._session = EditSession(source, symbol, task, include)
        if semantic is not None and semantic.symbol != symbol:
            fail("E-SKETCH-CONTRACT", "Semantic contract names a different target.")
        self._semantic = semantic
        self._holes = {}
        self._sealed = False
        self._identity = None

    @property
    def symbol(self):
        return self._session.symbol

    @property
    def source(self):
        return self._session.source

    @property
    def semantic(self):
        return self._semantic

    @property
    def holes(self):
        return copy.deepcopy(self._holes)

    def hole(self, name: str, original: str, *, occurrence: int | None = None) -> Sketch:
        if self._sealed:
            fail("E-SKETCH-SEALED", "Create a new sketch before changing its slot map.")
        if not isinstance(name, str) or not IDENT.fullmatch(name) or name in RESERVED:
            fail("E-SKETCH-NAME", "Slot names must be descriptive identifiers.")
        if name in self._holes or len(self._holes) >= MAX_HOLES:
            fail("E-SKETCH-NAME", "Slot name duplicates a slot or exceeds the 16-slot budget.")
        found = sorted(
            (s for s in self._session.sites.values() if s["source"] == original), key=lambda x: (x["start"], x["end"])
        )
        if occurrence is None:
            if len(found) != 1:
                fail(
                    "E-SKETCH-SITE",
                    "Slot source must identify exactly one expression; select an occurrence explicitly.",
                    matches=len(found),
                )
            selected = found[0]
        else:
            if type(occurrence) is not int or not 0 <= occurrence < len(found):
                fail("E-SKETCH-SITE", "Occurrence is outside the exact source matches.")
            selected = found[occurrence]
        for h in self._holes.values():
            if max(h["start"], selected["start"]) < min(h["end"], selected["end"]):
                fail("E-SKETCH-OVERLAP", "Nested/overlapping slots cannot be changed independently.")
        self._holes[name] = copy.deepcopy(selected)
        return self

    def seal(self):
        if not self._holes:
            fail("E-SKETCH-EMPTY", "Declare at least one expression slot.")
        if self._identity is None:
            self._identity = digest(
                stable_json(
                    {
                        "session": self._session.session,
                        "holes": self._holes,
                        "semantic": None if self._semantic is None else self._semantic.__dict__,
                    }
                )
            )
        self._sealed = True
        return self._identity

    def _fresh(self):
        # Host state is private by convention, not Python security. Recompute the
        # complete session binding so accidental task/compiler changes are stale.
        fresh = EditSession(self.source, self.symbol, self._session.contract, tuple(self._session.visible))
        if fresh.session != self._session.session:
            fail("E-SESSION", "Source, host contract, context or compiler changed; create a new sketch.")
        check = digest(
            stable_json(
                {
                    "session": self._session.session,
                    "holes": self._holes,
                    "semantic": None if self._semantic is None else self._semantic.__dict__,
                }
            )
        )
        if check != self._identity:
            fail("E-SESSION", "Sealed sketch content changed.")

    def fill(self, **choices: str) -> Candidate:
        self.seal()
        self._fresh()
        if set(choices) != set(self._holes):
            fail(
                "E-SKETCH-CHOICES",
                "Supply each declared slot exactly once, with no extra fields.",
                missing=sorted(set(self._holes) - set(choices)),
                extra=sorted(set(choices) - set(self._holes)),
            )
        total = 0
        for text in choices.values():
            if not isinstance(text, str):
                fail("E-SKETCH-CHOICES", "Every slot value must be an expression string.")
            total += len(text.encode())
            if total > 64000:
                fail("E-SKETCH-CHOICES", "Combined choices exceed 64000 bytes.")
            parser = Parser(text)
            parser.expr()
            parser.need("<eof>")
        f = self._session.f
        body = self.source[f.body_start : f.end]
        for name, h in sorted(self._holes.items(), key=lambda kv: kv[1]["start"], reverse=True):
            a, b = h["start"] - f.body_start, h["end"] - f.body_start
            body = body[:a] + "(" + choices[name] + ")" + body[b:]
        text, receipt = self._session.check(
            {"protocol": PROTOCOL, "session": self._session.session, "kind": "body", "replacement": body}
        )
        receipt = {
            **receipt,
            "sketch_sha256": self._identity,
            "slots": list(choices),
            "only_declared_expressions_changed": True,
            "semantic_status": "not-run",
        }
        return Candidate(text, receipt, dict(choices))

    def fill_json(self, text: str) -> Candidate:
        """Strict data-only adapter boundary; never evaluates model Python."""
        if not isinstance(text, str) or len(text.encode()) > 128000:
            fail("E-SKETCH-CHOICES", "Reply must be a JSON object within the 128000-byte transport budget.")
        choices = load_json_strict(text)
        if not isinstance(choices, dict):
            fail("E-SKETCH-CHOICES", "Reply must be a slot-to-expression JSON object.")
        return self.fill(**choices)

    def packet(self) -> dict:
        """Full visible semantic content, without duplicate source/signatures.

        Hashes stay in the host receipt. A data-only reply is bound by the host
        Sketch instance, not accepted as a standalone unpinned editing request.
        This packet still charges all source context and selected rule cards.
        """
        self.seal()
        old = self._session.packet()
        source = "\n\n".join(([old["types"]] if old["types"] else []) + [x["source"] for x in old["context"]])
        slots = {
            n: {"original": h["source"], "expected_type": h["expected_type"] or h["type"], "bindings": h["bindings"]}
            for n, h in self._holes.items()
        }
        return {
            "protocol": "cairn.choices/1",
            "task": old["task"],
            "source": source,
            "slots": slots,
            "allowed_effects": old["allowed_effects"],
            "rule_cards": old["rule_cards"],
            "semantic_contract": None
            if self._semantic is None
            else {
                "reference_source": self._semantic.reference,
                "symbol": self._semantic.symbol,
                "assume": self._semantic.assume,
                "allow_reference_traps": self._semantic.allow_reference_traps,
                "runtime_precondition_guard": "not-inserted-by-builder",
            },
            "reply": {n: h["source"] for n, h in self._holes.items()},
            "instructions": "Return only the JSON slot-to-expression map. Do not change the task, signature, policies or source outside these slots.",
            "acceptance": "Host checks the full module. Types do not establish behavior. "
            + (
                "The host also checks a fixed scalar reference."
                if self._semantic
                else "No semantic reference is installed."
            ),
            "identity_binding": "The host binds this reply to its sealed Sketch instance; standalone choice JSON is not an authorized edit.",
        }

    def check_semantics(self, candidate: Candidate, *, query_log: list | None = None, timeout_ms: int = 3000) -> dict:
        self.seal()
        self._fresh()
        if candidate.receipt.get("sketch_sha256") != self._identity or digest(
            candidate.source
        ) != candidate.receipt.get("candidate_sha256"):
            fail("E-SESSION", "Candidate is not the artifact emitted by this sketch.")
        if self._semantic is None:
            return {"status": "not-run", "reason": "No host-owned semantic reference was supplied."}
        c = self._semantic
        return equivalent(
            c.reference,
            candidate.source,
            c.symbol,
            assume=c.assume,
            allow_reference_traps=c.allow_reference_traps,
            timeout_ms=timeout_ms,
            query_log=query_log,
        )


def public_feedback(result: dict) -> dict:
    """A concise semantic witness; excludes repeated reference source and raw SMT."""
    keys = {"status", "reason", "counterexample", "expected", "actual", "concrete_replay", "unsupported_profile"}
    answer = {k: v for k, v in result.items() if k in keys}
    answer["boundary"] = "Scalar SMT model only; solver/translator trusted; no native, Lean, or performance proof."
    return answer


def solve_finite(
    sketch: Sketch,
    choices: dict[str, list[str]],
    *,
    limit: int = MAX_CHOICES,
    timeout_ms: int = 3000,
    use_counterexample_cache: bool = True,
) -> dict:
    """Search an explicit finite, ordered grammar. This is NOT a model trial.

    Replayed counterexamples only reject; final acceptance always makes a fresh
    full-width solver query. No tests or domains are changed during search.
    """
    sketch.seal()
    if sketch.semantic is None:
        fail("E-SKETCH-CONTRACT", "Finite semantic search requires a fixed reference.")
    if set(choices) != set(sketch.holes):
        fail("E-SKETCH-CHOICES", "Search dimensions must equal the declared slots.")
    if type(limit) is not int or not 1 <= limit <= MAX_CHOICES:
        fail("E-SKETCH-BUDGET", "Search limit must be 1..4096.")
    total = 1
    for xs in choices.values():
        if not isinstance(xs, list) or not xs or not all(isinstance(x, str) for x in xs):
            fail("E-SKETCH-CHOICES", "Each search dimension needs a nonempty list of expression strings.")
        total *= len(xs)
    if total > limit:
        fail("E-SKETCH-BUDGET", "Candidate product exceeds the explicit search budget.", candidates=total)
    contract = sketch.semantic
    reference = Concrete(prepared(contract.reference))
    witnesses = []
    attempts = []
    solver_calls = 0
    smt_queries = 0
    cache_rejections = 0
    for combination in itertools.product(*choices.values()):
        proposal = dict(zip(choices, combination, strict=True))
        try:
            candidate = sketch.fill(**proposal)
        except Diagnostic as e:
            attempts.append({"choices": proposal, "stage": "frontend", "result": explain(e)})
            continue
        cached = None
        if use_counterexample_cache:
            try:
                concrete = Concrete(prepared(candidate.source))
                for inputs in witnesses:
                    expected = reference.outcome(contract.symbol, inputs)
                    actual = concrete.outcome(contract.symbol, inputs)
                    if outcome_key(expected) != outcome_key(actual):
                        cached = {
                            "status": "counterexample",
                            "counterexample": inputs,
                            "expected": expected,
                            "actual": actual,
                            "concrete_replay": True,
                        }
                        break
            except Unsupported:
                pass  # An unsupported replay never becomes acceptance.
        if cached:
            cache_rejections += 1
            attempts.append({"choices": proposal, "stage": "cached-counterexample", "result": public_feedback(cached)})
            continue
        solver_calls += 1
        result = sketch.check_semantics(candidate, timeout_ms=timeout_ms)
        smt_queries += len(result.get("queries", []))
        attempts.append(
            {
                "choices": proposal,
                "stage": "solver",
                "result": public_feedback(result),
                "receipt": {k: v for k, v in result.items() if k not in {"queries"}},
            }
        )
        if result["status"] == "counterexample" and result["counterexample"] not in witnesses:
            witnesses.append(result["counterexample"])
        if result["status"] == "smt-equivalent":
            return {
                "status": "smt-equivalent",
                "mode": "deterministic-finite-search-not-model",
                "candidate": candidate.source,
                "choices": proposal,
                "attempts": attempts,
                "candidate_product": total,
                "solver_calls": solver_calls,
                "smt_queries": smt_queries,
                "cache_rejections": cache_rejections,
                "counterexamples": witnesses,
                "semantic_receipt": result,
            }
    return {
        "status": "no-certified-candidate",
        "mode": "deterministic-finite-search-not-model",
        "attempts": attempts,
        "candidate_product": total,
        "solver_calls": solver_calls,
        "cache_rejections": cache_rejections,
        "counterexamples": witnesses,
    }

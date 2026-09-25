"""Whole-declaration coverage for restricted value equivalence.

A module receipt must not inherit a selected function's successful result. This
checks every entry and fails closed for missing, extra, unsupported or unknown
entries. It proves neither the reference's intent nor the C++ backend.

A caller may restrict one entry's inputs with a precondition, which is how a
pass over a symbolic extent gets a trip count the model can bound. A restricted
entry is equivalent only where its precondition holds, so the receipt carries
every precondition text and names them in the domain it quantifies over.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, compile_source
from ..compiler.syntax.parser import Parser
from .scalar.semantics import equivalent


def verify_module(reference: str, candidate: str, timeout_ms: int = 3000,
                  preconditions: dict[str, str] | None = None) -> dict:  # fmt: skip
    result: dict[str, Any] = {
        "protocol": "cairn.verification-coverage/1",
        "status": "incomplete",
        "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "domain": "all values the entry guards admit of the declared parameter types; reference must be total",
        "scope": "all declared functions in the restricted value source model",
        "preconditions": {},
        "native_proof": False,
        "lean_proof": False,
        "results": {},
        "coverage_checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 30000:
        return {**result, "reason": "Per-query timeout must be 1..30000 milliseconds."}
    given = dict(preconditions or {})
    if not all(isinstance(x, str) for x in [*given, *given.values()]):
        return {**result, "reason": "A precondition maps a function name to a source expression, text to text."}
    result["preconditions"] = dict(sorted(given.items()))
    if given:  # An entry holds only where its precondition does, so the quantification says which and what.
        named = "; ".join(f"{n} only where {t}" for n, t in sorted(given.items()))
        result["domain"] += f"; restricted by a host precondition where one is given ({named})"
    if len(reference.encode()) > 64000 or len(candidate.encode()) > 64000:
        return {**result, "reason": "Whole-module scalar source limit is 64000 bytes."}
    try:
        _, ref = compile_source(reference)
        _, cand = compile_source(candidate)
    except Diagnostic as e:
        return {**result, "status": "rejected", "diagnostic": e.data}
    a = Parser(reference).parse()
    b = Parser(candidate).parse()
    result["public_types_match"] = (a.records, a.enums, a.sums) == (b.records, b.enums, b.sums)
    result["reference_intent_proved"] = False
    names = {n for n, row in ref["functions"].items() if not row.get("test")}  # a test is run, never compared
    present = {n for n, row in cand["functions"].items() if not row.get("test")}
    result.update(expected=sorted(names), missing=sorted(names - present), extra=sorted(present - names))
    if not names or len(names | present) > 128:
        return {**result, "reason": "Coverage requires 1..128 declared functions."}
    stray = sorted(set(given) - names)
    if stray:  # A precondition on a name the reference does not declare is a typo, not a silent no-op.
        return {**result, "reason": f"A precondition names no declared function: {', '.join(stray)}."}
    deadline = time.monotonic() + 30
    for name in sorted(names & present):
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining < 4:
            result["results"][name] = {"status": "unknown", "reason": "Whole-module solver budget exhausted."}
        else:
            # A scalar comparison has several obligations; reserve its share for all.
            allowance = min(timeout_ms, max(1, remaining // 4))
            assume = given.get(name, "true")  # A function with none is compared over the whole domain.
            result["results"][name] = equivalent(reference, candidate, name, assume=assume, timeout_ms=allowance)
    result["covered"] = [name for name, r in result["results"].items() if r["status"] == "smt-equivalent"]
    result["uncovered"] = sorted((names | present) - set(result["covered"]))
    if result["public_types_match"] and names == present and len(result["covered"]) == len(names):
        result["status"] = "smt-module-equivalent"
    return result

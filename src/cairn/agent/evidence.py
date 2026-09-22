"""What a packet may say is established about a function, and the terms every packet is read under.

A dependency is shown with one evidence class. `interface`: the compiler established its signature, effect row,
types and extents, and nothing about what it computes. `declared`: the host gave a text that nothing checked.
`finite-tested`: the host's cases ran natively in this session and passed. `smt-equivalent`: Z3 found no input
of the stated domain on which the function differs from the host's reference, so the reference stands in for
the body. A check that does not pass leaves the host's text `declared`, with the status it reached, because
unknown is never success. The terms are constant, so a host sends them once.
"""

from __future__ import annotations

from typing import Any

from ..compiler.cairnc import fail

MAX_CASES_SHOWN = 8
MAX_REPLACEMENT = 64_000  # bytes of one reply
MAX_EXPAND = 32  # names in one expand request
TERMS = {
    "scopes": {
        "focused": "The target's source; the signature, effect row and evidence of everything it may call and "
        "everything that calls it; the types those name. Expand any further function or type by name.",
        "component": "The whole static call-graph component of the target and every type declaration.",
    },
    "limits": {"replacement_bytes": MAX_REPLACEMENT, "one_authored_function": True, "expand": MAX_EXPAND},
    "boundaries": [
        "No permission to change parameters, imports, target flags, tests or the task contract.",
        "The whole linked module is rechecked for every reply, beyond what the packet shows.",
        "Typed admission does not imply the requested behaviour, termination, equivalence or performance.",
        "No fresh-model success rate has been measured.",
    ],
    "evidence": {
        "interface": "The compiler established the signature and effect row, nothing about what it computes: "
        "expand the body before relying on its behaviour.",
        "declared": "The host's text, which nothing checked: expand the body before relying on it.",
        "finite-tested": "The host's cases passed natively in this session; inputs outside them are unchecked.",
        "smt-equivalent": "Z3 found no input of the domain on which it differs from the reference shown: rely on "
        "the reference, as the whole program was checked against it.",
        "comment": "Written above the declaration by its author; nothing checks it.",
    },
    "task": "A packet without a task has none; infer none.",
    "refusal": "Frontend only, not a semantic or machine proof; nothing is applied automatically.",
    "admission": "typed means only that the whole linked module checks with the reply spliced in, the text "
    "outside it and the contract unchanged. Nothing was built, tested, proved equivalent or measured, and check "
    "sites are static counts, not costs.",
}


def classes(contract: dict[str, Any], functions: set[str]) -> dict[str, dict[str, Any]]:
    """The host's evidence requests, validated: `contracts` (text), `references` (SMT) and `tests` (finite)."""
    texts, references, tests = (contract.get(k, {}) for k in ("contracts", "references", "tests"))
    if not isinstance(texts, dict) or not all(isinstance(v, str) for v in texts.values()):
        fail("E-CONTRACT", "contracts maps a function name to the host's text for it.")
    if not isinstance(references, dict) or not all(
        isinstance(v, dict) and isinstance(v.get("reference"), str) and set(v) <= {"reference", "assume"}
        and isinstance(v.get("assume", "true"), str) for v in references.values()
    ):  # fmt: skip
        fail("E-CONTRACT", "references maps a function name to {reference: source, assume: precondition}.")
    if not isinstance(tests, dict) or not all(isinstance(v, dict) for v in tests.values()):
        fail("E-CONTRACT", "tests maps a function name to a cairn.task/1 contract.")
    named = set(texts) | set(references) | set(tests)
    if named - functions:
        fail("E-SYMBOL", "An included or contracted symbol is not in this module.")
    if clash := sorted((set(texts) & set(references)) | (set(texts) & set(tests)) | (set(references) & set(tests))):
        fail("E-CONTRACT", "Give one kind of evidence per function.", symbols=clash)
    if mismatch := sorted(n for n, t in tests.items() if t.get("symbol", n) != n):
        fail("E-CONTRACT", "A task contract names another function.", symbols=mismatch)
    shown: dict[str, dict[str, Any]] = {n: {"evidence": "declared", "contract": t} for n, t in texts.items()}
    shown |= {n: {"kind": "smt", **r} for n, r in references.items()}
    shown |= {n: {"kind": "tests", "task": {**t, "symbol": n}} for n, t in tests.items()}
    return shown


def establish(source: str, requested: dict[str, dict[str, Any]], cxx: str = "clang++") -> dict[str, dict[str, Any]]:
    """Run each check the host asked for, now, against this exact source, and say what it established."""
    out: dict[str, dict[str, Any]] = {}
    for name, want in requested.items():
        if want.get("kind") == "smt":
            from ..verify.scalar_semantics import equivalent

            result = equivalent(want["reference"], source, name, assume=want.get("assume", "true"))
            status = result["status"]
            contract = {"reference": want["reference"], **({"assume": want["assume"]} if "assume" in want else {})}
            ok = status == "smt-equivalent"
            out[name] = {"evidence": "smt-equivalent" if ok else "declared", "contract": contract,
                         **({} if ok else {"check": status})}  # fmt: skip
        elif want.get("kind") == "tests":
            from ..verify.testing import evaluate

            result = evaluate(source, want["task"], cxx)
            cases = want["task"].get("cases", [])
            ok = result["status"] == "passed-finite-tests"
            shown = {"cases": len(cases), "shown": cases[:MAX_CASES_SHOWN]} if isinstance(cases, list) else {}
            out[name] = {"evidence": "finite-tested" if ok else "declared", "contract": shown,
                         **({} if ok else {"check": result["status"]})}  # fmt: skip
        else:
            out[name] = want
    return out

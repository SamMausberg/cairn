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
        "focused": "the target, and the interfaces of what it calls and what calls it; expand anything else by name",
        "component": "the target's whole call-graph component and every type",
    },
    "limits": {"replacement_bytes": MAX_REPLACEMENT, "one_authored_function": True, "expand": MAX_EXPAND},
    "boundaries": [
        "Never change parameters, imports, target flags, tests or the task.",
        "Every reply is rechecked with the whole linked module, beyond what the packet shows.",
        "Typed is not correct, terminating, equivalent or fast. No fresh-model success rate has been measured.",
    ],
    "evidence": {
        "interface": "only the signature and effect row are checked: read the body before relying on behaviour",
        "declared": "the host's text, unchecked: read the body before relying on it",
        "finite-tested": "the host's cases passed natively now; inputs outside them are unchecked",
        "smt-equivalent": "Z3 found it equal to the reference shown on the domain: rely on the reference",
        "comment": "the author's, unchecked",
    },
    "task": "no task: none was given; infer none",
    "state": "kind state is the program now, every function's [signature, row] by module, under a digest; kind "
    "delta with since: a digest is only what changed",
    "refusal": "frontend only, not a proof; nothing is applied automatically",
    "admission": "typed: the linked module checks with the reply in place and nothing outside it changed; nothing "
    "was built, tested, proved or measured, and check sites are static counts",
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

"""What Z3 says of an implementation against its reference, and its counterexample run through the finite path.

`smt` asks, as `cairn verify` would, whether the reference's body and the implementation's agree where the
implementation's condition holds, apart from any test. Z3 compares exactly, while validation holds float results to
the numerical policy (verify/agreement.py), and Z3 does not know the domain the host admits. So a counterexample is
evidence to replay, not a verdict: `replayed` runs its inputs through the same finite path as every generated case,
under the policy's domain and tolerance.

A witness outside the admitted domain, or one whose native results agree under the policy, leaves the finite result
standing, and the record says which. A witness whose native results break the policy fails the validation: it is
shrunk and kept in the regressions file like any failing case. A replay that does not finish, or on which the
implementation's condition does not hold natively, is `unknown`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..compiler.cairnc import Function, Parser
from .boundaries import Case, Param
from .isolated_calls import Calls


def smt(base: str, reference: str, implementation: str, impl: Function, policy: Any,
        timeout_ms: int) -> dict[str, Any]:  # fmt: skip
    """What Z3 says of the implementation against the reference where its condition holds, apart from any test:
    the reference's body replaced by the implementation's, compared in the modeled fragment."""
    from ..agent.projection import format_expr
    from ..compiler.plans.implementations import fixed
    from .diff import isolated
    from .scalar_semantics import equivalent
    from .scalar_values import MAX_UNROLL
    from .validation import body_of, written_as

    parsed = Parser(base).parse()
    ref, mine = next(f for f in parsed.functions if f.name == reference), written_as(parsed, impl)
    candidate = base[: ref.body_start] + body_of(base, mine, impl) + base[ref.end :]
    where = [format_expr(fixed(impl.implements.when))] if impl.implements and impl.implements.when is not None else []
    views = {t.extent for _, t in ref.params if t.extent}
    for name in (n for n, t in ref.params if n in views and t.name == "usize"):
        lo, hi = policy.domain.get("extents", {}).get(name, [0, policy.domain.get("largest_extent", 4096)])
        where.append(f"{name} >= {int(lo)} && {name} <= {int(hi)}")
    assume = " && ".join(f"({w})" for w in where) or "true"

    def ask(condition: str) -> dict[str, Any]:
        return isolated(lambda: equivalent(base, candidate, reference, assume=condition, allow_reference_traps=True,
                                           timeout_ms=timeout_ms), 3 * timeout_ms / 1000 + 5)  # fmt: skip

    counts = [n for n, t in ref.params if t.name == "usize" and t.mode == "value"]
    largest = policy.domain.get("largest_extent", 4096)
    widest = max([largest, *(hi for _, hi in policy.domain.get("extents", {}).values())])
    if counts and widest > MAX_UNROLL:  # a loop over an extent past the unrolling budget is never decided whole
        bounded = " && ".join([f"({assume})", *(f"{n} <= {MAX_UNROLL}" for n in counts)])
        return summary(ask(bounded), bounded, bounded=True)
    return summary(ask(assume), assume)


def summary(r: dict[str, Any], where: str, bounded: bool = False) -> dict[str, Any]:
    status = r.get("status") or r.get("class", "unknown")
    out = {"status": status, "where": where}
    if bounded:
        out["bounded"] = "only where every usize parameter is within the unrolling bound; beyond it nothing is decided"
    if status == "counterexample":
        out |= {k: r[k] for k in ("counterexample", "expected", "actual") if k in r}
    elif status != "smt-equivalent":
        out["reason"] = r.get("reason") or r.get("diagnostic", {}).get("message") or status
    return out


def outside(params: list[Param], inputs: dict[str, Any], domain: dict[str, Any]) -> str | None:
    """Why `inputs` lie outside the domain the policy admits, or None when they lie inside it."""
    extents, values = domain.get("extents", {}), domain.get("values", {})
    for p in params:
        if p.name not in inputs:
            return f"they give no value for {p.name}"
        given = inputs[p.name]
        if p.kind == "extent":
            lo, hi = extents.get(p.name, [0, domain.get("largest_extent", 4096)])
            if not lo <= given <= hi:
                return f"{p.name} = {given} is outside the admitted extents {lo}..{hi}"
        elif p.name in values:
            lo, hi = values[p.name]
            if not all(lo <= x <= hi for x in (given if p.kind == "view" else [given])):
                return f"{p.name} holds a value outside the admitted values {lo}..{hi}"
    return None


def replayed(s: Any, found: dict[str, Any], policy: Any, regressions: Path | None,
             implementation: str) -> dict[str, Any]:  # fmt: skip
    """Z3's counterexample `found` run as one more finite case: `failed` with the shrunk case kept, `within-policy`,
    `outside-domain` or `unknown`, each with what it rests on."""
    from .validation import finite

    inputs = found.get("counterexample")
    if not isinstance(inputs, dict):
        return {"status": "unknown", "reason": "Z3 named no inputs to replay."}
    if why := outside(s.params, inputs, policy.domain):
        return {"status": "outside-domain", "inputs": inputs,
                "reason": f"Z3's inputs lie outside the admitted domain: {why}; the finite result stands."}  # fmt: skip
    case = Case(inputs, {}, "Z3's counterexample")
    calls = Calls()
    try:
        outcome = s.run(calls, case, policy)
    finally:
        calls.close()
    if outcome["agrees"] is False:  # through `finite`, which shrinks the case and keeps it as any failure is kept
        result = finite(s, [case], 0, policy, regressions, implementation)
        if result["status"] == "failed":
            return {"status": "failed", "inputs": inputs, **{k: result[k] for k in ("failed", "kept") if k in result},
                    "reason": "Z3's inputs, run natively, break the numerical policy."}  # fmt: skip
        outcome = {"agrees": None}  # a second run that did not fail again decides nothing
    if outcome["agrees"] is None:
        return {"status": "unknown", "inputs": inputs, "reason": "The replay of Z3's inputs did not finish alike."}
    if not outcome["applies"]:
        return {"status": "unknown", "inputs": inputs,
                "reason": "The implementation's condition does not hold on Z3's inputs natively, so it did not run."}  # fmt: skip
    ran = {k: outcome[k] for k in ("reference", "implementation")}
    alike = ran["reference"] == ran["implementation"]
    return {"status": "within-policy", "inputs": inputs, **ran,
            "reason": "Run natively, both return the same bits on Z3's inputs." if alike else
            "Run natively, the results differ on Z3's inputs within the policy's tolerance; Z3 compares exactly."}  # fmt: skip

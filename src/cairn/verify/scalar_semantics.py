"""Exact-width value semantics and solver-backed equivalence for a source fragment.

Supported: bool, fixed integers, f32/f64, records, tag-only enums and payload
sums with match and try, array views (`ro<T>[n]`, `rw<T>[n]`) and their parts,
single borrows of values, fixed local storage (`stack x:T[N]`, `Array[T, N]`),
function-local heap owners (`buffer x:T[n]`, `Buf[T](n)`) and owners that move
(`take`, `swap`, an owner passed by value, an owner returned), `compact`, host
`reduce`, locals, assignment to a name/field/element, if/else, bounded for/while
with break/continue, early returns and acyclic calls. Excluded: an owner inside
a record, a sum or an array, recursion, tasks, lanes, device placement, closures,
dyn, atomics and FFI.

A value is flattened into its scalar components: a record is its fields, a sum
is the emitted u32 tag beside every variant payload, an array is its elements.
Only a sum's active payload is compared, so inactive storage is not observed. The
inputs are exactly what the emitted entry guards admit: an enum or sum passed by
value carries a declared tag, because the guard reads that one tag and nothing
else. A tag anywhere else, nested in a record, an array or a payload, reached
through a borrow, or behind a view, is any u32, and a `match` over one outside
the declared variants aborts, as the emitted `default: cr::trap()` does. A `try`
over one is not replayed, so a difference found there is reported unknown.

Storage behind a view is one SMT array per component of its element, read and
written at `offset + i` while `i` is below the extent the signature gives it; a
part shifts that window. What a call observes is its result together with the
final contents of every `rw` parameter, element by element over the whole
extent. The admitted inputs are the ones the emitted entry guards admit
(`cr::view`, and `cr::disjoint` between an `rw` view and every other view), so
storage behind two `rw` parameters is distinct while read-only views may alias,
which is why they are modeled as independent arrays of equal contents. An owner
is storage with a length of its own: `take` moves it out and leaves an empty
owner, `swap` exchanges two, a `Buf` parameter has any length because no guard
reads one, and a `Buf` result is observed by its length and its elements.

Every guard aborts and every abort is one observation, so "both trap" is equal
behaviour. Floats use Z3's FloatingPoint theory: one round-to-nearest-even per
operation, matching the `-ffp-contract=off -fno-fast-math` contract, IEEE
comparison predicates, and `cr::truncate` modeled as the header writes it. A
result is compared as an IEEE datum, so +0.0 and -0.0 differ; but the theory has
a single NaN, so a reachable NaN observation is reported unknown rather than
equal, and a caller who does not care excludes NaN with a precondition.

Loops, `compact` and `reduce` are unrolled to MAX_UNROLL iterations; whatever
would still be running is a residual obligation the solver must refute, so
exceeding the budget is unknown, never success. An extent is symbolic, so a pass
over `0..n` needs a precondition that bounds `n` within that budget.

This translator and Z3 are trusted. No result is a Lean-kernel proof or a
verification of the C++ backend. Unsupported syntax returns unknown.

The value model is scalar_values.py, the translator scalar_symbolic.py and the
concrete replay scalar_concrete.py; this module runs the query and writes the
receipt, pinned to every file it depends on by `implementation_hash`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..compiler.cairnc import Diagnostic, Parser
from ..compiler.tree import BOOL, SIGNED, USIZE, VOID, WIDTH, is_view
from .scalar_concrete import Concrete
from .scalar_symbolic import Formula, Symbolic
from .scalar_values import (
    MAX_REPLAY,
    MAX_UNROLL,
    PROFILE,
    Source,
    Term,
    Unsupported,
    conj,
    constant,
    declared,
    disj,
    identical,
    lifted,
    neg,
    observed,
    owned,
    rebuild,
    same,
    sha,
    sort,
    undecided,
    wellformed,
)
from .smt_bridge import Solver, SolverUnavailable

# A witness is asked for again with every integer input within each bound of zero in turn, under a short timeout,
# so it reads as small numbers; the first bound that holds one wins, and the solver's own witness stands otherwise.
WITNESS_BOUNDS = (16, 256, 65536)
WITNESS_MS = 500


def prepared(source: str) -> Source:
    return Source(source)


def outcome_key(outcome):
    """A scalar outcome as one comparable key; composite results use `identical` instead."""
    return (outcome["defined"], outcome.get("return") if outcome["defined"] else None)


def implementation_hash():
    parent = Path(__file__).parents[1]
    return hashlib.sha256(
        b"".join(
            (parent / n).read_bytes()
            for n in [
                "verify/scalar_semantics.py",
                "verify/scalar_values.py",
                "verify/scalar_symbolic.py",
                "verify/scalar_concrete.py",
                "verify/smt_bridge.py",
                "verify/elision.py",
                "compiler/cairnc.py",
                "compiler/lexing.py",
                "compiler/tree.py",
                "compiler/syntax.py",
                "compiler/syntax_expressions.py",
                "compiler/syntax_statements.py",
                "compiler/scope.py",
                "compiler/facts.py",
                "compiler/checking.py",
                "compiler/statements.py",
                "compiler/expressions.py",
                "compiler/calls.py",
                "compiler/places.py",
                "compiler/concurrency.py",
                "compiler/fusion.py",
                "compiler/chunks.py",
                "compiler/staging.py",
                "compiler/cooperative.py",
                "compiler/phases.py",
                "compiler/footprints.py",
                "compiler/pipelines.py",
                "compiler/tensor.py",
                "compiler/layouts.py",
                "compiler/fragments.py",
                "compiler/rings.py",
                "compiler/implementations.py",
                "compiler/effects.py",
                "compiler/traits.py",
                "compiler/constants.py",
                "compiler/builtins.py",
                "compiler/machine.py",
                "compiler/printing.py",
                "compiler/expansion.py",
                "compiler/gradients.py",
                "compiler/codegen.py",
                "compiler/execution.py",
                "compiler/modules.py",
                "version.py",
            ]
        )
    ).hexdigest()


def equivalent(reference: str, candidate: str, symbol: str, *, assume: str = "true",
               allow_reference_traps: bool = False, timeout_ms: int = 3000,
               query_log: list | None = None) -> dict[str, Any]:  # fmt: skip
    """Check a fixed reference contract, not a model-editable expected result.

    The default requires reference totality over a nonempty, total domain.
    Explicit allow_reference_traps compares return versus abort observations.
    Counterexamples are independently replayed before being exposed.
    """
    if not all(isinstance(x, str) for x in (reference, candidate, symbol, assume)):
        return {
            "protocol": "cairn.semantic/1",
            "status": "invalid-contract",
            "reason": "Sources, symbol and precondition must be strings.",
            "lean_verified": False,
            "native_verified": False,
        }
    common = {
        "protocol": "cairn.semantic/1",
        "profile": PROFILE,
        "reference_sha256": sha(reference),
        "candidate_sha256": sha(candidate),
        "symbol": symbol,
        "assume": assume,
        "allow_reference_traps": allow_reference_traps,
        "implementation_sha256": implementation_hash(),
        "trust": ["CAIRN parser/typechecker", "value SMT translation", "Z3 solver"],
        "lean_verified": False,
        "native_verified": False,
    }
    if type(allow_reference_traps) is not bool:
        return {**common, "status": "invalid-contract", "reason": "Trap policy must be Boolean."}
    try:
        precondition_parser = Parser(assume)
        precondition_parser.expr()
        precondition_parser.need("<eof>")
        refs = prepared(reference)
        cands = prepared(candidate)
        if symbol not in refs.functions or symbol not in cands.functions:
            raise Unsupported("Selected symbol not found.")
        rf = refs.functions[symbol]
        cf = cands.functions[symbol]
        if rf.params != cf.params or rf.ret != cf.ret:
            return {**common, "status": "invalid-contract", "reason": "Candidate signature differs from reference."}
        types = [t for _, t in rf.params] + [rf.ret]
        if [refs.shape(t) for t in types] != [cands.shape(t) for t in types]:
            return {
                **common,
                "status": "invalid-contract",
                "reason": "A type named in the signature has a different definition in the candidate.",
            }
        q = Formula(rf.params, refs)
        left = Symbolic(q, refs)
        right = Symbolic(q, cands)
        lv, lw = left.invoke(symbol, list(q.inputs.values()))
        rv, rw = right.invoke(symbol, list(q.inputs.values()))
        lent = [q.inputs[n] for n, t in rf.params if t.mode == "rw"]
        probe = "probe"  # One index: where the final contents of two rw views, or two owner results, may differ.
        if any(x.window for x in lent) or owned(rf.ret):
            q.declarations.append(f"(declare-const {probe} {sort(USIZE)})")
        named, limits = [], []
        for i, (n, t) in enumerate(rf.params):  # Name the leading elements, so a model carries its storage.
            if q.inputs[n].window is None:
                continue
            limits.append(f"(bvule {q.inputs[n].window.extent} {constant(MAX_REPLAY, 'usize')})")
            for j, leaf in enumerate(refs.leaves(t.args[0] if owned(t) else t)):
                for k in range(MAX_REPLAY):
                    at = f"elem_{i}_{j}_{k}"
                    q.declarations.append(f"(declare-const {at} {declared(leaf)})")
                    q.variables[at] = leaf.name
                    named.append(same(f"(select arg_{i}_{j} {constant(k, 'usize')})", lifted(at, leaf)))
        if owned(rf.ret):  # A witness whose result is replayable: an owner of at most MAX_REPLAY elements.
            limits += [f"(bvule {x.window.extent} {constant(MAX_REPLAY, 'usize')})" for x in (lv, rv)]
        small = conj(*limits)
        formed = conj(*(wellformed(refs, t, q.inputs[n].parts) for n, t in rf.params if not is_view(t)), *named)
        domain = Term(BOOL, ("true",))
        if assume != "true":
            dn = "cairn_domain"
            while dn in refs.functions:
                dn += "x"
            sig = ", ".join(n + ":" + t.display() for n, t in rf.params)
            ds = reference + f"\nfn {dn}({sig})->bool {{return ({assume});}}"
            domains = prepared(ds)
            domain = Symbolic(q, domains).invoke(dn, list(q.inputs.values()))[0]
        admitted = conj(formed, domain.value)

        def seen(held: Term, a: Term, b: Term) -> str:
            """Does what two runs left in one rw parameter look the same? A view is read at the probe."""
            if held.window is None:
                return observed(refs, a.ty, a.parts, b.parts)
            item = refs.item(held.ty)
            inside = f"(bvult {probe} {held.window.extent})"
            return disj(neg(inside), observed(refs, item, left.at(a, probe), right.at(b, probe)))

        def returned(a: Term, b: Term) -> str:
            """Do two results look the same? An owner shows its length and its elements, read at the probe."""
            if not owned(rf.ret):
                return observed(refs, rf.ret, a.parts, b.parts)
            inside = f"(bvult {probe} {a.window.extent})"
            elements = disj(neg(inside), observed(refs, rf.ret.args[0], left.at(a, probe), right.at(b, probe)))
            return conj(same(a.window.extent, b.window.extent), elements)

        def nan(held: Term, a: Term) -> str:
            """Could this rw parameter hold a NaN, whose payload bits the model does not track?"""
            if held.window is None:
                return undecided(refs, a.ty, a.parts)
            inside = f"(bvult {probe} {held.window.extent})"
            return conj(inside, undecided(refs, refs.item(held.ty), left.at(a, probe)))

        def nan_result(a: Term) -> str:
            if not owned(rf.ret):
                return undecided(refs, rf.ret, a.parts)
            return conj(f"(bvult {probe} {a.window.extent})", undecided(refs, rf.ret.args[0], left.at(a, probe)))

        query_summaries = []
        with Solver(timeout_ms) as solver:

            def run(stage, assertion, using=None):
                """Ask the solver for this fragment; a goal it reports incomplete goes to the general one."""
                text = q.text(assertion)
                for logic in [q.logic, None] if q.logic else [None]:
                    result = (using or solver).check(text, q.variables, logic)
                    if using is None:  # a narrowing query only picks which witness to show, and the replay checks it
                        query_summaries.append({"stage": stage, **result})
                        if query_log is not None:
                            query_log.append({"stage": stage, "smt2": text + "(check-sat)\n", "result": result})
                    if result["status"] != "unknown" or "incomplete" not in result.get("reason", ""):
                        break
                return result

            def finish(status, **fields):
                return {
                    **common,
                    "status": status,
                    "solver_version": solver.version,
                    "queries": query_summaries,
                    **fields,
                }

            def inputs(result):
                out = {}
                for i, (n, t) in enumerate(rf.params):
                    item = t.args[0] if owned(t) else t.value
                    width = len(refs.leaves(item if q.inputs[n].window else t))
                    if q.inputs[n].window is None:
                        out[n] = rebuild(refs, t, [result["values"][f"arg_{i}_{j}"] for j in range(width)])
                        continue
                    if owned(t):
                        size = result["values"][f"arg_{i}_len"]
                    else:
                        size = int(t.extent) if t.extent.isdigit() else out[t.extent]
                    if not 0 <= size <= MAX_REPLAY:
                        raise Unsupported(f"An extent past {MAX_REPLAY} elements cannot be replayed.")
                    read = [[result["values"][f"elem_{i}_{j}_{k}"] for j in range(width)] for k in range(size)]
                    out[n] = [rebuild(refs, item, x) for x in read]
                return out

            def modest(bound: int) -> str:
                """Every integer input, array elements included, within `bound` of zero."""
                terms = []
                for n, t in q.variables.items():
                    if t in WIDTH:
                        top = constant(min(bound, (1 << (WIDTH[t] - (t in SIGNED))) - 1), t)
                        if t in SIGNED:
                            low = constant(-min(bound, 1 << (WIDTH[t] - 1)), t)
                            terms.append(f"(and (bvsle {low} {n}) (bvsle {n} {top}))")
                        else:
                            terms.append(f"(bvule {n} {top})")
                return conj(*terms)

            def shown(stage, assertion, result):
                """Inputs a caller can rerun: with storage in play, ask again within the replay budget, then in the
                smallest numbers of WITNESS_BOUNDS the solver finds quickly."""
                if small != "true":
                    result = run(stage + "-storage", conj(assertion, small))
                    if result["status"] != "sat":
                        return None
                if modest(1) != "true":
                    with Solver(min(timeout_ms, WITNESS_MS)) as quick:
                        for bound in WITNESS_BOUNDS:
                            tight = run(f"{stage}-within-{bound}", conj(assertion, small, modest(bound)), quick)
                            if tight["status"] == "sat":
                                return inputs(tight)
                return inputs(result)

            def witnessed(stage, assertion, result) -> dict:
                """The counterexample field of a receipt, when inputs a caller can rerun were found."""
                witness = shown(stage, assertion, result) if result["status"] == "sat" else None
                return {"counterexample": witness} if witness is not None else {}

            if domain.defined != "true":
                trapping = conj(formed, neg(domain.defined))
                r = run("domain-totality", trapping)
                if r["status"] == "sat":
                    return finish(
                        "invalid-domain", reason="Precondition may trap.", **witnessed("domain-totality", trapping, r)
                    )
                if r["status"] != "unsat":
                    return finish("unknown", reason="Domain totality was not established.")
            if admitted != "true":
                r = run("domain-nonempty", admitted)
                if r["status"] == "unsat":
                    return finish(
                        "invalid-domain", reason="Precondition admits no inputs; vacuous acceptance rejected."
                    )
                if r["status"] != "sat":
                    return finish("unknown", reason="Nonempty domain was not established.")
            residual = disj(left.residual, right.residual)
            if residual != "false":
                r = run("loop-unrolling", conj(admitted, residual))
                if r["status"] != "unsat":
                    return finish(
                        "unknown",
                        reason=f"A loop may run past the {MAX_UNROLL}-iteration unrolling budget; deciding it needs "
                        f"a precondition that holds every trip count, and so every symbolic extent it reads, "
                        f"at {MAX_UNROLL} or below.",
                    )
            if not allow_reference_traps:
                partial = conj(admitted, neg(lv.defined))
                r = run("reference-totality", partial)
                if r["status"] == "sat":
                    reason = "Reference traps on an admitted input."
                    return finish("invalid-reference", reason=reason, **witnessed("reference-totality", partial, r))
                if r["status"] != "unsat":
                    return finish("unknown", reason="Reference totality was not established.")
            differs = [neg(seen(held, a, b)) for held, a, b in zip(lent, lw, rw, strict=True)]
            mismatch = disj(
                neg(same(lv.defined, rv.defined)), conj(lv.defined, rv.defined, disj(neg(returned(lv, rv)), *differs))
            )
            r = run("equivalence", conj(admitted, mismatch))
            if r["status"] == "unsat":
                unsure = (nan(held, a) for held, a in zip(lent, lw, strict=True))
                floats = disj(nan_result(lv), *unsure)
                unspoken = conj(admitted, lv.defined, floats)
                if unspoken != "false":
                    n = run("nan-observation", unspoken)
                    if n["status"] != "unsat":
                        reason = "An observed float may be NaN, whose payload bits this model does not track."
                        return finish("unknown", reason=reason, **witnessed("nan-observation", unspoken, n))
                watched = [*(["the result"] if rf.ret != VOID else []), *(n for n, t in rf.params if t.mode == "rw")]
                visible = ", ".join(watched) or "no value"
                return finish(
                    "smt-equivalent",
                    quantification="All values of the declared parameter types satisfying the host precondition; "
                    "the top-level tag of an enum or sum passed by value names a declared variant, as the entry "
                    "guard checks, while a tag nested in a record, an array or a payload, reached through a borrow "
                    "or held in storage carries any value and a match over one outside them aborts; storage the "
                    "entry guards admit, with distinct storage behind every rw view.",
                    observation=f"{visible}, or one undifferentiated abort outcome; an rw view, and an owner that "
                    "is returned, is compared element by element over its whole extent; a sum shows its tag and "
                    "active payload only; no memory/timing observation.",
                )
            if r["status"] != "sat":
                return finish("unknown", reason="Equivalence solver did not decide the obligation.")
            args = shown("equivalence", conj(admitted, mismatch), r)
            if args is None:
                return finish("unknown", reason=f"No counterexample lends {MAX_REPLAY} elements or fewer.")
            expected = Concrete(refs).outcome(symbol, args)
            actual = Concrete(cands).outcome(symbol, args)
            if assume != "true":
                observation = Concrete(domains).outcome(dn, args)
                if not observation["defined"] or not observation["return"]:
                    return finish("unknown", reason="Solver/concrete precondition disagreement.", counterexample=args)

            def alike(x, y) -> bool:
                if x["defined"] != y["defined"]:
                    return False
                lend = ((t, n) for n, t in rf.params if t.mode == "rw")
                return not x["defined"] or (
                    identical(refs, rf.ret, x["return"], y["return"])
                    and all(identical(refs, t, x["written"][n], y["written"][n]) for t, n in lend)
                )

            if alike(expected, actual):
                return finish(
                    "unknown",
                    reason="Solver/concrete replay disagreed; no semantic rejection is certified.",
                    counterexample=args,
                )
            return finish(
                "counterexample",
                counterexample=args,
                expected=expected,
                actual=actual,
                concrete_replay=True,
                native_replay="not-run",
            )
    except Diagnostic as e:
        return {**common, "status": "rejected", "diagnostic": e.data}
    except Unsupported as e:
        return {**common, "status": "unknown", "reason": str(e), "unsupported_profile": True}
    except (SolverUnavailable, OSError, ValueError, KeyError, IndexError, RecursionError) as e:
        return {**common, "status": "unknown", "reason": str(e)}

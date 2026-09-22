# The AI edit protocol

How an agent edits CAIRN: what it is given, what it may propose, and what decides whether the result is kept. [internals.md](internals.md#safety-and-trust) states what an edit response can never move. An edit request is the `cairn.edit/1` object of [project/edit_schema.json](project/edit_schema.json): a session digest, a replacement of at most 64000 UTF-8 bytes, and a kind, `body` for a whole function or `expr` for one site named by its own digest.

## The rule cards

`src/cairn/agent/teaching.py` holds seventeen rule cards: base, integers, views, compact, calls, floats, records, generators, memory, sums, generics, owners, effects, parallel, tasks, closures and modules. Each is a dozen lines of what that part of the language admits and refuses, and `select_cards` picks them from the lexical tokens of the source at hand, so a packet carries only the cards its program touches. `cairn inspect --symbol f` prints the whole packet: source, scope, effects and those cards.

The complete set is what `tools/ai/ai_pilot.py prepare` writes as a subject's only documentation and what `tools/ai/measure_context.py` counts. Shorter wording never grants edit authority, and supplying a card implies no model training or proficiency result.

## Using the agent's skills without trusting its guesses

The design target is successful, independently checked changes per total budget, not the shortest possible string. The model chooses an algorithm or expression; the compiler reconstructs routine structure, rejects unauthorized changes, and checks what it can. Familiar syntax and Python tooling are hypotheses about transfer from pretraining, not evidence that a model is proficient. No model weights have been changed here, and the only fresh-model trial that has run is the preregistered pilot under `evidence/v1_1/ai_pilot/`.

The host chooses the unit of reasoning. Use an ordinary full-function edit when the algorithm's structure must change. Use one or more named expression slots when the decision is local. Slots must be nonoverlapping and resolve against the exact original source; they are not a mechanism for concealing missing dependencies. The packet still includes the same bidirectional call-graph source component and selected language cards as a whole-function edit, plus expected types and lexical bindings at each slot. An attached scalar contract discloses its full reference source, input domain and trap policy. These are read-only task facts: the model must not guess a hidden meaning or substitute an easier reference.

A trusted host prepares a sketch in Python:

```python
from cairn.agent.sketches import Sketch, ScalarContract

source = "fn average(x:u64,y:u64)->u64 { return (x+y)/2; }"
reference = """fn average(x:u64,y:u64)->u64 {
  return (x/2)+(y/2)+((x%2+y%2)/2);
}"""
sketch = Sketch(
    source,
    "average",
    task={"task": "Return floor((x+y)/2) for all u64 inputs, without traps."},
    semantic=ScalarContract(reference, "average"),
).hole("value", "(x+y)/2")
packet = sketch.packet()
# Send packet to a model using the caller's chosen adapter.
# Here this explicit string is an authored example, not a model response.
reply = '{"value":"(x & y) + shr(x ^ y, 1)"}'
candidate = sketch.fill_json(reply)
result = sketch.check_semantics(candidate)
assert result["status"] == "smt-equivalent"
```

Host scripts import the installed package, so run them from the repository root after `pip install -e '.[dev]'`. The model returns data, not Python to execute. `fill_json` rejects duplicates, nonfinite JSON, extra fields, non-string choices, syntax injection, stale bindings and choices outside the compiler's rules. A data-only response need not echo long hashes: identity, source, slot map and reference remain bound in the host. That is a local-session transport rule, not a signed authorization protocol; a saved JSON map is not a standalone approved patch, and distributed or multi-tenant integrations need authenticated session routing and OS-level isolation, neither of which is supplied here.

Behavior feedback is stronger than compiler feedback. `x+y` and `add_wrap(x,y)` may have identical types while differing at overflow. For the implemented scalar fragment, the checker asks whether any valid input makes the candidate return something different from the fixed reference, or abort when the reference returns, and a distinguishing input becomes feedback only after replay in the independent Python interpreter. This avoids treating compiler acceptance as correctness, and it avoids asking the model to invent a plausible test result. The reference is an executable definition supplied by the host, and it may itself misstate the human's intent; the checker cannot solve that authority problem. An owner that moves, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary remain on the finite-test path, and unsupported symbolic work returns unknown, which is neither a proof nor permission to shrink the task to the supported subset.

Counterexamples become persistent regression facts. `solve_finite` searches a caller-supplied finite list or product of expression choices and typechecks every candidate. Previously found counterexamples can reject later candidates without another solver call; they can never accept one, since acceptance always uses a fresh all-width semantic check. The included average demo examines four authored candidates, and its savings from cached witnesses are deterministic test plumbing, not an AI success rate.

```sh
python3 tools/ai/sketch_demo.py
python3 tools/checks/semantic_check.py examples/sketch/reference.cairn examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/release/build.py examples/sketch/after.cairn
```

The semantic status is `smt-equivalent`, not `Lean-verified`, `native-verified` or `fastest`. Reference and candidate hashes, model profile, domain, query hashes and Z3 version accompany the receipt. Saved SMT-LIB obligations allow independent re-execution; no proof certificate is reconstructed in Lean. A nontrivial `--assume` is a caller precondition, not a guard emitted by the native builder, and the builder does not use this receipt to remove checks.

Training material must match the real tool protocol. The semantic curriculum has 40 same-signature, same-domain tasks in 14 algorithm families, each with an equivalent implementation and a type-correct inequivalent one; twenty-eight preference pairs are training material and twelve prompts belong to four proposed evaluation families. Twenty-nine tasks also have executed named-choice repair transcripts, twenty as training targets and nine as evaluation prompts. The SFT export puts the failed proposal and checker feedback in user context and includes only the correct JSON reply as an assistant target. These are authored, executed fixtures, not transcripts of a model, and all answers ship, so they are not secret tests. Keep the API-changing language lessons from 0.3 apart from these repair targets: rewarding an agent for weakening a signature or choosing an empty domain would train it to defeat the task. Solver timeouts, failed translations and tool errors must never become positive labels.

What to evaluate next: freeze models, task semantics, inference budget, tool budget and native workload; compare full-source edits, 0.3 expression packets, named choices, and named choices plus scalar feedback; give C++ and Rust comparable scoped-edit and solver tools, or a workflow gain will be misreported as a language gain; use independently authored algorithm families, not these shipped answers; report first-attempt and bounded-repair success, unauthorized changes, regressions in callers, total model-token usage including failed attempts, solver and compile time, and measured native performance.

## Closed generator contracts

The small dependency packet for editing existing family and wire examples. It is not the full language card or a proof certificate. `compiler/expansion.py` expands both, `std/wire.cairn` is the wire recipe and `verify/linear_certificates.py` checks the collector; changing any of them needs a larger audit context.

family/1. A static function with one natural parameter K and family prefix=base[a..b] produces b-a independent monomorphic functions prefix_a through prefix_(b-1). Each uses its literal K. Empty, negative, reversed, oversize, colliding, or unbound expansions are rejected. All emitted functions are checked. There is no dynamic dispatch, allocation, or evaluation of source strings. Increasing the range increases object size and compilation work. Expansion is capped at 1024 per family, 2048 functions total and 200000 AST/check visits.

wire/1. A record of fixed unsigned fields derives encode, decode, and byte-length functions. Field order is declaration order, each field little endian, no wire padding. Native record layout is not the wire layout. Encoded size is the sum of field widths divided by eight. Every field bit is retained. The buffer caller must supply the exact live extent; guards cannot recover a forged pointer's provenance. No tags, checksums, semantic ranges, alignment padding, implicit versions, or allocation are added.

bounded-collector/1. On immutable extent n with output capacity n, visit indices 0..n in order. Evaluate a read-only predicate once per input. Evaluate a read-only projection only for selected inputs and append it once. Never read output through the predicate or projection. The private cursor k starts at zero and satisfies k<=i at the start of iteration i. Thus every output store has k<n and final k<=n. Nonselected tail bytes/elements are unchanged. Inputs must not alias output; entry checks enforce interval disjointness under the FFI precondition. No worker threads, buffering, reordering, vectorization guarantee, or hidden heap allocation are part of this contract.

## The named-choice card

What the model is told when the host asks for named expressions rather than a whole function. Read the host packet: task, source, named slots, expected types, local bindings, allowed effects and selected language cards. Return only one JSON object mapping EVERY slot name to a CAIRN expression string. No Markdown, extra keys or duplicate keys. Example reply: {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses, then rechecks the complete module. Do not rewrite function signatures, task, reference, permissions, preconditions, compiler settings or tests. Missing context is not permission to invent a dependency. Slot names are descriptive identifiers, not code or persistent IDs. The host binds your reply; do not invent hashes.

Use familiar expressions, but preserve CAIRN semantics: fixed-width integers; ordinary +,-,* trap on overflow; explicit unsigned add_wrap/sub_wrap/mul_wrap; checked casts and shift counts; sequential loops; no implicit allocation. A type error requests a local type repair, not changing the public API. A semantic counterexample gives an input where your proposal returns a wrong value or traps. Fix the algorithm on the original domain. Do not patch only that example.

Typed means only the static checks passed. The optional scalar checker compares with a fixed host reference for all declared-width inputs under a total, nonempty host precondition. smt-equivalent trusts the translator and Z3; it is not a Lean proof or native-code proof. It models integers, bools, floats, records, sums, branches, acyclic calls, array views and their parts, fixed and function-local storage, `compact`, host `reduce` and loops it can unroll, but not an owner that moves, recursion, concurrency or GPU execution. Unknown/timeout is never accepted. Tests and benchmarks are separate stages. No model has been trained or evaluated by this release merely because this card is supplied.

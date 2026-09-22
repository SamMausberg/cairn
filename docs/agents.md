# The AI edit protocol

An agent edits CAIRN through a host. The host holds the program, shows the agent a packet, and decides whether the agent's reply is kept. [internals.md](internals.md#safety-and-trust) lists what a reply can never change. The request an agent sends is the `cairn.edit/1` object of [project/edit_schema.json](project/edit_schema.json): a session digest, a replacement of at most 64000 UTF-8 bytes, and a kind, either `body` for a whole function or `expr` for one site named by its own digest.

## The rule cards

`src/cairn/agent/teaching.py` holds seventeen rule cards: base, integers, views, compact, calls, floats, records, generators, memory, sums, generics, owners, effects, parallel, tasks, closures and modules. Each is about a dozen lines saying what that part of the language accepts and refuses. `select_cards` picks the cards whose lexical tokens appear in the source at hand, so a packet carries only what its program uses. `cairn inspect --symbol f` prints the whole packet: source, scope, effects and cards.

The full set of cards is what `tools/ai/ai_pilot.py prepare` gives a pilot subject as its only documentation, and what `tools/ai/measure_context.py` counts.

## What the host checks

The host chooses how much the agent may change. A full-function edit replaces one body, for changes to an algorithm's structure. Named expression slots replace one or more expressions, for local decisions. Slots may not overlap and are resolved against the exact original source.

Both kinds of packet carry the same context: the function's call-graph component in both directions, the cards it selects, and, for slots, the expected type and the lexical bindings at each one. A scalar contract attached to the task shows its full reference source, its input domain and its trap policy. The agent reads these facts and cannot replace them.

When a reply arrives, the host splices it into the pinned original, leaves everything outside the authorized range untouched, and rechecks the complete linked module. It refuses a changed signature (`E-SIGNATURE`), an added or removed declaration (`E-DECLARATION`), an effect beyond the ceiling (`E-EFFECT-EXPANSION`) and a call to a function the packet did not show (`E-CONTEXT-CLOSURE`). A stale session is `E-SESSION`. An accepted reply is `typed`, which says nothing yet about its behaviour.

## Named choices

A host prepares a sketch in Python:

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

Run host scripts from the repository root after `pip install -e '.[dev]'`. The model replies with a JSON object that maps each slot to an expression string. `fill_json` refuses duplicate keys, nonfinite JSON, extra fields, non-string values, text that would escape its slot, stale bindings and any choice the compiler refuses.

The reply does not repeat hashes. The host keeps the session's identity, source, slot map and reference, and binds the reply to them itself. That binding lives in one host process. A saved JSON map is not an approved patch on its own, and a service shared between users would need its own authenticated routing and process isolation.

## Behavioural feedback

Types cannot tell `x+y` from `add_wrap(x,y)`, which differ only at overflow. For the scalar fragment, the checker asks Z3 for an admitted input on which the candidate returns something other than the fixed reference, or aborts where the reference returns. A distinguishing input is replayed in an independent Python interpreter before it is shown to the model as feedback.

The reference is the host's executable definition of the task, and it can misstate what a person wanted. Code the SMT model does not cover goes to finite tests instead: an owner held inside a record or an array, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary. Unsupported work comes back `unknown`, and the task stays as it was.

`solve_finite` searches a finite list or product of expression choices supplied by the caller, and typechecks each candidate. A counterexample found earlier can reject a later candidate without another solver call. Acceptance always needs a fresh check over every width. The average demo tries four authored candidates.

```sh
python3 tools/ai/sketch_demo.py
python3 tools/checks/semantic_check.py examples/sketch/reference.cairn examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/release/build.py examples/sketch/after.cairn
```

The receipt's status is `smt-equivalent`. It records the reference and candidate hashes, the model profile, the domain, the query hashes and the Z3 version, and `--obligations` saves the SMT-LIB queries so anyone can rerun them. Nothing is reconstructed in Lean. A nontrivial `--assume` is a precondition on callers: the native build emits no guard for it and removes no check because of it.

## Training material

The fixtures follow the real protocol. The semantic curriculum has 40 tasks in 14 algorithm families, each with one equivalent implementation and one inequivalent implementation of the same type. Twenty-eight preference pairs are training material, and twelve prompts form four proposed evaluation families. Twenty-nine tasks also have executed repair transcripts for named choices, twenty as training targets and nine as evaluation prompts. The SFT export puts the failed proposal and the checker's feedback in the user turn and only the correct JSON reply in the assistant turn.

All of these were written and executed by hand, and all the answers ship, so none of them is a held-out test. The language lessons from 0.3 that change an API belong in a separate set: rewarding an agent for weakening a signature or choosing an empty domain would teach it to defeat the task. Solver timeouts, failed translations and tool errors must never become positive labels.

The comparison that has not been run would freeze the models, the task semantics, the inference and tool budgets and the native workload. It would compare full-source edits, 0.3 expression packets, named choices, and named choices with scalar feedback, and give C++ and Rust equivalent scoped-edit and solver tools so that a workflow gain is not reported as a language gain. It would use independently written algorithm families, and report first-attempt and bounded-repair success, unauthorized changes, regressions in callers, total tokens including failed attempts, solver and compile time, and native performance.

## Closed generator contracts

The dependency packet for editing the existing family and wire examples. It is neither the full language card nor a proof certificate. `compiler/expansion.py` expands both, `std/wire.cairn` is the wire recipe and `verify/linear_certificates.py` checks the collector; changing any of them needs a larger audit context.

family/1. A static function with one natural parameter K and family prefix=base[a..b] produces b-a independent monomorphic functions prefix_a through prefix_(b-1). Each uses its literal K. Empty, negative, reversed, oversize, colliding, or unbound expansions are rejected. All emitted functions are checked. There is no dynamic dispatch, allocation, or evaluation of source strings. Increasing the range increases object size and compilation work. Expansion is capped at 1024 per family, 2048 functions total and 200000 AST/check visits.

wire/1. A record of fixed unsigned fields derives encode, decode, and byte-length functions. Field order is declaration order, each field little endian, no wire padding. Native record layout is not the wire layout. Encoded size is the sum of field widths divided by eight. Every field bit is retained. The buffer caller must supply the exact live extent; guards cannot recover a forged pointer's provenance. No tags, checksums, semantic ranges, alignment padding, implicit versions, or allocation are added.

bounded-collector/1. On immutable extent n with output capacity n, visit indices 0..n in order. Evaluate a read-only predicate once per input. Evaluate a read-only projection only for selected inputs and append it once. Never read output through the predicate or projection. The private cursor k starts at zero and satisfies k<=i at the start of iteration i. Thus every output store has k<n and final k<=n. Nonselected tail bytes/elements are unchanged. Inputs must not alias output; entry checks enforce interval disjointness under the FFI precondition. No worker threads, buffering, reordering, vectorization guarantee, or hidden heap allocation are part of this contract.

## The named-choice card

This is what the model is told when the host asks for named expressions instead of a whole function. Read the host packet: task, source, named slots, expected types, local bindings, allowed effects and selected language cards. Return only one JSON object mapping EVERY slot name to a CAIRN expression string. No Markdown, extra keys or duplicate keys. Example reply: {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses, then rechecks the complete module. Do not rewrite function signatures, task, reference, permissions, preconditions, compiler settings or tests. Missing context is not permission to invent a dependency. Slot names are descriptive identifiers, not code or persistent IDs. The host binds your reply; do not invent hashes.

Use familiar expressions, but preserve CAIRN semantics: fixed-width integers; ordinary +,-,* trap on overflow; explicit unsigned add_wrap/sub_wrap/mul_wrap; checked casts and shift counts; sequential loops; no implicit allocation. A type error asks for a local type repair; keep the public API. A semantic counterexample gives an input where your proposal returns a wrong value or traps. Fix the algorithm on the original domain. Do not patch only that example.

Typed means only the static checks passed. The optional scalar checker compares with a fixed host reference for all declared-width inputs under a total, nonempty host precondition. smt-equivalent trusts the translator and Z3; it is not a Lean proof or native-code proof. It models integers, bools, floats, records, sums, branches, acyclic calls, array views and their parts, fixed and function-local storage, `compact`, host `reduce` and loops it can unroll, but not an owner held inside a record or an array, recursion, concurrency or GPU execution. Unknown/timeout is never accepted. Tests and benchmarks are separate stages. Supplying this card trains no model and evaluates none.

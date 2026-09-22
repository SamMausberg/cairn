# The AI edit protocol

An agent edits CAIRN through a host. The host holds the program, shows the agent a packet, and decides whether the agent's reply is kept. [internals.md](internals.md#safety-and-trust) lists what a reply can never change. A request is one of the objects of [project/edit_schema.json](project/edit_schema.json). A `cairn.edit/2` request names a session by the handle the host gave it, such as `e1`, and is a `body` edit of the whole function, an `expr` edit of one site named like `x3`, or an `expand` request. A `cairn.edit/1` request carries the session digest itself and a site's own digest. Replacements are at most 64000 UTF-8 bytes.

## The rule cards

`src/cairn/agent/teaching.py` holds eighteen rule cards: base, integers, views, compact, calls, floats, records, generators, memory, sums, generics, owners, effects, parallel, tasks, rings, closures and modules. Each is a few paragraphs saying what that part of the language accepts and refuses, and names the diagnostic code of each rule it states, so a refusal leads back to its card. `base`, `integers` and `calls` go with every packet, and no other card repeats them. `select_cards` picks the others by the lexical tokens of the source at hand, so a packet carries only what its program uses. `cairn inspect --symbol f` prints the whole packet: source, scope, effects, evidence and cards.

The full set of cards is what `tools/ai/ai_pilot.py prepare` gives a pilot subject as its only documentation, and what `tools/ai/measure_context.py` counts.

## What the host checks

The host chooses how much the agent may change. A full-function edit replaces one body, for changes to an algorithm's structure. Named expression slots replace one or more expressions, for local decisions. Slots may not overlap and are resolved against the exact original source.

A packet starts focused. It holds the target's source, its effect row and ceiling, and the signature and effect row of every function it calls and every function that calls it, each with its evidence class and the comment written above it. It holds the types those name, the cards the target selects, and, for slots, the expected type and the lexical bindings at each one. `not_shown` lists every other function of the program. A scalar contract attached to the task shows its full reference source, its input domain and its trap policy. The agent reads these facts and cannot replace them.

The evidence class says how much of a callee's behaviour the agent may rely on without reading its body. The host asks for evidence in the contract, and the session establishes it when it opens, against the exact source, so it is never a claim carried in from elsewhere.

| Class | Established by | What the packet shows | May the agent rely on it |
|---|---|---|---|
| `interface` | the compiler: signature, effect row, types, extents | nothing more | only for the interface; expand the body first |
| `declared` | nobody: the host's text under `contracts` | the text | no; expand the body first |
| `finite-tested` | the host's `cairn.task/1` cases under `tests`, run natively now | the case count and up to eight cases | for those inputs only |
| `smt-equivalent` | Z3 over the host's `references`, checked now | the reference source and its precondition | yes, in place of the body |

A check that does not pass leaves the class `declared` and names the status it reached (`counterexample`, `unknown`, `failed-tests`), because unknown is never success. The comment above a declaration is always the author's claim, and the packet's terms say so.

```python
from cairn.agent.agent_tools import EditSession

source = "fn step(x:u64)->u64 { return add_wrap(x, 1); }\nfn caller(x:u64)->u64 { return step(x); }\n"
reference = "fn step(x:u64)->u64 { if x == 18446744073709551615 { return 0; } return x + 1; }"
packet = EditSession(source, "caller", {"references": {"step": {"reference": reference}}}).packet()
assert packet["dependencies"]["step"]["evidence"] == "smt-equivalent"
```

Every packet carries `terms`: the scopes, the limits, the boundaries, what each evidence class means, what a refusal and an admission do and do not say, and the compiler profile. They are the same for every packet, so a packet names nothing they already state, and a missing `task` means the host gave none.

The agent asks for more with `expand`, naming up to 32 functions or types. The host answers from the pinned program with each body as written, the types those bodies use, and any card they add. A function the agent has expanded may then be called. `cairn inspect --symbol f --expand g` prints the packet after the same request, and `--scope component` prints the 1.3 packet, which shows the whole call-graph component in both directions at once.

```json
{"protocol": "cairn.edit/2", "handle": "e1", "kind": "expand", "symbols": ["append", "Header"]}
```

An `explain` request returns [`cairn explain`](tools.md#cairn-explain) for the functions the packet discloses, in the candidate the host last admitted for that handle, or in the original before any. It is how an agent sees whether an edit left a guard in a loop or stopped it vectorizing without running anything.

The host keeps the digests of source, contract, compiler and disclosed context behind each handle, so the agent never copies a hash. Expanding a function it had not disclosed changes the session digest, and an `edit/1` request made before is refused as stale. One host sends each card and the terms once, and later packets name them under `sent_before`. Its admissions and refusals leave out what the terms say of every admission and every refusal: an admission gives the status, the symbol, the effect row and the check sites, and the check sites before the edit only where they differ.

A refusal points into the reply the agent wrote: `line`, `column` and `source_line` are the reply's, and `in` is `reply`, unless the whole-module recheck found the fault elsewhere, when `source_line` is that line of the spliced program. Its `repair_hint` is the smallest fix the host can state without guessing, computed from the diagnostic's data where it can be: a close name for an unknown one, the construct that brings each effect the ceiling refuses, the expand request that discloses a callee, the conversion between two scalar types. A code whose message already says how to repair it carries no hint of its own. On thirty scripted edits of five example programs a focused packet with one expansion took about a quarter of the context of the component packet (`evidence/v1_4/context/`). No model took part in that measurement.

When a reply arrives, the host splices it into the pinned original, leaves everything outside the authorized range untouched, and rechecks the complete linked module, whatever the packet showed. It refuses a changed signature (`E-SIGNATURE`), an added or removed declaration (`E-DECLARATION`), an effect beyond the ceiling (`E-EFFECT-EXPANSION`) and a call to a function the packet did not show (`E-CONTEXT-CLOSURE`). A stale session or an unknown handle is `E-SESSION`, and an expansion that names nothing, or two things, is `E-SYMBOL`. An accepted reply is `typed`, which says nothing yet about its behaviour. The functions a focused packet shows are a subset of what the component packet shows, so a reply admitted under the focused packet is admitted under the component one.

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

One narrower trial is designed and has not run: [tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md) fixes twelve repairs of planted bugs in three example programs, one fresh subject per repair under the component packet and one under the focused packet, eight host calls each, and the rule for what the effort ratio lets anyone claim. `tools/ai/protocol_trial.py` prepares the sandboxes, answers the subjects, and scores every byte, call and hidden check; `evidence/v1_4/protocol_trial/` shows that every task fails as planted, passes as shipped, and can be solved through the host under both arms.

The broader comparison that has not been run would freeze the models, the task semantics, the inference and tool budgets and the native workload. It would compare full-source edits, 0.3 expression packets, named choices, and named choices with scalar feedback, and give C++ and Rust equivalent scoped-edit and solver tools so that a workflow gain is not reported as a language gain. It would use independently written algorithm families, and report first-attempt and bounded-repair success, unauthorized changes, regressions in callers, total tokens including failed attempts, solver and compile time, and native performance.

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

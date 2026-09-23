# The AI edit protocol

An agent edits CAIRN through a host. The host holds the program, shows the agent a packet, and decides whether the agent's reply is kept. A request is one of the objects of [project/edit_schema.json](project/edit_schema.json): a `cairn.edit/2` request names its session by a handle such as `e1` and is a `body` edit of a whole function, an `expr` edit of one site named like `x3`, or a request for more context. A replacement is at most 64000 UTF-8 bytes. [internals.md](internals.md#safety-and-trust) lists what a reply can never change. [demos/repair](../demos/repair/README.md) shows a whole session.

## The rule cards

`src/cairn/agent/teaching.py` holds eighteen rule cards, one per part of the language: base, integers, views, compact, calls, floats, records, generators, memory, sums, generics, owners, effects, parallel, tasks, rings, closures and modules. Each says in a few paragraphs what that part accepts and refuses, and names the diagnostic code of each rule, so a refusal leads back to its card. `base`, `integers` and `calls` go with every packet. The others are picked by the lexical tokens of the source at hand, so a packet carries only what its program uses.

## Packets

A packet starts focused. It holds:

- the target's source, its effect row and its ceiling;
- the signature, effect row, comment and evidence class of every function it calls and every function that calls it, and the types those name;
- the cards the target selects, and for an expression edit the expected type and the bindings at each site;
- `not_shown`, the names of every other function;
- the task's reference, input domain and trap policy, when the host gives one;
- `terms`, which say once what every scope, limit, evidence class, refusal and admission means.

`cairn inspect --symbol f` prints the packet. The agent reads these facts and cannot replace them.

The evidence class says how much of a callee's behaviour the agent may rely on without reading its body. The session establishes it when it opens, against the exact source, so it is never a claim carried in from elsewhere:

| Class | Established by | What the packet shows | May the agent rely on it |
|---|---|---|---|
| `interface` | the compiler: signature, effect row, types, extents | nothing more | only for the interface; expand the body first |
| `declared` | nobody: the host's text under `contracts` | the text | no; expand the body first |
| `finite-tested` | the host's `cairn.task/1` cases under `tests`, run natively now | the case count and up to eight cases | for those inputs only |
| `smt-equivalent` | Z3 over the host's `references`, checked now | the reference source and its precondition | yes, in place of the body |

A check that does not pass leaves the class `declared` and names what it reached (`counterexample`, `unknown`, `failed-tests`), because unknown is never success. The comment above a declaration is always the author's claim.

```python
from cairn.agent.agent_tools import EditSession

source = "fn step(x:u64)->u64 { return add_wrap(x, 1); }\nfn caller(x:u64)->u64 { return step(x); }\n"
reference = "fn step(x:u64)->u64 { if x == 18446744073709551615 { return 0; } return x + 1; }"
packet = EditSession(source, "caller", {"references": {"step": {"reference": reference}}}).packet()
assert packet["dependencies"]["step"]["evidence"] == "smt-equivalent"
```

## Requests beyond an edit

`expand` asks for up to 32 more functions or types. The host answers with each body as written, the types those bodies use and any card they add, and a function the agent has expanded may then be called. `cairn inspect --symbol f --expand g` shows the same, and `--scope component` shows the whole call-graph component at once.

```json
{"protocol": "cairn.edit/2", "handle": "e1", "kind": "expand", "symbols": ["append", "Header"]}
```

`explain` returns [`cairn explain`](tools.md#cairn-explain) for the disclosed functions of the last admitted candidate, so an agent sees whether its edit left a guard in a loop or stopped it vectorizing, without running anything.

`predict`, as in `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "predict", "sizes": [{"n": 1e7}]}`, returns [`cairn predict`](tools.md#cairn-predict) for the disclosed functions, and after an admission the predicted ratio against the original at each size. An agent can price candidates this way and build and time only the one it keeps. A prediction is not evidence of speed, and the host still decides what is measured.

`shot`, as in `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "shot", "functions": ["panel.ui.update"]}`, shows the agent what the program draws. The host runs the latest admitted candidate once, headless, and returns every frame [`std.draw.capture`](library.md#stddraw) wrote, with the effect rows of the named functions and what each gained or lost against the original. `cairn shot app --symbol f` gives the same from the command line. Nothing opens a window or touches a device.

```json
{"schema": "cairn.shot/1", "status": "shot", "exit_code": 0,
 "frames": [{"frame": 1, "png": "…/shot-x/frame-1.png", "since_previous_ns": 3261000,
             "layout": {"width": 320, "height": 200, "at_ns": 17035994170318,
                        "elements": [{"name": "list", "x": 8, "y": 8, "w": 150, "h": 170}]}}],
 "effects": {"panel.ui.update": ["read:ui", "trap", "write:ui"]},
 "changed": {"panel.render.frame": {"added": ["ffi:write", "io"], "removed": []}}}
```

The PNG is the pixels. The layout record is what the program says it drew where, so "the detail panel does not overlap the list" is checked on numbers and a test can hold it. The rows say what the edit costs: a frame function that gained `alloc` allocates every frame. A failed program still returns the frames it wrote before failing.

## What the host admits

When a reply arrives, the host splices it into the pinned original and rechecks the whole linked module, whatever the packet showed. Every token outside the authorized range must lex as it did, so a reply that ends in a comment, which would hide the rest of the line, is refused. The host refuses:

| Code | Why |
|---|---|
| `E-SIGNATURE` | the signature changed |
| `E-DECLARATION` | a declaration was added, removed or hidden behind a trailing comment |
| `E-EFFECT-EXPANSION` | the new body has an effect beyond the ceiling |
| `E-CONTEXT-CLOSURE` | it calls a function the packet did not show |
| `E-SKETCH-CHOICES` | a named choice carries a comment |
| `E-SESSION` | the session is stale or the handle unknown |
| `E-SYMBOL` | an expansion names nothing, or two things |
| `E-PRESERVE` | a refactoring changed behaviour, or could not be shown not to (below) |

An admitted reply is `typed`, which says nothing yet about its behaviour. A refusal points into the reply the agent wrote (`line`, `column`, `source_line`, and `in` is `reply`) unless the whole-module recheck found the fault elsewhere. Its `repair_hint` is the smallest fix the host can state without guessing: a close name for an unknown one, the construct that brings each refused effect, the expand request that discloses a callee, or the conversion between two types.

The host keeps the digests behind each handle, so the agent never copies a hash, and sends each card and the terms once per host. On thirty scripted edits of five example programs, a focused packet with one expansion took about a quarter of the context of the component packet (`evidence/v1_4/context/`). No model took part in that measurement.

A host that asks for a refactoring puts `{"preserve": "equivalent"}` or `{"preserve": "identical"}` in the contract. The candidate is then compared with the original as [`cairn diff`](tools.md#cairn-diff) compares versions: `identical` admits only code that is the original's up to renaming, and `equivalent` also admits code Z3 shows behaves the same. Anything else is `E-PRESERVE`, with the witness input as the `repair_hint` when there is one. An admission says in `equivalence` which class it established, or `not-proved` without the contract.

## The program's state

An agent that has made several edits does not need the conversation that made them. `cairn state`, or a `state` request, prints the program as it stands: every function of the program's own modules as `[signature, effect row]`, the declared types, the open diagnostics and the admitted edits, under one `digest`. A refused program keeps its parsed signatures with a row of `null`, which means unknown, never empty.

```sh
cairn state examples/apps/kvstore > before.json
cairn state examples/apps/kvstore --since before.json    # only what changed
```

A `delta` request, or `--since`, gives only what changed, and applying it to the state it names reproduces the new digest exactly. A delta from a digest the host never sent is `E-SESSION`.

```json
{"protocol": "cairn.edit/2", "handle": "e1", "kind": "delta", "since": "<the digest of an earlier state>"}
```

## Interface migrations

An edit can never change a signature. Changing one, such as adding a parameter or changing an error type, is a separate authorization that the host grants by name, and an edit session cannot reach it (`E-REQUEST`).

```sh
cairn migrate app --symbol lib.checksum --to "fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32" > packet.json
cairn migrate app --symbol lib.checksum --to "fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32" --reply reply.json
```

The host names the function, its new signature, any other function whose signature changes with it (`--also NAME=SIGNATURE`), and the effects rows may gain (`--allow EFFECT`). The packet shows every function the migration may rewrite: the migrated ones and all their callers, in every file. A reply maps function names to whole new declarations. The tool refuses:

| Code | Why |
|---|---|
| `E-SESSION` | the reply is for another authorization, or a file changed since |
| `E-MIGRATION-SCOPE` | it rewrites a function it was not authorized to |
| `E-MIGRATION` | it changes visibility, or declares anything beside the function |
| `E-SIGNATURE` | a signature is not the authorized one |
| `E-DECLARATION` | a declaration was added, removed or hidden behind a comment |
| `E-CALLER-EFFECT` | a row gained an effect the host did not allow |

It rechecks the whole program with every replacement in place, then writes each file beside itself and renames it into place, putting every file back if one rename fails, so the project holds all the changes or none. Nothing is built or tested, and the result says so.

## Plan edits

Tuning a function should not mean rewriting it. A plan edit (`cairn.plan/1`, `agent/plans.py`) lets the agent change only how one function's regions are scheduled. The packet lists the [plan items](concurrency.md#plans) its regions take, their ranges, the current plan and its predicted cost.

```json
{"protocol": "cairn.plan/1", "session": "<the packet's digest>", "items": {"grain": 1, "lanes": 8}}
```

A reply names items and whole numbers, never source text, so nothing else can ride along. The host writes the plan, rechecks the program, and requires every function's receipt to be what it was apart from the plan. It refuses an item the regions do not take or a value out of range (`E-PLAN`), anything else in the reply (`E-REQUEST`), and a session already spent or reopened (`E-SESSION`). A plan sent to an edit host, or a body to a plan host, is `E-REQUEST`. A plan changes no result, so an admitted plan needs no test to be correct, only a measurement to be worth keeping.

## Named choices

A host can instead ask for named expressions, and check them against a reference:

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

The reply maps each slot to an expression string. `fill_json` refuses duplicate keys, extra fields, non-string values, text that would escape its slot, stale bindings and any choice the compiler refuses. The binding between a reply and its session lives in one host process: a saved JSON map is not an approved patch on its own.

Types cannot tell `x+y` from `add_wrap(x,y)`, which differ only at overflow, so for the scalar fragment the checker asks Z3 for an admitted input on which the candidate and the reference differ, or on which only the candidate aborts. A distinguishing input is replayed in an independent Python interpreter before it is shown to the model. Code outside the SMT model (owners inside records, recursion, tasks, lanes, the device, closures, `dyn`, the foreign boundary) goes to finite tests, and anything the model cannot decide is `unknown`. The reference can misstate what a person wanted, and a nontrivial `--assume` is a precondition on callers that the native build does not check.

```sh
python3 tools/ai/sketch_demo.py
python3 tools/checks/semantic_check.py examples/sketch/reference.cairn examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/checks/build_library.py examples/sketch/after.cairn
```

The receipt records the reference and candidate hashes, the domain, the query hashes and the Z3 version, and `--obligations` saves the SMT-LIB queries so anyone can rerun them.

## Training material and trials

`tools/corpus/` holds hand-written teaching material in the protocol's format: 40 tasks in 14 algorithm families, each with an equivalent and an inequivalent implementation, 28 preference pairs, and 29 executed repair transcripts. Its answers ship, so none of it is a held-out test. Solver timeouts, failed translations and tool errors never become positive labels, and edits that weaken a signature or empty a domain are not rewarded.

Two experiments are designed. [tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md) compares the focused and component packets on twelve planted repairs, and has not run. [bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md) gives ten tasks to fresh model subjects in CAIRN, C++ and Rust at equal budgets, with results under `evidence/v0_9/ai_benchmark/`. Only the second compares languages, and neither tests other model families or large programs.

## Closed generator contracts

The dependency packet for editing the existing family and wire examples. It is neither the full language card nor a proof certificate. `compiler/expansion.py` expands both, `std/wire.cairn` is the wire recipe and `verify/linear_certificates.py` checks the collector; changing any of them needs a larger audit context.

family/1. A static function with one natural parameter K and family prefix=base[a..b] produces b-a independent monomorphic functions prefix_a through prefix_(b-1). Each uses its literal K. Empty, negative, reversed, oversize, colliding, or unbound expansions are rejected. All emitted functions are checked. There is no dynamic dispatch, allocation, or evaluation of source strings. Increasing the range increases object size and compilation work. Expansion is capped at 1024 per family, 2048 functions total and 200000 AST/check visits.

wire/1. A record of fixed unsigned fields derives encode, decode, and byte-length functions. Field order is declaration order, each field little endian, no wire padding. Native record layout is not the wire layout. Encoded size is the sum of field widths divided by eight. Every field bit is retained. The buffer caller must supply the exact live extent; guards cannot recover a forged pointer's provenance. No tags, checksums, semantic ranges, alignment padding, implicit versions, or allocation are added.

bounded-collector/1. On immutable extent n with output capacity n, visit indices 0..n in order. Evaluate a read-only predicate once per input. Evaluate a read-only projection only for selected inputs and append it once. Never read output through the predicate or projection. The private cursor k starts at zero and satisfies k<=i at the start of iteration i. Thus every output store has k<n and final k<=n. Nonselected tail bytes/elements are unchanged. Inputs must not alias output; entry checks enforce interval disjointness under the FFI precondition. No worker threads, buffering, reordering, vectorization guarantee, or hidden heap allocation are part of this contract.

## The named-choice card

What the model is told when the host asks for named expressions instead of a whole function:

Read the host packet: task, source, named slots, expected types, local bindings, allowed effects and selected language cards. Return only one JSON object mapping EVERY slot name to a CAIRN expression string. No Markdown, extra keys or duplicate keys. Example reply: {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses, then rechecks the complete module. Do not rewrite function signatures, task, reference, permissions, preconditions, compiler settings or tests. Missing context is not permission to invent a dependency. Slot names are descriptive identifiers, not code or persistent IDs. The host binds your reply; do not invent hashes.

Use familiar expressions, but preserve CAIRN semantics: fixed-width integers; ordinary +,-,* trap on overflow; explicit unsigned add_wrap/sub_wrap/mul_wrap; checked casts and shift counts; sequential loops; no implicit allocation. A type error asks for a local type repair; keep the public API. A semantic counterexample gives an input where your proposal returns a wrong value or traps. Fix the algorithm on the original domain. Do not patch only that example.

Typed means only the static checks passed. The optional scalar checker compares with a fixed host reference for all declared-width inputs under a total, nonempty host precondition. smt-equivalent trusts the translator and Z3; it is not a Lean proof or native-code proof. It models integers, bools, floats, records, sums, branches, acyclic calls, array views and their parts, fixed and function-local storage, `compact`, host `reduce` and loops it can unroll, but not an owner held inside a record or an array, recursion, concurrency or GPU execution. Unknown/timeout is never accepted. Tests and benchmarks are separate stages. Supplying this card trains no model and evaluates none.

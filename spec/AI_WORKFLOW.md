# CAIRN 0.4: using the agent's existing skills without trusting its guesses

The design target is successful, independently checked changes per total budget,
not the shortest possible string. The model chooses an algorithm or expression;
the compiler reconstructs routine structure, rejects unauthorized changes, and
checks what it can. Familiar syntax and Python tooling are hypotheses about
transfer from pretraining, not evidence that a particular model is proficient.
No model weights were changed and no fresh-model trial ran in this revision.

## The host chooses the unit of reasoning

Use an ordinary full-function edit when the algorithm's structure must change.
Use one or more named expression slots when the decision really is local. Slots
must be nonoverlapping and resolve against the exact original source. They are
not a mechanism for concealing missing dependencies. The packet still includes
the same bidirectional call-graph source component and selected language cards
as the existing editor, plus expected types and lexical bindings at each slot.
An attached scalar contract discloses its full reference source, input domain and
trap policy. These are read-only task facts. The model must not guess a hidden
meaning or substitute an easier reference.

A trusted host can prepare the following sketch in Python:

```python
from sketches import Sketch, ScalarContract

source = "fn average(x:u64,y:u64)->u64 { return (x+y)/2; }"
reference = """fn average(x:u64,y:u64)->u64 {
  return (x/2)+(y/2)+((x%2+y%2)/2);
}"""
sketch = Sketch(
    source, "average",
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

Run host scripts with `PYTHONPATH=compiler` from the repository root. The model
normally returns data, not Python to execute. Python is a familiar orchestration
API for a trusted author; it is not a secure sandbox for arbitrary model code.
`fill_json` rejects duplicates, nonfinite JSON, extra fields, non-string choices,
syntax injection, stale bindings and choices outside the compiler's rules.

A data-only response need not echo long hashes. Identity, source, slot map and
reference remain bound in the host. That is a local-session transport rule, not
a signed authorization protocol. A saved JSON map is not a standalone approved
patch. Distributed or multi-tenant integrations require authenticated session
routing and OS-level isolation, neither of which is supplied here.

## Behavior feedback is stronger than compiler feedback

`x+y` and `add_wrap(x,y)` may have identical types while differing at overflow.
For the implemented scalar fragment, the checker asks whether any valid input
makes the candidate return something different from the fixed reference, or
abort when the reference returns. A distinguishing input becomes feedback only
after replay in the independent Python arithmetic interpreter.

This avoids treating compiler acceptance as correctness. It also avoids asking
the model to invent a plausible test result. The 40 negative curriculum cases
were additionally replayed through instrumented Clang and GCC builds. General
interactive checks do not automatically perform native replay.

The reference is an executable definition supplied by the host. It may itself
misstate the human's intent. The checker cannot solve that authority problem.
The default rejects references that can trap on admitted inputs, preconditions
that trap, and empty input domains. A deliberate partial contract can opt into
return-versus-abort equivalence. The model cannot change that policy.

Arrays, loops, floats and asynchronous code remain on the existing finite-test
path. Unsupported symbolic work returns unknown. Do not treat that result as a
proof or silently replace the task by the supported scalar subset.

## Counterexamples become persistent regression facts

`solve_finite` searches a caller-supplied finite list or product of expression
choices. It typechecks every candidate. Previously found counterexamples can
reject later candidates without another solver call. They can never accept a
candidate: acceptance always uses a fresh all-width semantic check.

The included average demo examines four authored candidates. With cached
witnesses it makes fewer semantic-checker calls in the recorded run, but the
search is deterministic test plumbing, not an AI success rate. A solver's choice
of witness can change the savings. No optimal candidate-ranking rule is claimed.

```sh
python3 tools/sketch_demo.py
python3 tools/semantic_check.py examples/sketch/reference.cairn \
  examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/build.py examples/sketch/after.cairn
```

The semantic status is `smt-equivalent`, not `Lean-verified`, `native-verified`,
or `fastest`. Reference and candidate hashes, model profile, domain, query hashes
and Z3 version accompany the receipt. Saved SMT-LIB obligations allow independent
re-execution; no proof certificate is reconstructed in Lean. A nontrivial
`--assume` is a caller precondition, not a guard emitted by the native builder.
The builder does not use this receipt to remove checks, and the equivalence
result makes no claim outside that domain. The average example uses `true`.

## Training material must match the real tool protocol

The new semantic curriculum has 40 same-signature, same-domain tasks in 14
algorithm families. Each has an equivalent implementation and a type-correct
inequivalent one. Twenty-eight preference pairs are training material; twelve
prompts belong to four proposed evaluation families. Width variants do not count
as distinct algorithms. All audit answers ship, so these are not secret tests.

Twenty-nine applicable tasks also have executed named-choice repair transcripts.
Twenty are training targets and nine are evaluation prompts. The SFT export puts
the failed proposal and checker feedback in user context and includes only the
correct JSON reply as an assistant target. It does not accidentally teach the
wrong assistant turn. These are authored, executed fixtures, not transcripts of
an actual LLM. Raw preference and protocol exports are provider-neutral records,
not a promise that any provider's training endpoint accepts the metadata as-is.

Keep API-changing language lessons from 0.3 separate from these repair targets.
Rewarding an agent for making a signature weaker or choosing an empty domain
would train it to defeat the task rather than solve it. Solver timeouts, failed
translations and tool errors must never become positive labels.

## What to evaluate next

Freeze models, task semantics, inference budget, tool budget and native workload.
Compare full-source edits, 0.3 expression packets, named choices, and named
choices plus scalar feedback. Give C++ and Rust comparable scoped-edit and solver
tools; otherwise a workflow gain will be misreported as a language gain. Use
independently authored algorithm/application families, not these shipped answers.

Report first-attempt and bounded-repair functional success, unauthorized changes,
regressions in callers, total model-token usage including failed attempts and
history, solver/compile time, and measured native performance. Separately test
models before and after any real fine-tuning. Byte-token transport measurements
and synthetic repair logs are not replacements for that study.

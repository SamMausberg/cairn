# Edit context, whole scripted tasks

No model took part in anything here. Every number counts authored transcripts, and says nothing about whether a model solves a task or how many turns it needs.

`tools/ai/measure_context.py` picks the first six editable functions of each of five programs (`examples/apps/kvstore`, `analytics`, `service`, `simulator` and `examples/systems`), thirty tasks in all. Each task is one authored transcript: the packet, a reply with a type error, its diagnostic, and the function's own body as the correct reply, with its admission. Under a focused packet the transcript also expands the first callee written in the program, because a real agent reads a body before it relies on one. Every message is counted once, and again as a model rereads it: the whole conversation so far on every turn it reads. Tokens are OpenAI's `o200k_base` encoding, which is one real BPE vocabulary and not Claude's tokenizer.

Cold means a new host for every task. Warm means one host and one conversation per program, so each rule card and each entry of the terms is sent once, and the rereading column charges the whole growing conversation on every turn, which is what a model without prompt caching pays.

## Before and after the packet rework

`before.json` was taken on commit `9f409f2`, before packets, cards and diagnostics were reworked. `after.json` was taken at the commit that adds this table, on the same program sources: `git archive 9f409f2 examples | tar -x -C DIR`, then `measure_context.py --programs DIR`. The same sources isolate the protocol from the language changes that shortened the examples in between.

| Setting | Tokens once, before | after | ratio | With rereading, before | after | ratio |
|---|---|---|---|---|---|---|
| component packet, `cairn.edit/1`, cold | 293,942 | 299,094 | 1.018 | 853,986 | 869,818 | 1.019 |
| component packet, `cairn.edit/2`, cold | 284,436 | 286,353 | 1.007 | 833,294 | 846,261 | 1.016 |
| component packet, `cairn.edit/2`, warm | 223,876 | 220,768 | 0.986 | 2,418,626 | 2,400,990 | 0.993 |
| focused packet, `cairn.edit/2`, cold | 74,233 | 72,086 | 0.971 | 223,654 | 225,163 | 1.007 |
| focused packet, `cairn.edit/2`, warm | 46,792 | 39,318 | 0.840 | 565,008 | 497,136 | 0.880 |

Under a warm focused host, by kind of message:

| Kind | Before | After |
|---|---|---|
| packets | 31,613 | 28,550 |
| diagnostics | 3,906 | 2,280 |
| admissions | 4,145 | 1,350 |
| expansions | 1,550 | 1,560 |
| replies | 5,578 | 5,578 |

The saving is in what a host says once. The terms of a packet (scopes, limits, boundaries, what each evidence class and a refusal and an admission mean) go once per host, each entry once, so an admission shrinks to its status, symbol, row and check sites and a refusal to its code, message, place and fix. Eight representative refusals (`diagnostic_sizes`) went from 886 tokens to 602.

A cold packet grew. It now carries the terms and each callee's evidence class, and the rule cards grew from 4,163 tokens to 4,370: they name the diagnostic code of each rule they state, correct what they said wrongly about sums and `try`, and describe the bare variants and compound assignment that were added to the language meanwhile. A cold focused packet averages 2,021 tokens, where it averaged 1,947.

## Resuming instead of rereading

`resume` sets the whole warm focused conversation of each program beside the `cairn state` object that describes the same program: every function by module with its signature and effect row, its types, diagnostics and admitted edits.

| Program | Conversation of six tasks | State |
|---|---|---|
| `examples/apps/analytics` | 8,903 | 4,138 |
| `examples/apps/kvstore` | 8,458 | 1,410 |
| `examples/apps/service` | 7,869 | 1,019 |
| `examples/apps/simulator` | 6,374 | 499 |
| `examples/systems` | 7,714 | 428 |

The two do not hold the same things. The conversation holds the cards and the bodies the agent read, and the state holds every function's interface, so an agent that resumes from the state still needs a packet for its next edit. What it no longer carries is the transcript of the edits already made.

`context.json` is the same run on the current tree's programs. `measure_context.py --quick` runs two programs of two tasks each; it shows the shape of the output and is not a record.

## What this does not show

That a model given any of these packets solves as many tasks, needs no more repair rounds, or relies on an `smt-equivalent` reference where it would otherwise have read the body. A real agent may expand more than once, and every expansion shrinks the focused packet's saving. Only a trial with model subjects can measure that, and none has run: `tools/ai/protocol_trial.md` is preregistered and waits on fresh subjects.

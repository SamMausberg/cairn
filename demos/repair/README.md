# Repair: an agent fixes a bug, and cairn diff reviews it

`latency.cairn` prints a latency report. Its `bucket` function has a planted bug: a sample of exactly 100 us is counted as under 100 us, so the report's two counts of fast samples disagree and the program exits 1. An agent fixes it through the edit host, then tidies `clamp` under a contract that says the tidy-up must not change what `clamp` does. A reviewer then compares the two programs with `cairn diff`.

```sh
make demo-repair                                    # or: python3 demos/repair/run.py
```

## What happens

The host opens a session on `bucket` and sends the agent a packet of 4,687 bytes: the function's source, the signature and effect row of its caller `main`, three rule cards, the bug report and four public test cases. The agent sees nothing else of the program.

The agent's first reply adds a debug print to `bucket`. The host refuses it, because printing adds effects that `bucket` did not have, and says what brought them:

```text
[bucket] host: refused E-EFFECT-EXPANSION: Candidate exceeds its effect ceiling.
    added_effects: ffi:write, io
    repair_hint: Remove what brings ffi:write: the foreign call write; io: a foreign call declared io. The ceiling is the host's.
```

The second reply fixes the comparison. The host admits it, runs the four public cases, then five hidden cases the agent never saw. All nine pass.

The host then asks for a shorter `clamp` under the contract `preserve: equivalent`. The agent's first try, `min(max(x, lo), hi)`, reads well and is wrong whenever `hi < lo`. Z3 finds such an input, the host replays it on both versions, and the refusal names it:

```text
[clamp] host: refused E-PRESERVE: The host asks this edit to keep clamp equivalent; it is behavior-changed.
    repair_hint: At x = 0, lo = 16, hi = 12 the function returns 16 and the edit returns 12: keep that answer.
```

The second try keeps the `lo` test first and is admitted as `smt-equivalent`. The fixed program runs and exits 0, and the reviewer runs `cairn diff`:

```text
$ cairn diff demos/repair/latency.cairn results/demos/repair/latency_after.cairn
demos/repair/latency.cairn -> results/demos/repair/latency_after.cairn: 2 identical-code, 1 smt-equivalent, 1 behavior-changed, 1 unknown
  behavior-changed  bucket  at us = 100: before returns 0, after returns 1 (native: clang++ and g++ agree)
  unknown           main    Expression form 'str' is not modeled. Its own code is identical; something it calls changed.
  smt-equivalent    clamp   Z3 found no input on which they differ
predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): bucket x1.0, clamp x0.735, main x1.028
semver: major: bucket behaves differently at us = 100
  these public functions are unproven, and cannot raise it further: main
```

The two `identical-code` functions are `count_below` and `percentile`, and `main` is `unknown` because the value model has no strings. No effect row, guard count or signature moved, so the diff lists none. The bump is major because a public function now behaves differently, which is what a bug fix is.

## A live model

`python3 demos/repair/run.py --live sonnet` sends the same packets to a model through `claude -p` and writes its replies where `--replay` reads them. One such run, claude-sonnet-5 on 2026-09-23 for 0.05 USD, is `live-sonnet-5.json`. It fixed `bucket` on its first reply. For `clamp` it first sent the whole declaration where the body goes, and the host refused it:

```text
[clamp] host: refused E-PARSE: Expected '{', found 'fn'.
    repair_hint: Use braces, semicolons and CAIRN's grammar, not Rust's or Python's.
```

Its second reply was the body alone, reformatted, which the host admitted as `identical-code`. `python3 demos/repair/run.py --replay demos/repair/live-sonnet-5.json` plays that run back. One run of one model on one task says nothing about how often models succeed.

## What is verified and what is not

Every refusal and admission, the finite tests, the Z3 queries, the native replays and the diff are computed on every run, and `tests/projects/test_demos.py` checks each outcome above. The default agent is `scripted.json`: replies written by hand to show one refusal of each kind, which no model wrote. `smt-equivalent` holds within the value model that [verification.md](../../docs/verification.md#value-level-source-equivalence) describes, and it trusts the translator and Z3. The predicted ratios come from `cairn predict`, and nothing was timed.

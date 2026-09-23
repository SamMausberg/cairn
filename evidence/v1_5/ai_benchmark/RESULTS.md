# Equal-budget benchmark of CAIRN, C++ and Rust: results

Run on 23 September 2026 as preregistered in [bench/ai/PREREGISTRATION.md](../../../bench/ai/PREREGISTRATION.md), committed first at b6e53c0. The primary run is complete: two replicates of the thirty cells, 60 subjects of `claude-sonnet-5` at effort `high`, every one reported. [RUN_NOTES.md](RUN_NOTES.md) lists every event that was not a subject's own result, and the deviations are below.

## Answer

Every subject solved its task. That is 20 of 20 in CAIRN, 20 of 20 in C++ and 20 of 20 in Rust, each program passing its hidden check under the sanitizers or Rust's debug checks. No cell is discordant, so the exact McNemar test gives p = 1.0 for every pair of languages. Under the preregistration's rule the only sentence this run supports about tasks solved is: **this run cannot tell the languages apart by tasks solved.**

The cost differed. Over the 20 paired cells, CAIRN subjects used 11.6 times the total tokens of C++ subjects and 12.3 times those of Rust subjects. The per-cell ratio has a median of 7.0 against C++ (range 1.5 to 50.3) and 8.2 against Rust (range 1.7 to 50.9). C++ and Rust were close, at 1.06. These ratios describe this run, not other tasks.

| language | subjects | solved | total tokens | output tokens | cost (USD) | median turns | median seconds | compile runs (flagged failed) |
|---|---|---|---|---|---|---|---|---|
| CAIRN | 20 | 20 | 34,667,459 | 424,344 | 15.48 | 30.5 | 204 | 162 (21) |
| C++ | 20 | 20 | 2,977,636 | 77,761 | 1.88 | 8 | 34 | 25 (0) |
| Rust | 20 | 20 | 2,816,890 | 78,008 | 1.86 | 8 | 29 | 26 (0) |

Total tokens are the platform's own count: fresh input, cache writes, cache reads and output. Cache reads dominate them, because a longer session re-reads a longer context on every turn. On narrower measures the gap is smaller: CAIRN subjects wrote 5.5 times the output tokens of C++ subjects and cost 8.2 times as much.

No final program failed a sanitizer, a guard or a Rust check, because every one passed. The preregistration forbids reading safety into these counts, and there is nothing to count.

`tables_primary.md` has every subject's row. `results_primary.json` has the same data with the paired comparisons. `subjects/` holds each subject's final program and record, and each record names its transcript by path and sha256.

## Where the CAIRN tokens went

This section is a description, not a preregistered measure. It comes from `scoring.breakdown` over the transcripts.

- **Reading the documentation.** CAIRN subjects made 157 calls that read `docs/`, 28 percent of their calls. Those calls returned 1.15 million characters, 76 percent of everything the tools returned to them. The C++ and Rust subjects had no documentation and read none.
- **More turns, and more compiling.** CAIRN subjects took a median of 30.5 turns against 8, and ran the compiler 162 times against 25 and 26. The transcript heuristic flags 21 of those runs as failed; it flags no C++ or Rust run.
- **Diagnostics seen outside the documentation.** `E-LITERAL-RANGE` 9, `E-EFFECT-ORDER` 5, `E-TYPE-MISMATCH` 3, `E-WRAP-TYPE` 1, `E-OWNER-EXTENT` 1, `E-VIEW-ALIAS` 1. All nine `E-LITERAL-RANGE` came from the two `chunk_sums` subjects writing the minimum of `i64`: neither `-9223372036854775808` nor a constant `0 - MAX - 1` is accepted, so each wrote a function that computes it or rearranged its overflow test. The reference solution had to do the same. That is a gap in the language, not in the task.
- **The most expensive cell.** `chunk_sums` in replicate 1 took 63 turns and 6.8 million tokens in CAIRN, against 8 turns and 0.14 million in C++. That cell gives the 50 times ratio.

## Pilot and secondary run

The pilot (`tables_pilot.md`) ran `dedupe` and `records` once in each language before the preregistration was committed. All six subjects solved their task. It is reported apart and counted nowhere.

The preregistered secondary run, the thirty cells once on `claude-opus-5-5`, **was not performed**. The owner stopped the campaign to keep usage low after the primary run finished. The chain had already started it. One subject finished (`histogram` in CAIRN: solved, 12 turns, 224,137 tokens, 0.32 dollars, 56 seconds; `tables_secondary.md`). A second (`histogram` in C++) was stopped after 28 seconds, before it produced a closing record, and is kept on file as `secondary/r1/histogram/cpp/infrastructure-0.json` under `results/`. No sentence here rests on the secondary run. The one finished subject used a tenth of the tokens of the two primary CAIRN `histogram` subjects (2.2 and 3.5 million). With a single subject, that difference cannot be told apart from chance.

## Audit

Nothing the audit found shows a subject reading task material outside its sandbox or using the network. It flagged 12 items in four subjects, and each was decided and recorded in `audit_decisions.json`. None is contaminated. The flags were:

- six reads of the machine's thread, process and memory limits under `/proc` and `/sys/fs/cgroup`, by the two CAIRN `split_sum` subjects while they checked how many threads they could start;
- five shell expressions the path pattern misread: `"$EXE"/solution` and `"$DIR"/program.cpp` inside the subject's own sandbox, and a `sed '/h_check/d'`;
- one number written as `~1000000`.

The audit rule changed three times during the run. Each change is in `RUN_NOTES.md`, and the report ran the audit again over every transcript under the final rule.

## Deviations from the preregistration

- The secondary run was not performed, as above.
- The chunk_sums CAIRN subject of replicate 1 was judged after the harness stopped on an audit bug, from its saved transcript and program. It was not rerun, and its wall time is the platform's `duration_ms`.
- From the middle of replicate 1 the owner's device tests ran beside the benchmark in another checkout. Wall times after that point were measured on a busier machine. Tokens, turns and cost do not depend on machine load.
- `results/` is not tracked, so the transcripts stay on the machine that ran them (`results/ai_benchmark/`), named here by sha256.

## What this does not show

The tasks were too easy to separate the languages by solve rate: the model solved all 60. A harder set, larger programs, or a smaller budget would be needed to see a difference in tasks solved, and this run shows none. The model has seen a great deal of C++ and Rust and has never seen CAIRN. CAIRN subjects paid to learn the language inside the budget, and this run cannot say what that cost would be for a model that already knows it. One model family wrote the language, its documentation, the tasks, the oracles, the bug reports and this report, and one model family was measured. The hidden checks are finite tests. Subjects were audited from their transcripts, not isolated by the operating system.

## What the README may say

At equal budgets on ten small systems tasks, `claude-sonnet-5` solved every task in all three languages (20 of 20 each in CAIRN, C++ and Rust), so this run cannot tell the languages apart by tasks solved. CAIRN subjects, who had never seen the language and read its documentation inside the budget, used 11.6 times the tokens of the C++ subjects and 12.3 times those of the Rust subjects (`evidence/v1_5/ai_benchmark/`).

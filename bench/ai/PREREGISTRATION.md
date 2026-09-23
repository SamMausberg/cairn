# Equal-budget benchmark of CAIRN, C++ and Rust: preregistration

This file fixes the design before any counted subject runs. It is committed first, and the commit's time is the proof of order. Anything that changes afterwards is reported as a deviation beside the results in `evidence/v1_0/ai_benchmark/`. The harness is `bench/ai/harness.py`.

## Question

Given the same small systems tasks and the same budget, does a model solve as many in CAIRN as in C++ and in Rust, and what does each language cost it in tokens, turns and time? When a program fails, does it fail by giving a wrong answer or by a safety failure the judged build catches?

The model has never seen CAIRN and reads its documentation inside the budget. It has seen a great deal of C++ and Rust. That difference is part of what is measured, not corrected for.

This is not a test of CAIRN's edit protocol (packets, rule cards, `cairn inspect`): every subject works through the ordinary command line of its language. It is not a test of large programs either.

## Tasks

Ten tasks, each a whole program that reads standard input and writes standard output, so one Python oracle and one set of hidden cases judge all three languages. Each task directory under `bench/ai/tasks/` holds `SPEC.md`, and a starter and a reference program in each language.

| task | kind | what it exercises |
|---|---|---|
| `histogram` | implement | counting on several threads without a race |
| `chunk_sums` | implement | a task per chunk, and signed sums that report overflow instead of wrapping |
| `records` | implement | a parser with typed errors, columns and range checks |
| `pool` | implement | owned buffers moved between slots, and handles that go stale |
| `rle` | implement | a decoder bounded by its output capacity, and wrapping hash arithmetic |
| `varint` | repair | a read past the end of the input |
| `split_sum` | repair | chunk bounds that drop the remainder, on several threads |
| `dedupe` | repair | an unsigned count that goes below zero on empty input |
| `basis_points` | repair | an intermediate product that overflows 64 bits |
| `csv_field` | repair | a scan past the end of the last row |

An implement task's starter is scaffolding in each language: a helper that reads standard input and an empty `main`. CAIRN's starter also holds a small tokenizer (`next_u64`, `next_i64`), because its library has no ready-made one for standard input and the plumbing is not what the task measures. A repair task's starter is a working program with one planted defect, the same defect in every language, and its SPEC.md carries a bug report that names the symptom and one failing input, not the fix.

The hidden cases come from each task's generator in `bench/ai/tasks.py` under a fixed seed, plus the SPEC.md example: 180 cases over the ten tasks, including large inputs and edge cases. Every case follows the input rules the subject is given (`tasks.VALID`). Before any subject ran, `harness.py verify` showed every reference passing its hidden check and every starter failing it, in all three languages: 70 of 70 as required (`results/ai_benchmark/verify.json`, copied into the evidence).

## The hidden check

A case passes when the program exits 0 and its standard output is byte for byte the oracle's. A task is solved when every case passes in every judged build.

- C++: `clang++ -std=c++20 -O1 -g -fno-omit-frame-pointer -fno-sanitize-recover=all -pthread -fsanitize=address,undefined`, run with leak detection on.
- CAIRN: `cairn build`, then its generated C++ built again from the command line its receipt records, at `-O1` with the same sanitizer flags. A failed guard is a trap, so it fails the case.
- Rust: `cargo build` in the debug profile, with overflow checks and debug assertions. A panic fails the case.
- A threaded task (`histogram`, `chunk_sums`, `split_sum`) is also built with `-fsanitize=thread` for C++ and CAIRN, and its source must contain a thread construct (`std::thread`, `std::jthread`, `std::async` or `pthread_create`; `thread::spawn` or `thread::scope`; `spawn` or `parallel`). No Rust sanitizer is installed, so Rust and CAIRN programs may not contain `unsafe`, and one that does fails.

Every subject is told these rules, in the same words but for the paragraph about its own build (`subjects.task_md`).

## Subjects

Each subject is a fresh headless Claude Code session (version 2.1.280): `claude -p "Read TASK.md in this directory and do what it says."` in a sandbox directory outside the repository, with `--restricted`, the tools `Bash, Read, Write, Edit, Glob, Grep` and no others, `--permission-mode dontAsk`, `--strict-mcp-config`, `--disable-slash-commands` and `--no-session-persistence`. No memory, CLAUDE.md, AGENTS.md, skill, MCP server or web tool reaches it. The environment of the parent session is removed. The transcript is kept as JSON Lines.

A sandbox holds `TASK.md` and the starter in its project layout (`main.cpp`; `Cargo.toml` and `src/main.rs`; `cairn.toml` and `src/main.cairn`). A CAIRN sandbox also holds `docs/`: the guide, the five reference files, the library guide, the command-line reference and the generated library API, copied from the checkout at the commit that ran. CAIRN is installed for the subjects from a wheel of that checkout into a virtual environment of its own, and the judge uses the same installation. `clang++`, `g++` and offline `cargo` are on every subject's path.

The model is `claude-sonnet-5` at effort `high`. A mid-size model shows the difficulty of a language more than the largest one does, and costs less per subject, so the run can afford a second replicate.

## Budget

The same for every subject: at most 80 turns, at most 5 US dollars of the platform's own cost count (`--max-budget-usd`), and 40 minutes of wall time, after which the session is killed. A subject that reaches a limit is judged on the program it left. The limits were set from the pilot, whose dearest subject (`records` in CAIRN) used 32 turns, 1.30 dollars and 513 seconds: the limits leave room for a task twice as hard in every language, and they bind on a subject that is stuck.

## Order and replicates

The primary run is two replicates of the thirty cells (ten tasks, three languages), 60 subjects. Replicate 1 runs every cell before replicate 2 starts. Within a replicate the tasks run in the order of the table above, and within task i the languages rotate by i, so no language always goes first. Subjects run one at a time, never in parallel.

After the primary run, a secondary run gives the thirty cells once to `claude-opus-5-5` at effort `high`. It is reported separately, and no sentence below rests on it.

Every subject that starts is reported. There are no reruns and no replacements, with one exception: a session the platform ends for its own reasons (a usage limit, an API error, a crash before a closing record) is an infrastructure failure, is kept on file, and the cell is run again. A session that reaches its turn, cost or time limit is not an infrastructure failure. If the platform's usage limit stops the run, the run stops, and what completed is reported as it stands.

## Pilot

Before this file was committed, two tasks ran as a pilot to debug the harness and to set the budget: `dedupe` and `records`, once in each language. All six pilot subjects solved their task, and the CAIRN subjects used 4.0 and 9.0 times the tokens of the C++ subjects (`evidence/v1_0/ai_benchmark/tables_pilot.md`). After the pilot the audit rule for scratch files under `/tmp` was narrowed, sandboxes were deleted after judging, and the documentation was pinned to one snapshot per run. No task, oracle or case changed. The CAIRN programs were put through `cairn fmt`, which moved comments in the `csv_field` starter and in three references and changed nothing else a program does, and the `records` CAIRN reference now writes one `else if` chain as three `if`s. Pilot subjects are labelled `pilot`, reported apart and never counted. Both tasks run again in the counted run with fresh subjects.

## Audit

The audit lists every path a subject named outside its sandbox, other than the compilers, their headers and libraries, `~/.cargo`, `~/.rustup`, the installed CAIRN toolchain and its own scratch files under `/tmp`, and every command that could reach the network. A path under the benchmark's root that is not the subject's own sandbox is flagged, so one subject reaching another's work is caught, and each sandbox is deleted as soon as its subject has been judged. Each flag is decided by a person and recorded with the reason in `audit_decisions.json`. A subject that read files outside its sandbox (other than the toolchains) or used the network is contaminated: it is listed with its numbers and left out of every count.

## Measures

Primary: tasks solved by the hidden check.

Secondary, from the platform's own closing record: total tokens (fresh input, cache writes, cache reads and output, over every model the session used), cost, turns and wall time. From the transcript: compile runs and the compile runs that failed. From the judge: why an unsolved program failed (construct, unsafe, build, output, exit, sanitizer, timeout).

## Analysis and what may be claimed

For each language: tasks solved per replicate and in total, the median and sum of each secondary measure, and each unsolved program's failure kind. Per task: every subject's numbers.

- "At equal budgets, subjects solved more tasks in CAIRN than in C++" (or fewer, or the same with Rust) may be written only if an exact two-sided McNemar test over the paired cells of the primary run (same task, same replicate) gives p < 0.05. Otherwise the counts are reported with the sentence "this run cannot tell the languages apart by tasks solved".
- The token cost of a language is reported as the ratio of summed total tokens between two languages, over all paired cells and over the cells both solved, with the per-cell ratios and their median. It is a measurement of this run, not a claim about other tasks.
- Safety failures are reported as counts by kind. No sentence says a language is safer than another from these counts alone.

## Known weaknesses

Ten small tasks, each a single file, with finite hidden tests. One model family, which also wrote the language, its documentation, these tasks, their oracles and their bug reports. The CAIRN starters carry a tokenizer the others do not need, and the CAIRN subjects are given documentation the others are not, which is the fair setting for a new language and still a difference. The subjects are audited from their transcripts, not isolated by the operating system. Only ThreadSanitizer, AddressSanitizer and UndefinedBehaviorSanitizer judge C++ and CAIRN, and Rust has no sanitizer here. Two replicates of ten tasks can show a large difference and cannot show a small one.

After the runs, the version names and evidence paths in this file were changed to the release numbering (1.0.0, with the earlier milestones as 0.8.0 to 0.8.3). Nothing else changed; the text each run followed is in git at the commit its record names.

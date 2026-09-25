# Where the 1.1 evaluation's CAIRN tokens went

The 1.1 evaluation's CAIRN subjects solved every task and cost 5.5 (plugin) and 8.2 (documentation) times the tokens of C++ per solved task ([ai_eval/RESULTS.md](../ai_eval/RESULTS.md)). This record takes their transcripts apart: what each request carried, what each tool call bought, every refusal the compiler gave and what it cost, and the first program each subject wrote that passes the hidden check. It ends with the causes, what each cost, and which ones this track fixes. No model ran for it.

## What was measured

The subjects are the 27 counted CAIRN subjects (14 documentation, 13 plugin) and the 14 C++ subjects of the same cells, from [ai_eval/subjects/counted](../ai_eval/subjects/counted). The 11 plugin subjects set aside for the harness failure add refusals to the refusal table and appear nowhere else. The 20 CAIRN subjects of the 1.0 benchmark, whose transcripts are in the owner's untracked `results/ai_benchmark/primary/`, are used for refusal counts only.

A session's total tokens are almost all context reads: output is under 1.5% of every CAIRN total, and each request reads everything before it. So a tool result costs its size at every later request. For each request the analysis takes the context the platform reported, splits the growth to the next request between the tool results that arrived (0.46 tokens per character, the fit over all 65 counted transcripts, capped at the growth) and the model's own writing, and carries each piece to the end of the session. The pieces add up to the session's context exactly. `subjects.json` holds each subject's numbers.

Every version of each program was rebuilt from the task's starter and the subject's `Write` and `Edit` calls; two subjects rewrote their source with `sed` or Python, and those commands were run on a copy. Each rebuilt final version matches the kept `main.cairn`. The versions were judged in order by the evaluation's own judge, `bench/ai/checking.judge`, with the evaluation's own compiler (dd3f75e), until one passed. The CAIRN judging ran the address and thread sanitizer builds on this machine; `block_scan` ran under `--emulate`, never on a GPU. The judging ran twice, once from scratch scripts and once from the tool, and agreed on every subject but one: the C++ `block_scan` subject's first version timed out once at a load average above 500 and passed when judged again.

A refusal is a diagnostic code in the compiler's own output: a `cairn check`, `build`, `run` or `test` of the subject's project or of a scratch program, or the plugin's `check` tool. Codes quoted in documentation the subject read are not counted. A refusal's round trip is the requests after it, up to and including the next check the subject ran.

`tools/ai/friction.py` does all of this; the last section gives its commands and the files they wrote.

## The first program that type-checked was correct

Every CAIRN subject's first program that `cairn check` accepted passed the hidden check, in both arms, all 38 subjects. Two C++ subjects needed a second version (a wrong output in `pool`, a build error in `tally`). So no CAIRN subject spent a token debugging a wrong answer: the tokens went to learning the language before writing, to refusals between the first draft and an accepted one, and to testing afterwards.

| arm | tokens | requests per subject | before the first edit | first edit to first pass | after the first pass |
|---|---|---|---|---|---|
| documentation (14) | 27.9M | 28.9 | 10.6 requests, 7.1M (25%) | 4.1 requests, 5.2M (19%) | 14.1 requests, 15.7M (56%) |
| plugin (13) | 17.2M | 28.9 | 11.2 requests, 5.2M (30%) | 3.6 requests, 2.6M (15%) | 14.2 requests, 9.4M (55%) |
| C++ (14) | 3.3M | 11.5 | 1.9 requests, 0.4M (11%) | 1.4 requests, 0.3M (9%) | 8.2 requests, 2.7M (80%) |

A documentation subject gained 48k tokens of context before its first edit, and a plugin subject 24k; a C++ subject gained 1.6k, the task. That reading is carried by every later request: 15.4M tokens (55%) of the documentation arm and 6.2M (36%) of the plugin arm. With the exploring requests themselves, learning the language before writing a line is 80% of the documentation arm's tokens and 67% of the plugin arm's, against 17% in C++. Testing after the first pass takes a C++-like number of requests, but each of them reads 60k to 110k tokens of context instead of 15k.

## What they read

The documentation arm read whole files. The tokens below are each file's results carried to the end of the session, over the 14 counted subjects.

| file | subjects who read it | tokens carried |
|---|---|---|
| `guide.md`, whole, first (TASK.md says to start there) | 8 | 2.73M |
| `concurrency.md` | 6 | 2.42M |
| `language.md` | 10 | 2.17M |
| `memory.md` | 5 | 1.67M |
| `library.md` | 5 | 1.29M |
| `std/io.md`, `std/text.md`, `std/vec.md` and four more module pages | 7 | 1.70M |
| every documentation read | 14 | 13.3M (48%) |

The plugin arm read the skill (4.0k tokens of context, loaded by 11 of 13, 1.32M carried, 7.7% of the arm), a few cards each (0.5M in all), and then searched the documentation for what the skill does not say. By the pattern searched and the file read, the plugin subjects looked for, in order of cost: the standard library's signatures (`library.md`, the module pages and `cairn doc --std`, which prints all twenty modules, 47 KB, even when given `--module`), Vec and Buf of records, reading and parsing input, tasks and groups, and the limits of `i64`. The three `chunk_sums` subjects spent 27 requests between them looking for the minimum of `i64` and a way to detect signed overflow, including one reading `roadmap.md`, which names the missing literal.

The plugin also puts 2.5k more tokens in front of every request than the documentation arm has (15.9k against 13.4k): the eight MCP tools (1.2k tokens by `o200k_base`) and the Skill tool. That is about 0.9M of the plugin arm.

## Refusals

| cause | subjects (counted) | refusals | round-trip requests | tokens |
|---|---|---|---|---|
| a writing call as the one operand of a conversion, `let k = usize(next_u64(inp));` (`E-EFFECT-ORDER`) | 14 (10) | 14 | 43 | 3.40M |
| the minimum of a signed type written as a literal or constant, `-9223372036854775807 - 1`, `0 - MAX - 1` (`E-LITERAL-RANGE`) | 2 (1) | 3 | 18 | 1.45M |
| a `Buf` passed where `[n]` is expected: its extent is `len(b)`, and the hint says only "use the expected type" (`E-TYPE-MISMATCH`) | 4 (3) | 4 | 13 | 1.32M |
| `let p0 = 0;` is a `u64`, later used as a `usize`; the message names a column, not the binding (`E-TYPE-MISMATCH`) | 1 (1) | 1 | 4 | 0.42M |
| no wrapping or checked arithmetic on signed integers (`E-WRAP-TYPE`) | 1 (1) | 1 | 7 | 0.32M |
| disjoint parts `p[0..BINS]`, `p[BINS..2 * BINS]` not seen as disjoint, the message says `partial[?..?]` (`E-LEASED`) | 1 (1) | 1 | 3 | 0.31M |
| two allocating calls in one record construction (`E-EFFECT-ORDER`) | 1 (1) | 1 | 3 | 0.28M |
| a part bound to a local (`E-VIEW-ALIAS`) | 1 (1) | 1 | 4 | 0.22M |
| `import std.io (println)` hides the builtin `println`; the refusal says "Expected usize, got ro<u8>[6]" (`E-TYPE-MISMATCH`) | 1 (1) | 1 | 3 | 0.19M |
| `if t == 255 { sums[b] = ... }` in a cooperative region not seen as one writer, while `t == 0` is (`E-COOP-GLOBAL`) | 1 (1) | 1 | 2 | 0.18M |
| `len(v)` of a Vec (`E-LEN`) | 1 (1) | 1 | 2 | 0.12M |
| `if` as an expression (`E-NAME`: "Expected an identifier, found 'if'") | 2 (0) | 2 | 5 | 0.12M |
| `cairn test` of a project with no test blocks exits as a refusal | 4 (3) | 4 | 2 | 0.09M |
| the `tally` repair task's planted race, then a Group not waited on every path (`E-LEASED`, `E-LINEAR-LEAK`): the checker doing its job | 1 (1) | 2 | 7 | 0.20M |

The 1.0 benchmark's 20 CAIRN subjects saw the same shape: `E-LITERAL-RANGE` 9 (all in `chunk_sums`), `E-EFFECT-ORDER` 5, a `Buf` extent against `[n]` 2, and one each of `E-OWNER-EXTENT`, `E-WRAP-TYPE`, `E-VIEW-ALIAS` and a `u64` literal used as a `usize`.

The first row is a correct program refused. `usize(next_u64(inp))` has one operand, so no other operand can run before or after the call, and the refusal buys nothing the next statement does not. The starters of eleven of the thirteen tasks hand the subject `next_u64(inp:rw<Input>) -> u64`, and most tasks read a count that indexes, so the natural line is `usize(next_u64(inp))`: 14 of the 38 subjects wrote it. The skill's core rules state the rule, and six plugin subjects who had loaded the skill still wrote it.

## After the first pass

A CAIRN subject tested as often as a C++ subject did, and spent about two requests more per subject on getting at the build the judge runs. Every counted documentation subject and 12 of 13 plugin subjects ran `cairn build` to find the executable or the generated C++; its JSON record is 54 KB for an 85-line program (most of it every library function's receipt), and for 14 of those 26 subjects the platform saved it to a file and showed a 2 KB preview, so the subject ran it again through `python3 -c` or `tail`. Then they found `program.cpp`, compiled it with each sanitizer as TASK.md states, and ran their tests again against those binaries.

One plugin subject (`split_sum`) spent 15 requests on a correct program that `cairn run` aborted with "Resource temporarily unavailable". `cairn run` caps the program's address space at 1024 MiB, and each thread reserves an 8 MiB stack and, when it contends for the allocator, a 64 MiB glibc malloc arena, so a 16-thread program fails to start a thread whenever enough of them get arenas of their own. It reproduces on today's main in 4 of 6 runs of that subject's program; the same binary under a 1024 MiB data limit instead passes 8 of 8.

## The causes

| cause | class | subjects | tokens, roughly | this track |
|---|---|---|---|---|
| learning the language before writing: whole reference files read up front, carried by every later request | a fact spread over several files | 27 of 27 | 22.5M documentation, 11.4M plugin | fixes: one first page of what a program needs, where each arm looks first (the guide's opening and the skill), with fewer words elsewhere; `cairn doc --std --module` printing one module |
| a writing call as the one operand of a conversion | a correct program refused | 14 (10 counted), 5 in 1.0 | 3.4M | fixes: a language change |
| the minimum of a signed type, and a way to detect signed overflow | syntax friction | 3 (2 counted), 2 in 1.0 | 1.5M in refusals, 1.9M in lookups | fixes the literal: a language change; a negated literal also stops carrying an overflow guard, so `fn f() -> i64 effects() { return -1; }` is accepted |
| getting at the judged build: a 54 KB `cairn build` record, then the generated C++ and the sanitizers by hand | tooling friction | 26 of 27 | about 2 requests per subject, 3.8M | fixes, last: `cairn run --sanitize address` or `thread`, and a short `cairn build` record on stdout |
| a `Buf` against `[n]`, a `u64` literal, `println` hidden by an import, `len(v)` of a Vec, a part bound to a local, `if` as an expression | a diagnostic that did not say what to change | 9 (6 counted), 4 in 1.0 | 2.4M | fixes: each refusal states the fix the compiler knows |
| `cairn run`'s address-space cap aborts a correct 16-thread program | a correct program refused, by the tool | 1 | 15 requests, 0.5M | fixes: cap the data segment instead |
| disjoint parts with constant-expression bounds | a correct program refused | 1 | 0.3M | fixes if time allows |
| a `t == 255` guard in a cooperative region | a correct program refused | 1 | 0.2M | FIXES, pull request #68 |
| the plugin's per-request tool definitions | fixed cost | 13 | 0.9M | not changed: the session tools serve other work |

## Replaying the checks

The CAIRN subjects checked 69 distinct programs, counting a check, build, run or test of an edited program once per text. Main at 3c4b9e3 refuses 30 of them, the refusals the subjects saw (`replay_main.json`). Each pull request of this track that changes a rule or a message runs the same replay and reports what it refuses and says. That measures the compiler on the programs the subjects wrote, not what a subject would do next.

## What this does not show

These are 27 subjects of one model family on small tasks, most seen once. The token split is an accounting of what each request read, not an experiment: removing a document would change what a subject does next, and nothing here reruns the evaluation. The topic of a search is read from its pattern and is approximate.

## Commands

```sh
git archive dd3f75e | tar -x -C /tmp/dd3f75e      # the evaluation's compiler
python3 tools/ai/friction.py judge --compiler /tmp/dd3f75e --output evidence/v1_1/friction/first_pass.json
python3 tools/ai/friction.py costs --first-pass evidence/v1_1/friction/first_pass.json --output evidence/v1_1/friction/subjects.json
python3 tools/ai/friction.py replay --compiler . --output evidence/v1_1/friction/replay_main.json
```

`judge` builds every version it judges under the sanitizers and took about half an hour at three jobs on a loaded machine; `costs` reads the kept transcripts in under a second, and `replay` checks 69 programs in about a minute. `subjects.json` holds each subject's requests, context, first edit and first pass, the tokens of each phase, the context carried from before the first edit, each documentation file's carried tokens, and every refusal with its line, cause and round trip, with the per-arm sums under `summary`.

# Equal-budget evaluation of CAIRN with its plugin: preregistration

This file fixes the design of the 1.1 evaluation before any counted subject runs. It is committed first, and the commit's time is the proof of order. Anything that changes afterwards is reported as a deviation beside the results in `evidence/v1_1/ai_eval/`. [PREREGISTRATION.md](PREREGISTRATION.md) is the 1.0 benchmark's, kept as history. The harness is `bench/ai/harness.py --study v1_1`.

## Question

What does one solved task cost a model in CAIRN with the Claude Code plugin, in CAIRN with the documentation alone, in C++ and in Rust, at equal budgets? Does it solve as many in each? When a program fails, is it a wrong answer or a safety failure the judged build catches?

The 1.0 benchmark gave every subject the same ten tasks. All sixty solved theirs, and CAIRN subjects, who read the documentation inside the budget, used 11.6 times the tokens of C++ subjects. It ran before the plugin existed. Since then the plugin gives the rules as a skill of rule cards, a refusal names its card and fix, and one check reports every independent refusal. This evaluation measures that CAIRN, adds a plugin arm beside the documentation arm, and adds three tasks where CAIRN's checks are the point.

## Arms

| arm | language | what the subject is given |
|---|---|---|
| `plugin` | CAIRN | the CAIRN plugin for Claude Code: its skill, its language server, `cairn mcp`, and the `cairn` command |
| `cairn` | CAIRN | the documentation in `docs/` of the sandbox, as in 1.0: the guide, the six reference files, the library guide, the command line and the generated library API |
| `cpp` | C++20 | nothing beyond the compilers, as in 1.0 |
| `rust` | Rust 2021 | nothing beyond offline `cargo`, as in 1.0 |

The plugin arm's plugin is installed from this checkout at the pinned commit. The toolchain step copies the plugin's manifest (`.claude-plugin/`), its skill (`skills/cairn/`) and the documentation the skill points to (`docs/`, which it names as `${CLAUDE_SKILL_DIR}/../../docs/`), and gives it a `bin/cairn` that runs the same installed wheel every CAIRN arm and the judge use. Nothing else of the repository is in it, so no task's reference, test or earlier result is reachable. Each plugin subject gets its own read-only copy beside its sandbox, loaded with `--plugin-dir` and readable through `--add-dir`, and deleted after judging. Its sandbox holds no `docs/`.

## Tasks

Thirteen tasks, each a whole program that reads standard input and writes standard output, judged by one Python oracle and one set of hidden cases in every language. The first ten are the 1.0 benchmark's, unchanged: `histogram`, `chunk_sums`, `records`, `pool`, `rle`, `varint`, `split_sum`, `dedupe`, `basis_points` and `csv_field` (PREREGISTRATION.md describes them). The last three are new, each with a SPEC.md, a starter and a reference in each language, an oracle and a generator in `bench/ai/checked.py`, and input rules every hidden case follows:

| task | kind | what CAIRN's checks have to say about it |
|---|---|---|
| `sieve` | implement | The primes up to 20,000,000 on several threads: their count, sum, largest and largest gap. The design that shares one sieve between threads races, byte by byte in C++ and word by word with `vector<bool>`; CAIRN refuses it (`E-PARALLEL-RACE`, `E-LEASED`), and the design that gives each thread its own segment needs a careful join for the gap that spans two segments. |
| `block_scan` | implement | Running totals of up to 100,000 values, as a GPU computes them: blocks of 256 threads that scan in a shared array, with a barrier between the steps, then each block's offset. CAIRN writes it as a cooperative region over `@device` arrays, and the judged build emulates the device on host threads (`cairn build --emulate --device-target sm_120`). C++ and Rust write it with host threads that meet at `std::barrier` and `std::sync::Barrier`. The scan that adds a neighbour's element in place, with one barrier a step, is the classic race; CAIRN refuses it (`E-COOP-REUSE`), and C++'s thread sanitizer reports it when it runs. |
| `tally` | repair | A program that tallies large values on `k` threads into one shared tally without synchronization. The C++ starter races (the thread sanitizer reports it, and counts are lost), the Rust starter can write the same design only with `unsafe`, and CAIRN refuses it (`E-LEASED`, so the CAIRN starter does not build). The tempting fixes keep the defect: a `volatile` counter in C++ still races, and a patched `static mut` in Rust is still `unsafe`. |

The judge requires more of two new tasks than their output. `sieve` and `tally` are threaded tasks as 1.0 defines them. `block_scan` requires `blocks ... threads`, `barrier` and `@device` in CAIRN, a thread construct and `std::barrier` or `pthread_barrier_wait` in C++, and a thread construct and `Barrier` in Rust. Each TASK.md says so in the paragraph about its language's build.

The hidden cases come from each task's generator under the fixed seed, plus the SPEC.md example: 236 cases over the thirteen tasks, 180 of them the 1.0 cases. Before any counted subject runs, `harness.py --study v1_1 verify` must show every reference passing its hidden check and every starter failing it, in all three languages and at the pinned commit: 91 of 91 as required (thirteen examples and 78 programs). Its record is copied into the evidence.

## The hidden check

As in 1.0, with two additions. A case passes when the program exits 0 and its standard output is byte for byte the oracle's, and a task is solved when every case passes in every judged build: C++ under `-fsanitize=address,undefined` with leak detection, CAIRN's generated C++ rebuilt the same way from its receipt, Rust's debug build with overflow checks, and a threaded task (`histogram`, `chunk_sums`, `split_sum`, `sieve`, `block_scan`, `tally`) also under `-fsanitize=thread` for C++ and CAIRN. Rust and CAIRN programs may not contain `unsafe`.

The additions: `block_scan` in CAIRN is built with `--emulate --device-target sm_120`, whose receipt's command carries `-DCAIRN_EMULATE=1`, and is rebuilt with the sanitizers from that command; and a failure is classified more finely than in 1.0, with an abort (a failed CAIRN guard, or a C++ abort) and a Rust panic apart from other nonzero exits.

## Subjects

Each subject is a fresh headless Claude Code session (version 2.1.281): `claude -p "Read TASK.md in this directory and do what it says."` in a sandbox directory outside the repository, with `--restricted`, `--permission-mode dontAsk`, `--no-session-persistence`, the platform's own limits below, and the environment of the parent session removed. The environment sets `ENABLE_CLAUDEAI_MCP_SERVERS=false` and `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1` for every arm, so no claude.ai connector and none of Claude Code's bundled skills reach any subject. No memory, CLAUDE.md, AGENTS.md or web tool reaches any subject.

- `cairn`, `cpp` and `rust`: the tools `Bash, Read, Write, Edit, Glob, Grep` and no others, `--strict-mcp-config` and `--disable-slash-commands`, as in 1.0.
- `plugin`: the same tools and `Skill`, the plugin's eight MCP tools allowed (`mcp__plugin_cairn_cairn`), and the plugin loaded as above. Its language server sends the compiler's diagnostics after each edit of a `.cairn` file. Claude Code 2.1.281 still lists two skills of its own, `design` and `doctor`, which the bundled switch does not remove; neither is about programming.

A sandbox holds `TASK.md` and the starter in its project layout, and the `cairn` arm's also holds `docs/`. TASK.md is the SPEC.md and then the same words for every arm but the paragraph about its build (`subjects.task_md`); the plugin arm's paragraph names the plugin where the `cairn` arm's names `docs/`, and its environment paragraph lets it read the plugin's files. CAIRN is installed for the subjects from a wheel of the pinned commit into a virtual environment of its own, and the judge uses the same installation. `clang++`, `g++` and offline `cargo` are on every subject's path.

The model is `claude-sonnet-5` at effort `high`, as in 1.0, so the arms compare with the 1.0 run.

## Budget

Each subject has the 1.0 limits: at most 80 turns, at most 5 US dollars of the platform's own cost count (`--max-budget-usd`) and 40 minutes of wall time, after which the session is killed. A subject that reaches a limit is judged on the program it left.

The counted run as a whole stops at 200 US dollars of the platform's cost count: once the recorded subjects of the counted phase reach it, no further subject starts, and what completed is reported as it stands. The 1.0 run spent 19.22 dollars on sixty subjects, 15.48 of it in CAIRN, and the pilot spent 4.25 on one cell of the hardest new task; this run's 156 subjects are expected to spend 70 to 120.

## Order, replicates and concurrency

The counted run is three replicates of the 52 cells (thirteen tasks, four arms), 156 subjects. Every cell of replicate 1 starts before any cell of replicate 2, and replicate 2 before replicate 3. Within a replicate the tasks run in the order of this file, the ten 1.0 tasks and then `sieve`, `block_scan` and `tally`, and within task i the arms `plugin`, `cairn`, `cpp`, `rust` rotate by i, so no arm always goes first.

Up to three subjects run at once, which 1.0 did not do. Each has its own sandbox, its own copy of the plugin when it has one, its own `/tmp` and its own judge's build directories. The session runs in a user and mount namespace of its own, as the same user, with a directory of its own mounted over `/tmp`, so a scratch file one subject writes to `/tmp` is never another's. Nothing that could hold an answer is shared: CAIRN builds into the sandbox, `cargo` is offline with no crates, the platform's saved tool output is named after each sandbox, and a sandbox, its plugin copy and its `/tmp` are deleted as soon as the subject is judged. The audit flags any path under the run's root that is not the subject's own. Wall time is measured on a machine running three subjects and other work, so it is the least comparable measure.

Every subject that starts is reported. There are no reruns and no replacements, except for a session the platform ends for its own reasons (a usage or rate limit, an API error, a crash before a closing record): that is an infrastructure failure, kept on file, and the run stops starting subjects. A stopped run continues from the cell that failed. If one cell fails for infrastructure three times, or the run has five infrastructure failures in all, the run stops for good and what completed is reported as it stands. A session that reaches its turn, cost or time limit is not an infrastructure failure.

## Pilot

Before this file was committed, one subject per arm ran on `block_scan` to debug the harness, three at a time, with the compiler, documentation and skill of main at 2679cb3 (`evidence/v1_1/ai_eval/tables_pilot.md`). All four solved it: the plugin subject in 48 turns for 1.39 dollars, the documentation subject in 38 turns for 1.42, the C++ subject in 39 turns for 0.94 and the Rust subject in 20 turns for 0.50. Pilot subjects are labelled `pilot`, reported apart and never counted, and `block_scan` runs again in the counted run with fresh subjects.

The pilot changed three things and no task, oracle or case. Its subjects shared the machine's `/tmp` and wrote scratch files there under names like `/tmp/big_input.txt`, which a subject running beside them could have read, so every counted subject gets a `/tmp` of its own. The count of diagnostics a subject saw now reads the MCP server's compact JSON, and token counts print as whole numbers.

## Audit

As in 1.0: the audit lists every path a subject named outside its sandbox, other than the compilers, their headers and libraries, `~/.cargo`, `~/.rustup`, the installed CAIRN toolchain, its own saved tool output and its own scratch files under `/tmp`, and every command that could reach the network. A plugin subject may also name its own copy of the plugin; another subject's copy is flagged, and so is any write into its own. Paths are read with `..` resolved, so the skill's `${CLAUDE_SKILL_DIR}/../../docs/` is the plugin's own documentation. Each flag is decided by a person and recorded with the reason in `audit_decisions.json`. A subject that read files outside what it was given or used the network is contaminated: listed with its numbers and left out of every count.

## Measures

Primary: cost per solved task, in dollars and in tokens. For an arm it is the sum over the arm's counted subjects of the platform's cost (or total tokens: fresh input, cache writes, cache reads and output, over every model the session used) divided by the number of those subjects that solved their task, so a failed attempt is charged to the tasks that were solved.

Secondary: tasks solved; turns and wall time, from the platform's closing record and the harness's clock; the safety failures the judged build caught, counted by kind from each unsolved program's verdict (a sanitizer report, an abort, a Rust panic, `unsafe` in the source); compile runs, including the plugin's MCP `check`.

## Analysis and what may be claimed

`bench/ai/analysis.py` computes everything below. A cell is one task in one replicate. Every interval is the 2.5th to 97.5th percentile of 10,000 bootstrap resamples of the 39 cells with replacement, under the seed 20260924. Resampling cells keeps the four arms on the same resampled tasks in each draw, and every arm and every ratio is computed from the same draws.

- For each arm: cost per solved task in dollars and in tokens, and the solve rate, each with its interval; the median and quartiles of turns and of wall time; the sum of cost and tokens; the safety failures by kind.
- For each pair of arms: the ratio of their costs per solved task, in dollars and in tokens, with its interval, and the exact two-sided McNemar test over the cells where both subjects count.
- "Arm X costs less per solved task than arm Y" may be written only if the interval of the ratio X/Y, in the unit named, lies wholly below 1, and "more" only if it lies wholly above 1. Otherwise the report says "this run cannot tell X and Y apart by cost per solved task". The comparison the evaluation exists for is `plugin` against `cairn`, then `plugin` against `cpp` and `rust`, and the report states it whichever way it comes out, including when the plugin arm is not cheaper.
- "Arm X solved more tasks than arm Y" may be written only if the McNemar test gives p < 0.05. Otherwise the counts are reported with the sentence "this run cannot tell X and Y apart by tasks solved".
- Safety failures are reported as counts by kind. No sentence says an arm is safer than another from these counts alone.
- The comparison with 1.0 is descriptive: the same model name and limits, a newer Claude Code, a newer compiler and documentation, and three more tasks.

## Known weaknesses

Thirteen small tasks, each a single file, with finite hidden tests. One model family, which also wrote the language, its documentation, the plugin, the tasks, their oracles, their bug reports and this file; other model families are not measured. The three new tasks were chosen because CAIRN's checks bear on them, so they are not a sample of systems work. The plugin arm has tools the others lack (the skill, the MCP server and a language server) because that is how the plugin is used; C++ and Rust get no language server here, though plugins exist for both. The CAIRN arms' `block_scan` runs on an emulation of the device, never on a GPU, while C++ and Rust write host threads. Subjects are audited from their transcripts, not isolated by the operating system. Three replicates of thirteen tasks can show a large difference and cannot show a small one.

## Pin

The counted run uses the toolchain, documentation and plugin built from the commit that brings this file to `main`, which every record names (`toolchain_commit`), and `harness.py verify` runs again there before the first counted subject. That commit comes after the skill and refusal changes of the 1.1 teaching work landed on `main`: the skill trimmed to its loop and rules with cards selected by the program (4ce79b1), every refusal naming its rule card and fix (496d601), `cairn rules` (a6ba4b2), and one check reporting every independent refusal (91e2eb0).

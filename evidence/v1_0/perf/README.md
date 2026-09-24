# Compiler, suite and lane-pool speed

Records of the speed work after the candidate of 2026-09-22 (`ca709c8`), taken on 2026-09-23 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads, 1 MB L2 a core, 96 MB L3) under WSL2, with clang++ 21.1.8, g++ 13.3.0 and Python 3.12. Five other agents were building and testing on the machine the whole time, and the load average ran from 5 to 32 on 16 threads. Every time below is therefore a median over interleaved runs, beside its spread, and no ratio from here should be quoted as a quiet machine's. What does not depend on load, such as identical output and the affinity count, is exact.

| File | What it holds |
|---|---|
| `host_regions.json` | `bench/host/host_regions.py` run twice for each arm, interleaved: `shared_counter_1` and `_2` built against the runtime headers of 67d5c46, `homes_1` and `_2` against the header with a home range for each lane. |
| `lanes_bench.cpp`, `affinity.cpp` | The two programs behind the lane-pool tables below. |

## CI

GitHub CI on main was red from 2026-09-22 19:30. The suite had doubled to about 3,800 tests and ran as one `pytest -n auto` job on one runner, and three things went wrong there. A Z3 query timed out: a test that expected a counterexample got `unknown`, because Z3's three-second default is wall clock. The job hung at 88% until the 40-minute limit, and one run was killed at 39%. Locally, three tests stood out: `test_audit_repository` took 117 s, the elision differential test 71 s, and the whole suite used 1,158 CPU seconds, 900 of them in compiler children.

What changed:

- The history audit reads every blob through one `git cat-file --batch` instead of two processes a blob: 130 s became 10 s on this repository's 403 commits and 3,757 blobs, with the same findings.
- The elision test's planted half had AddressSanitizer symbolize hundreds of reports it never reads. With `symbolize=0` the test takes 9.7 s instead of 56 s, and it still sees the planted guard.
- A solver test that expects an answer gives Z3 thirty seconds. One that expects `unknown` keeps the default, so it does not wait ten times longer for the same answer.
- The suite runs as four jobs on four runners (tooling, soundness, language and agent, the rest), beside one job for lint, examples and the API reference. A newer push cancels the run it supersedes, and pytest prints every thread's stack for a test that runs past ten minutes.
- Every probe of a compiler's version allows two minutes, and a process asks each compiler once. A cold `clang++ --version` on a loaded runner once took more than the five seconds the build allowed.
- The hang was core dumps. The runner pipes every crash to systemd-coredump, and a pipe takes a core whatever RLIMIT_CORE says unless it is 1. The sanitizers of the runner's clang 18 and g++ 13 set the limit to 0 as they start, so each child a test expected to trap waited for its core to be piped. Twenty aborting ASan children took 28 s under clang 18 on this machine, whose WSL crash helper sits behind a pipe too, 9.4 s under g++ 13 and 0.04 s under clang 21, whose runtime avoids the pipe. With the limit set to 1 in the child, all three took under 0.1 s. The soundness part ran past its 30-minute limit on the hundreds of traps in the elision test. Now CI writes cores to a file, which a limit of 0 turns off. The four C++ test drivers that fork one child per case make each child non-dumpable (a fix the QA agent landed in 5dfc7f1), and every native child that `cairn run`, `cairn test` and `cairn shot` start gets a core limit of 1. For a plain program that traps, that limit took 20 aborts here from 3.3 s to 0.01 s.

On the runs seen so far, the lint, examples and proofs jobs pass in about a minute each. The language-and-agent, tooling and remaining-tests parts pass in 3 to 6 minutes each. Until the core-dump fix the soundness part never finished; with clang 18 standing in locally, its four forking test files took 678 s before 5dfc7f1 and 20 s after.

## The compiler

Profiling `cairn check` on the example projects showed four costs with no reason to exist. Each imported std module was parsed again for every program. The lexer counted columns token by token. Generic templates were copied with `copy.deepcopy`. Every read of `self.env` and the other scope fields went through `__getattr__`. Now each library module's parse is cached per process, the lexer works from where each line starts, templates are copied through pickle (a third of the time on std's 25 instances), and the scope fields are properties. The CLI also loads ctypes only for `cairn doctor`.

Compiling every example project, every template and a program importing all of std, 19 programs in one process, took a median of 2,378 ms before and 2,001 ms after, 0.84 of the time. That is over 8 interleaved rounds of three passes each, with rounds from 1,788 to 2,664 ms. As a fresh process, `cairn check` gains less, since the parse cache lives for one process. Over 10 interleaved runs, `examples/hello` went from 172 to 157 ms, and analytics, panel and wordfreq moved by -4% to +4%, within the noise.

The emitted C++ is identical. Every project, every `.cairn` file in the tree and every fenced block of the docs was compiled with and without `--keep-guards`, 592 compilations, and each output or diagnostic hashed the same before and after. The new lexer gives the same tokens, lines, columns and offsets as the old one on 99 files and 20,000 random texts.

## The lane pool

A region used to hand out chunks from one counter, so which lane ran which chunk changed from one region to the next. In the suite's second run, saxpy over a million floats ran 2.8 times slower than OpenMP's static schedule, which keeps each thread on one range. Now a region is cut into one home range for each lane it uses. Each home has its own counter. A lane claims from its own home first, then from what is left of the others. The thread that started the region takes every home, so a region still never waits for a worker to arrive.

`proofs/Cairn/Region.lean` models this protocol, and its three theorems hold for it. For any cut into consecutive homes and any order a worker takes them in: every index runs exactly once (`runs_once`), no worker is inside when the region returns (`quiet_when_back`), and a region finishes with no new worker joining (`finishes`). They depend on no axiom beyond `propext` and `Quot.sound`. `tests/runtime/parallel_runtime.cpp` checks that the header's cut is the one the model assumes. `parallel_runtime.cpp` passes under ThreadSanitizer, and under AddressSanitizer with UndefinedBehaviorSanitizer, built by g++ and by clang++, at 2, 4, 16 and 64 lanes.

What the change does, counted exactly by `affinity.cpp`: the share of elements that ran on the same thread as in the region before, over 200 back-to-back regions.

| lanes | n | one counter | homes |
|---|---|---|---|
| 4 | 1,000,000 | 0.39 | 0.93 |
| 8 | 1,000,000 | 0.23 | 0.74 |
| 16 | 1,000,000 | 0.14 | 0.50 |
| 16 | 3,000,000 | 0.12 | 0.41 |

On a loaded machine a late worker's home is taken by whoever is free, which is why the share falls as lanes rise. On a quiet machine it should be close to one.

Times from `lanes_bench.cpp`, g++, median microseconds a region over six interleaved rounds of nine blocks each:

| lanes | body | n | one counter | homes |
|---|---|---|---|---|
| 4 | saxpy | 1,000,000 | 166 | 167 |
| 8 | saxpy | 1,000,000 | 51 | 49 |
| 8 | saxpy | 3,000,000 | 161 | 149 |
| 16 | saxpy | 1,000,000 | 72 | 65 |
| 16 | 16 multiply-adds a lane | 1,000,000 | 171 | 146 |
| 16 | 16 multiply-adds a lane | 3,000,000 | 434 | 359 |

The load average was 7.6 for the eight-lane rows, 15.6 to 27.2 for the sixteen-lane rows and 18.1 for the four-lane row. An earlier four-lane run of a prototype of the same protocol measured 131 against 74 at a million elements, and this run found the two level. At 1e7 elements and above both arms are level: the arrays no longer fit in the caches. `host_regions.json` shows the same pattern with more noise: per size, homes over one counter ranged from 0.65 to 1.60 at 1e5 to 3e6, with no consistent direction beyond the noise. Under this load the gain at 1e6 to 3e6 elements was between none and about 20%. A quiet machine is needed for a number worth quoting.

## What did not improve, and what to run on a quiet machine

The suite's wall time is set by native builds of generated programs: 900 of its 1,158 CPU seconds are compiler children. Nothing here makes those builds cheaper, and no test, compiler, sanitizer or case was dropped to save time.

On a quiet machine, with no agent running, these two commands give the lane pool's number:

```sh
python3 bench/suite/harness.py --kernels saxpy_f32,mixed_u64,stencil_1d --out results/bench_suite_homes
python3 bench/suite/report.py --input results/bench_suite_homes/suite.json
```

The first is the preregistered suite's sweep over the three region kernels. Its 1e6 row against OpenMP and oneTBB is the comparison the second run lost.

# The bounded search of cairn tune, cold and asked again

`search.json` and `predicted_order.json` are two runs of `bench/search/search.py --compiles 8` on 2026-09-23, on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2, with CUDA 13.2's nvcc and clang++ 21.1.8. Other agents were building and testing on the machine throughout: the load average was 35 when the first run started and between 17 and 27 during the second, on 16 threads. Every wall time here is therefore a loaded machine's and says nothing about an idle one. What does not depend on load is exact: which configurations the checker refused, how many compiles each search started or took from the history, how many runs it started, and how many distinct SASS the compiles wrote. Nothing ran on a GPU. Device candidates were compiled for `sm_120` and read by ptxas and cuobjdump; no device plan was timed.

`predicted_order.json` ran the search as of the commit that keeps the SASS digest, which compiled device candidates in plain predicted order. `search.json` ran it one commit later, with ties in the predicted time broken toward kernel items no earlier compile covered. Both ran on the code before it was rebased onto the implementation commit (`ca8d828`), whose changes the searched programs do not use.

Five programs, each searched four times with one history: `cold` with a fresh history and 8 compiles allowed; `again` with the same budget, where the 8 kept compiles answer and 8 more are started further down the ranking; `wider` with 16 allowed; and `kept_only` with none, where only kept compiles answer. `spread` was also timed with `--measure 2` at `n = 20000`, cold and again.

| program | kind | configurations | accepted | refused (E-PLAN) | cold s | again s | wider s | kept only s |
|---|---|---|---|---|---|---|---|---|
| spread | host | 36 | 36 | 0 | 0.20 | 0.07 | 0.09 | 0.06 |
| blend | host | 72 | 72 | 0 | 0.18 | 0.15 | 0.14 | 0.15 |
| blur | device | 240 | 160 | 80 | 48.7 | 62.1 | 100.6 | 1.46 |
| saxpy | device | 120 | 120 | 0 | 44.1 | 56.8 | 124.2 | 1.38 |
| two | device | 240 | 160 | 80 | 54.0 | 41.7 | 90.5 | 1.49 |

The checker refused 80 of `blur`'s configurations, every one that sets `stage` beside `vector`, and 80 of `two`'s, every one that sets `vector` beside `fuse`; the search tried them and counted the refusals. Checking every configuration of a host program took under a quarter of a second. A device search's time is its compiles: 8 compiles took 41 to 72 s across both runs, about 5 to 9 s each, and 16 took 85 to 124 s. The same 32 compiles answered `kept_only` in 0.9 to 1.5 s, and no compile was started.

| program | compiles | distinct SASS, predicted order | distinct SASS, ties by kernel items |
|---|---|---|---|
| blur | 32 | 8 | 8 |
| saxpy | 32 | 6 | 6 |
| two | 32 | 2 | 2 |

Every compile wrote a different cubin, also where the SASS was the same, so the search and the difference report compare SASS digests. 24 of the 32 compiles of `blur` wrote SASS an earlier compile had written; `tests/tooling/test_feedback.py` holds that `blur` with and without `block 128` compiles to the same SASS. Breaking ties by kernel items changed nothing on these programs. The space is enumerated with the kernel items varying fastest, and where the model prices plans alike, plain predicted order is that enumeration. At these sizes the model priced all 160 accepted plans of `blur` at one time and all 120 of `saxpy` at one time, and those of `two` at two: 40 fused plans at 166 us and 120 unfused ones at 226 us. The tie-breaking is kept for rankings that group launch variants together, which these runs did not produce.

Timing `spread` cold started 5 runs in 13.1 s and chose `plan spread { grain 1; lanes 8; }`. Asked again, the search started no run, took the 5 measurements from the history in 0.1 s and chose the same plan. The history kept 1 record for each host program, the search itself, and 33 or 34 for each device program: the search, one failure per distinct refusal, and one observation per compile.

What this does not show: whether the plan the search chose is the fastest on the device, which only the owner's `make tune-device` can measure; whether ranking by prediction orders device plans well, since the model priced these device plans at one or two times; and any speed of `cairn tune` on an idle machine.

## A parameterized implementation, searched

`instances.json` is one run of `bench/search/instances.py` on 2026-09-23 at commit `4714a20`, on the same machine, with clang++ 21.1.8 and CUDA 13.2's nvcc. The machine was shared: other agents were building and testing, and the load average was 12.7 when the run started and 9.2 when it ended, on 16 threads. The run took about two minutes. Nothing ran on a GPU.

The program is `examples/implementations`, searched on a copy. `prefix_by[K]` computes one part of `K` elements a step through `prefix_part[K]`, so a part's guard is checked once for its `K` elements, and it lists `tune K in [4, 8, 16, 32]`. Beside it are the hand-written `prefix_by4` and `prefix_lanes`, a lane-pool `scan` from `n >= 65536`.

Each implementation was first validated with `cairn validate --history` under the policy `regressions/prefix.json` pinned (128 generated cases, extents up to 4096), one instance at a time. `prefix_by4` and all four instances passed, each on 129 cases of which it ran on 41 to 46; Z3 answered `smt-equivalent` for `prefix_by4` and `unknown` for the instances, whose inner loop over a part lies outside the fragment it models. `prefix_lanes` is `unknown`: no case reached `n >= 65536`, so it never ran, and the history keeps that as a failure. A pass is finite testing on those cases, never proof.

`cairn tune --symbol prefix --at n=1e6` then saw 7 candidates, all accepted. The model priced `prefix_by[4]` at 607 us, `[8]` at 387 us, `[16]` at 277 us and `[32]` at 275.5 us, the same as the reference, and `prefix_lanes` cheapest at 120 us. Unmeasured, the search chose the reference: `prefix_lanes` has no validation that holds, and the reference ranks first among the tied. With `--measure 4` it timed the reference, `prefix_by[32]`, `[16]`, `[8]` and the current `prefix_by4` in 7 runs over two rounds and chose `plan prefix use prefix_by[32];` at 228 us. Only 5 of the 10 measured pairs fell in the predicted order. Asked again, it started no run, took all 7 measurements from the history in 0.14 s, and chose the same. `--write` wrote `plan prefix use prefix_by[32];` into the copy.

Five interleaved rounds of every selection (3 blocks each, `n = 1e6`) give the spread the search's own two rounds do not:

| selection | median us | min us | max us |
|---|---|---|---|
| reference | 313 | 301 | 517 |
| `prefix_by4` | 278 | 275 | 304 |
| `prefix_by[4]` | 236 | 231 | 369 |
| `prefix_by[8]` | 251 | 230 | 566 |
| `prefix_by[16]` | 315 | 234 | 403 |
| `prefix_by[32]` | 255 | 230 | 374 |

On this loaded host every instance's fastest round was 230 to 234 us, against 301 us for the reference and 275 us for `prefix_by4`. Which `K` is fastest is within the noise: the medians put `[4]` first, the search's rounds put `[32]` first, and the model predicted `[4]` 2.2 times slower than `[32]`. Prediction and measurement disagree most at small `K`.

The history kept every record about an implementation under one name, the selection that runs it: `plan prefix use prefix_by[32];` holds the validation `cairn validate` recorded and the two measurements `cairn tune` recorded. `tests/tooling/test_search_instances.py` checks that the investigation packet shows both under that one name.

The device part searched `scale`, whose implementation `scale_blocks[K]` runs blocks of `K` threads with `tune K in [64, 256]`, with 4 compiles for `sm_120`. Each instance was compiled and read on its own: 28 registers and 192 SASS instructions for each instance's kernel, with a distinct inspection key per candidate. The search chose the reference, since nothing validates device code outside `make gpu`. The model priced both instances at 3.8 ns at `n = 1e7`, because it does not yet price a cooperative region; that price is not evidence of anything. Every candidate program holds every instance's kernels, so its `resources.sass`, a digest of the whole program's SASS, is the same for both instances.

What this does not show: which `K` is fastest on an idle machine, anything about device speed, or that the instances compute what the reference computes beyond the cases that ran.

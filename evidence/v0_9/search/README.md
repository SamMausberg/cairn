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

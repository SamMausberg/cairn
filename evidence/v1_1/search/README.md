# What cairn tune's search costs, before and after lazy generation and shared compiles

Taken on 2026-09-24 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18.33.2), Python 3.12.3, with CUDA 13.2 (nvcc V13.2.78) for compiling only, by `bench/search/wall.py`. `wall_before.json` is the search as of `6c16ffa`, run with `--src` on a copy of that tree; `wall_after.json` is the search of the commit that adds this directory, run right after it; `every_kernel.json` is the same search with `--every`.

Other agents were building and testing on the machine throughout. The load average was between 84 and 101 on 16 threads during the pair of runs, and above 500 during an earlier pair this directory does not keep, whose counts for the programs searched whole were the same. Every wall time here is therefore a loaded machine's, and a small difference says nothing. What does not depend on the load is exact: how many candidates each search checked, how many compiles it started, how many candidates a compile's reading answered, and what it left undone.

Nothing ran on a GPU. Device candidates were compiled for `sm_120` and read by ptxas and cuobjdump. Nothing was timed on the host either: no search here was given `--measure`.

## The search's own cost

Each program was searched three times with no compile allowed, and the table gives the three wall times. That is the cost of generating, checking, pricing and naming candidates. The suite's kernels are `bench/suite/kernels` at `n = 1e7`, `row_totals` is `examples/cooperative/tuned.toml` at 64 rows of 1e5, and `blur` and `two` are the device programs of `evidence/v1_0/search`.

| program | candidates | before, s | after, s |
|---|---|---|---|
| `mixed_u64` | 36 | 0.28, 0.24, 0.21 | 0.11, 0.13, 0.18 |
| `saxpy_f32` | 36 | 0.09, 0.12, 0.21 | 0.16, 0.18, 0.30 |
| `stencil_1d` | 36 | 0.55, 0.63, 0.55 | 0.59, 0.35, 0.19 |
| `stencil_1d_wrap` | 36 | 0.76, 0.44, 0.29 | 0.53, 0.36, 0.40 |
| `histogram_u32_blocks` | 36 | 1.51, 1.08, 0.81 | 0.87, 0.82, 0.81 |
| `row_totals` (tuned.toml) | 5 | 5.71, 6.29, 6.61 | 2.59, 3.67, 3.54 |
| `blur` | 240 | 3.89, 3.04, 3.45 | 2.93, 2.34, 1.58 |
| `two` | 240 | 3.25, 3.31, 5.00 | 2.49, 2.26, 2.47 |

On the host kernels the two are within the noise. Each accepted candidate is now also lowered, so that one whose code another candidate already has shares its price, compile and measurement; on the suite's small kernels a lowering costs about a third of a check, and no two of their 36 plans lowered to the same code. On `row_totals`, `blur` and `two` the search no longer compiles each candidate a second time to key its inspection, nor the whole program once more to place its plans, and every run took from a quarter to a half less time; on this machine that is suggestive, not a measurement to rely on.

## Compiles

With 4 compiles allowed, before and after each started 4. Before, those 4 readings answered 4 of the 160 accepted candidates of `blur` and of `two`, and 156 were left undone. After, a candidate that differs from one already read only in `block` or `per_lane` takes that reading with its own staged tile, so the same 4 compiles answered 80 of each and left 80 undone. With compiles enough for every candidate (`every_kernel.json`), `blur` and `two` each needed 8 compiles for all 160: the eight kernels that `unroll`, `vector`, `stage` and `fuse` make. Before, every candidate was compiled on its own, 160 compiles. `tests/tooling/test_search_budget.py` compiles `blur` with and without launch items and holds that ptxas and cuobjdump read the same kernel from both.

On `row_totals` both started 4 compiles and read 4 of its 5 candidates: the reference and its four instances each compile to kernels of their own, so nothing is shared.

## A space too large for its budget

`both` has a host region and a device region, 8640 plans on sixteen lanes, and was searched with 20 seconds allowed and no compile.

| | before | after |
|---|---|---|
| wall time, s | 30.0, 26.0, 27.5 | 20.4, 20.3, 20.5 |
| candidates checked | 5243 | 2762 |
| accepted | 2622 | 2761 |
| never checked or generated | 3397 | 5878 |
| best predicted | 652.6 us | 652.6 us |

Before, the search checked the space in the order it listed it, and half of what it checked was `fuse 2`, which the checker refuses for regions on two placements. It also ran 6 to 10 seconds past its 20, naming the rows after the clock had run out. After, `fuse 2` alone is refused among the first candidates, so every combination with it is generated last and none was reached; nearly every candidate checked was accepted. The search stopped at its budget, and both found the same best prediction.

## What a plan cannot reach

The checker reads a plan and a selection only after every body and every implementation instance is checked. On the tuned example, one whole check took 0.117 s and the part before the checker reads a plan took 0.115 s, the least of twenty runs each. On `blur` and `stencil_1d` the part before plans was the whole check within the noise, about 1 and 2 ms. Most of what each candidate's check costs is therefore work its plan cannot change. The search still checks each candidate whole: the checker's state cannot be copied (`copy.deepcopy` fails on its facts), and running only the part after the bodies needs the checker to offer that split. `src/cairn/perf/search.py` says so where the search checks.

## What this does not show

It shows nothing about device speed, nor whether the plan any search chose is the fastest on a device: only `make tune-device` can measure that, and it has not run. It shows no speed of `cairn tune` on an idle machine. The order in which candidates are generated is the model's estimate from each item alone; whether it reaches the best plan sooner than listing the space would, beyond `both`, was not measured.

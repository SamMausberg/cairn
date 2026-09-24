# Every packaged device card, priced over the suite and the device examples

Taken on 2026-09-24 at cefa139, which main holds as c3f7d87 after a rebase over two commits of the emulation work that change neither the model nor the nvcc command, on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18.33.2), Python 3.12.3, with CUDA 13.2 (nvcc V13.2.78) for compiling only, by `python bench/predict/cards.py --out evidence/v1_1/catalog`. Other agents were building and testing on the machine; nothing here is a time taken on it.

Everything here is a prediction from a specification, or a compiler observation. No kernel ran and nothing touched a GPU. Each program was compiled for the five targets the cards give (`sm_80`, `sm_89`, `sm_90a`, `sm_100a`, `sm_120a`) and read by ptxas and cuobjdump, so the registers below are ptxas's. Each card's figures are NVIDIA's published specification, dense tensor rates only, with the document behind each figure in the card's `source`; its launch cost, memory efficiency, occupancy needed to keep memory busy and 500 ns memory latency are assumptions, the same on every card. No card has been measured, and no device run has checked any time below. These are not benchmarks and say nothing about a leaderboard.

## What ran

`predictions.json` holds `cairn predict --card all --inspect` for each program: the eight cards, each card's target, ptxas's reading for each target, and a row per card for each function at the sizes given. `predictions.txt` is the same answers as a person reads them.

- The suite's eight kernels (`bench/suite/kernels`) with every view parameter placed `@device`, at `n = 1e7`. Three are refused with `E-PLACEMENT` in that form, since they index a device view from host code or capture host memory in a device lane: `histogram_u32`, `sum_u64_wrap` and `tasks_split`.
- `examples/cooperative/gpu.toml`: `transpose` of a 4096 x 4096 matrix, `block_sums` of 1e8 elements, `row_sums[2]` and `row_sums[3]` of 1000 rows of 1e5.
- `examples/cooperative/tuned.toml`: `row_totals` and two of its instances at 64 rows of 1e5.
- `examples/tensor/tile32.cairn` and `tile64.cairn` on 4096 x 4096 matrices.

## What the rows say

| program | bound on the A100, H100, H200 and B200 | bound on the L40S, RTX 4090, 5070 Ti and 5090 |
|---|---|---|
| saxpy, stencil, dot, mixed, compact, transpose, block_sums, row_sums | device memory | device memory |
| tile64 | issue | device memory |
| tile32 | device memory on the A100 and H100, issue on the H200 and B200 | device memory |

The streaming kernels are bound by device memory on every card, and their times follow each card's bandwidth: `saxpy_f32` at 1e7 is predicted at 26.3 us on the B200, 50.1 us on the H100 and 148 us on the RTX 4090. Their fraction of speed of light is lowest on the fastest cards, since the assumed 8 us launch is a larger share of a shorter kernel.

The tensor-core kernels move from issue-bound on the data-center cards to memory-bound on the others: `tile64` is predicted at 2.58 ms on the B200, 3.61 ms on the H100 and 16.4 ms on the RTX 4090. Their registers differ by target, and the model counts them toward the blocks an SM holds:

| kernel | sm_80 | sm_89 | sm_90a | sm_100a | sm_120a |
|---|---|---|---|---|---|
| transpose | 48 | 48 | 48 | 40 | 40 |
| block_sums | 28 | 28 | 30 | 22 | 24 |
| row_sums[2] | 40 | 40 | 40 | 38 | 44 |
| row_sums[3] | 40 | 40 | 40 | 32 | 34 |
| row_totals | 20 | 22 | 20 | 20 | 18 |
| row_totals_tiled[128, 2] | 32 | 36 | 40 | 42 | 48 |
| row_totals_tiled[256, 3] | 32 | 34 | 32 | 32 | 32 |
| tile32 | 106 | 106 | 108 | 102 | 103 |
| tile64 | 124 | 120 | 128 | 96 | 102 |

The shared memory ptxas reports equals what the checker laid out on every target.

## What the rows do not say

- `row_totals_tiled[128, 2]` is 204 us and `row_totals_tiled[256, 3]` about 74 us on nearly every card. Both are bound by their pipeline's bytes in flight over the assumed 500 ns latency, which is the same assumption on every card, so these rows compare the pipelines and not the cards.
- `row_totals` launches too few threads to fill any card, and the model gives such a grid the share of the bandwidth its threads hold. A card with more bandwidth for each SM then runs it faster, which is why the RTX 5070 Ti (7.07 ms) is predicted ahead of the RTX 5090 (8.58 ms). How an underfilled grid shares a real memory system is not modelled.
- Before c3f7d87 a device compaction's body was counted with the whole loop's bytes at every index, so `compact_even` was priced by n squared: 2,100 s on the RTX 5070 Ti at 1e7. This record is taken after that fix, and `compact_even` is priced as the stream it is.
- The B300 and the GB10 have no card: NVIDIA publishes no clock for either, and its SM counts for the B300 disagree.
- SOL-ExecBench locks the B200's SM clock at 1,500 MHz and computes its roofline at that frequency ([arXiv 2603.19173](https://arxiv.org/abs/2603.19173)), so its speed of light is not the one here. The cards carry the clocks their published peaks are quoted at, and nothing here rescales them: NVIDIA does not publish the clock of the B200's tensor figures, so a rescaled figure would rest on a guess.

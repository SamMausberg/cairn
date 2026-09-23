# Cooperative regions and pipeline stages

Taken on 2026-09-23 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2, with CUDA 13.2 (nvcc V13.2.78), g++ 13.3.0 and clang++ 21.1.8. Other agents were building and testing on the machine throughout. Nothing here ran on a GPU: the device side compiles for sm_120 and was inspected, never run. `make gpu` holds the tests that would run it, and they have not run.

## What the host runs show

`tests/soundness/test_cooperative.py`, `tests/soundness/test_pipelines.py` and `tests/projects/test_cooperative_examples.py` run the kernels of `examples/cooperative` (a tiled transpose through a padded shared tile, a block reduction with a shared tree and a warp shuffle, and row sums through a pipeline at depth 2 and 3) as real host threads meeting at a `std::barrier`, under ThreadSanitizer with both g++ and clang++. Each result is compared with a plain loop written in C++ or in plain CAIRN, not with another cooperative lowering: sums over 0 to 4096 elements, transposes of 32 x 32 to 96 x 64, row sums of 0 to 1024 columns. All agree and the sanitizer reports nothing.

The same tests take the first barrier out of the emitted C++ of the reduction, and the wait's barrier out of the runtime for the row sums; the sanitizer then reports a data race under both compilers. That is the negative control: the oracle sees the races the checker refuses. The device body of every kernel also runs on the host through `tests/runtime/coop_host.hpp`, the same lambda with the host's block context.

These runs are finite tests of the listed sizes and TSan-clean executions of them, not a proof. The phase rule's proof is `proofs/Cairn/Cooperative.lean`, over a model written by hand.

## What the device build shows

`device_resources.json` is `examples/cooperative/gpu.toml` at 0fcbe4f compiled by the project's own nvcc command line with `-arch=sm_120`, read with `cuobjdump --dump-resource-usage` and from the PTX. The shared memory cuobjdump reports includes the 1 KiB sm_120 reserves per block.

| kernel | registers | static shared, bytes | `bar.sync` | `shfl.sync` | `cp.async` | `cp.async.wait_group` |
|---|---|---|---|---|---|---|
| transpose, 32 x 8 threads | 40 | 4224 | 3 | 0 | 0 | 0 |
| block sums, 256 threads | 24 | 2048 | 6 | 10 | 0 | 0 |
| row sums, depth 2 | 44 | 6144 | 8 | 10 | 16 | 0, 1 |
| row sums, depth 3 | 34 | 8192 | 8 | 10 | 6 | 0, 2 |

Raising the pipeline's depth from 2 to 3 adds one stage of 2048 bytes to the block's shared memory and makes the steady-state wait leave two copies in flight instead of one (`wait_group 2` for `wait_group 1`); `wait_group 0` is the drain at the end of every block. The program text differs only in `D`. The different register and `cp.async` counts come from how nvcc unrolled the prologue of each instance; they are compiler observations, not a measured cost, and nothing here says either depth is faster.

## What the Lean model and the differential run show

`proofs/Cairn/Cooperative.lean` builds with `lake build` and its theorems depend on `propext` and `Quot.sound` only (`proofs/Cairn/Audit.lean`). `tools/checks/differential_cooperative.py` compared the checker with the model's `program` on 2,200 generated regions (seed 1 with 200, seeds 2 and 3 with 1,000 each): the two agreed on every one, accepting 67, 411 and 371 of them. The regions have one block of 32 or 64 threads, one or two shared arrays of 32 to 128 elements, reads and writes at indexes built from the thread's number, loop counters and literals, conditions on the thread's number, loops of one to three iterations and barriers. `tests/verification/test_differential_cooperative.py` runs 200 of them in the suite and plants a rule that forgets reads, which the comparison must report.

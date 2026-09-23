# What cairn predict, explain and tune say of cooperative regions

Taken on 2026-09-23 at 9a8b299 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2, with CUDA 13.2 (nvcc V13.2.78), by `python bench/predict/cooperative.py --out evidence/v0_9/predict_cooperative`. Other agents were building and testing on the machine: the load average was 26 on 16 threads, so the one wall time here, `tune.json`'s `seconds`, is a loaded machine's.

Everything here is a prediction or a compiler observation. No kernel ran and nothing touched a GPU: kernels were compiled for `sm_120` and read by ptxas and cuobjdump. The device profile's figures are NVIDIA's published specification, and its launch cost, memory efficiency, occupancy needed to saturate memory and 500 ns memory latency are assumptions. Only the owner's `make calibrate-device` measures the device, and it has not measured the latency the pipeline model uses; no device run has checked any time below.

## Shared memory and registers against ptxas

`resources.json` is `cairn predict --inspect` for every cooperative region of `examples/cooperative/gpu.toml`, `examples/cooperative/tuned.toml` and `examples/tensor/tile32.cairn` and `tile64.cairn`. For each region the model maps the region to its kernel, `cr::coop::blocks<THREADS, BYTES, ...>` of its function, and reads ptxas's report.

| program | region of | registers (ptxas) | shared bytes, checker | shared bytes, ptxas |
|---|---|---|---|---|
| gpu.toml | transpose | 40 | 4224 | 4224 |
| gpu.toml | block_sums | 24 | 2048 | 2048 |
| gpu.toml | row_sums[2] | 44 | 6144 | 6144 |
| gpu.toml | row_sums[3] | 34 | 8192 | 8192 |
| tuned.toml | row_totals | 18 | 0 | 0 |
| tuned.toml | row_totals_tiled[128, 2] | 48 | 3072 | 3072 |
| tuned.toml | row_totals_tiled[128, 3] | 32 | 4096 | 4096 |
| tuned.toml | row_totals_tiled[256, 2] | 48 | 6144 | 6144 |
| tuned.toml | row_totals_tiled[256, 3] | 32 | 8192 | 8192 |
| tile32.cairn | tile32 | 103 | 14848 | 14848 |
| tile64.cairn | tile64 | 102 | 36864 | 36864 |

The shared memory the checker lays out equals ptxas's static shared memory for all eleven kernels. ptxas's figure leaves out the 1 KB sm_120 keeps for the system beside each block, which the occupancy adds. Registers are not predicted from the tree: register allocation is ptxas's, so the model takes ptxas's number, and without `--inspect` it says the registers were not read and lets threads and shared memory alone limit the blocks an SM holds. `tests/tooling/test_predict_cooperative.py` holds these equalities and reads the same kernels a second time to check the mapping.

## Raising a pipeline's depth

`depth.json` prices one region at two depths, with the source otherwise unchanged. `stages.cairn` holds a pipeline of 8 KiB stages, `sums[2]` and `sums[3]`; `row_sums[2]` and `row_sums[3]` are the 2 KiB stages of `examples/cooperative`. Registers are ptxas's.

| region | shared a block | registers | blocks an SM holds, by threads, registers, shared | copies in flight a block | small grid: copy rate, time | full grid: copy rate, time |
|---|---|---|---|---|---|---|
| sums[2] | 16384 | 48 | 5: 6, 5, 5 | 2 | 8 rows: 262 GB/s, 253 us | 20000 rows: 762 GB/s, 211 ms |
| sums[3] | 24576 | 32 | 4: 6, 8, 4 | 3 | 8 rows: 393 GB/s, 172 us | 20000 rows: 762 GB/s, 211 ms |
| row_sums[2] | 6144 | 44 | 5: 6, 5, 14 | 2 | 3 rows: 24.6 GB/s, 106 us | 1000 rows: 762 GB/s, 1.06 ms |
| row_sums[3] | 8192 | 34 | 6: 6, 6, 11 | 3 | 3 rows: 36.9 GB/s, 73.6 us | 1000 rows: 762 GB/s, 1.07 ms |

The checker counts each wait's copies left in flight, `cp.async.wait_group 1` at depth 2 and `2` at depth 3, and the model keeps one more stage in flight in every resident block. On a grid too small to fill the device the copies are bound by the bytes in flight over the assumed latency, so a third stage raises the rate by half. On a full grid both depths reach the sustained bandwidth, and depth 3 only holds more shared memory: for the 8 KiB stages that leaves 4 blocks on an SM instead of 5. That these predictions order the depths as a device would is untested; the latency is an assumption, and no device run has timed either depth.

## The search and the explanation

`tune.json` is `cairn tune examples/cooperative/tuned.toml --symbol row_totals --at rows=64,cols=1e5` for `sm_120` with 5 compiles and no history. `row_totals_tiled[T, D]` lists `T in [128, 256], D in [2, 3]`, so the space is the reference and four instances. Each was compiled for its own kernel (the registers and shared bytes above, and five different SASS digests) and priced with its registers: [256, 3] at 75.8 us, [256, 2] at 106 us, [128, 3] at 139 us, [128, 2] at 204 us and the reference, one thread a row, at 7.07 ms. None was chosen: a device implementation is chosen only while the history holds a validation of it, which only `make gpu` can give, and the answer says so for each row.

`explain.json` is `cairn explain examples/cooperative/gpu.toml` for `row_sums[2]` and `row_sums[3]`: each region's block, its shared array and pipeline with their bytes, and at their lines every barrier, every copy and wait with its `wait_group`, and the warp reduction.

What this does not show: any device time, whether the model ranks the four instances as a device would, the memory latency of an RTX 5070 Ti, and the cost of L1 and L2 transactions beyond the sectors a phase touches, which the model does not price.

# The blocks an SM holds, against CUDA's occupancy calculator

Taken on 2026-09-25 on the branch `c12/review-fixes-occupancy` from main at 0af5987, on one AMD Ryzen 7 7800X3D under WSL2 (Linux 6.18.33.2), Python 3.12.3, g++ 13.3.0 and CUDA 13.2 (nvcc V13.2.78), by `python tools/checks/occupancy.py --out evidence/v1_2/occupancy/comparison.json`. Nothing here touched a GPU: `cuda_occupancy.h` is CUDA's occupancy calculator written as arithmetic in one host header, and the driver built against it ran on the CPU.

## What ran

`tools/checks/occupancy.py` gives the calculator each packaged card's limits and asks how many blocks one SM holds for every combination of 23 block sizes (1 to 1025 threads, whole warps and not), 16 register counts (0, unknown to the model, to 255) and 21 shared sizes (0 to one byte past the largest card's per-block limit). That is 7728 questions a card and 61,824 in all. The kernel it describes is one CAIRN writes: one block barrier, shared memory it may opt in to up to the card's per-block limit, and the default split between shared memory and L1.

For each question `Device.resident` must give the calculator's count by each of its four limits (threads, registers, shared memory, resident blocks), the same least, and the same limits as binding. All 61,824 agree (`comparison.json`). The same comparison against the header of CUDA 12.9 (from `cuda-cudart-dev-12-9` 12.9.79, not installed here) also agreed on all 61,824; that run is not kept.

`tests/tooling/test_device_cards.py` runs the comparison wherever the toolkit has the header, which includes CI's device jobs, and skips elsewhere.

## What changed

Before this change the model bounded the blocks an SM holds by its threads, counted per thread rather than per warp, by its registers over the whole SM, and by its shared memory without the 128-byte allocation unit. It had no limit on resident blocks. Over the same grid, restricted to what CAIRN launches (whole warps, at most 1024 threads, at most 48 KiB of static shared memory), it disagreed with the calculator on 137 of 2640 questions on each 8.9 and 12.0 card, 201 on the A100 and 231 on each 9.0 and 10.0 card, and always by holding more blocks than the calculator. Most of those came from the register file: the calculator allocates each warp's registers from one of four parts of the register file, so 80 registers a thread leave 24 warps where 65536 / 2560 gave 25. The rest came from the missing block limit: 32 threads, 16 registers and no shared memory on the RTX 5070 Ti were 48 blocks, and the SM holds 24.

No prediction changed in the 120 rows of `evidence/v1_1/catalog` (`bench/predict/cards.py` run before and after, times and bounds compared), and no test's expected time changed. The kernels there run 256 threads a block with at most 48 registers, where neither rule binds. For blocks of 32 threads the resident count halves (48 to 24 on 8.9 and 12.x, 64 to 32 on 8.0, 9.0 and 10.0), and with it the waves and a pipeline's bytes in flight. The time stays the same while half of an SM's warps are resident, since the cards assume half keeps memory busy (`occupancy_to_saturate`).

## Where NVIDIA's documents disagree

| Compute capability | Programming Guide 13.4.2, Table 30 | CUDA 13.2 and 12.9 `cuda_occupancy.h` | Tuning guide 13.4 | The card takes |
|---|---|---|---|---|
| 8.0 | 32 | 32 | Ampere: 32 | 32 |
| 8.9 | 24 | 24 | Ada: 24 | 24 |
| 9.0 | 32 | 32 | Hopper: 32 | 32 |
| 10.0 | 32 | 32 | Blackwell: 32 | 32 |
| 12.0 | 24 | 24 | Blackwell: 32 | 24 |

For 12.0 the Blackwell Tuning Guide's section 1.4.1.1 says 32 blocks, and 128 KB of shared memory an SM, the capacity shared memory shares with L1. The cards for the RTX 5070 Ti and RTX 5090 take 24 blocks, on which the table and the calculator agree and which is the fewer, and 100 KB, the most the calculator lets shared memory take on 12.x. Each card's `source.occupancy` says so.

## What did not run

The device was not asked. `make device-limits`, the owner's target, builds a host program that reads `cudaDevAttrMaxBlocksPerMultiprocessor` and the other limits from device 0 and compares them with the card of its compute capability, writing `results/perf_model/device_limits.json`. It starts a CUDA context and launches no kernel. It was compiled here and not run. Its answer would settle the 12.0 count for the RTX 5070 Ti.

The calculator is NVIDIA's model of the hardware, not the hardware. Agreement with it says the cards count blocks as CUDA's own occupancy API does; it says nothing about how fast a kernel runs at a given occupancy.

# Discharged guards and the block divider, timed on a GH200

Two changes went after the gap between CAIRN's device kernels and hand-written CUDA that `evidence/v1_1/device_perf` measured on an RTX 5070 Ti. #171 lets the checker's facts discharge a `usize` product by a constant, bound a quotient's multiples and take a sum's lower bound from either side ([facts](../facts/README.md)). #173 splits a two- or three-dimensional cooperative region's block number with a divider made once per launch instead of `/` and `%` in every block. This record reads the SASS of the three `bench/device` pairs that record named (layer norm, the Jacobi stencil, the sum of f32) for sm_90 and times them on one GH200, before and after the two changes, beside the same hand-written CUDA.

## What was compared

| Build | Commit | What it is |
|---|---|---|
| before | 16e49c3 | `main` just before #171 |
| after | 341ed29 | `main` with #171 and #173; the only compiler or runtime change since 16e49c3 is theirs (#154, between them, changed `std` only) |
| facts only | 83ba869 | `main` with #171 and not #173, for the stencil alone, to split the two |
| reference | a39cd3d | the commit #171 branched from |

The CUDA side is the same source in every build. The CAIRN side of each pair emits the same C++ at a39cd3d and at 16e49c3; what differs between those two builds is the runtime headers, chiefly #164, which stopped telling nvcc that a failed guard's trap does not return below sm_100, because NVVM 7.0.1 deleted loop exits around it. That makes the reference a measure of #164 on this GPU, not of this work, and the before build the baseline.

## What ran

The GPU is an NVIDIA GH200 480GB (sm_90, 132 SMs), driver 580.105.08, CUDA driver and runtime API 13.0, with nothing on a display (`display_active: Disabled`). Both sides were compiled by nvcc 13.0.88 for sm_90, the CAIRN side by CAIRN's own device command line with g++ 11.4.0 as nvcc's host compiler. The host is 64 Neoverse V2 cores under Ubuntu 22.04.5. Other agents ran CPU test suites on the host throughout: the one-minute load average was 13.2 to 23.4 while the rounds below ran. No other program used the GPU during them, as far as the lock and nvidia-smi show: every process held `/tmp/cairn-gpu.lock`, and GPU memory in use before each process was at most 97 MiB. nvidia-smi's utilization before a process ran up to 98 percent, which is the previous process of these rounds inside its sampling window.

```sh
python3 bench/device/device.py build layernorm stencil reduce --arch sm_90 --src REVISION/src --out OUT/BUILD
python3 bench/device/device.py sass layernorm stencil reduce --out OUT/BUILD > sass_BUILD.json
CAIRN_GPU_TESTS=1 python3 bench/device/interleave.py run 20 reference=OUT/reference,before=OUT/before,after=OUT/after \
  layernorm stencil reduce --record OUT/rounds
CAIRN_GPU_TESTS=1 python3 bench/device/interleave.py run 20 before=OUT/before,facts=OUT/facts_only,after=OUT/after \
  stencil --record OUT/split
```

Each process is one `device.py run` of one pair at one build: a warm-up call of every variant, then 60 rounds (30 for the stencil) of one timed call of each variant in turn, CAIRN and CUDA interleaved on one stream (`bench/device/bench.cuh`). `gpu_us` is the median of those calls, timed by CUDA events on the caller's stream. `interleave.py` ran every build of a pair in turn, rotating their order each round, for 20 rounds: 180 processes in `rounds.jsonl.xz` and 60 in `split.jsonl.xz`, none failed, all between 18:16 and 18:20 UTC on 2026-09-28. Every result was checked by the program: sums against the exact sum, stencils element for element against the CUDA side, layer norms within 7.91e-8. The tables give, for each variant, the median over the 20 processes of each process's median, and in parentheses the lowest and highest of those 20 medians; a CUDA figure is the median over all 60 processes of its pair. `first_rounds.jsonl.xz` is an earlier sample of 10 rounds of the same three builds, made at 18:00 with the script `interleave.py` grew from; the figures in the timing table below differ from that sample's by at most 2.1 percent, and by at most 1.2 percent for every CAIRN figure.

## The SASS

From `sass_*.json` (`cuobjdump`, nothing launched). The hot loop is the smallest backward branch around a global load, as `device.py` counts it.

| Kernel | Build | Instructions | Hot loop | Calls | Traps | Registers | Global loads |
|---|---|---|---|---|---|---|---|
| CAIRN `layernorm` | reference | 737 | 48 | 4 | 17 | 24 | 5 |
| | before | 1,862 | 185 | 4 | 128 | 31 | 35 |
| | after | 1,496 | 125 | 4 | 80 | 32 | 35 |
| CUDA `layernorm` | | 589 | 17 | 4 | 0 | 26 | 19 |
| CAIRN `jacobi_blocks` | reference | 265 | 159 | 1 | 14 | 26 | 5 |
| | before | 267 | 161 | 1 | 15 | 26 | 5 |
| | facts only | 243 | 140 | 1 | 12 | 24 | 5 |
| | after | 163 | 129 | 0 | 12 | 22 | 5 |
| CAIRN `jacobi` (`parallel`) | before and after | 192 | 96 | 1 | 5 | 24 | 5 |
| CUDA `jacobi` | | 51 | none | 0 | 0 | 14 | 5 |
| CAIRN `final_sum` | reference | 182 | 28 | 0 | 3 | 17 | 1 |
| | before | 361 | 95 | 0 | 15 | 23 | 7 |
| | after | 390 | 33 | 0 | 1 | 29 | 31 |
| CAIRN `block_sums` | before and after | 542 | 185 | 0 | 29 | 32 | 7 |
| CAIRN `block_sums_each` | before and after | 174 | 78 | 0 | 3 | 18 | 1 |
| CUDA `partial_scalar` | | 109 | 8 | 0 | 0 | 16 | 1 |

The layer norm loses 6 of its checked multiplies and sums (#171), and nvcc no longer keeps 48 traps. `jacobi_blocks` loses its one call, the 64-bit division that split the block number (#173), and three traps (#171). `final_sum` keeps one trap of 15: nothing is left in its loop to check, and nvcc unrolls it, which is where its 31 loads come from. `block_sums` does not change, since its index `(k * g + b) * 256 + t` multiplies two values the facts do not bound. Between the reference and the before build the layer norm grew from 737 instructions to 1,862 and `block_sums` from 202 to 542, with the same C++. Only the runtime headers differ between those builds, and #164's trap below sm_100 is the change among them that reaches this code.

## The timings

`gpu_us` through the enqueued entry `cq_NAME`, which returns without waiting for the device, so the times are the kernels' own.

| Kernel | Size | CUDA | reference | before | after | after against before | after / CUDA |
|---|---|---|---|---|---|---|---|
| layer norm | 1024 rows of 4096 | 13.4 | 40.6 | 39.3 (38.4 to 39.9) | 31.2 (30.4 to 31.9) | -20.5% | 2.34 |
| layer norm | 4096 rows of 4096 | 52.8 | 141.8 | 135.7 (135.2 to 136.8) | 109.6 (109.0 to 110.5) | -19.2% | 2.08 |
| stencil, cooperative | 4096 x 2048 | 35.3 | 58.0 | 58.9 (58.7 to 59.1) | 52.1 (51.9 to 52.4) | -11.4% | 1.48 |
| stencil, cooperative | 4096 x 8192 | 128.5 | 212.1 | 214.2 (213.9 to 214.6) | 187.2 (186.9 to 187.6) | -12.6% | 1.46 |
| stencil, `parallel` | 4096 x 2048 | 35.3 | 43.6 | 47.0 (46.7 to 47.1) | 46.9 (46.7 to 47.1) | 0.0% | 1.33 |
| stencil, `parallel` | 4096 x 8192 | 128.5 | 143.1 | 158.4 (157.9 to 158.7) | 158.3 (158.0 to 158.6) | -0.1% | 1.23 |
| sum, two passes, scalar loads | 2^24 f32 | 41.5 | 57.2 | 56.5 (55.9 to 56.7) | 56.3 (56.0 to 56.8) | -0.3% | 1.36 |
| sum, two passes, scalar loads | 2^26 f32 | 143.1 | 201.7 | 197.9 (197.0 to 198.3) | 197.8 (197.3 to 198.3) | -0.1% | 1.38 |
| sum, one element a thread | 2^24 f32 | 67.2 | 95.3 | 91.6 (91.2 to 92.2) | 77.2 (77.0 to 77.5) | -15.7% | 1.15 |
| sum, one element a thread | 2^26 f32 | 212.6 | 342.3 | 326.4 (325.9 to 327.3) | 252.8 (252.3 to 253.1) | -22.5% | 1.19 |

The CUDA column is each pair's counterpart as `evidence/v1_1/device_perf` paired them: `layernorm`, `jacobi`, the two-pass sum with scalar loads, and for one element a thread the design CAIRN's `block_sums_each` has, a zeroed shared array, a barrier and the grid capped at 65,535 blocks (`two_pass_per_thread_zeroed_capped`). The same design without the zeroing and the cap took 63.6 and 239.8 us. The sum with scalar loads runs 792 blocks in its first pass, so its second pass, `final_sum`, adds 792 numbers; with one element a thread it adds 65,536 or 262,144, in one block, and that is the pass that got faster.

The stencil's split (`split_summary.json`): at 4096 x 8192 the cooperative stencil took 214.2 us before, 191.8 us with #171 alone, and 187.1 us with both; at 4096 x 2048, 58.8, 53.5 and 52.2 us. Most of its change comes from the discharged guards, and about 2 percent of the time from the divider.

## What the numbers say

On this GPU and at these sizes, the two changes took 19 to 21 percent off the layer norm, 11 to 13 percent off the cooperative stencil, and 16 to 23 percent off the two-pass sum with one element a thread. They changed nothing measurable in the sum with scalar loads or in the `parallel` stencil, whose kernels they do not touch, and none of the CUDA kernels in the table moved between builds by more than 0.19 us. The 20 process medians of each CAIRN figure span at most 4.8 percent of it (the smaller layer norm) and under 1 percent for 20 of the 30, so the changes above are far outside that spread.

The kernels are still well behind the CUDA on this GPU: the layer norm takes 2.1 to 2.3 times as long, the cooperative stencil 1.5 times, the sum with scalar loads 1.4 times, and the sum with one element a thread 1.15 to 1.19 times. Those gaps are larger than the 11 to 26 percent the RTX 5070 Ti record gave for the same pairs, and the two are not comparable. Besides the different GPU, #164 has since changed what these kernels compile to below sm_100, and it applies here and not on sm_120. The reference build shows that the runtime's change between a39cd3d and 16e49c3 does not act in one direction: it made the `parallel` stencil 11 percent slower (143.1 to 158.4 us) and the layer norm 4 percent faster (141.8 to 135.7 us), although the layer norm's SASS more than doubled. A likely reading, which nothing here tests, is that the GH200's memory is fast enough for the instructions around CAIRN's checks, not memory, to set these kernels' pace: the CUDA layer norm reads its 67 MB input and writes its 67 MB output in 52.8 us, about 2.5 TB/s if each is moved once.

## What this does not show

One GH200, one driver, one nvcc release, sm_90 only; nothing here says what the changes do on sm_100 or later, where the trap is lowered as before #164, or on the RTX 5070 Ti of the earlier record. Only these pairs at these sizes were timed. Nothing was profiled: which instructions the time went to is inferred from the SASS and from which kernels changed, not measured. The bench programs print each variant's median and fastest call, not every call, so the spread given is between processes, not within one. The host was loaded by other agents' suites, which the enqueued figures are the least sensitive to; the synchronous `cf_` entries were timed too and are in the raw records. Instruction counts show the code's shape, and the timings above are the only speed claims this record makes.

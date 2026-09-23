# Fused regions against the regions they were written as

`fusion.json` is one run of `bench/cpu/fusion.py --rounds 3` on 2026-09-22, repriced with `--reprice` once the model had learned two things this run showed it: a fused body reads back from a register or L1 what an earlier body of its lane just wrote, and an allocation of 32 MiB or more faults every page on every call (`evidence/v1_4/perf_model/README.md`). The host is an AMD Ryzen 7 7800X3D (eight cores, sixteen lanes, 96 MiB L3) under WSL2, with clang++ 21.1.8 and g++ 13.3, each with the build's own flags. Other agents were building and testing on the machine throughout, and its load average rose from about nine to about twelve during the run's seven minutes. Written and fused alternate in order from one size and round to the next, so a change of load falls on both, and each cell is the median of three rounds of `cairn.perf.measure` medians.

Every kernel is one source built twice, as written and with `plan f { fuse K; }`. `blend` keeps its intermediate in a local buffer only its two regions touch, so the fused chain holds it in each lane and never allocates it. `layers` writes three arrays its caller keeps, so fusing saves passes over memory and no array. `energy` squares on the lane pool and then sums in order on one thread; fused, it squares inside that in-order fold, so the pool is never used. `digest` hashes on the pool and folds with a pooled reduce; fused, it hashes inside the pool's blocks.

| kernel | compiler | n | written ms | fused ms | measured | predicted |
|---|---|---|---|---|---|---|
| blend | clang++ | 1e5 | 0.067 | 0.038 | 1.77x | 3.04x |
| blend | clang++ | 1e6 | 0.447 | 0.114 | 3.93x | 5.94x |
| blend | clang++ | 1e7 | 65.485 | 3.305 | 19.81x | 27.14x |
| blend | clang++ | 3e7 | 213.316 | 13.986 | 15.25x | 16.59x |
| layers | clang++ | 1e5 | 0.082 | 0.032 | 2.56x | 2.47x |
| layers | clang++ | 1e6 | 0.398 | 0.283 | 1.41x | 1.61x |
| layers | clang++ | 1e7 | 14.672 | 10.107 | 1.45x | 1.31x |
| layers | clang++ | 3e7 | 52.036 | 33.658 | 1.55x | 1.53x |
| energy | clang++ | 1e5 | 0.104 | 0.068 | 1.53x | 0.81x |
| energy | clang++ | 1e6 | 1.119 | 0.694 | 1.61x | 0.73x |
| energy | clang++ | 1e7 | 64.878 | 7.153 | 9.07x | 5.99x |
| energy | clang++ | 3e7 | 180.977 | 22.821 | 7.93x | 6.51x |
| digest | clang++ | 1e5 | 0.067 | 0.031 | 2.19x | 3.16x |
| digest | clang++ | 1e6 | 0.413 | 0.120 | 3.44x | 7.55x |
| digest | clang++ | 1e7 | 60.782 | 0.979 | 62.06x | 231.86x |
| digest | clang++ | 3e7 | 195.869 | 5.522 | 35.47x | 55.03x |
| blend | g++ | 1e5 | 0.061 | 0.023 | 2.66x | 3.04x |
| blend | g++ | 1e6 | 0.504 | 0.151 | 3.34x | 5.94x |
| blend | g++ | 1e7 | 68.129 | 5.067 | 13.45x | 27.14x |
| blend | g++ | 3e7 | 212.905 | 15.298 | 13.92x | 16.59x |
| layers | g++ | 1e5 | 0.052 | 0.048 | 1.08x | 2.47x |
| layers | g++ | 1e6 | 0.492 | 0.228 | 2.16x | 1.61x |
| layers | g++ | 1e7 | 15.277 | 10.533 | 1.45x | 1.31x |
| layers | g++ | 3e7 | 57.871 | 34.730 | 1.67x | 1.53x |
| energy | g++ | 1e5 | 0.137 | 0.070 | 1.97x | 0.81x |
| energy | g++ | 1e6 | 1.583 | 0.723 | 2.19x | 0.73x |
| energy | g++ | 1e7 | 77.853 | 7.498 | 10.38x | 5.99x |
| energy | g++ | 3e7 | 229.662 | 22.648 | 10.14x | 6.51x |
| digest | g++ | 1e5 | 0.103 | 0.046 | 2.25x | 3.16x |
| digest | g++ | 1e6 | 0.491 | 0.318 | 1.54x | 7.55x |
| digest | g++ | 1e7 | 63.015 | 1.623 | 38.82x | 231.86x |
| digest | g++ | 3e7 | 192.527 | 5.642 | 34.12x | 55.03x |

Most of the large gains at ten million elements and more are not a pass over memory saved. Each written kernel there allocates a fresh zeroed buffer of 80 to 240 MB on every call, which the allocator maps afresh and whose every page faults when it is zeroed, at 2.7 us a page on this host. A fused chain that holds the buffer in its lanes pays none of that. Below 32 MiB the allocator reuses what the previous call released, so a repeated call pays no fault, and the gains there come from the passes and the pool starts a chain saves.

`layers` isolates what fusion itself saves, since it keeps every array: one pass over the extent instead of three, reading back what the earlier bodies just wrote. It ran 1.08x to 2.56x faster under both compilers, and the model predicts 1.31x to 2.47x.

The model is wrong where it matters most for `cairn tune` in two places. It predicts that fusing `energy` below ten million elements loses, 0.73x to 0.81x, because the fused squares then run on the fold's one thread; they ran 1.5x to 2.2x faster, because the fold is bound by the latency of its dependent adds and the independent squares hide in that latency, while the model adds the costs of the two. It also overestimates `digest` fused at ten million elements, 232x against 39x to 62x. A tuner reading these predictions would keep `energy` unfused at those sizes, which costs time and never correctness. Both are open.

What this does not show: one machine, four kernels, a loaded host, and no comparison with a hand-fused C++ loop or another language. Nothing ran on a device: a fused device chain is compiled for `sm_120` and never run, and a device reduce is never fused. That a fused chain changes no result is what `tests/soundness/test_fusion.py` holds, under both compilers and ThreadSanitizer; this record says only how long the two versions took here.

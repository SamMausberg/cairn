# Fused regions against the regions they were written as

`fusion.json` is one run of `bench/cpu/fusion.py --rounds 3` on 2026-09-22, repriced with `--reprice` after the model learned that a fused body reads back what an earlier body of the same lane wrote from a register or L1. The host is an AMD Ryzen 7 7800X3D (eight cores, sixteen lanes, 96 MiB L3) under WSL2, with clang++ 21.1.8 and g++ 13.3, each with the build's own flags. Two to five other agents were building and testing on the machine throughout, and its load average fell from about nine to about three during the run. Written and fused alternate in order from one size and round to the next, so a change of load falls on both, and each cell is the median of three rounds of `cairn.perf.measure` medians.

`blend` writes its intermediate into a local buffer that only its two regions touch, so the fused chain holds it in each lane and never allocates it. `layers` writes three arrays its caller keeps, so fusing its three regions saves passes over memory and no array.

| kernel | compiler | n | written ms | fused ms | measured | predicted |
|---|---|---|---|---|---|---|
| blend | clang++ | 1e5 | 0.048 | 0.013 | 3.55x | 3.03x |
| blend | clang++ | 1e6 | 0.423 | 0.072 | 5.90x | 5.94x |
| blend | clang++ | 1e7 | 57.644 | 2.611 | 22.07x | 3.42x |
| blend | clang++ | 3e7 | 178.866 | 12.697 | 14.09x | 3.61x |
| layers | clang++ | 1e5 | 0.049 | 0.020 | 2.47x | 2.47x |
| layers | clang++ | 1e6 | 0.270 | 0.126 | 2.14x | 1.61x |
| layers | clang++ | 1e7 | 13.271 | 8.791 | 1.51x | 1.31x |
| layers | clang++ | 3e7 | 53.180 | 32.513 | 1.64x | 1.53x |
| blend | g++ | 1e5 | 0.055 | 0.017 | 3.31x | 3.03x |
| blend | g++ | 1e6 | 0.466 | 0.094 | 4.94x | 5.94x |
| blend | g++ | 1e7 | 58.228 | 2.882 | 20.21x | 3.42x |
| blend | g++ | 3e7 | 178.263 | 12.390 | 14.39x | 3.61x |
| layers | g++ | 1e5 | 0.048 | 0.021 | 2.32x | 2.47x |
| layers | g++ | 1e6 | 0.252 | 0.120 | 2.10x | 1.61x |
| layers | g++ | 1e7 | 12.772 | 8.806 | 1.45x | 1.31x |
| layers | g++ | 3e7 | 49.997 | 32.547 | 1.54x | 1.53x |

Most of `blend`'s gain at ten million elements and more is not the second pass over memory. The written version allocates a fresh zeroed buffer of 80 to 240 MB on every call, and its first touch pays a page fault per 4 KiB page. From these numbers that costs about 2.4 us a page on this host; that is inferred from the difference, not measured on its own. The model prices an allocation as its bytes written at the host's bandwidth and a constant measured on a one-element allocation, so it predicts 3.4x where 14x to 22x ran. That gap belongs to the allocation model, not to fusion, and it is open.

`layers` isolates what fusion itself saves: one pass over the extent instead of three, reading back in the same lane what the earlier bodies just wrote. It ran 1.45x to 2.47x faster, and the repriced model predicts 1.31x to 2.47x. Before the model priced that read-back as a hit it predicted 1.0x to 1.2x from 1e6 up.

What this does not show: one machine, two kernels, a shared host, and no comparison with a hand-fused C++ loop or another language. Nothing ran on a device: a fused device chain is compiled for `sm_120` and never run. A fused chain changes no result, which `tests/soundness/test_fusion.py` holds under both compilers and ThreadSanitizer; this record says only how long the two versions took here.

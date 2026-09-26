# Host timing

These scripts time code on the host CPU by the wall clock, each against the code it replaces, under both compilers unless the table says otherwise. They need `g++` and `clang++` with C++20 and nothing else, and none of them runs on a device. A timing is one machine at one moment, so each script records the machine and its load.

| File | What it times | Writes by default |
|---|---|---|
| `paired.py` | `examples/basics/native.cairn` against the independent C++ in `reference.cpp`, both driven by `driver.cpp` on one pinned core under `clang++`, plus a comparison of object sections; it needs `results/native/` from `tools/checks/verify.py` | `results/timing/` |
| `entry_checks.py` | calls between CAIRN functions with and without the callee's entry checks, alternating the two builds | `results/timing/entry_checks.json` |
| `fusion.py` | fused chains against the regions they were written as, interleaved, beside the model's prediction for each; `--reprice` redoes the prediction alone | `results/fusion/fusion.json` |
| `host_regions.py`, `host_regions.cpp` | a host `parallel` region (`cr::par::run`) against its sequential loop: the cost at each size, the size where the region starts to win, and one small region repeated | `evidence/v0_8_2/host_regions/benchmark.json`, merged under `--label` |
| `host_tasks.py`, `host_tasks.cpp` | repeated task pipelines on `cr::par::Task` and `cr::par::Group` (`--cxx`, default `clang++`), against older headers with `--before DIR` | `evidence/v1_0/runtime/benchmark.json`, merged under `--label` |
| `scan.py`, `scan.cairn` | the pooled host `scan` against its loop, and `std.sort.radix_sort` against the heapsort, timed inside one process | `--out FILE` (required) |
| `std_input.py` | reading all of standard input into a `Vec[u8]`, and pushing into one, from a file and through a pipe, by this tree's `std` and, with `--before DIR`, an older checkout's, alternating; wall time and peak memory from GNU time | `--out FILE` (required) |

`host_regions.py` and `host_tasks.py` write into the record they were run for. To measure without touching `evidence/`, give `--out` a path under `results/`. Each writes nothing unless every case agreed with its own sequential result.

```sh
python3 tools/checks/verify.py && python3 bench/host/paired.py
python3 bench/host/fusion.py --rounds 3
python3 bench/host/scan.py --out results/bench_scan/scan.json
```

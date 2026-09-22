# The performance model, calibrated and checked against measurements it was not fitted to

`cairn predict` prices a program from its checked work and a machine profile, without building or running it. This directory holds the one host profile the package ships, how it was measured, and how well its predictions matched two sets of measurements that calibration never saw. Every number here is from one machine, and the device side is a published specification that no run has confirmed.

## The profile

`zen4-7800x3d.json` was written by `python -m cairn.perf.calibrate --out ...` on 2026-09-22: an AMD Ryzen 7 7800X3D (eight cores, sixteen lanes, 32 KiB L1d and 1 MiB L2 per core, 96 MiB L3) under WSL2, clang++ 21.1.8, `-march=x86-64-v4` with the build's own flags. Every figure comes from a CAIRN kernel compiled and timed by `cairn.perf.measure`: stream bandwidth at four working-set sizes on one thread and on every lane, the latency of an access whose address comes from data, the cost of starting a host region on two to sixteen lanes, a task start, an allocation, and a shared atomic. The cost of each kind of operation is a non-negative least-squares fit over twenty-four single-purpose kernels, each counted by the same counter the predictions use, built once with the vectorizer and once without. The clock, 4.67 GHz, is llvm-mca's cycle count for a dependent xor-shift-multiply chain divided by its measured time. `--arch x86-64 --into` then measured the same operation kernels for the family baseline, which a project builds for by default, so a default build is priced at its own vector width: there a 64-bit multiply, a double and a compare keep a loop scalar, which on `x86-64-v4` they do not.

The machine was shared: five other agents were building and testing on it throughout. Each figure is the least-disturbed of nine blocks, because a busy machine only ever adds time, so a quiet machine may be a little faster. Three calibration runs within an hour measured a task start at 31, 85 and 121 us, which is the size of the noise in the least stable figures.

## The validation

`tools/checks/perf_validation.py` predicts every CAIRN arm of the preregistered suite's eight kernels at six sizes and compares with two sets of timings. `validation_v1_3_record.json` compares with `evidence/v1_3/bench/suite.json`, the suite's recorded first run on this machine, under both compilers. `validation_fresh.json` compares with `fresh_record.json`, the same entry points timed on the same day by `cairn.perf.measure` on the shared machine, clang++ only, including the two 1.4 arms the first run did not have. No kernel of the suite is among the calibration kernels.

| measurements | points | median error | within 2x | Kendall tau | tau within one size |
|---|---|---|---|---|---|
| v1.3 record, clang++ | 60 | 44% | 41 | 0.92 | 0.67 to 0.96 |
| v1.3 record, g++ | 60 | 42% | 47 | 0.91 | 0.60 to 0.87 |
| fresh, clang++ | 66 | 33% | 47 | 0.86 | 0.31 to 0.88 |

The error depends on the regime, and the table says where to trust the model and where not to.

| regime (clang++) | v1.3 record | fresh |
|---|---|---|
| up to 1e4 elements, one lane | 23% median error, 16 of 20 within 2x | 20%, 18 of 22 |
| 1e5 to 1e7 elements, a wide region | 57%, 15 of 30 | 71%, 18 of 33 |
| 1e8 elements, bound by memory | 6%, 10 of 10 | 16%, 11 of 11 |

Small regions, sequential folds and streams too large for the last cache are predicted within tens of percent. A wide host region between a hundred thousand and ten million elements is the model's weak range: both sets of timings found such regions up to ten times slower than predicted, and the fresh timings of one kernel at one size varied by up to six times from one run to the next as the other agents' load changed. The model says so: every prediction for a wide host region carries `medium` confidence and names this finding. A histogram's bins and anything else addressed by data carry `low`, and the model tells the agent to measure.

The ranking is better than the times. Within one size, a Kendall tau of 0.6 to 0.96 on the v1.3 record means the model orders kernels mostly correctly, which is what a tuner needs from it. The fresh timings at 1e5 and 1e6, the noisiest points, rank worse.

## What this does not show

Nothing here is a device measurement. `src/cairn/perf/profiles/rtx-5070-ti.json` is NVIDIA's published figures for the RTX 5070 Ti with four assumed values marked as assumed, and device kernels are read at compile time (ptxas registers and spills, cuobjdump instruction mix) and never run. The device model has no validation: it gains one only from device timings the owner runs. Nothing here compares CAIRN with another language, and one machine is not a population: another host needs its own calibration, and `cairn predict` says when it is using a profile measured somewhere else.

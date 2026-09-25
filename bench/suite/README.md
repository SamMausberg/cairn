# Preregistered CPU suite

This suite times eight kernels on the host, each written in CAIRN and as plain, OpenMP and oneTBB C++, and built under `g++` and `clang++` with the project's flags. [PREREGISTRATION.md](PREREGISTRATION.md) fixed the protocol, kernels, arms, sizes and acceptance rule before any run. `make bench` runs the whole sweep, which takes hours.

| File | What it does |
|---|---|
| `harness.py` | emits each kernel, probes whether OpenMP and oneTBB run in parallel here, builds every arm, counts each arm's safety boundaries against the CAIRN build receipt, runs what built, and writes `results/bench_suite/`; it writes nothing when a case disagrees with its sequential result |
| `report.py` | reads `results/bench_suite/suite.json` and prints the tables, losses beside wins; it measures nothing |
| `kernels/<name>/` | each kernel's CAIRN source (`kernel.cairn`), its arms (`cairn.cpp`, `plain.cpp`, `omp.cpp`, `tbb.cpp`), the `case.hpp` they share, and a Python `oracle.py`; the result an arm dumps must equal the oracle's `expected(n)` exactly, unless the oracle states its own `agrees(n, result)` |
| `bench.hpp`, `guards.hpp`, `omp_arm.hpp`, `tbb_arm.hpp` | the timing loop, the guards a guarded baseline arm pays, and the two library arms |

It needs `g++` and `clang++`, and an arm whose library (OpenMP, oneTBB) is missing is reported as unavailable. The harness never writes under `evidence/`; `evidence/v1_0/bench/` holds a release's copy.

```sh
python3 bench/suite/harness.py --smoke          # tiny sizes, seconds
python3 bench/suite/harness.py --build-only     # every arm built and priced, nothing timed
make bench                                      # the full sweep, then report.py
```

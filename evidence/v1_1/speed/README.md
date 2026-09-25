# Speed of the suite and of the tools an agent calls

Where the test suite and `cairn check`, `build` and `validate` spent their time before the 1.1 speed work, and what each change of that work cut. Everything ran on one machine on 2026-09-25: an AMD Ryzen 7 7800X3D (8 cores, 16 threads, 70 GB) under WSL2 (Linux 6.18), Python 3.12.3, clang++ 21.1.8, g++ 13.3, nvcc 13.2.78. Four other agents ran test suites on the same machine throughout, so the load average moved between 5 and 590 while these numbers were taken. Every comparison below is interleaved: the before and after arms alternate in fresh processes, and the load is given beside each. Read the ratios, not the absolute seconds.

## Where the suite's time went

`pytest -q tests -n 3` on main at 3c4b9e3 passed 5611 tests and skipped 58 in 1269 seconds. A second run with a measuring plugin, kept out of the repository, recorded every subprocess and every `compile_program` call of each test. Of 4967 seconds summed over tests in that run:

| What ran | Seconds | Share |
|---|---|---|
| nvcc, 253 device builds | 1286 | 25.9% |
| the Python compiler and the tests themselves | 1129 | 22.7% |
| clang++, 779 compiles | 722 | 14.5% |
| Python subprocesses (scripts under `tools/`, demos, isolated calls) | 549 | 11.1% |
| g++, 342 compiles | 484 | 9.8% |
| built programs | 409 | 8.2% |
| sanitized programs under `setarch` | 235 | 4.7% |

The suite seldom builds one program twice: 626 of 779 clang++ compiles had distinct arguments and sources. It does check one source again and again: 14690 `compile_program` calls on 5739 distinct sources, and the repeats cost 358 of their 548 seconds. `examples/tensor/tile64.cairn` alone was checked 14 times, at 13.6 seconds a check under that load.

## The phase rule

`cairn check examples/tensor/tile64.cairn` spent all but a second in the phase rule's run of one block (`compiler/cooperative`). Four things made that run slow, and none of them was the rule. Every writer of an element was compared with every access to it, so an element one warp's store wrote 32 times cost 1024 comparisons; `arith` built a table of fifteen closures for each of 547000 integer operations; a fragment's footprint was worked out again for each of a warp's 32 threads; and `Layout.shape` and `cosize` were recomputed on each of 427000 reads. The change decides an element with no possible conflict from who made its accesses, keeps the operations in one table, works each footprint out once per call and each layout's shape once per value, and expands each access into its threads and elements in one place.

`cairn check` from a fresh process, the median of four interleaved runs, main at 349c77b against the change, load 86 to 156:

| Program | Before, s | After, s |
|---|---|---|
| `examples/tensor/tile64.cairn` | 9.48 | 4.11 |
| `examples/tensor/tile32.cairn` | 3.03 | 1.34 |
| `examples/tensor/transpose.cairn` | 1.27 | 1.00 |
| `examples/cooperative/tuned.toml` | 0.25 | 0.21 |

Later that day, with the machine nearly idle (load 3), the same comparison gave 5.51 against 2.71 seconds for tile64, 1.75 against 0.82 for tile32 and 0.77 against 0.60 for the transpose.

What the checker says is unchanged. Every program `tools/checks/emission_identity.py` takes, 2101 of them with 1173 refusals, gives the same C++, the same whole manifest and the same whole refusal record, message included. 3000 regions from `tools/checks/differential_cooperative.py`'s generator, with some writes made atomic updates, some reads at an index the rule cannot follow and some arrays left unzeroed, give the same record before and after: 974 accepted, and every code of the rule among the refusals (E-COOP-UNWRITTEN 701, E-COOP-CONFLICT 665, E-COOP-BARRIER 289, E-ATOMIC-MIXED 225, E-COOP-UNORDERED 69, E-COOP-REUSE 59, E-COOP-UNDECIDED 18). `tools/checks/differential_cooperative.py --count 1000` agreed with the Lean model on all 1000 regions. `cairn predict --card all`, which runs the same block for its census, printed identical output for both tile examples, the transpose, `examples/cooperative/tuned.toml` and `examples/reduction/gpu.toml`.

## CUB only where a program has a device collector

Every device program read CUB's reduce and scan headers through `runtime/cairn_gpu.hpp`, and CUB is about half of what nvcc reads and compiles for a small program: its host preprocessing, cudafe++, cicc and host compile all shrink without it. Only a device `reduce`, `scan` or `compact` calls CUB. Its calls now live in `runtime/cairn_cub.hpp`, which the lowering includes only in a program with one of those, so a program without one builds to the same kernels and no longer carries CUB's `EmptyKernel`, which CUB defines wherever it is included.

Every device example built for sm_120 by `tools/checks/device_examples.py --targets sm_120 --jobs 1`, main at 349c77b against the change, in the order main, change, change, main. The first three runs saw load 3 to 8 and the last rose to 48; the table gives each example's mean of its two runs, in seconds, including the check of its source.

| Example | Main | Change |
|---|---|---|
| `demos/numeric/gpu.toml` | 12.5 | 3.6 |
| `examples/apps/analytics/gpu.toml` (a device reduce and compact) | 15.4 | 11.7 |
| `examples/apps/gpu_pipeline/cairn.toml` (a device reduce and compact) | 11.1 | 10.3 |
| `examples/apps/matmul/gpu.toml` | 8.6 | 3.4 |
| `examples/apps/simulator/cairn.toml` | 8.8 | 3.8 |
| `examples/cooperative/gpu.toml` | 8.6 | 3.5 |
| `examples/cooperative/tuned.toml` | 8.4 | 3.4 |
| `examples/foreign/device/cairn.toml` (vendored CUDA that calls the synchronous reduce) | 20.8 | 13.6 |
| `examples/harness/cairn.toml` | 8.9 | 2.6 |
| `examples/reduction/gpu.toml` | 9.2 | 3.5 |
| `examples/tensor/tile32.cairn` | 11.5 | 6.5 |
| `examples/tensor/tile64.cairn` | 15.9 | 11.8 |
| all twelve | 139.8 | 77.6 |

The four runs took 131 and 171 seconds on main and 82 and 92 with the change. The test modules the CI device jobs run (`make device-build`, at `-n 3`) took 757 seconds on main and 475 with the change, one right after the other, the load 18 when the first began and 29 when the second ended.

Of the 2101 programs `tools/checks/emission_identity.py` takes, 2090 emit the same C++ as before. The other 11 are exactly the programs with a device collector, and each differs by one added line, `#include "cairn_cub.hpp"`.

## What `cairn validate` and `cairn predict` did twice

`cairn validate` built the base and the selected library one after the other, though neither needs the other; it now starts both builds at once. `cairn predict --device-target sm_120 --inspect` checked its program four times, once to count its work, once for the target's demands and twice to read its kernels; it now checks it once, through the per-process cache `compiler/compilations.py` keeps for the other tools, and each part reads its own copy. `cairn predict --card all` checked it twice, and now once.

Main at b14beb5 against the change, interleaved in fresh processes, load 9 to 10:

| Command | Main, s | Change, s | Runs |
|---|---|---|---|
| `cairn validate examples/implementations --symbol prefix_by4` | 4.09 | 3.29 | 6 |
| `cairn predict examples/tensor/tile64.cairn --device-target sm_120 --inspect` | 16.16 | 9.10 | 2 |
| `cairn predict examples/tensor/tile64.cairn --card all` | 6.83 | 4.66 | 2 |
| `cairn predict examples/apps/analytics` | 0.54 | 0.56 | 2 |

Each command printed the same output before and after, and the validation the same record. A program checked only once pays for keeping its check: 14 ms of 125 for analytics and 38 of 1750 for tile32, in-process, which the last row does not separate from no change.

## What did not run

Nothing here ran on a GPU. The suite's device builds compile for sm_120 and stop there, as everywhere outside `make gpu`. Only CUDA 13.2 is installed on this machine; the CI device jobs build the same modules under CUDA 12.9 as well.

# One numerical policy, and counterexamples that block

Records of the validation changes after 1.0.0: one numerical policy for every path that compares float results, device tests that write every input exactly, a Z3 counterexample replayed natively, and validation records keyed by the policy and the native compiler. Everything here ran on 2026-09-24 on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2, with clang 21.1.8, g++ 13.3.0, nvcc 13.2 (compile only) and Z3 4.8.12. Four other agents were building and testing on the machine throughout, with a one-minute load average between 10 and 26.

| File | What it holds |
|---|---|
| `implement.json` | `demos/implement/run.py` on the tree this record was committed with: the packet, every submission with the host's full answer, both searches with every round's times, the difference report and the run of the program with the chosen selection. |

## What ran

The full suite at `2b1658f`, the commit that brought in `verify/agreement.py` and the device generator's exact inputs, passed 4875 tests and skipped 54, four workers, in 17 minutes. Among them, `tests/verification/test_agreement.py` drove the host's verdict and the generated CAIRN function, built by clang++ and by g++, over 54 pairs under 7 tolerances each, and every verdict matched. `tests/verification/test_device_validation.py` compiled generated device tests holding a NaN, both infinities, `-0.0`, a 1000-element view and views one element off for sm_120 with nvcc, and ran the generated tests of a host function under both compilers: its implementation returns a checksum computed from the inputs as given, so the tests pass only if every bit reached the reference.

The full suite on the code of the second commit, rebased on the host emulation of device code, passed 5024 tests and skipped 54, four workers, in 23 minutes; the implement demo ran among them. The tests it adds include a validation whose finite cases pass and whose Z3 counterexample, `n = 1, xs = [12345]`, fails natively: the validation fails, keeps that input, the implementation session refuses it, and `cairn tune` does not choose it. They also include a counterexample outside the admitted values and one within the tolerance, each leaving the finite pass standing, and a validation built by g++ that `cairn tune`, building with clang++, does not cite.

The implement demo told the story its README tells. The packet grew from 7,353 to 7,650 bytes, because it now pins the numerical policy (`agreement` and `agreement_sha256`). Z3 answered `unknown` for every submission, so nothing was replayed. The search chose `plan sumsq use sumsq_blocks[8];` on this run; which instance wins depends on the machine and its load, and the times in `implement.json` were taken on a busy machine and say nothing about the implementations' speed.

## What did not run

Nothing ran on a GPU. The generated device tests compile for sm_120 and have not run under Compute Sanitizer, which only `make gpu` does. The host emulation of device code that another track is adding was not used. No proof covers the policy: its two renderings agree on the table the test drives, which is finite testing.

# The preregistered CPU baseline suite, 1.4 run

One full run of `bench/suite/` on 2026-09-22, from 20:48 to 21:04, commit `a090489`, under [bench/suite/PREREGISTRATION.md](../../../bench/suite/PREREGISTRATION.md) with its 1.4 addendum. The preregistration's sha256 at that commit is `38b58f50eeb58e5dab9a0934cfec2903cd5418264390c50ce78ba3613ac0c8a6`, last changed in `d1e2f97`. The 1.4 addendum was committed before any 1.4 measurement: it adds the `matched` baseline build (a baseline that checks exactly what the CAIRN arm still checks), the `cairn_pool` arm of `sum_u64_wrap` and the `cairn_blocks` arm of `histogram_u32`, and changes no kernel, size or threshold. `suite.json` is the raw record the harness wrote and `report.md` is what `bench/suite/report.py` printed from it. Nothing here was edited by hand.

Host: the machine of the 1.3 run, Linux x86-64 under WSL2, sixteen lanes, `x86-64-v4`, CPUs not pinned; g++ 13.3.0 and clang++ 21.1.8; oneTBB and OpenMP present for both compilers. The load average was 1.06 when the run began, with no agent of this repository running, while another session on the machine was doing light work of its own. An earlier attempt at this run, from 14:24 the same day, overlapped a whole test suite and is not used.

| kernel | CAIRN arm | against plain C++ (matched) | against OpenMP (matched) | against oneTBB (matched) |
| --- | --- | --- | --- | --- |
| `saxpy_f32` | region | win from n=10^5 (1.59 clang++, 1.62 g++) | level (1.02 to 1.16) | level (0.97 to 1.03) |
| `mixed_u64` | region | win from n=10^5 (1.80, 1.55) | level (1.05 to 1.50) | level (1.01 to 1.11) |
| `sum_u64_wrap` | sequential `reduce` | level (1.14, 1.07) | level (0.58 to 0.94) | loss (0.56 to 0.66) |
| `sum_u64_wrap` | `cairn_pool`, `reduce + parallel` | win from n=10^6 (1.75, 1.91) | level (1.04 to 1.45) | level (0.99 to 1.03) |
| `sum_u64_wrap` | `cairn_atomic` | loss (0.03) | loss (0.02) | loss (0.02) |
| `dot_f64` | sequential in-order fold | level (1.02, 1.05) | loss (0.41 to 0.49) | loss (0.40 to 0.41) |
| `compact_even` | collector | level (0.99, 0.91) | level (0.66 to 1.19) | level (0.65 to 0.97) |
| `histogram_u32` | sequential bins | level (1.02, 1.01) | loss (0.21 to 0.38) | loss (0.18 to 0.25) |
| `histogram_u32` | `cairn_blocks`, lane-owned blocks | win from n=10^6 (3.69, 4.59), cairn checks more | level (0.86 to 1.38) | level (0.81 to 0.91) |
| `stencil_1d` | region | win from n=10^7 (1.47, 1.39) | level (0.98 to 1.08) | level (0.95 to 0.98) |
| `tasks_split` | four leased parts | no equal-boundary baseline | no equal-boundary baseline | no equal-boundary baseline |

What changed since 1.3. Two of the three losses the preregistration predicted for 1.3 were losses of a shape the language could not write, and 1.4 can write both. A parallel host `reduce` over wrapping integers is level with OpenMP and oneTBB and beats the sequential loop by the preregistered margin from a million elements. A histogram whose lanes each count a block into their own bins is level with the privatized OpenMP and oneTBB histograms and beats the sequential loop about fourfold, while checking more than either baseline. The third loss stands by design: `dot_f64` folds floats in the written order, which a reassociating parallel reduction does not, and `reduce op parallel` refuses floats for that reason. The atomic arm of `sum_u64_wrap` measures one shared atomic add per element, which is the cost the language's `atomic` row names, and is not a way to sum.

The regions are level with OpenMP and oneTBB at equal guards, as in 1.3. Against the matched sequential loop, `saxpy_f32` and `mixed_u64` now win from a hundred thousand elements rather than ten million and `stencil_1d` still from ten million, because the matched build compares CAIRN as it now emits with a C++ loop that checks exactly what CAIRN still checks, where 1.3 compared it with one that checked everything.

`tasks_split` has no ratio this time: its CAIRN arm keeps part of a boundary category that the matched build turns on whole, so no baseline's counts equal it, and the rule forbids the ratio. Its times are in `report.md`.

What was not measured. The clang++ OpenMP rows in the `cairn_claim_on_demand` grain did not build, as in 1.3: this machine has LLVM 18's `libomp` and no LLVM 21 one. Nothing here says anything about a tuned kernel, another host or core count, the GPU, or any claim the documentation keeps separate from benchmarked. One run on one machine is not a population.

# The preregistered CPU baseline suite, first run

One full run of `bench/suite/` on 2026-09-22, commit `892b309`, as [bench/suite/PREREGISTRATION.md](../../../bench/suite/PREREGISTRATION.md) fixed it before any number existed. The preregistration's sha256 at that commit is `d2ca179d84ca61c405ed6e6587f1debca90544485bd72cb3ce9f90a7887a8646`, last changed in `6fdae7b`, which reworded two sentences and touched no kernel, size, arm or threshold. `suite.json` is the raw record the harness wrote (every build command, every safety-boundary count, every timed block) and `report.md` is what `bench/suite/report.py` printed from it. Nothing here was edited by hand.

Host: Linux x86-64 under WSL2, sixteen lanes, `x86-64-v4` profile, CPUs not pinned; g++ 13.3.0 and clang++ 21.1.8; oneTBB and OpenMP present for both compilers. The acceptance rule is the preregistered one: a win is a median ratio of at least 1.25 in CAIRN's favour that holds at every larger size under both compilers, over blocks covering 16 million elements, median of 9 after warm-up. Ratios compare arms with equal safety boundaries only; every unguarded baseline arm is excluded from ratios and reported separately as the cost of the boundary.

| kernel | against plain C++ (guarded) | against OpenMP (guarded) | against oneTBB (guarded) |
| --- | --- | --- | --- |
| `saxpy_f32` | win from n=10^7 (1.47 clang++, 1.54 g++) | level (0.98 to 1.03) | level (0.97 to 0.99) |
| `mixed_u64` | win from n=10^7 (1.53, 1.47) | level (1.24 to 1.28 clang++, 1.06 to 1.07 g++) | level (0.92 to 1.00) |
| `sum_u64_wrap` | level (1.14, 1.33) | loss (0.68 to 0.84) | loss (0.62 to 0.72) |
| `dot_f64` | level (1.00, 1.03) | loss (0.36 to 0.47) | loss (0.36 to 0.41) |
| `compact_even` | level (1.07 clang++, 0.77 g++) | level (1.17 to 1.23 clang++, 0.66 g++) | level (0.98 clang++, 0.64 g++) |
| `histogram_u32` | level (1.03, 1.00) | loss (0.25 to 0.37) | loss (0.19 to 0.30) |
| `stencil_1d` | win from n=10^7 (1.33, 1.30) | level (1.03 to 1.11) | level (0.98 to 1.00) |
| `tasks_split` | win from n=10^7 (1.25, 1.26) | level (0.99, 1.01) | level (0.96, 1.01) |

What the table says. A CAIRN host region against the sequential loop written in C++ with the same guards wins by the preregistered margin on the four kernels where a region applies at the largest sizes, and is level below ten million elements, where the lane pool's hand-off is the whole cost. Against OpenMP and oneTBB at equal worker counts and equal guards, CAIRN's regions are level: every ratio is within a few percent of one, on both compilers, with no kernel meeting the 1.25 bar in either direction. The three losses are the three findings the preregistration named before the run: `sum_u64_wrap` and `dot_f64` use `reduce`, which on the host is a sequential in-order fold, so a parallel reduction beats it and the like-for-like comparison is the level one against the sequential loop; `histogram_u32` cannot be written with parallel lanes because the lane rule forbids the shared-bin shape, so its CAIRN arm is the sequential one and the privatized OpenMP and oneTBB histograms measure a capability the language does not offer.

`dot_f64` is a semantic-difference report, not a speed. Every arm was asked whether it returned the strict in-order fold bit for bit: the CAIRN arm, the plain arms and every oneTBB arm did, and so did the guarded OpenMP arms, while the unguarded OpenMP `reduction(+:)` under g++ did not, since it reassociates. `stencil_1d` recorded its refusal: the in-place shape in `in_place_rejected.cairn` was compiled and the compiler printed `E-PARALLEL-RACE`, as preregistered, while the out-of-place shape was measured like any other kernel.

What the safety boundary costs. For every baseline built once guarded and once not, the unguarded time divided by the guarded time at n=10^8 lies between 0.83 and 1.10 across all kernels, arms and compilers, with most rows within three percent of one, and the plain sequential loop under g++ paying the most (1.07 on `saxpy_f32`, 1.32 on `compact_even`). At these sizes the guards are a small fraction of a memory-bound region.

What was not measured. The clang++ OpenMP rows in the `cairn_claim_on_demand` grain did not build: this machine has LLVM 18's `libomp` and no LLVM 21 one, and the runtime it links lacks a symbol clang 21 emits, so those arms are recorded `did-not-build` and no number stands in for them. The `tasks_split` `std::thread` arm is in the raw record but has no equal-boundary column in the ratio table. Nothing here says anything about a tuned kernel, about another host or core count, about the GPU, or about any claim the documentation keeps separate from benchmarked.

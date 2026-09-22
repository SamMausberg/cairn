# The CPU baseline suite, preregistered

This file fixes what the suite measures, how it measures it, and what a number out of it may be used to say. It exists in git before the first measurement is taken, so that no kernel, size, arm or threshold can be chosen after a result is seen. `bench/suite/harness.py` carries it out and writes nothing but raw timings under `results/`; `bench/suite/report.py` turns those into tables. Recording a run under `evidence/` is a separate, later step with its own gate.

Read this next to [internals.md](../../docs/internals.md), which says which claims are kept apart. Accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are different statements. Nothing here establishes any of the others.

## What the suite is for

CAIRN emits ordinary C++ with a guard at every element, every checked arithmetic operation, every narrowing conversion and every array entry point. The question is what that costs against C++ written the way C++ is written, and against the two host parallelism libraries a systems programmer would otherwise reach for. The question is not whether CAIRN beats a hand-tuned kernel, and no row of these tables may be read that way.

The suite answers three narrower questions. What a boundary costs, by building every baseline twice from one source behind a single `-D`. What CAIRN's host lane pool costs against OpenMP and oneTBB at equal worker counts and equal grain. And where CAIRN's rules forbid a shape the baselines are free to write, which is reported as a finding rather than hidden as a missing column.

## The kernels

Eight kernels are preregistered. Each one is in for a stated reason, and no ninth may be added to a table after a run.

| kernel | why it is in | what a row may claim |
|---|---|---|
| `saxpy_f32` | two flops over twelve bytes, so a large region is bound by memory bandwidth and prices the region itself | ratio |
| `mixed_u64` | a dependent integer mix in registers, already recorded at about 1.5 nanoseconds an element in `evidence/v1_2/host_regions`, so a large region is bound by the cores | ratio |
| `sum_u64_wrap` | wrapping addition is order independent, so a reassociating parallel baseline computes the same function | ratio |
| `dot_f64` | a strict in-order fold and a reassociating reduction are different functions of the same inputs | semantic difference only |
| `compact_even` | the certified collector against `std::copy_if` and a two-pass parallel compaction | ratio |
| `histogram_u32` | the lane rule forbids the shared-bin parallel shape, so the CAIRN arm is sequential | expressiveness, and a ratio against the sequential baseline only |
| `stencil_1d` | the out-of-place shape is accepted in a region and the in-place shape is refused, which no C++ toolchain refuses | ratio, and a recorded refusal |
| `tasks_split` | four visibly disjoint parts under leases, against `std::thread`, OpenMP sections and `tbb::parallel_invoke` | ratio |

`mixed_u64` carries its body verbatim from `bench/host_regions/host_regions.cpp`, and both it and `saxpy_f32` keep that file's input fill, so the numbers chain with `evidence/v1_2/host_regions/benchmark.json` rather than starting a second unrelated series.

Three of the eight carry a finding that is not a speed. They are stated here, before any run, because each one is a limit of the language and not a defect of a baseline.

`sum_u64_wrap` and `dot_f64` both use `reduce`, and on the host `reduce` is an ordinary sequential in-order fold with no threads behind it (`src/cairn/compiler/codegen.py`, `s_reduce`, and `docs/concurrency.md` under "reduce and compact"). The CAIRN arm of a reduction is therefore sequential by construction, and losing to a parallel baseline is the expected outcome, not news. The like-for-like row is CAIRN against the sequential baseline; the parallel baselines measure a capability the host reduction does not have. `sum_u64_wrap` carries a second CAIRN arm, `cairn_atomic`, which is the only parallel host reduction the language offers: one `Atomic[u64]` and a `fetch_add` from every lane.

`dot_f64` is preregistered as a semantic difference report and not as a speed comparison at all. The CAIRN fold and the sequential baseline are the same expression in the same order under `-ffp-contract=off -fno-fast-math`, so they must agree bit for bit. An OpenMP `reduction(+:)` and a `tbb::parallel_reduce` reassociate, so they compute a different function. Its oracle answers two questions apart from each other: whether the arm's own result is right, which is what stops a run, and whether the arm returned the in-order fold bit for bit, which is the finding. A parallel arm answering no to the second is the expected result and is never counted as a failure. No table divides their times.

`histogram_u32` cannot be written as parallel CAIRN. A lane may touch only element `[i]` of anything lanes write (`E-PARALLEL-RACE`, `src/cairn/compiler/checking.py`), a shared-bin histogram writes `out[bin]` from every lane, and `Atomic[T]` is declared in place and never stored in an array, so there is no array of atomics to privatize into. The CAIRN arm is the sequential binned histogram, the like-for-like comparison is against the sequential baseline, and the privatized OpenMP and TBB arms are reported as a capability the language does not offer here.

`stencil_1d` carries the opposite result, stated here because the rule is easy to read as stricter than it is. The lane rule constrains only what lanes write, so the out-of-place three-point stencil, which reads `x[i-1]`, `x[i]` and `x[i+1]` from a read-only view and writes `out[i]`, is accepted and is measured like any other kernel. The in-place form, which writes `x[i]` from the same lane that reads its neighbours, is refused with `E-PARALLEL-RACE`, while g++ and clang++ compile the same shape into a silent data race. `bench/suite/kernels/stencil_1d/in_place_rejected.cairn` holds that program, and the harness compiles it and records the diagnostic code it actually printed, so the asymmetry is established by running the compiler rather than by assertion.

## The six boundaries, and how a baseline carries them

A ratio between CAIRN and a baseline means nothing unless both programs check the same things. Six boundaries are named here, each one traceable to the file that emits it, and every baseline is written twice: once carrying boundaries one to four and once carrying none of them, behind the single `-D` that `bench/suite/guards.hpp` reads.

| boundary | what it is | where the emitter writes it |
|---|---|---|
| 1 | `cr::view` once per array parameter and `cr::disjoint` once per pair including a writer, at every entry point | `src/cairn/compiler/codegen.py`, `Emitter.function`, lines 388 to 394 at this commit |
| 2 | `cr::at` at every element access, and `cr::part` at every visibly disjoint slice handed to a callee | `src/cairn/compiler/codegen.py`, `Emitter.e_index`, lines 185 to 187, and `Emitter.e_slice`, line 196 |
| 3 | `cr::add`, `cr::sub`, `cr::mul` and `cr::divide` at every checked integer operation, and `cr::shr`, which traps on a shift count at or beyond the width | `src/cairn/compiler/codegen.py`, `Emitter.e_binary`, lines 288 to 294, and `src/cairn/runtime/cairn_runtime.hpp`, `shr` |
| 4 | `cr::convert` for a narrowing integer conversion and `cr::truncate` for float to integer | `src/cairn/compiler/builtins.py`, `lower_convert` |
| 5 | the strict floating contract, `-ffp-contract=off -fno-fast-math`, so the written order of operations is the executed one | `src/cairn/projects/toolchain.py`, `STRICT`, line 20 |
| 6 | the exact build line `toolchain.flags` produces, including `-Werror` and the architecture profile this host resolves | `src/cairn/projects/toolchain.py`, `flags` |

The guarded baseline does not imitate a CAIRN guard, it calls one. `guards.hpp` includes the runtime header the emitter includes and forwards each `BG_` macro to the same `cr::` function the emitter writes at that site, so boundary equality is a fact about the two programs and not a claim about them.

Equality is derived rather than asserted, twice over. The CAIRN arm's counts come from the build receipt's `syntactic_check_sites`, summed over that arm's own entry point and every function it calls, which is the program the baseline mirrors; a kernel source that holds a second entry point, as `stencil_1d` and `sum_u64_wrap` do, is never summed whole, because that would price a program no arm runs. The `cr::disjoint` calls are counted in that entry's emitted body, because a disjointness check follows from the parameter list and has no receipt key of its own. Each baseline arm's counts come from the `BG_` sites in its own source, which is why those macros appear only in an arm file and never in a shared case header. A pair whose counts differ in any of the four categories is recorded `boundaries:unequal` and is excluded from every ratio the report prints.

The unit of that count is a site in the source, not a call in the emitted text, and the two are not always the same number. `tasks_split` writes `n - q3` once and the emitter writes `cr::sub` twice for it, once as the length argument of the fourth task and once inside the `cr::part` that task is handed, so the receipt counts four checked arithmetic sites where the emitted file holds five calls. The receipt is what both sides are counted against, because it is the count the language makes and the one a baseline can mirror; counting the emitted text instead would be counting the emitter's repeated subexpressions.

A third check asks the object file rather than the source. `harness.py` compiles each arm's kernel translation unit on its own, finds every section whose name carries the kernel entry, and counts the relocations in it that name `cr::trap` or `abort`, which is where a failed guard ends. The count is read as a difference and not as a total: an arm may call `abort` for a reason of its own, and that relocation stands in both of its builds, so what proves the boundary is that the guarded build has more of them than the unguarded one. A CAIRN arm has no unguarded twin, so its own count is what there is. Where the object does not build or the section cannot be found the result is `unknown`, which is never a pass.

## The arms

| arm | what it is | grain rows |
|---|---|---|
| `cairn` | the emitted function, measured as emitted | not applicable |
| `cairn_wrap` | the same kernel with `add_wrap` and `sub_wrap` where the semantics permit, so the checked index arithmetic is gone, for `stencil_1d` only | not applicable |
| `cairn_atomic` | the parallel host reduction, one `Atomic` and a `fetch_add` from every lane, for `sum_u64_wrap` only | not applicable |
| `plain` | one thread, the same loop, the boundary the `-D` selects | not applicable |
| `threads` | four `std::thread` objects over four disjoint ranges, for `tasks_split` only | not applicable |
| `omp` | OpenMP, a team as wide as the lane pool | all three |
| `tbb` | oneTBB, an arena as wide as the lane pool | two |

`cairn_wrap` exists for `stencil_1d` alone. Every other kernel's emitted code already uses wrapping operators or floating point at the sites in question, so its as-emitted and wrapping forms are the same program and a second arm would be a second copy of the first.

`plain` and every library arm is built twice, guarded and unguarded. A CAIRN arm is built once, because its boundaries are in the generated code and removing them would be a different language.

## Concurrency parity

One number sets every worker count. `CAIRN_LANES` is what `src/cairn/runtime/cairn_parallel.hpp` reads, and the harness gives the same number to `omp_set_num_threads` and to `tbb::global_control(max_allowed_parallelism)`, so the lane pool, the OpenMP team and the TBB arena are the same width by construction. It is the number of CPUs in this process's affinity mask. Nothing is pinned: a region wants every core, and pinning would measure one.

The grain rows exist because CAIRN's claim size is not a constant and a library's default is not CAIRN's. Reading `cairn_parallel.hpp`, a region below `CUTOFF`, which is sixteen thousand three hundred and eighty four elements, is compiled as the ordinary loop it replaces and starts nothing at all. Above it the pool uses `min(n / 8192, lanes)` lanes and hands each one claims of `max(8192, n / (2 * used lanes))` taken on demand from a shared counter, so 8192 is the floor of the claim and not the claim. `bench.hpp` recomputes that number from the same constants and the report records it beside every row.

The three rows are `library_default`, which hands a library nothing and lets it choose; `cairn_claim_assigned`, which hands it CAIRN's claim size assigned in advance, meaning OpenMP's `schedule(static, g)` and TBB's `simple_partitioner` over a range of grain `g`; and `cairn_claim_on_demand`, which hands it the same size taken on demand, meaning OpenMP's `schedule(dynamic, g)`. TBB claims and steals in both of its rows, so it has no third row. `tasks_split` has no grain at all, because its split is fixed at four parts.

A library that is absent, that does not build under the project's flags, or that links and then runs on one thread is recorded `unavailable` with the reason and the attempted link lines. It never becomes a silent sequential baseline. The harness proves parallelism by running a probe and counting the workers it actually saw, not by checking that a link succeeded, because on this machine one OpenMP configuration builds and links and then runs every region on one thread. A single row may also come back unavailable while the rest of its library works, when a runtime cannot link the schedule that row asks for; the harness records the linker's own message and the report leaves that column empty and decides nothing from it.

## The protocol

The protocol is `bench/host_regions/host_regions.py`'s, kept deliberately so that a number here and a number there were taken the same way.

A case is one kernel, one arm, one size. It runs once to warm up, then nine timed blocks, and the median of the nine is the number. Each block repeats the case until it covers sixteen million elements, at most a thousand times, and the total is divided back, so a small region is not measured against the clock's floor. A compiler barrier sits between repetitions, or the compiler keeps one pass and drops the rest.

Every case is checked twice. Once in process, against a sequential loop the same binary computed at construction, at every size. Once out of process at sixty-four elements, against the kernel's own `oracle.py`, which shares neither the compiler nor the flags nor the in-process loop, so a mistake common to both C++ paths still fails. If any case in any arm of any kernel disagrees with either reference, the harness writes nothing at all and exits nonzero.

The sizes are a thousand, ten thousand, a hundred thousand, a million, ten million and a hundred million. The back-to-back row runs a thousand regions one after another at widths sixty-four, one thousand and twenty four, and sixteen thousand three hundred and eighty four, which is what a loop of `parallel` statements costs; the first two of those widths are below the runtime's serial cutoff and so measure the loop the region compiles to, and the third is the first width that engages the pool.

Buffers start on a cache line. `host_regions.cpp` records why: a sequential case that happened to be aligned measured a quarter faster than the parallel case it was compared against, which is a fact about the allocator and not about either loop.

The largest size needs several gigabytes of resident memory, because a case holds its inputs, its output and the sequential result it is checked against. A host that cannot hold them is not a host this sweep runs on, and shrinking the ladder to fit one would be choosing a size after seeing a machine.

## The acceptance rule

A win is a median ratio of at least 1.25 in CAIRN's favour that still holds at every larger size, under both compilers, and the smallest size from which it holds is reported with it. A ratio that reaches 1.25 at one size and falls back at a larger one is not a win. A loss is a ratio of at most the reciprocal, 0.8, at the largest size under both compilers. Everything else is level, and losses print in the same tables as wins, in the same columns, with the same emphasis.

A ratio is only computed between a CAIRN arm and a baseline arm whose boundaries are equal to it. A `boundaries:unequal` pair, which is every unguarded arm by construction, is reported as its own column and is never divided into a CAIRN time. The guarded and unguarded columns of one baseline are divided into each other, and that ratio is what the boundary costs.

An `unavailable` column stays empty. A missing library is never replaced by a default, an estimate or a sequential stand-in.

## What a result can and cannot claim

It can say what these programs did on this machine, with this lane count, under these two compilers, at this architecture profile, with these flags, on the commit the run records. It can say what a safety boundary cost, because the two columns differ in one `-D` and nothing else. It can say where CAIRN's rules refused a shape, because the harness ran the compiler and recorded the code.

It cannot say that CAIRN is faster than C++, than OpenMP or than oneTBB. It cannot say anything about a tuned kernel: every baseline here is the ordinary way to write the loop, not an expert's. It cannot carry to another host, another core count, another compiler version or another architecture profile. It cannot say anything about whole-compiler correctness, about the GPU, or about any of the claims [internals.md](../../docs/internals.md) keeps separate. A run that is interrupted, that times out, or in which a case disagrees is recorded as that and is not rerun into silence.

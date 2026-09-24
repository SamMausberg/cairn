# Remaining gates

Each gate below is open. A gate closes when its tests, its documentation and its evidence exist. [project/capabilities.json](project/capabilities.json) holds the same list as data, the reference files say what already works, and [verification.md](verification.md#what-each-feature-has-shown) says what each feature has shown: compiled, run on a CPU or a GPU, sanitizer-tested, measured.

## Language

- Cancelling a task, recovering from the loss of a device, and collectives across devices. The last two cannot be exercised on the reference machine, which has one GPU and runs device code only under `make gpu`.
- Plans that transform code beyond fusion, vector chunks and stencil tiles: host tiling, fusion of bodies that can trap, and plans for queued device regions.
- Recipes over arbitrary expression fragments, and a checked theorem replayed for each recipe instance. A user cannot write a loop that carries its own certificates, as the collector does.
- Extent identity through an element of an array of records (`cs[0].price` is still a part), and `alloc` fully separate from `free` in effect rows (a constructor's row still carries `free`).
- Requiring, not only reporting, that a program's own templates need only their bounds (`cairn check --generics` does it on request).
- The kernel features [devices.md](devices.md#what-fast-kernels-use) marks foreign or typed PTX: shared memory nobody zeroes, warp match and `shuffle_up`, dynamic shared memory past 48 KiB, packed half and bf16 math, fast approximate math with stated error bounds, telling nvcc what an `ro` view promises, `wgmma`, TMA, clusters, grid-wide sync and CUDA graphs. Until one lands, a kernel that needs it is a foreign CUDA implementation that `cairn foreign` inspects and `cairn validate` holds to its reference.
- Writing the minimum of a signed type as a literal: `-9223372036854775808` and a constant `0 - MAX - 1` are both `E-LITERAL-RANGE` for `i64`, which cost the benchmark's subjects nine refusals.

## Proof

- A proof that the emitted collector loop refines the Lean model, and that native code refines the emitted C++.
- A link from `checking.py` to the ownership calculus stronger than review and the differential run (twenty thousand generated programs agreed, `evidence/v1_0/gates_2026_09_22/lean/differential.json`).
- The calculus extended to closures, `lane:f`, device placement, `reduce`, `compact`, queued device work, a group submitted to inside a loop, and declared field extents.
- Proofs of what the calculus assumes of the emitter: that a part's guard runs on the spawning thread before the task starts (pinned by `tests/soundness` for `spawn` and `spawn ... into g`), and that `cairn_parallel.hpp` performs the proved region protocol under its memory orders.
- The elision audit's own rules in Lean.
- Lean models of the rule that each element of an array from outside a cooperative region has one writer (`E-COOP-GLOBAL`), of pipeline stages and of warp collectives. Finite tests and the adversarial review hold them now; `Cooperative.lean` models only the phase rule.
- What the implementation-layer review did not attack: `launch(threads, block)` with blocks that are not whole warps or from a lane, one symbol in two vendored sources, shared arrays declared in loops and conditions, the execution contexts' scratch reuse, and routes to a reference through `dyn` or a trait method ([evidence/v1_0/review_implementation_layer](../evidence/v1_0/review_implementation_layer/README.md)).
- SMT coverage of an owner held inside a record, a sum or an array, recursion, concurrency, device placement, storage floats and quantization, a function that asserts, and unbounded loops without a precondition. Owners inside values are the next step.

[verification.md](verification.md) says what each model contains, what it assumes and what it leaves out.

## Performance

- Device validation. The device half of `cairn predict` is NVIDIA's published figures for one card. Device plans, the execution context, the tensor-core multiply and its fragments, cooperative regions and pipeline stages, typed PTX in a lane, foreign CUDA implementations, and storage floats, gradients and asserts in a lane compile for sm_120. They run only under the owner's `make gpu`, `make tune-device` and `make calibrate-device`, which have not run them. Until then no device prediction, plan or speed-of-light fraction is a measurement, and nothing shows that `cairn predict` ranks a cooperative region's instances as a device would.
- Measurements beyond one machine. One x86-64 host and one AArch64 host have been measured, nothing has run across more than one memory domain, and nothing is claimed against tuned C++ or CUDA.
- `cairn predict`'s weak range: wide host regions of 1e5 to 1e7 elements, which it has predicted up to five times too fast (`evidence/v1_0/perf_model/`).
- `dot_f64` in the preregistered suite loses to a reassociating reduction by design, because CAIRN keeps a float fold in its written order (`evidence/v1_0/bench/`).
- Checking less than the whole program after an edit. A process compiles each distinct source once (`compiler/compilations.py`), but a changed body still costs a whole check and emission, about two seconds on a generated project of 185 modules ([evidence/v1_1/workspace](../evidence/v1_1/workspace/README.md)). Rechecking only what a body edit of `f` can reach has to redo, and a differential test has to hold against a full check on every example and on generated edits: `f`'s row, and through it the fixed point over every transitive caller with each one's ceiling (`E-EFFECT-CEILING`), operand order (`E-EFFECT-ORDER`) and lane and device rules (`E-PARALLEL-CALL`, `E-PLACEMENT`); whether a device lane now reaches `f` or what `f` calls; the generic instances `f`'s body asks for, each checked as new code; a function value `f` takes, which changes every indirect call's row; the layouts, rings and pipeline stages `f` uses; and every implementation of `f` and plan naming it, whose `E-IMPL-*` rules and schedules read `f`'s row and regions. Constants, types and signatures are declarations an edit host keeps fixed, so a body edit cannot change them, but a checker that takes other edits must redo their dependents too. Until that differential test exists, every edit is checked whole.

## AI evidence

- An equal-budget comparison where the languages differ in what gets solved. The preregistered run ([bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md), `evidence/v1_0/ai_benchmark/`) found every task solved in CAIRN, C++ and Rust, with CAIRN at 11.6 times the tokens of C++, mostly spent reading its documentation. Open: larger programs, other model families, and a CAIRN that costs a newcomer fewer tokens to learn.
- The benchmark with the Claude Code plugin. The run above predates the skill, the plugin and `cairn mcp`; the plugin's only measurement is a six-session smoke comparison ([evidence/v1_0/skill](../evidence/v1_0/skill/README.md)), and the `bench/skill/` eval suite has not run.
- The preregistered packet trial ([tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md)), which needs fresh model subjects. The context savings in `evidence/v1_0/context/` come from authored transcripts and show nothing about how a model does.

## Packaging

- A package registry, version resolution and fetching. Dependencies are vendored and pinned by hash, and the package installs from a checkout.
- An owner crossing the C boundary (no release function is generated for a `Buf`), two CAIRN headers in one translation unit (their `ct_` names collide), and the header and ctypes binding on any platform but x86-64 Linux.
- Benchmark submissions that ran. `cairn export --harness` writes SOL-ExecBench, GPU MODE and KernelBench submissions that build against a CUDA torch for sm_100a, and none has run on a GPU, under an official evaluator or on a leaderboard. GPU MODE's AMD tasks need HIP, which CAIRN does not emit. NVFP4 and the MX scale formats have no CAIRN type, so a problem on them is refused by name. A KernelBench `Model` with parameters or constructor inputs has no adapter yet.

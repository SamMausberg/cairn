# Remaining gates

The [CAIRN roadmap](https://github.com/users/SamMausberg/projects/2) on GitHub tracks the work in flight and next, one issue per item. Each gate below is open. A gate closes when its tests, its documentation and its evidence exist. [project/capabilities.json](project/capabilities.json) holds the same list as data, the reference files say what already works, and [verification.md](verification.md#what-each-feature-has-shown) says what each feature has shown: compiled, run on a CPU or a GPU, sanitizer-tested, measured.

## Language

- Cancelling a task, recovering from the loss of a device, and collectives across devices. The last two cannot be exercised on the reference machine, which has one GPU and runs device code only under `make gpu`.
- Plans that transform code beyond fusion, vector chunks and stencil tiles: host tiling, fusion of bodies that can trap, and plans for queued device regions. The step after that is scheduling an agent composes one operation at a time, such as tiling with a tail or reordering a fold where the numerical contract allows it, each operation's preconditions checked by the compiler and its worth decided by a measurement.
- Refusals the 1.1 evaluation's subjects still meet ([friction](../evidence/v1_1/friction/README.md)): a writing call beside plain names in one expression (`vec.push(values, next_i64(inp))`) and two allocating calls in one record construction are `E-EFFECT-ORDER`, and signed integers have no checked or wrapping arithmetic (`E-WRAP-TYPE`).
- Properties of what a function computes, stated beside it and held by `cairn validate` or proved where the SMT fragment reaches: that a sort returns a permutation of its input, not only that it returns a sorted list.
- Recipes over arbitrary expression fragments, and a checked theorem replayed for each recipe instance. A user cannot write a loop that carries its own certificates, as the collector does.
- Extent identity through an element of an array of records (`cs[0].price` is still a part), and `alloc` fully separate from `free` in effect rows (a constructor's row still carries `free`).
- Requiring, not only reporting, that a program's own templates need only their bounds (`cairn check --generics` does it on request).
- The kernel features [devices.md](devices.md#what-fast-kernels-use) marks foreign or typed PTX: dynamic shared memory past 48 KiB, packed half and bf16 math, fast approximate math with stated error bounds, telling nvcc what an `ro` view promises, `wgmma`, TMA, clusters, grid-wide sync and CUDA graphs. Until one lands, a kernel that needs it is a foreign CUDA implementation that `cairn foreign` inspects and `cairn validate` holds to its reference.

## Proof

- A proof that the emitted collector loop refines the Lean model, and that native code refines the emitted C++.
- A link from `checking.py` to the ownership calculus stronger than review and the differential run (twenty thousand generated programs agreed, `evidence/v1_0/gates_2026_09_22/lean/differential.json`).
- The calculus extended to closures, `lane:f`, device placement, `reduce`, `compact`, queued device work, a group submitted to inside a loop, and declared field extents.
- Proofs of what the calculus assumes of the emitter: that a part's guard runs on the spawning thread before the task starts (pinned by `tests/soundness` for `spawn` and `spawn ... into g`), and that `cairn_parallel.hpp` performs the proved region protocol under its memory orders.
- The elision audit's own rules in Lean, and a witness for each guard lowering leaves out that names the operation emitted in its place, so the independent check reads the emitted operation rather than the checker's facts about the source.
- Lean models of the rule that each element of an array from outside a cooperative region has one writer (`E-COOP-GLOBAL`), of pipeline stages, of warp collectives, of atomic updates beside plain accesses (`E-ATOMIC-MIXED`), of a region's finish and of the rule that lets a shared array go unzeroed (`E-COOP-UNWRITTEN`). Finite tests and the adversarial review hold them now; `Cooperative.lean` models only the phase rule.
- What the implementation-layer review did not attack: `launch(threads, block)` with blocks that are not whole warps or from a lane, one symbol in two vendored sources, shared arrays declared in loops and conditions, the execution contexts' scratch reuse, and routes to a reference through `dyn` or a trait method ([evidence/v1_0/review_implementation_layer](../evidence/v1_0/review_implementation_layer/README.md)).
- SMT coverage of an owner held inside a record, a sum or an array, recursion, concurrency, device placement, storage floats and quantization, a function that asserts, and unbounded loops without a precondition. Owners inside values are the next step.

[verification.md](verification.md) says what each model contains, what it assumes and what it leaves out.

## Performance

- Device validation. The device half of `cairn predict` is NVIDIA's published figures for one card. Device plans, the execution context, the tensor-core multiply and its fragments, cooperative regions and pipeline stages, typed PTX in a lane, foreign CUDA implementations, and storage floats, gradients and asserts in a lane compile for sm_120. They run only under the owner's `make gpu`, `make tune-device` and `make calibrate-device`, which have not run them. Until then no device prediction, plan or speed-of-light fraction is a measurement, and nothing shows that `cairn predict` ranks a cooperative region's instances as a device would.
- Measurements beyond one machine. One x86-64 host and one AArch64 host have been measured, nothing has run across more than one memory domain, and nothing is claimed against tuned C++ or CUDA.
- `cairn predict`'s weak range: wide host regions of 1e5 to 1e7 elements, which it has predicted up to five times too fast (`evidence/v1_0/perf_model/`). A prediction states no range from the model's measured error, and `cairn tune` without a measurement keeps one candidate where it could keep several.
- The rest of the gap to hand-written CUDA ([#2](https://github.com/SamMausberg/cairn/issues/2)): layer norm, the cooperative stencil and scalar-load reductions ran 11 to 26 percent slower, which `evidence/v1_1/device_perf` attributes to checked 64-bit index arithmetic the checker cannot prove in range and a 64-bit division per block in two-dimensional cooperative regions.
- A run of held device work waits before the host observes it through a copy, a release or a total; a function that prints, calls C or takes a lock still waits after each region. Writing the wait before the I/O, the call or the lock instead needs its own review and a device run.
- `dot_f64` in the preregistered suite loses to a reassociating reduction by design, because CAIRN keeps a float fold in its written order (`evidence/v1_0/bench/`).
- Checking less than the whole program after an edit. An edit of one function's body is checked from the walk the last check kept (`compiler/check/incremental.py`): that body alone is checked, every rule after the walk runs again, and `tests/verification/test_incremental.py` holds the answer to a whole check's on every example and on generated edits. Still whole: the emission of the edited source; the rules after the walk, which run over every function where only those that read a changed row need to, the operand-order audit (`E-EFFECT-ORDER`) most of all; an edit outside one body, of a template, of an implementation or its reference, or of a function a derivation reads; and an edit whose function no longer makes first a generic instance, a layout or an implementation a later body may read, or now makes first one a later body made, which falls back to a whole check.

## AI evidence

- The 1.1 evaluation ([bench/ai/PREREGISTRATION_V1_1.md](../bench/ai/PREREGISTRATION_V1_1.md)) stopped at 54 of its 156 subjects (`evidence/v1_1/ai_eval/`), before 1.1.0's fixes to what cost its subjects most ([friction](../evidence/v1_1/friction/README.md)). Open: its remaining subjects, a run on 1.1.0, other model families, and sessions that already know the language beside cold ones.
- An equal-budget comparison where the languages differ in what gets solved. The preregistered run ([bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md), `evidence/v1_0/ai_benchmark/`) found every task solved in CAIRN, C++ and Rust, with CAIRN at 11.6 times the tokens of C++, mostly spent reading its documentation. Open: larger programs, other model families, and a CAIRN that costs a newcomer fewer tokens to learn.
- The `bench/skill/` eval suite has not run, and the plugin's tool definitions, sent with every request, cost its arm about 0.9M tokens in the 1.1 evaluation.
- A search by type for an agent: the functions whose signatures fit the values it has and whose effects fit a ceiling. The standard library's signatures were the plugin arm's largest search after the skill.
- The preregistered packet trial ([tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md)), which needs fresh model subjects. The context savings in `evidence/v1_0/context/` come from authored transcripts and show nothing about how a model does.

## Packaging

- A package registry, version resolution and fetching. Dependencies are vendored and pinned by hash, and the package installs from a checkout.
- An owner crossing the C boundary (no release function is generated for a `Buf`), two CAIRN headers in one translation unit (their `ct_` names collide), a version and ABI identity in the header, and the header and ctypes binding on any platform but x86-64 Linux.
- Benchmark submissions that ran. `cairn export --harness` writes SOL-ExecBench, GPU MODE and KernelBench submissions that build against a CUDA torch for sm_100a, and none has run on a GPU, under an official evaluator or on a leaderboard. GPU MODE's AMD tasks need HIP, which CAIRN does not emit. NVFP4 and the MX scale formats have no CAIRN type, so a problem on them is refused by name. A KernelBench `Model` with parameters or constructor inputs has no adapter yet.

# Roadmap

This page lists the work on CAIRN that is still open, grouped by subject. Each item is a gate: it stays open until its tests, its documentation and its evidence exist. The reference pages say what already works, and [verification.md](verification.md#what-each-feature-has-shown) says what each feature has shown: compiled, run on a CPU or a GPU, sanitizer-tested, measured.

The [CAIRN roadmap](https://github.com/users/SamMausberg/projects/2) on GitHub tracks the work in flight and next, one issue for each item. [project/capabilities.json](project/capabilities.json) states, as data, what is implemented and what is not.

## Language

Cancelling a task, recovering from the loss of a device, and collectives across devices ([#30](https://github.com/SamMausberg/cairn/issues/30)). The last two cannot be exercised on the reference machine, which has one GPU and runs device code only under `make gpu`.

Plans that transform code beyond fusion, vector chunks and stencil tiles: host tiling, fusion of bodies that can trap, and plans for queued device regions. The step after that is a schedule that an agent composes one operation at a time, such as tiling with a tail or reordering a fold where the numerical contract allows it. The compiler would check each operation's preconditions, and a measurement would decide its worth ([#27](https://github.com/SamMausberg/cairn/issues/27)).

Refusals the subjects of the 1.1 evaluation still meet ([friction](../evidence/v1_1/friction/README.md), [#124](https://github.com/SamMausberg/cairn/issues/124)). A writing call beside plain names in one expression (`vec.push(values, next_i64(inp))`) and two allocating calls in one record construction are `E-EFFECT-ORDER`. Signed integers have no checked or wrapping arithmetic (`E-WRAP-TYPE`), and `len(v)` of a `Vec` is `E-LEN`.

Properties of what a function computes, stated beside it, held by `cairn validate` or proved where the SMT fragment reaches ([#128](https://github.com/SamMausberg/cairn/issues/128)). For a sort, such a property says that it returns a permutation of its input as well as a sorted list.

Recipes over arbitrary expression fragments, and a checked theorem replayed for each recipe instance. Today a recipe takes naturals, a record type and names of functions, and a user cannot write a loop that carries its own certificates, as the collector does.

Two refinements of the rules on extents and effects: extent identity through an element of an array of records, since `cs[0].price` is still a part; and `alloc` fully separate from `free` in effect rows, since a constructor's row still carries `free`.

Requiring by default that a program's own templates need only their bounds. `cairn check --generics` requires it on request.

Further refusals after a refused type, constant or signature, and from the rules about lanes, plans, implementations, fusion and layouts once anything is refused. The check stops there, and its record counts what it did not judge in `not_judged`.

The kernel features that [devices.md](devices.md#what-fast-kernels-use) marks foreign or typed PTX: dynamic shared memory past 48 KiB, packed half and bf16 math, fast approximate math with stated error bounds, telling nvcc what an `ro` view promises, `wgmma`, TMA, clusters, a barrier across the whole grid, and a CUDA graph built inside CAIRN code. Until one lands, a kernel that needs it is a foreign CUDA implementation that `cairn foreign` inspects and `cairn validate` holds to its reference.

## Proof

A proof that the emitted collector loop refines the Lean model, and that native code refines the emitted C++ ([#32](https://github.com/SamMausberg/cairn/issues/32)).

A link from `checking.py` to the ownership calculus stronger than review and the differential run, in which twenty thousand generated programs agreed (`evidence/v1_0/gates_2026_09_22/lean/differential.json`).

The calculus extended to closures, `lane:f`, device placement, `reduce`, `compact`, queued device work, a group submitted to inside a loop, and declared field extents.

Proofs of what the calculus assumes of the emitter. One assumption is that a part's guard runs on the spawning thread before the task starts, which `tests/soundness` pins for `spawn` and `spawn ... into g`. The other is that `cairn_parallel.hpp` performs the proved region protocol under its memory orders.

The elision audit's own rules in Lean, and a record of each guard that lowering leaves out, naming the emitted operation and the facts that show it cannot fail, which a checker outside the compiler can replay.

Lean models of the rule that each element of an array from outside a cooperative region has one writer (`E-COOP-GLOBAL`), of pipeline stages, of warp collectives, of atomic updates beside plain accesses (`E-ATOMIC-MIXED`), of a region's finish, and of the rule that lets a shared array go unzeroed (`E-COOP-UNWRITTEN`). Finite tests and the adversarial review hold them now, and `Cooperative.lean` models only the phase rule.

What the review of the implementation layer did not attack: `launch(threads, block)` with blocks that are not whole warps or from a lane, one symbol in two vendored sources, shared arrays declared in loops and conditions, the execution contexts' reuse of scratch memory, and routes to a reference through `dyn` or a trait method ([evidence/v1_0/review_implementation_layer](../evidence/v1_0/review_implementation_layer/README.md)).

SMT coverage of an owner held inside a record, a sum or an array, recursion, concurrency, device placement, storage floats and quantization, a function that asserts, and unbounded loops without a precondition ([#31](https://github.com/SamMausberg/cairn/issues/31)). Owners inside values are the next step.

[verification.md](verification.md) says what each model contains, what it assumes and what it leaves out.

## Performance

Device measurement ([#20](https://github.com/SamMausberg/cairn/issues/20)). The device half of `cairn predict` is NVIDIA's published figures for eight cards. `make calibrate-device` and `make tune-device`, the owner's targets that would measure a device, have not run. Until they do, no device prediction, plan or fraction of the speed of light is a measurement, and nothing shows that `cairn predict` ranks a cooperative region's instances as a device would.

Device runs of what has not run on a GPU ([#19](https://github.com/SamMausberg/cairn/issues/19)). Device plans, the execution context, the tensor-core multiply and its fragments, cooperative regions and pipeline stages, typed PTX in a lane, foreign CUDA implementations, and storage floats, gradients and asserts in a lane all compile for sm_120. `make gpu` ran the device suite once, in the 1.1.0 session on an RTX 5070 Ti, and 48 of its 52 tests passed ([evidence/v1_1/gpu](../evidence/v1_1/gpu/README.md)). Storage floats, gradients and asserts in a lane, the tests that trap on the device on purpose, and Compute Sanitizer have not run there, and Compute Sanitizer cannot instrument that GPU under WSL2.

Measurements beyond one machine. One x86-64 host and one AArch64 host have been measured, nothing has run across more than one memory domain, and nothing is claimed against tuned C++ or CUDA.

The weak range of `cairn predict`: wide host regions of 1e5 to 1e7 elements, which it has predicted up to five times too fast (`evidence/v1_0/perf_model/`). A prediction states no range from the model's measured error. So `cairn tune` ranks candidates by one number per size, and without `--measure` its `--write` writes the one ranked first ([#129](https://github.com/SamMausberg/cairn/issues/129)).

The rest of the gap to CUDA written by hand ([#2](https://github.com/SamMausberg/cairn/issues/2)). Layer norm, the cooperative stencil and reductions with scalar loads ran 11 to 26 percent slower. `evidence/v1_1/device_perf` attributes this to checked 64-bit index arithmetic the checker cannot prove in range, and to a 64-bit division per block in cooperative regions of two dimensions.

Holding device work across I/O, foreign calls and locks ([#125](https://github.com/SamMausberg/cairn/issues/125)). A run of held device work ([devices.md](devices.md#one-wait-or-none)) waits once, before the host observes it through a copy, a release or a total. A function that also prints, calls C, takes a lock, runs host assembly or reads `@unified` memory still waits after each region. Writing the wait before the I/O, the call or the lock instead needs its own review and a device run.

`dot_f64` in the preregistered suite loses to a reduction that reassociates, by design, because CAIRN keeps a float fold in its written order (`evidence/v1_0/bench/`).

Checking less than the whole program. The checker judges the whole program, because effect rows are a fixed point over the call graph, and the ceilings, operand order and lane and device reach read them. A check first walks every function body in program order, and then runs the rules that read effect rows. An edit of one function's body is checked from the record of that walk which the last check kept (`compiler/check/incremental.py`). That body alone is checked again, every rule after the walk runs again, and `tests/verification/test_incremental.py` requires the same answer as a whole check on every example and on generated edits.

What is still checked whole: the emission of the edited source; the rules after the walk, which run over every function where only those that read a changed row need to, the audit of operand order (`E-EFFECT-ORDER`) most of all; an edit outside one body, of a template, of an implementation or its reference, or of a function a derivation reads; and an edit after which its function is no longer the first to make a generic instance, a layout or an implementation that a later body may read, or is now the first to make one that a later body made, which falls back to a whole check.

## AI evidence

The rest of the 1.1 evaluation ([bench/ai/PREREGISTRATION_V1_1.md](../bench/ai/PREREGISTRATION_V1_1.md)). It stopped at 54 of its 156 subjects (`evidence/v1_1/ai_eval/`), before the fixes in 1.1.0 to what cost its subjects most ([friction](../evidence/v1_1/friction/README.md)). Open: its remaining subjects, a run on 1.1.0, other model families, and sessions that already know the language beside cold ones ([#11](https://github.com/SamMausberg/cairn/issues/11), [#37](https://github.com/SamMausberg/cairn/issues/37)).

A comparison at equal budgets where the languages differ in what gets solved. The preregistered run ([bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md), `evidence/v1_0/ai_benchmark/`) found every task solved in CAIRN, C++ and Rust, with CAIRN at 11.6 times the tokens of C++, mostly spent reading its documentation. Open: larger programs, other model families, and a CAIRN that costs a newcomer fewer tokens to learn.

A run of the `bench/skill/` eval suite, which has not run. The plugin's tool definitions, sent with every request, cost its arm about 0.9M tokens in the 1.1 evaluation.

A search by type for an agent: the functions whose signatures fit the values it has and whose effects fit a ceiling ([#126](https://github.com/SamMausberg/cairn/issues/126)). The standard library's signatures were the plugin arm's largest search after the skill.

The preregistered packet trial ([tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md)), which needs fresh model subjects. The context savings in `evidence/v1_0/context/` come from transcripts written by hand, and show nothing about how a model does.

## Packaging

A package registry, version resolution and fetching ([#33](https://github.com/SamMausberg/cairn/issues/33)). Dependencies are vendored and pinned by hash, and the package installs from a checkout.

A library another program can adopt through its C header ([#127](https://github.com/SamMausberg/cairn/issues/127)). An owner cannot cross the C boundary, since no release function is generated for a `Buf`. Two CAIRN headers in one translation unit collide on their `ct_` names. A header names an interface hash, so a program built against another version of the library fails to link, and it states no CAIRN version or ABI identity. The header and the ctypes binding have been tested on x86-64 Linux alone.

A hermetic Bazel toolchain, and one Bazel action for each module. `rules_cairn` runs the compiler and the C++ compiler from the host, and each binary and test runs the compiler once over all of its sources.

Benchmark submissions that ran. `cairn export --harness` writes SOL-ExecBench, GPU MODE and KernelBench submissions that build against a CUDA build of torch for sm_100a. None has run on a GPU, under an official evaluator or on a leaderboard. GPU MODE's AMD tasks need HIP, which CAIRN does not emit ([#28](https://github.com/SamMausberg/cairn/issues/28)). NVFP4 and the MX scale formats have no CAIRN type, so a problem on them is refused by name ([#44](https://github.com/SamMausberg/cairn/issues/44)). A KernelBench `Model` with parameters or constructor inputs has no adapter yet.

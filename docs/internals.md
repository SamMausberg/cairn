# Internals

This page is for people who work on CAIRN itself. It follows a program through the compiler's stages, says which file owns which rule and what each runtime header does, and then covers how the tests and CI check a change, what an accepted program promises, and how a release is cut. Paths are under `src/cairn/` unless they say otherwise.

## Compiler architecture

A project becomes a native artifact in nine stages, and only the emitter produces C++.

| Stage | Module | Entry point | What it produces |
|---|---|---|---|
| Load | `projects/project.py` | `load_project` | one combined source from the inputs the manifest lists, a sha256 per file, a map from each line to its file |
| Parse | `compiler/syntax/lexing.py`, `compiler/syntax/parser.py` (with `compiler/syntax/expressions.py`, `compiler/syntax/statements.py`) | `Parser.parse` | tokens, source ranges, and a tree (`compiler/syntax/tree.py`) in which no name is resolved yet |
| Link modules | `compiler/syntax/modules.py` | `link` | the imported `std.*` modules, merged in; nothing else is importable and nothing is fetched |
| Derive recipes | `compiler/derive/expansion.py`, `compiler/derive/gradients.py` | `derive`, `differentiate` | the declarations each `derive` asks for, as ordinary code of the deriving module; `derive grad` writes a function's adjoint |
| Specialize families | `compiler/derive/expansion.py` | `specialize` | one function per variant of a family's natural range |
| Check | `compiler/check/checking.py` and its rule modules | `Checker.bodies` | one typed tree, annotated in place (`Expr.ty`, `Expr.ref`) |
| Judge | `compiler/check/checking.py` | `Checker.judge` | every effect row, and the rules that need all of them |
| Emit | `compiler/lower/codegen.py`, `compiler/lower/region_lowering.py` | `Emitter.units` | readable C++20: one shared header, and one unit per module |
| Build | `projects/build.py` | `build` | a fresh directory, a hashed native artifact, a `cairn.build/1` receipt |

The receipt is the JSON record a build writes beside the artifact: the hashes of the source, the generated C++, the runtime and the artifact, each function's effects and guards, and the rules the lowering trusts.

The compiler's modules sit in one package per stage or subject. Only the facade and the compile cache are at the top of `compiler/`.

| Package | Holds |
|---|---|
| `compiler/syntax/` | tokens, the parser, the tree it builds, and linking `std` |
| `compiler/derive/` | what `derive` writes before checking: recipes, static families, gradients |
| `compiler/check/` | the checker, and the rules of statements, expressions, calls, places, concurrency, effects, traits, constants and facts |
| `compiler/primitives/` | the table of builtins and the families beside it: printing, wide accesses, atomic updates, the machine, I/O rings |
| `compiler/plans/` | what a plan chooses: an implementation, fused regions, vector chunks, staged tiles |
| `compiler/cooperative/` | cooperative regions, their phase rule and their rule of one writer per element, pipelines |
| `compiler/device/` | layouts, tensor-core fragments and the multiply, launches of foreign kernels |
| `compiler/lower/` | the emitter, region lowering, device execution, the C header of a library |

`compiler/cairnc.py` is the facade, the entry point the rest of the package calls. `compile_program` runs every stage from parse through judge. `generate` checks the collector's seventeen certificates before it emits a line. The `Emitter` then asks the independent audit, `verify/elision.py`, to accept every guard lowering leaves out, and it writes the guard of each one the audit refuses.

### One compile per source

`compiler/compilations.py` makes each of those stages run once per distinct source in a process. It serves the tools that ask for the same compile again and again: the edit, plan and implementation hosts, `cairn state`, `cairn mcp`, `cairn lsp`, and `cairn predict` with the device inspection it reads. A compile is kept under a key made of everything it reads: the source, the compiler the process loaded (`implementation_hash()` with the runtime headers), the `std` files as they are on disk now, and whether the check records expression sites or reports every refusal. The device target and emulation are outside the key, because `projects/target.py` and `projects/emulation.py` judge the receipt afterwards.

The cache keeps the check as a pickle, and the parse and the emission too once a caller asks for them, and it keeps every refusal as its record. Each caller unpickles its own copy, so a change one caller makes is never read by another. An accepted check answers a caller whether or not it asks for every refusal, and a check that recorded sites answers a caller that wants none. At most 16 sources and 256 MiB of pickles are kept, and the source used least recently goes first. `tests/agent/test_compilations.py` holds that a kept copy of every example emits the same C++ and receipt as a compile from scratch.

### Checking an edit from the last check

An accepted check also keeps a record of its pass over the function bodies: the checker pickled as that pass left it, before implementations, plans and the judge change it, with how far each table of the whole program had grown after each function (`compiler/check/incremental.py`). A source that differs from a kept one inside one function's body is checked from that record. The edited body is parsed where it stands, every position below it moves by what the edit added, each other function's additions to the tables are put back as its check made them, and only the edited body is checked. Everything after that pass then runs as in a whole check: implementations, plans, the fixed point of the effect rows, the ceilings, the audit of operand order, and the lane and placement rules.

That gives the answer of a whole check only under conditions `incremental.py` tests, and an edit that fails one is checked whole. The texts are compared, and a caller's claim about what changed is never read. So an edit outside one body is checked whole, and so is an edit of a generic template, an implementation or its reference, a function a derivation reads, or any body of a program with a derived gradient. An edited body whose text does not parse alone, or does not end where the old body ended, is checked whole too.

The edited body's earlier check may have been the first to make something later checks read: a generic instance, a layout of a generic type, an implementation, or a folded layout. When its new check asks for one again, it gets that object back as it was made, so a later body reads the same object. The edit falls back to a whole check when the body no longer makes one of those or no longer takes first the address of a function it took, when its new check makes first something a later check made, when it names another number of unknown places while a later extent counts them, or when the program passes a limit a whole check reports. An edited source's parse is made the same way from a kept parse, with the edited body parsed in place and every position below it moved. Its emission is still whole.

`tests/verification/test_incremental.py` edits the first, middle and last body of every example, and of a program written to add and remove calls, effects, loops, spawns, recursion, a device lane's reach and a call through a function value. It requires the answer of a whole check: acceptance and every diagnostic with `further` and `not_judged`, the checked program and the checker object for object, the receipts, and the emitted C++ and manifest.

### The checker's rules

Checking is one pass per function over one typed tree, and each generic instance is checked as ordinary code. One `Checker` class holds the tables of the whole program and runs the pass over the bodies. Each group of rules is a module of functions that take the checker first. An explicit table binds them as methods, so mypy checks every call, and `s_<tag>` and `e_<tag>` dispatch on the tag of the tree node.

| Rule | File | Functions |
|---|---|---|
| Names, types, generic instances, signatures | `compiler/check/checking.py` | `resolve`, `define`, `signature`, `function`, `judge` |
| State within one function and one region | `compiler/check/scope.py` | `Scope`, `Lanes` |
| Statements | `compiler/check/statements.py` | `s_let`, `s_assign`, `s_if`, `s_match`, `s_for`, `s_defer`, `s_compact`, `branches` |
| Expressions | `compiler/check/expressions.py` | `e_name`, `e_index`, `e_field`, `e_lambda`, `e_try`, `e_binary` |
| Calls, construction and declared extents | `compiler/check/calls.py` | `e_call`, `invoke`, `view_argument`, `construct`, `establish` |
| Places, borrows (which are second class), moves, leases, aliasing | `compiler/check/places.py` | `path`, `place`, `overlaps`, `leased`, `lend`, `consume`, `disjoint` |
| Tasks and tickets, lanes and regions, atomics, placement | `compiler/check/concurrency.py` | `e_spawn`, `region`, `s_parallel`, `s_reduce`, `s_scan`, `judge_lane_callbacks`, `host_only` |
| The effect vocabulary, the fixed point, operand order | `compiler/check/effects.py` | `fixed_point`, `audit` |
| Every independent refusal of one check: what is kept, what a failed check takes back, what is judged after one | `compiler/check/refusals.py` | `refusing`, `rollback`, `rest`, `verdict` |
| Traits, bounds, overlap, dynamic tables | `compiler/check/traits.py` | `implemented`, `dispatch`, `vtable`, `certify` |
| Constant folding | `compiler/check/constants.py` | `constant`, `fold` |
| I/O rings and their lowering | `compiler/primitives/rings.py` | `check_ring`, `method`, `waited`, `lower` |
| Alternative implementations and their dispatch | `compiler/plans/implementations.py` | `declared`, `condition`, `select`, `joined`, `called`, `lower` |
| Facts about `usize` values that let lowering drop a guard | `compiler/check/facts.py` | `binder`, `defined`, `assume`, `index`, `arithmetic`, `conversion` |
| The independent check of each guard lowering leaves out | `verify/elision.py` | `audit`, `decide`, `part` |
| A plan's `fuse`: which regions join, and their scratch | `compiler/plans/fusion.py` | `chains`, `quiet`, `compatible`, `scratch` |
| A plan's `vector` and its lowering | `compiler/plans/chunks.py` | `chunkable`, `vectored`, `lower` |
| A plan's `stage` and its lowering | `compiler/plans/staging.py` | `stageable`, `staged`, `lower` |
| Cooperative regions, which threads reach a statement, and their lowering | `compiler/cooperative/cooperative.py` | `s_blocks`, `Reach`, `participation`, `collective`, `lower_blocks` |
| The phase rule: no two threads of a block at one shared element between barriers | `compiler/cooperative/phases.py` | `Phases`, `check`, `check_array` |
| A cooperative body run for every thread of one block together, phase by phase | `compiler/cooperative/block_run.py` | `BlockRun`, `arith`, `holds_barrier` |
| One writer for every element of an array from outside a cooperative region | `compiler/cooperative/footprints.py` | `Poly`, `Globals`, `disjoint`, `radix` |
| Pipeline stages and their lowering | `compiler/cooperative/pipelines.py` | `s_pipeline`, `method`, `Stages`, `lower` |
| The tensor-core multiply, its numerical contract and its lowering | `compiler/device/tensor.py` | `check_mma`, `lower_mma` |
| Tensor-core fragments and their lowering | `compiler/device/fragments.py` | `valid`, `tile`, `consumer`, `check_mma`, `lower_load` |
| `layout` declarations, their receipt, `L.at(...)` and its lowering | `compiler/device/layouts.py` | `value`, `evaluate`, `explained`, `method`, `apply`, `lower` |
| Layouts as values: coverage, owners, runs, bank conflicts, conversions | `compiler/device/layout_algebra.py` | `Layout`, `Spread`, `cover`, `exactly_once`, `runs`, `conflicts`, `conversion` |
| Each primitive's type and cost, beside its lowering | `compiler/primitives/builtins.py` | `check_*` and `lower_*` |
| The runtime operation each piece of device work lowers to | `compiler/lower/execution.py` | `call`, `unrolled` |
| `mmio_read`, `mmio_write`, `asm` and typed assembly, beside their lowering | `compiler/primitives/machine.py` | `check_machine`, `s_asm`, `lower_asm`, `unbuildable` |
| An `extern` CUDA kernel's `launch(threads, block)` and its launch | `compiler/device/launches.py` | `check_launch`, `lower_launch` |
| `print`, `println`, `eprint`, `eprintln`, `format` and their lowering | `compiler/primitives/printing.py` | `check_print`, `target`, `piece`, `lower_print` |
| The C header of a library | `compiler/lower/header.py` | `Header.render`, `shape`, `refusal` |
| Manifests, vendored dependencies | `projects/project.py` | `read_manifest`, `contained_file`, `claim`, `dependencies` |
| Vendored C++ and CUDA, compiled by the program's command line and held to their externs' types | `projects/foreign.py` | `compile_sources`, `binding`, `inspect` |
| What a foreign implementation has: its contract, build, device inspection and validation | `verify/foreign.py` | `identify`, `report`, `device_tests` |
| The numerical policy: when a float result agrees with the reference's, for the host and for generated tests | `verify/validation/agreement.py` | `agrees`, `same`, `helper`, `stated` |
| Z3's answer on an implementation, and its counterexample replayed through the finite path | `verify/validation/counterexamples.py` | `smt`, `outside`, `replayed` |
| Native flags, the closed table of system libraries, the effects a freestanding image bans | `projects/toolchain.py` | `command`, `flags`, `LIBRARIES`, `audit_effects` |
| The device target | `projects/target.py` | `resolve`, `parse`, `require`, `accept`, `fits` |
| A device program built for the host: what emulation refuses and what its records say | `projects/emulation.py` | `check`, `record`, `MODELED` |
| Exports and the commands that take one | `projects/export.py` | `export`, `check`, `build`, `run`, `test`, `compare` |

The state of one function lives in one `Scope`. The checker swaps it out when it checks an instance in the middle of its caller, so instantiation is re-entrant. Every concrete signature is resolved before any body is checked.

Three questions have one answer each. Who may touch this place now is `leased`, which every access passes through. Who implements this trait for this type is `implemented`, which serves static calls, `dyn` borrows and `Dyn` values alike. What may run in a lane is one rule, applied to the lane's row, to its callees' rows, and through `lane:f` to the closure finally passed.

### Lowering and the runtime

A lane body is one lambda whose entry point, `cr::par::run` or `cr::gpu::run`, is chosen by placement, so the host and the device share the emitter. `toolchain.py` chooses the one native command line. A device program's host pass takes `-fexceptions`, because the CUB headers of CUDA 13 contain throw and catch. Nothing in the runtime throws, and `tests/tooling/test_tools.py` pins that as the one departure.

| Header in `runtime/` | Owns |
|---|---|
| `cairn_runtime.hpp` | the guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard can be called on the host and on the device |
| `cairn_owners.hpp` | the movable zeroed `Buf`, `Defer`, borrowed callables, checked parts |
| `cairn_parallel.hpp` | the host lane pool with its pooled reduction and its scan in two passes, `Mutex` and `Atomic` with explicit orders; it includes `cairn_tasks.hpp` |
| `cairn_tasks.hpp` | the crew of reusable task threads, linear tasks, task groups with a bounded ring of completions |
| `cairn_kernels.hpp` | the device side of every region in plain CUDA: the lane, chunk and staged tile kernels, and their launch on a stream the caller names, with no execution context |
| `cairn_gpu.hpp` | CUDA as the machine `cairn_exec.hpp` runs on: streams, events, allocation and copies; and synchronous entry points (`launch`, `Ticket`) that wait for the whole device and that generated code does not call |
| `cairn_cub.hpp` | CUB's reduce and scan over the whole device, which a device `reduce`, `scan` and `compact` run on, and the synchronous `reduce`, `scan` and `compact` beside `launch`; only a program with a device collector includes it, since CUB's headers are about half of an nvcc build |
| `cairn_emulate.hpp` | the machine an emulated build runs device work on, read before the program while `CAIRN_EMULATE` leaves CUDA out of `cairn_gpu.hpp`: host memory, lanes on the host lane pool, collectors in index order, cooperative regions on host threads, the reference multiply |
| `cairn_exec.hpp` | what generated code calls for device work, written once for any machine: the calling thread's execution context, device owners, regions on its stream, reductions, scans and compactions in its arena, queued work on lent lanes, a C caller's own stream |
| `cairn_reuse.hpp` | execution contexts apart from the machine: lanes (a stream and its event) lent until their work completes, one scratch arena ordered between its users, a declared budget, a caller's bound stream; and the reductions, scans and compactions written against the machine |
| `cairn_io.hpp` | the I/O ring over io_uring: fixed berths that own each operation's `Buf`, collection in the order operations complete, a wait that drains before it releases |
| `cairn_fragment.hpp` | tensor-core fragments: on the host every thread of a warp holding each whole and storing its lane's elements, on the device WMMA and `mma.sync` with `ldmatrix` |
| `cairn_layout.hpp` | a layout's coordinate checked against its extent, and CuTe's swizzle, on the host and in a device lane alike |
| `cairn_access.hpp` | wide loads and stores with their two guards and the cache operator a hint names, and atomic updates of one element: one instruction on the device, K plain accesses or a `std::atomic_ref` on the host |
| `cairn_float.hpp` | the storage floats `f16 bf16 f8e4m3 f8e5m2`: one integer routine that rounds on the host and in a device lane alike, `quantize` and `quantize_stochastic` |
| `cairn_tensor.hpp` | `mma_unordered`: the reference loop on the host, and on the device 64 x 64 tensor-core tiles over two stages in shared memory, written once against the operations a tile is given so a host test runs every thread's phases |
| `cairn_coop.hpp` | cooperative regions: on the host each block's threads as real threads at a `std::barrier`, two blocks at a time, with each warp exchange one barrier over the warp's own two banks of slots, one slot per thread, used in turn, and the butterfly of a warp reduce worked out by each thread from one exchange; on the device one launch with static shared memory, `__syncthreads` and `__shfl_*_sync` |
| `cairn_print.hpp` | `print` and `format`: one 4096-byte stack buffer, shortest floats that read back to the same value through `std::to_chars`, a byte record grown as `std.vec` grows |
| `cairn_assert.hpp` | `assert`: the message a failed one prints, on standard error, through the device's printf in a lane, or not at all in an image, before the trap |

Generated code includes only the headers it needs, and a freestanding image includes neither `cairn_parallel.hpp` nor `cairn_tasks.hpp`.

The runtime has two pieces of global state. The first is the lane pool. The first host region of a process creates it, with `hardware_concurrency()` threads or `CAIRN_LANES` (1 to 1024). A region below 16384 elements (`lanes::CUTOFF`) is the plain loop. Above that it engages one lane per 8192 elements (`lanes::GRAIN`) and cuts its indices into one home per lane, which that lane claims from first. The thread that starts a region can finish it alone, so a busy or absent worker delays a region and cannot deadlock it. [verification.md](verification.md#the-lane-pool) has the protocol and its proof.

The second is the crew of task threads. A spawn takes a parked thread or starts a new one, so a task never waits for a thread, and tasks that wait on each other cannot deadlock the crew. At most `hardware_concurrency()` threads stay parked. The crew is separate from the lane pool, so a lane may spawn a task and wait for it.

What each operation takes when it runs, and gives back when it ends:

| Operation | Takes | Gives back |
|---|---|---|
| host `parallel`, pooled `reduce` | a descriptor on the caller's stack, and for a reduction its 256 block slots there too; the pool, once | nothing to release |
| host `compact`, `reduce` in index order | nothing: the loop writes the output in place | nothing |
| `spawn f(args)` | one heap cell for the result and one for the captured arguments; a parked thread, or a new one | the thread at `wait`, to the crew |
| `Group[T](n)`, `spawn ... into g` | four arrays of `n` at the declaration; per submission one heap cell for the captures, and a thread the first time a berth runs | the berths' threads at `wait(g)` |
| `IoRing(n)` | eight arrays of `n`, the io_uring descriptor and three mappings, at the declaration | all of it at `wait(q)`; nothing per operation |
| device `parallel`, a synchronous `transfer` | a launch or a copy on the stream of the thread's execution context, then a wait for that stream | nothing |
| queued device work, `transfer` after a ticket | a lane of the thread's context: a stream and an event, made the first time and reused after | the lane at its `wait`, after a wait for its stream |
| device `reduce`, `scan` | a cell and the library's storage in the context's arena, one copy to the host, one stream wait; the arena grows the first time a larger request arrives | nothing |
| device `compact` | flags, offsets and CUB's storage in the arena, two launches, two copies of one element to the host, one stream wait | nothing |
| a `@device`, `@pinned` or `@unified` buffer | one CUDA allocation, zeroed on the context's stream, which is waited for | freed at scope exit |

Device work runs on the calling thread's execution context, `cr::gpu::here()`, which `compiler/lower/execution.py` names at every call. Its bookkeeping is tested against a mock device (`tests/runtime/reuse_runtime.cpp`), and generated programs are tested against a host machine that counts every stream, allocation and wait (`tests/runtime/gpu_host.hpp`). [devices.md](devices.md#device-execution) says what that shows and what no device run has checked.

### The rest of the package

The ownership table of [AGENTS.md](../AGENTS.md) names the owner of every other module. In `perf/`, only `measure.py`, on the host, and `on_device.py`, under the owner's make targets, run a program. No agent, test generator or solver may rewrite the authority it is checked against, and native libraries never import the agent tooling or Z3.

The wheel holds the compiler package, the runtime headers, the target support files, the `std` sources and the command line. Tests, benchmarks, proofs and evidence stay out of it.

Outside `compiler/`, each package keeps its modules at its top level, apart from seven subpackages:

| Package | Holds |
|---|---|
| `verify/scalar/` | value-level source equivalence: the value model, the SMT translator, the concrete replay, the query and receipt, the Z3 bridge |
| `verify/validation/` | an implementation validated against its reference: boundary inputs, isolated calls, agreement, counterexamples, the device side |
| `projects/harness/` | `cairn export --harness`, its `harness.toml` mapping and sources, and `cairn new --from-sol-execbench` |
| `editor/lsp/` | `cairn lsp`: the server, one open buffer, a project's workspace, and one module per feature |
| `agent/hosts/` | the edit, plan, implementation and migration hosts, and writing their changes back to files |
| `agent/mcp/` | `cairn mcp`: the server and the tools it serves |
| `perf/tuning/` | `cairn tune`: the search, its objective, device resources, feedback, and a plan as source |

## Testing

Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

```sh
make lint          # ruff format --check, ruff check, mypy over the package, cairn fmt --check
make test          # the whole suite in parallel; parts that need hardware or a tool skip with a reason
make proof         # certificates, the Lean export drift check, lake build, the differential runs, scalar module equivalence
make native        # both compilers with the sanitizers that bite, and the codegen comparison
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make gpu`, `make tune-device` and `make calibrate-device` are the only commands that run code on a CUDA device, and only the owner runs them. `make device-limits` launches nothing, but it starts a CUDA context to ask the device for the limits its card takes from NVIDIA's tables, so only the owner runs it too. Each sets `CAIRN_GPU_TESTS=1` and holds `/tmp/cairn-gpu.lock` around every device run (`tools/support.py`), and `tests/tooling/test_on_device.py` holds the Makefile to those four. `make tune-device` and `make calibrate-device` rest two seconds after each device run, and one process makes at most 64 of them. Everywhere else a device test compiles its code and skips the run. On the reference machine the GPU also drives the display, and repeated device test runs reset its driver and twice crashed the host.

`make gpu` runs the modules the Makefile's `GPU_TESTS` names, one test at a time, and `tests/tooling/test_workflow.py` holds that list to every module that calls `on_device`, `device_reason` or `device_lock`. A test that traps on the device on purpose, such as a guard failing in a lane, runs only when the owner also sets `CAIRN_GPU_TRAPS=1` by hand (`CAIRN_GPU_TRAPS=1 make gpu`). Otherwise it skips with that reason.

`make proof` needs `lake`, on `PATH` or in `~/.elan/bin` where elan puts it. Its axiom audit allows `propext`, `Quot.sound` and, outside the ownership theorems, `Classical.choice`; the 1.1.0 record found only the first two ([verification.md](verification.md#pinned-versions-and-the-audit)).

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted language, built and run natively under both compilers; `std` module by module; the tour; every `cairn` block in README.md and under `docs/` |
| `soundness/` | every hole an adversarial review found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures; guard elision against its audit; plans, fusion, `scan` and rings |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, the checker and the Lean models deciding generated programs alike, the SMT translator against concrete replay, module coverage, the classes of `cairn diff` |
| `projects/` | manifests, vendored dependencies, incremental builds, every application under `examples/apps/`, the `cairn new` templates, test blocks, a C++ host linking a CAIRN library, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the C++ and CUDA binaries beside it, which check themselves, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus fuzzing, a real `cairn lsp` and the extension's client, the grammars against a TextMate engine, `predict` and `tune`, terminal output and completions, the tree's own rules, the wheel built offline and what it holds, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets and their evidence classes, rule cards, sketches, guarded edits, migrations, plan edits, state and deltas, a real `cairn mcp` writing accepted changes back and refusing stale ones, and the canonical projection printing every sample and `std` module back as source that compiles to identical native code |
| `oracles/` | Python oracles, which are not pytest modules and which `tools/checks/verify.py` drives |

Sanitizers run where they bite: ownership under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. A task whose child exits abnormally has failed, whatever it printed.

Every safety rule has a line in a rejection table (`REJECTED` in `tests/soundness/test_soundness.py`) that maps a sentence naming the rule to a diagnostic code and a program, and one parametrized test requires exactly that code, so a rule that stops biting fails by name. A behaviour table (`BEHAVIOR` in `tests/soundness/test_soundness_behavior.py`) maps a sentence to a program and its expected exit status under clang++ with the address and undefined sanitizers, `-6` where a guard must abort.

| Script | What it checks |
|---|---|
| `tools/checks/verify.py` | rebuilds the native artifacts and drives the oracles in `tests/oracles/`: equal boundary checks, strict floating-point flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `tools/checks/validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an observer built at `-O0` that counts allocation and release across returns, loops and match exits |
| `tools/checks/validate_semantics.py` | the translator and the interpreter that models traps against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and SMT with pinned inputs |
| `tools/checks/semantic_check.py`, `tools/corpus/semantic_corpus.py` | two scalar implementations against one immutable reference; pairs with the same contract, every label decided and replayed |
| `tools/corpus/curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one defect written by hand per family of algorithms, all of which the finite tests must catch |
| `tools/checks/density.py`, `export_lean_certificates.py` | lexical density accounting over the whole compiler; `--check` fails when `collector_rules()` and `proofs/` have drifted apart |
| `tools/checks/emission_identity.py` | whether a source change left the C++ and effect rows of every example, `std`, test program and docs block as they were, optionally up to two named C++ identities, and with `--normalize guards` whether it only left guards out |
| `tools/checks/refusal_differential.py` | every refused example, test program and docs block checked again reporting every refusal: the first refusal identical, each further one a whole diagnostic in source order, and no check ended early on a fault |
| `tools/checks/differential_ownership.py` | generated programs of a shared fragment, as CAIRN source and Lean `Program` literals, classified alike by the checker and the Lean `accepts` |
| `tools/checks/differential_facts.py` | generated facts and `usize` expressions, decided alike by `compiler/check/facts.py` and `Facts.lean` |
| `tools/checks/differential_layouts.py` | generated storage layouts and spreads, judged alike by `compiler/device/layout_algebra.py` and `Layout.lean` |
| `tools/checks/differential_cooperative.py` | generated cooperative regions of a shared fragment, as CAIRN source and `Cooperative.lean` terms, decided alike by `compiler/cooperative/phases.py` and the model's `program` |
| `tools/checks/differential_guards.py` | generated programs built as emitted and with every guard and checked entry kept, under clang++ with the address and undefined sanitizers and under g++, returning the same value or trap on every input; a mismatch is minimized into a program to keep |
| `tools/checks/occupancy.py` | every packaged card's resident blocks against CUDA's own occupancy calculator, `cuda_occupancy.h` built with g++ for the host, over block sizes, registers and shared bytes, by every limit; with `--device`, under `make device-limits`, the limits the GPU reports |
| `tools/checks/device_examples.py` | every device example of the repository built by `cairn build` for each named target, sm_80, sm_90a, sm_100a and sm_120 by default, with nothing run; a target the installed nvcc does not compile is skipped and named |
| `bench/suite/harness.py`, `report.py` | the eight kernels of [bench/suite/PREREGISTRATION.md](../bench/suite/PREREGISTRATION.md) under both compilers with the project's flags, each baseline guarded and unguarded, safety boundaries counted against the receipt, no result written when a case disagrees with its oracle; losses printed beside wins |

These scripts write under `results/`, which is not tracked, one subdirectory per kind of output.

`tools/corpus/` holds generated teaching fixtures beside the scripts that write and check them; no model was trained on them. Each generator's `--check` fails when a committed file differs from a fresh run, so a changed rule card, packet or receipt fails `tests/tooling/test_tools.py` with the command that regenerates it.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request, every push to `main` and every Monday. A pull request runs ten of its eighteen jobs: the compatibility jobs (`compilers`, `python`, `arm` and CUDA 12.9's two `device` jobs) run on `main` and on Monday, since several pull requests at once share the account's runners and a run of eighteen jobs held each one for most of an hour. A pull request needs one check, `ci-passed`, which passes only when every other job passed, so a job added later is required once it is in that job's `needs`; `tests/tooling/test_workflow.py` fails until it is. Every action is pinned by commit, the workflow reads the repository and writes nothing, and no job sets `CAIRN_GPU_TESTS`, so device code is compiled on runners without a GPU and never run.

| Job | What it catches | Runner | Time |
|---|---|---|---|
| `checks` | formatting, lint and types; a stale API reference; the examples, certificates and scalar equivalence | ubuntu-24.04 | 1 min |
| `tests`, four parts | the whole suite under the runner's Clang 18, GCC 13 and Python 3.12 | ubuntu-24.04 | 4 to 6 min |
| `proofs` | the Lean build, its axiom audit, and the differential runs against the checker | ubuntu-24.04 | 2 min |
| `device`, four | device code nvcc refuses: under CUDA 12.9 and 13.2, each with g++ and with clang++ as nvcc's host compiler, every test that compiles device code, and every device example built for sm_80, sm_90a, sm_100a and sm_120 | ubuntu-24.04 | 12 to 15 min |
| `compilers`, two | runtime headers and emitted C++ another compiler refuses or builds differently: the runtime, soundness, project and language tests under GCC 11 and Clang 13, the oldest supported, and under GCC 15 and Clang 23 | ubuntu-22.04, ubuntu-26.04 | 9 to 11 min |
| `python`, three | the compiler, the agent layer, the tools and the verifiers under Python 3.11, 3.13 and 3.14 | ubuntu-24.04 | 6 to 8 min |
| `arm` | an AArch64 host: the runtime, soundness and project tests, and the freestanding image under `qemu-system-aarch64`, which must run rather than skip | ubuntu-24.04-arm | 6 min |
| `package` | a file the sdist or the wheel leaves out: `cairn` installed from the wheel built from the sdist and run away from the checkout, and the Claude Code plugin from a clean copy of the repository | ubuntu-24.04 | 1 min |
| `ci-passed` | any job above that failed or was cancelled, or was skipped outside a pull request | ubuntu-24.04 | seconds |

The jobs run at once, so a run takes about as long as its longest device job, 15 minutes in the runs of September 2026 ([what they found](../evidence/v1_1/ci/README.md)), and a push to `main` queues behind the run before it rather than cancelling it. The device job's tests are `make device-build` where nvcc is installed. A test's own device builds give nvcc g++ unless `CAIRN_TEST_NVCC_HOST` names another host compiler, as `make device-build NVCC_HOST=clang++` does; `cairn build` gives it clang++. NVIDIA's and LLVM's packages, elan with the pinned Lean toolchain, and pip's downloads are cached between runs, and the proofs job tries a failed toolchain download four times before it fails.

`.github/workflows/docs.yml` builds the documentation site from `docs/` with `mkdocs build --strict` (`make site`) on every pull request and every push to `main`. A broken link, a heading a link names that does not exist, or a page left out of the navigation in `mkdocs.yml` fails that build. On `main` its `deploy` job then publishes the site to GitHub Pages at https://sammausberg.github.io/cairn/. The docs workflow is separate from `ci-passed`, so a pull request can merge while the site build fails, and the suite's own link check (`tests/tooling/test_repository.py`) still holds every relative link and heading.

## Safety and trust

CAIRN does not sandbox what it builds, and its toolchain is not verified, so run hostile programs, adapters or compilers under the isolation your operating system provides. `cairn run` caps a program's data, its heap and its thread stacks of 8 MiB each, at 1024 MiB by default, and `--memory-mib` sets the cap from 64 to 65536 MiB. The cap is lifted for a program that runs on a real device and for a run under a sanitizer. It protects no file, credential, system call or network access.

Outside `unsafe` and `extern`, an accepted program cannot use a moved owner, leak or consume twice a linear value, alias a mutable borrow, keep a borrow past its call, race in a parallel region, touch a place a live task was lent, or index memory of the wrong placement. Arithmetic, bounds, tags, extents and array parts are guarded, and a failed guard aborts the process, on the device as on the host, without running cleanup. These rules are implemented in the Python checker and tested. The checker itself is not proved, so a bug in it is a soundness bug: a way for an unsafe program to be accepted. [verification.md](verification.md) says which parts have a model proved in Lean.

`extern`, `mmio_read`, `mmio_write` and `asm` are usable only inside `unsafe { }`. Their effects reach every caller, and the receipt counts `unsafe` blocks per function, so an audit starts from the receipt instead of a text search:

```json
"process": { "effects": ["ffi:getpid", "io"], "syntactic_check_sites": { "unsafe_blocks": 1 } }
```

An extern's signature and effects are trusted as written, as are the effects [typed assembly](memory.md#layout-and-the-machine) declares and the contract of a [foreign implementation](memory.md#foreign-implementations), which `cairn foreign` holds to its reference only on the inputs it ran. A foreign caller of an exported function must supply live, initialized, correctly typed storage for every borrow. The entry guards cannot prove where a pointer came from or exclude concurrent foreign access. Those guards run only in the exported `cf_` symbol (`checked-entries/1` among the receipt's trusted lowering rules).

Manifests are data. They accept local listed paths only: no hooks, commands, downloads, arbitrary flags, traversal or symbolic links. Each rule is pinned in `tests/projects/test_projects.py`:

| Rule | Enforced by | Test that pins it |
|---|---|---|
| Manifests are data, and a dependency's manifest is read as strictly as the root's | `project.read_manifest` | `test_bad_manifests_fail_closed` |
| No path segment is a symbolic link | `project.contained_file` | `test_symlink_source_rejected` |
| A dependency is vendored inside the root, modules only, pinned by hash, nothing fetched | `project.dependencies` | `test_vendored_dependencies_load_first_stay_private_and_are_pinned` |
| A symbolic link is not a dependency directory | `project.dependencies` | `test_a_symbolic_link_is_not_a_dependency` |
| One directory is one project under one name, and one name is one project | `project.dependencies` | `test_one_name_is_one_project_of_the_build` |
| No project declares a `std.*` module, and no project reopens a module another declared | `project.claim` | `test_a_project_of_one_file_declares_no_module_of_the_packaged_library` |
| An executable's `main` comes only from the sources the root project lists | `build.build` | `test_the_entry_point_is_the_root_project_s_own` |
| A cached object is reused only while its bytes still match the digest beside it | `build.intact`, `build.store` | `test_a_cached_object_is_reused_only_while_its_bytes_still_match_its_key` |
| The object cache is `build/objects`; neither it, an object nor a digest may be a link | `build.objects` | `test_the_object_cache_stays_inside_the_project_build_directory` |
| A unit whose compile times out or is killed leaves no object and still writes `cairn.build/1` | `build.objects` | `test_a_unit_that_times_out_or_is_killed_records_the_same_build_as_one_unit_does` |

A dependency is source you chose to vendor. It is checked like your own code, its `unsafe` blocks and externs show in its callers' rows, and nothing else vouches for it. Compiler paths are trusted choices of the host machine, and `toolchain.py` is the only place a native flag is chosen.

An AI model that edits through the hosts cannot change a pinned reference, domain, signature, effect ceiling, visible dependency set or source outside its authorized range, and the whole linked module is checked again after every edit. The channels for proofs and feedback refuse, or report `unknown`, when a tool is missing. The language server executes nothing; a code lens only names the command the editor runs when a person clicks it.

No script here changes the repository's visibility, force-pushes, deletes a remote resource or uploads to a package index. Every workflow reads the repository and writes nothing, except the docs workflow's `deploy` job, which holds `pages: write` and `id-token: write` to publish the site from `main` (`tests/tooling/test_workflow.py` holds both). Report a soundness bug privately, as [SECURITY.md](../SECURITY.md) says.

## Releasing

A release is a tag on `main` whose evidence directory records what ran, on which machine, and what did not. Nothing is published by a test or a script.

The version is stated in eleven places, and `tests/tooling/test_release.py` and `tests/tooling/test_skill.py` hold them together: `pyproject.toml`, `src/cairn/version.py`, `CITATION.cff`, `proofs/lakefile.toml`, `editors/vscode/package.json`, the `profile` of `docs/project/capabilities.json`, the opening of the `base` rule card in `src/cairn/agent/teaching.py`, `bazel/MODULE.bazel` and `examples/bazel/MODULE.bazel`, and `.claude-plugin/plugin.json` and `marketplace.json`. `make editors` writes it into `skills/cairn/SKILL.md`. CHANGELOG.md gets one section per release.

Every change lands with the gates AGENTS.md lists. Then, on a clean committed tree:

```sh
make all native systems                        # lint, the suite, proofs, sanitizers, the systems examples
make gpu embedded                              # where the hardware and the emulator are present
make bench && python3 bench/suite/report.py    # the preregistered CPU suite, hours
python3 tools/release/collect_evidence.py --release "$RELEASE"          # the evidence folder name
python3 tools/release/collect_lean_evidence.py --release "$RELEASE"
make wheel audit
```

`collect_evidence.py` runs the release gates and writes to `evidence/<release>/summary.json` each gate's command, status and output, the commit, and whether the tree was dirty. `collect_lean_evidence.py` records a build of `proofs/` from scratch and the axiom audit under `lean/`. `RUN_NOTES.md` beside them names what did not run and why: a gate whose tool is absent is `unavailable`, never passed.

Tag the commit the evidence names with the version the eleven places state, and push `main` and the tag. A release that does not pass every gate on a committed tree is not tagged. A release claims only what its evidence shows: a speed, a GPU advantage or an AI result needs an executed run recorded under `evidence/`, with losses beside wins. `docs/project/capabilities.json` and [roadmap.md](roadmap.md) are rewritten at each release.

Two scripted paths touch a remote. The first, `tools/release/publish_private.py`, creates a new private personal repository and pushes `main` to it:

```sh
python tools/release/publish_private.py SamMausberg/cairn             # local-only dry run
python tools/release/publish_private.py SamMausberg/cairn --execute   # needs an authenticated gh
```

It first runs `tools/release/audit_repository.py`, which scans every committed blob for credential patterns and binaries; that scan is not an exhaustive detector. It then requires a clean tree with no remote, creates the repository, checks that it is private before and after a push that never forces, and registers `origin`. It never asks for a token, deletes or changes visibility. Its tests fake every remote call, so they cannot show that a real account accepts the push, and its checks cannot stop an administrator who changes the visibility after it has looked.

The second is `tools/release/sync_labels.py`. It makes the repository's labels the ones [.github/labels.yml](../.github/labels.yml) names, creating or updating each with `gh label create --force`, and it never deletes one. `--dry-run` prints the commands and runs none, and under a test or in CI it refuses to call `gh`. Its test runs it against a fake `gh` that must never be called, so it cannot show that GitHub accepts the labels.

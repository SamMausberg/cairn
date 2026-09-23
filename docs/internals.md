# Internals

How the compiler is built, how it is tested, what an accepted program promises, and how a release is cut. Paths are under `src/cairn/` unless they say otherwise.

## Compiler architecture

A project becomes a native artifact in nine stages. Each owns one question, and nothing but the emitter produces C++.

| Stage | Module | Entry point | What it produces |
|---|---|---|---|
| Load | `projects/project.py` | `load_project` | one combined source from the manifest's listed inputs, a sha256 per file, a line-to-file map |
| Parse | `compiler/lexing.py`, `compiler/syntax.py` (with `syntax_expressions.py`, `syntax_statements.py`) | `Parser.parse` | tokens, spans, a tree (`compiler/tree.py`) that resolves nothing |
| Link modules | `compiler/modules.py` | `link` | the imported `std.*` modules, merged in; nothing else is importable and nothing is fetched |
| Derive recipes | `compiler/expansion.py`, `compiler/gradients.py` | `derive`, `differentiate` | each `derive`'s declarations, as ordinary code of the deriving module; `derive grad` writes a function's adjoint |
| Specialize families | `compiler/expansion.py` | `specialize` | one function per variant of a family's natural range |
| Check | `compiler/checking.py` and its rule modules | `Checker.bodies` | one typed tree, annotated in place (`Expr.ty`, `Expr.ref`) |
| Judge | `compiler/checking.py` | `Checker.judge` | every effect row, and the rules that need all of them |
| Emit | `compiler/codegen.py` | `Emitter.units` | readable C++20: one shared header, one unit per module |
| Build | `projects/build.py` | `build` | a fresh directory, a hashed native artifact, a `cairn.build/1` receipt |

`compiler/cairnc.py` is the facade. `compile_program` runs parse through judge. `generate` checks the collector's seventeen certificates before it emits a line, and the `Emitter` asks the independent audit (`verify/elision.py`) to accept every discharged guard, writing the guard of each one it refuses.

Checking is one pass per function over one typed tree, and each generic instance is checked as ordinary code. One `Checker` class holds the program-wide tables and the walk over bodies. Each rule group is a module of functions taking the checker first, bound as methods by an explicit table so mypy checks every call, and `s_<tag>` and `e_<tag>` dispatch on the tree node's tag.

| Rule | File | Functions |
|---|---|---|
| Names, types, generic instances, signatures | `compiler/checking.py` | `resolve`, `define`, `signature`, `function`, `judge` |
| Per-function and per-region state | `compiler/scope.py` | `Scope`, `Lanes` |
| Statements: declarations, control flow, `match`, loops, `defer`, collectors | `compiler/statements.py` | `s_let`, `s_assign`, `s_if`, `s_match`, `s_for`, `s_defer`, `s_compact`, `branches` |
| Expressions: literals, names, indexing, fields, variants, closures, `try`, operators | `compiler/expressions.py` | `e_name`, `e_index`, `e_field`, `e_lambda`, `e_try`, `e_binary` |
| Calls, instances at the call, function values, construction and declared extents | `compiler/calls.py` | `e_call`, `invoke`, `view_argument`, `construct`, `establish` |
| Places, second-class borrows, moves, leases, aliasing | `compiler/places.py` | `path`, `place`, `overlaps`, `leased`, `lend`, `consume`, `disjoint` |
| Tasks and tickets, lanes and regions, atomics, placement | `compiler/concurrency.py` | `e_spawn`, `region`, `s_parallel`, `s_reduce`, `s_scan`, `judge_lane_callbacks`, `host_only` |
| Effect vocabulary, fixed point, operand order | `compiler/effects.py` | `fixed_point`, `audit` |
| Traits, bounds, overlap, dynamic tables | `compiler/traits.py` | `implemented`, `dispatch`, `vtable`, `certify` |
| Constant folding | `compiler/constants.py` | `constant`, `fold` |
| I/O rings: the declaration, the operations that move a `Buf` in and hand it back, their lowering | `compiler/rings.py` | `check_ring`, `method`, `waited`, `lower` |
| Facts about `usize` values that let lowering drop a guard | `compiler/facts.py` | `binder`, `defined`, `assume`, `index`, `arithmetic`, `conversion` |
| The independent check of each guard lowering leaves out | `verify/elision.py` | `audit`, `decide`, `part` |
| Which adjacent regions a plan's `fuse` joins, and the scratch they hold in their lanes | `compiler/fusion.py` | `chains`, `quiet`, `compatible`, `scratch` |
| Which arrays a plan's `vector` moves a chunk at a time in a device region, and that lowering | `compiler/chunks.py` | `chunkable`, `vectored`, `lower` |
| Which arrays a plan's `stage` loads into a device block's shared tile, and that lowering | `compiler/staging.py` | `stageable`, `staged`, `lower` |
| The tensor-core multiply, its numerical contract and its lowering | `compiler/tensor.py` | `check_mma`, `lower_mma` |
| Each primitive's type and cost, beside its lowering | `compiler/builtins.py` | `check_*` and `lower_*` |
| What each argument of `print`, `println`, `eprint`, `eprintln` and `format` writes, and their lowering | `compiler/printing.py` | `check_print`, `target`, `piece`, `lower_print` |
| The C header of a library: declarations, layouts it states and checks, what cannot cross | `compiler/header.py` | `Header.render`, `shape`, `refusal` |
| Manifests, vendored dependencies | `projects/project.py` | `read_manifest`, `contained_file`, `claim`, `dependencies` |
| Native flags, the closed table of system libraries, the freestanding effect ban | `projects/toolchain.py` | `command`, `flags`, `LIBRARIES`, `audit_effects` |

Per-function state lives in one `Scope`, swapped when an instance is checked in the middle of its caller, so instantiation is re-entrant. Every concrete signature is resolved before any body is checked.

Three questions have one answer each. Who may touch this place now is `leased`, which every access passes. Who implements this trait for this type is `implemented`, which serves static calls, `dyn` borrows and `Dyn` values alike. What may run in a lane is one rule, applied to the lane's row, to its callees' rows, and through `lane:f` to the closure finally passed.

A lane body is one lambda whose entry point, `cr::par::run` or `cr::gpu::launch`, is chosen by placement, so host and device share the emitter. `toolchain.py` chooses the one native command line. A device program's host pass takes `-fexceptions`, because CUDA 13's CUB headers contain throw and catch; nothing in the runtime throws, and `tests/tooling/test_tools.py` pins that as the one departure.

| Header in `runtime/` | Owns |
|---|---|
| `cairn_runtime.hpp` | the guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard is host and device callable |
| `cairn_owners.hpp` | the movable zeroed `Buf`, `Defer`, borrowed callables, checked parts |
| `cairn_parallel.hpp` | the host lane pool with its pooled reduction and two-pass scan, the crew of reusable task threads, linear tasks, task groups with a bounded completion ring, `Mutex` and `Atomic` with explicit orders |
| `cairn_gpu.hpp` | scoped device, pinned and unified memory, lanes, linear stream tickets, reduction, stable compaction, and execution contexts bound to CUDA |
| `cairn_reuse.hpp` | execution contexts apart from the machine: lanes (a stream and its event) lent until their work completes, one scratch arena ordered between its users on the device, a declared budget |
| `cairn_io.hpp` | the I/O ring over io_uring: fixed berths that own each operation's `Buf`, completion-order collection, a wait that drains before it releases |
| `cairn_float.hpp` | the storage floats `f16 bf16 f8e4m3 f8e5m2`: one integer routine that rounds on the host and in a device lane alike, `quantize` and `quantize_stochastic` |
| `cairn_tensor.hpp` | `mma_unordered`: the reference loop on the host, and on the device 64 x 64 tensor-core tiles over two shared-memory stages, written once against the operations a tile is given so a host test runs every thread's phases |
| `cairn_print.hpp` | `print` and `format`: every piece computed before a byte is written, one 4096-byte stack buffer, shortest round-trip floats through `std::to_chars`, a byte record grown as `std.vec` grows |
| `cairn_assert.hpp` | `assert`: the message a failed one prints, on standard error, through the device's printf in a lane, or not at all in an image, before the trap |

Generated code includes only the headers it needs, and a freestanding image includes neither concurrent header.

The lane pool is one of two pieces of global state. The first host region of a process creates it, with `hardware_concurrency()` threads or `CAIRN_LANES` (1 to 1024). A region below 16384 elements (`lanes::CUTOFF`) is the plain loop. Above that it engages one lane per 8192 elements (`lanes::GRAIN`), cuts its indices into one home per lane, and each lane claims chunks from its own home first, so a lane reruns the same indices from its own cache region after region. The thread that starts a region is one of its lanes and can finish it alone, so a busy or absent worker delays a region but cannot deadlock it; `proofs/Cairn/Region.lean` proves the protocol.

The other is the crew of task threads. A spawn takes a parked thread or starts a new one, so a task never waits for a thread and tasks that wait on each other cannot deadlock the crew. At most `hardware_concurrency()` threads stay parked. The crew is separate from the lane pool, so a lane may spawn a task and wait for it.

What each operation takes when it runs, and gives back when it ends:

| Operation | Takes | Gives back |
|---|---|---|
| host `parallel`, pooled `reduce` | a descriptor on the caller's stack, and a reduction's 256 block slots there too; the pool, once | nothing to release |
| host `compact`, in-order `reduce` | nothing: the loop writes the output in place | nothing |
| `spawn f(args)` | one heap cell for the result and one for the captured arguments; a parked thread, or a new one | the thread at `wait`, to the crew |
| `Group[T](n)`, `spawn ... into g` | four arrays of `n` at the declaration; per submission one heap cell for the captures, and a thread the first time a berth runs | the berths' threads at `wait(g)` |
| `IoRing(n)` | eight arrays of `n`, the io_uring descriptor and three mappings, at the declaration | all of it at `wait(q)`; nothing per operation |
| device `parallel` | a launch, then a whole-device synchronize | nothing |
| queued device work, `transfer` after a ticket | a new stream and a new event per ticket | both destroyed at its `wait`, after a stream synchronize |
| device `reduce` | a device cell for the result, CUB's temporary storage, a synchronous copy of the result to the host | both freed before it returns |
| device `compact` | two device arrays of `n` (flags, offsets), CUB's temporary storage, two launches, three synchronizations and two one-element copies to the host | all freed before it returns |
| a `@device`, `@pinned` or `@unified` buffer | one CUDA allocation, zeroed | freed at scope exit |

The device rows are where reuse is still to come. `cr::gpu::Context` (`cairn_reuse.hpp`) lends a stream and its event until the work queued on it completes, hands one scratch arena to each user in device order, and allocates within a budget declared up front. It is tested against a mock device (`tests/runtime/reuse_runtime.cpp`) and compiles for `sm_120`, but its device test runs only under `make gpu`, which has not run it, so the lowering does not use it yet.

The rest of the package, by folder, is in the ownership table of [AGENTS.md](../AGENTS.md): `agent/` the edit protocol, `perf/` the performance model (nothing in it runs a program except `measure.py` on the host and `on_device.py` under the owner's targets), `editor/` the formatter, language server and grammars, and `verify/` the certificates, the SMT model, the test runners and `cairn diff`. No agent, test generator or solver may rewrite the authority it is checked against, and native libraries never import the agent tooling or Z3.

The wheel holds the compiler package, the runtime headers, the target support files, the `std` sources and the CLI. Tests, benchmarks, proofs and evidence stay out of it.

## Testing

Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

```sh
make lint          # ruff format --check, ruff check, mypy over the package, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, the Lean export drift check, lake build, the differential run, scalar module equivalence
make native        # both compilers with the sanitizers that bite, and the codegen comparison
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make gpu`, `make tune-device` and `make calibrate-device` are the only commands that run code on a CUDA device, and only the owner runs them. Each sets `CAIRN_GPU_TESTS=1` and holds `/tmp/cairn-gpu.lock` around every device run (`tools/support.py`), and `tests/tooling/test_on_device.py` holds the Makefile to those three. Everywhere else a device test compiles and skips the run. The reason is the reference machine: under WSL2 the GPU also drives the display, and repeated device test runs reset its driver and twice crashed the host.

`make proof` needs `lake` on `PATH` (elan puts it in `~/.elan/bin`), and allows no axiom but `propext` and `Quot.sound`.

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted breadth of the language, built and run natively under both compilers; the standard library module by module; the twelve tour programs; every `cairn` block in README.md and under `docs/` |
| `soundness/` | every hole an adversarial review found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures; guard elision against its independent audit; plans, fusion, `scan` and rings |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, the checker and the Lean calculus classifying generated programs alike, the SMT translator against concrete replay, coverage that no single function can confer, the classes of `cairn diff` |
| `projects/` | manifests, vendored dependencies, incremental builds, every application under `examples/apps/`, the templates of `cairn new`, test blocks and their runner, a C++ host linking a CAIRN library, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the self-checking C++ and CUDA binaries beside it, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus whitespace and comment fuzz, a real `cairn lsp` subprocess and the extension's client, the generated grammars against a TextMate engine, `predict` and `tune`, the terminal output and shell completions, the tree's own rules, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets and their evidence classes, rule cards, sketches, guarded edits, migrations, plan edits, state and deltas, and the canonical projection round-tripping every sample and `std` module to identical native code |
| `oracles/` | not pytest modules: the Python oracles `tools/checks/verify.py` drives |

Sanitizers run where they bite: ownership under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. A task whose child exits abnormally has failed, whatever it printed.

A rejection table maps a sentence naming a rule to a diagnostic code and a program, and one parametrized test requires exactly that code, so a rule that stops biting fails by name. Every safety rule has one. A behaviour table maps a sentence to a program and its expected exit status under clang++ with the address and undefined sanitizers, `-6` where a guard must abort.

| Script | What it checks |
|---|---|
| `tools/checks/verify.py` | rebuilds the native artifacts and drives the oracles in `tests/oracles/`: equal boundary checks, strict floating flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `tools/checks/validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an O0 observer counting allocation and release across returns, loops and match exits |
| `tools/checks/validate_semantics.py` | the translator and trap-aware interpreter against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and input-pinned SMT |
| `tools/checks/semantic_check.py`, `tools/corpus/semantic_corpus.py` | two scalar implementations against one immutable reference; same-contract pairs, every label decided and replayed |
| `tools/corpus/curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one hand-authored defect per algorithm family, all of which the finite tests must catch |
| `tools/checks/density.py`, `export_lean_certificates.py` | lexical density accounting over the whole compiler; `--check` fails when `collector_rules()` and `proofs/` have drifted |
| `tools/checks/emission_identity.py` | whether a source change left the C++ and effect rows of every example, the whole `std`, every program written into a test and every `cairn` block of the docs as they were, optionally up to two named identities of C++, and with `--normalize guards` whether a change did nothing but discharge guards, counting each program's guards |
| `tools/checks/differential_ownership.py` | generated programs of one shared fragment, rendered as CAIRN source and as Lean `Program` literals, classified identically by the checker and by the Lean `accepts` |
| `tools/checks/differential_guards.py` | generated programs built as emitted and with every guard and checked entry kept, under both compilers and the sanitizers, returning the same value or trap on every input; a mismatch is minimized into a program to keep |
| `bench/suite/harness.py`, `report.py` | the eight kernels of [bench/suite/PREREGISTRATION.md](../bench/suite/PREREGISTRATION.md), every arm built under both compilers with the project's own flags, each baseline once guarded and once not, safety boundaries counted against the build receipt, no result written when a case disagrees with its oracle; the report prints losses beside wins |

These harnesses write under `results/`, which is not tracked, one subdirectory per kind of output.

`tools/corpus/` holds generated teaching fixtures beside the scripts that write and check them. No model was trained on them, and the answers ship beside the tasks. Each generator's `--check` fails when a committed file differs from a fresh run, so a changed rule card, packet or receipt shows up in `tests/tooling/test_tools.py` with the command that regenerates it.

## Safety and trust

CAIRN does not sandbox what it builds, and its toolchain is not verified, so run hostile programs, adapters or compilers under OS isolation. `cairn run` caps virtual address space at 1024 MiB by default (64 to 65536 MiB), lifted for device programs, and that limit protects no file, credential, syscall or network access.

Outside `unsafe` and `extern`, an accepted program cannot use a moved owner, leak or double-consume a linear value, alias a mutable borrow, keep a borrow past its call, race in a parallel region, touch a place a live task was lent, or index memory of the wrong placement. Arithmetic, bounds, tags, extents and array parts are guarded, and a failed guard aborts the process, on the device as on the host, without running cleanup. These rules are implemented in the Python checker and tested; they are not mechanized, so a checker bug is a soundness bug. [verification.md](verification.md) says which parts have a proved model.

`extern`, `mmio_read`, `mmio_write` and `asm` are usable only inside `unsafe { }`. Their effects reach every caller, and `unsafe` blocks are counted per function in the receipt, so an audit starts from the receipt rather than a text search:

```json
"process": { "effects": ["ffi:getpid", "io"], "syntactic_check_sites": { "unsafe_blocks": 1 } }
```

An extern's signature and effects are trusted as written. A foreign caller of an exported function must supply live, initialized, correctly typed storage for every borrow; the entry guards cannot prove provenance or exclude concurrent foreign access. Those guards run in the exported `cf_` symbol only, and a call from CAIRN reaches the lean body `ci_` (`checked-entries/1` among the receipt's trusted lowering rules).

Manifests are data and accept local listed paths only: no hooks, commands, downloads, arbitrary flags, traversal or symlinks. Each rule is pinned in `tests/projects/test_projects.py`:

| Rule | Enforced by | Test that pins it |
|---|---|---|
| Manifests are data, and a dependency's is read as strictly as the root's | `project.read_manifest` | `test_bad_manifests_fail_closed` |
| No path segment is a symbolic link | `project.contained_file` | `test_symlink_source_rejected` |
| A dependency is vendored inside the root, modules only, pinned by hash, nothing fetched | `project.dependencies` | `test_vendored_dependencies_load_first_stay_private_and_are_pinned` |
| A symbolic link is not a dependency directory | `project.dependencies` | `test_a_symbolic_link_is_not_a_dependency` |
| One directory is one project under one name, and one name is one project | `project.dependencies` | `test_one_name_is_one_project_of_the_build` |
| No project declares a `std.*` module, and no project reopens a module another declared | `project.claim` | `test_a_project_of_one_file_declares_no_module_of_the_packaged_library` |
| An executable's `main` comes only from the sources the root project lists | `build.build` | `test_the_entry_point_is_the_root_project_s_own` |
| A cached object is reused only while its bytes still match the digest beside it | `build.intact`, `build.store` | `test_a_cached_object_is_reused_only_while_its_bytes_still_match_its_key` |
| The object cache is `build/objects`; neither it, an object nor a digest may be a link | `build.objects` | `test_the_object_cache_stays_inside_the_project_build_directory` |
| A unit whose compile times out or is killed leaves no object and still writes `cairn.build/1` | `build.objects` | `test_a_unit_that_times_out_or_is_killed_records_the_same_build_as_one_unit_does` |

A dependency is source you chose to vendor: it is checked like your own code, its `unsafe` blocks and externs show in its callers' rows, and nothing else vouches for it. Compiler paths are trusted host choices, and `toolchain.py` is the only place a native flag is chosen.

A model cannot change a pinned reference, domain, signature, effect ceiling, visible dependency set or source outside its authorized range through an edit, and the whole linked module is rechecked after every edit. Proof and feedback channels fail closed when a tool is missing. The language server executes nothing; a code lens only names the command the editor runs when a person clicks it.

No script here changes the repository's visibility, force-pushes, deletes a remote resource or uploads to a package index. The CI workflow has read-only permissions. Report a soundness bug privately, as [SECURITY.md](../SECURITY.md) says.

## Releasing

A release is a tag on `main` whose evidence directory records what ran, on which machine, and what did not. Nothing is published by a test or a script.

The version is stated in six places that `tests/tooling/test_release.py` holds together: `pyproject.toml`, `src/cairn/version.py`, `proofs/lakefile.toml`, `editors/vscode/package.json`, the `profile` of `docs/project/capabilities.json` and the opening of the `base` rule card in `src/cairn/agent/teaching.py`. CHANGELOG.md gets one section per release.

Every change lands with the gates AGENTS.md lists. Then, on a clean committed tree:

```sh
make all native systems                        # lint, the suite, proofs, sanitizers, the systems examples
make gpu embedded                              # where the hardware and the emulator are present
make bench && python3 bench/suite/report.py    # the preregistered CPU suite, hours
python3 tools/release/collect_evidence.py --release "$RELEASE"          # the evidence folder name
python3 tools/release/collect_lean_evidence.py --release "$RELEASE"
make wheel audit
```

`collect_evidence.py` runs the release gates and writes each gate's command, status and output, the commit and whether the tree was dirty to `evidence/<release>/summary.json`. `collect_lean_evidence.py` rebuilds `proofs/` from scratch and records the build and the axiom audit under `lean/`. `RUN_NOTES.md` beside them names what did not run and why: a gate whose tool is absent is `unavailable`, never passed.

Tag the commit the evidence names with the version the six places state, and push `main` and the tag. A release that does not pass every gate on a committed tree is not tagged. A release claims only what its evidence shows, a speed, a GPU advantage or an AI result only with an executed run recorded under `evidence/`, losses beside wins. `docs/project/capabilities.json` and [roadmap.md](roadmap.md) are rewritten at each release.

`tools/release/publish_private.py` is the one scripted path that touches a remote. It creates a new private personal repository and pushes `main` to it, nothing more:

```sh
python tools/release/publish_private.py SamMausberg/cairn             # local-only dry run
python tools/release/publish_private.py SamMausberg/cairn --execute   # needs an authenticated gh
```

It first runs `tools/release/audit_repository.py`, which scans every committed blob for credential patterns and binaries (not an exhaustive detector), then requires a clean tree with no remote, creates the repository, checks it is private before and after a non-force push, and registers `origin`. It never asks for a token, forces, deletes or changes visibility. Its tests use fakes for every remote call, so they cannot show that a real account accepts the push.

# Internals

How the compiler is built, how it is tested, what an accepted program promises, and how a release is cut. Paths are under `src/cairn/` unless they say otherwise.

## Compiler architecture

A project becomes a native artifact in nine stages. Each owns one question, and nothing but the emitter produces text.

| Stage | Module | Entry point | What it produces |
|---|---|---|---|
| Load | `projects/project.py` | `load_project` | one combined source from the manifest's listed inputs, a sha256 per file, a line-to-file map |
| Parse | `compiler/lexing.py`, `compiler/syntax.py` | `Parser.parse` | tokens, spans, a tree (`compiler/tree.py`) that resolves nothing |
| Link modules | `compiler/modules.py` | `link` | the imported `std.*` modules, merged in; nothing else is importable and nothing is fetched |
| Derive recipes | `compiler/expansion.py` | `derive` | each `derive`'s declarations, as ordinary code of the deriving module |
| Specialize families | `compiler/expansion.py` | `specialize` | one function per variant of a family's natural range |
| Check | `compiler/checking.py` and its rule modules | `Checker.bodies` | one typed tree, annotated in place (`Expr.ty`, `Expr.ref`) |
| Judge | `compiler/checking.py` | `Checker.judge` | every effect row, and the rules that need all of them |
| Emit | `compiler/codegen.py` | `Emitter.units` | readable C++20: one shared header, one unit per module |
| Build | `projects/build.py` | `build` | a fresh directory, a hashed native artifact, a `cairn.build/1` receipt |

`compiler/cairnc.py` is the facade. `compile_program` runs parse through judge; `generate` calls `audit_collector` (`verify/linear_certificates.py`) before it emits a line, so the collector's one unchecked store never reaches C++ without its seventeen certificates.

Checking is one pass per function over one typed tree, and a generic instance is checked as ordinary monomorphic code. One `Checker` class holds the program-wide tables, name and type resolution, generic instances, the whole-program judge and the walk over bodies. Each rule group is a module of functions that take the checker, bound as methods by a short table, so `s_<tag>` and `e_<tag>` dispatch by the tree node's tag.

| Rule | File | Functions |
|---|---|---|
| Names, types, generic instances, signatures | `compiler/checking.py` | `resolve`, `define`, `signature`, `function`, `judge` |
| Per-function and per-region state | `compiler/scope.py` | `Scope`, `Lanes` |
| Statements: declarations, control flow, `match`, loops, `defer`, collectors | `compiler/statements.py` | `s_let`, `s_assign`, `s_if`, `s_match`, `s_for`, `s_defer`, `s_compact`, `branches` |
| Expressions: literals, names, indexing, fields, variants, closures, `try`, operators | `compiler/expressions.py` | `e_name`, `e_index`, `e_field`, `e_lambda`, `e_try`, `e_binary` |
| Calls, instances at the call, function values, construction and declared extents | `compiler/calls.py` | `e_call`, `invoke`, `view_argument`, `construct`, `establish` |
| Places, second-class borrows, moves, leases, aliasing | `compiler/places.py` | `path`, `place`, `overlaps`, `leased`, `lend`, `consume`, `disjoint` |
| Tasks and tickets, lanes and regions, atomics, placement | `compiler/concurrency.py` | `e_spawn`, `region`, `s_parallel`, `s_reduce`, `judge_lane_callbacks`, `host_only` |
| Effect vocabulary, fixed point, operand order | `compiler/effects.py` | `fixed_point`, `audit` |
| Traits, bounds, overlap, dynamic tables | `compiler/traits.py` | `implemented`, `dispatch`, `vtable`, `certify` |
| Constant folding | `compiler/constants.py` | `constant`, `fold` |
| Each primitive's type and cost, beside its lowering | `compiler/builtins.py` | `check_*` and `lower_*` |
| Manifests, vendored dependencies | `projects/project.py` | `read_manifest`, `contained_file`, `claim`, `dependencies` |
| Native flags, the freestanding effect ban | `projects/toolchain.py` | `command`, `flags`, `audit_effects` |

Per-function state lives in one `Scope` that the checker swaps when it checks an instance in the middle of its caller, so instantiation is re-entrant. Signatures are resolved for every concrete function before any body is checked, so a call site never sees an unresolved type.

Three questions have one answer each. Who may touch this place now is `leased`, which every access passes: it enforces task leases and records what a closure captures. Who implements this trait for this type is `implemented`: it holds an impl to its trait's declaration, rejects overlap, instantiates generic impls, and serves static calls, `dyn` borrows and `Dyn` values alike. What may run in a lane is one rule applied three times: to the lane's own row, to the rows of the functions it calls, and, through the `lane:f` footprint that the fixed point renames up the call graph, to the closure or function finally passed for a parameter that lanes call.

A lane body is one lambda whose entry point, `cr::par::run` or `cr::gpu::launch`, is chosen by placement, so host and device share the emitter. `toolchain.py` chooses the one native command line: clang++/g++, or nvcc under the same strict floating-point and warning contract. A device program's host pass makes one departure from that contract, `-fexceptions` in place of `-fno-exceptions`, because CCCL 3 (CUDA 13) writes unguarded throw and catch inside the headers CUB's dispatch requires. Nothing in the runtime throws, guards still abort, and `tests/tooling/test_tools.py` pins the swap to that one flag.

| Header in `runtime/` | Owns |
|---|---|
| `cairn_runtime.hpp` | the guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard is host and device callable |
| `cairn_owners.hpp` | the movable zeroed `Buf`, `Defer`, borrowed callables, checked parts |
| `cairn_parallel.hpp` | the host lane pool, linear tasks, task groups with a bounded completion ring, `Mutex` and `Atomic` with explicit orders |
| `cairn_gpu.hpp` | scoped device, pinned and unified memory, lanes, linear stream tickets, reduction, stable compaction |

Generated code includes only the headers it needs. A freestanding image includes neither concurrent header: `toolchain.audit_effects` rejects every effect that reaches them, and `cairn_parallel.hpp` refuses to compile under `CAIRN_FREESTANDING`.

The one piece of global state is the lane pool, and it is visible in the source: the first host `parallel` statement of a process creates it, every later one reuses it. Its size is `std::thread::hardware_concurrency()`, or `CAIRN_LANES` when that names a count from 1 to 1024; anything else traps. A region below 16384 elements (`lanes::CUTOFF`) is the loop it replaces and never touches the pool. Above that it publishes a descriptor on its own stack, engages one lane per 8192 elements (`lanes::GRAIN`) up to the pool's size, and returns when every claimed chunk has run. The thread that starts a region is always one of its lanes and can finish it alone, so a blocked, busy or absent worker delays a region but cannot deadlock one. The pool is never destroyed: an exit handler stops and joins its workers, after which a region still runs on the thread that starts it. Both numbers were measured on one machine; `evidence/v1_2/host_regions` records what was measured.

`agent/agent_tools.py` supplies typed source sites, the canonical read-only projection of the whole language, and sealed edit sessions whose effect ceiling can name any effect. `agent/sketches.py` binds named choices to ranges and contracts the host owns, and `agent/teaching.py` selects rule cards from lexical tokens. Splicing preserves everything outside the authorized range, and the host rechecks the complete linked module, beyond what the packet displayed.

`verify/linear_certificates.py` checks exact affine implications. `verify/scalar_values.py`, `scalar_symbolic.py`, `scalar_concrete.py` and `scalar_semantics.py` are the value model, the SMT translator, the concrete replay and the query. `verify/verification.py` owns aggregate coverage, and one passing function cannot mark a module checked. No agent, test generator or solver may rewrite the authority it is checked against. Native libraries never import the agent tooling or Z3.

The wheel holds the compiler package, the runtime headers, the target support files, the `std` sources, the typed marker and the CLI metadata. Tests, benchmarks, proofs, evidence and historical specifications stay out of it. Ordinary compilation has no third-party Python dependency; Z3, Lean, CUDA and QEMU are optional local tools.

## Testing

Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

```sh
make lint          # ruff format --check, ruff check, mypy over the package, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, the Lean export drift check, lake build, the differential run, scalar module equivalence
make native        # both compilers with the sanitizers that bite, and the codegen comparison
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make proof` needs `lake` on `PATH`; elan installs it in `~/.elan/bin`. Its Lean half prints the axioms behind every theorem, and only `propext` and `Quot.sound` are allowed. It runs only the positive coverage case; the negative one, `cairn verify examples/proof_scope/mixed.cairn examples/proof_scope/mixed.cairn --all`, must come back incomplete and nonzero.

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted breadth of the language, built and run natively under both compilers; the twelve tour programs; every `cairn` block in README.md and under `docs/` |
| `soundness/` | every hole an audit found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, the checker and the Lean calculus classifying generated programs alike, the SMT translator against concrete replay, coverage that no single function can confer |
| `projects/` | manifests, vendored dependencies, incremental builds, the five applications, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the self-checking binaries in `tests/native/`, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus whitespace and comment fuzz, a real `cairn lsp` subprocess, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets, rule cards, sketches, guarded edits, and the canonical projection round-tripping every sample and `std` module to identical native code |
| `oracles/`, `native/` | not pytest modules: the Python oracles `tools/checks/verify.py` drives, and the C++ and CUDA fixtures `runtime/` compiles |

Sanitizers run where they bite: the ownership program under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. Death tests must abort the host, one per invocation: a guard that fires inside a CUDA lane, an unawaited ticket. A task whose child exits abnormally has failed, whatever it printed.

A rejection table maps a sentence naming the rule to a diagnostic code and a program. One parametrized test compiles each entry and requires exactly that code, so a rule that stops biting fails by name. `tests/soundness/test_soundness.py` holds the largest table; smaller tables sit beside the feature they guard, and every safety rule has one. A native behaviour table maps a sentence to an expected process exit status and a program: the test emits C++ for that entry point alone, builds under clang++ with `-fsanitize=address,undefined`, runs it, and requires exactly that status, `0` where the program judges itself and `-6` where a guard must abort.

| Script | What it checks |
|---|---|
| `tools/checks/verify.py` | rebuilds the native artifacts and drives the oracles in `tests/oracles/`: equal boundary checks, strict floating flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `tools/checks/validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an O0 observer counting allocation and release across returns, loops and match exits |
| `tools/checks/validate_semantics.py` | the translator and trap-aware interpreter against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and input-pinned SMT |
| `tools/checks/semantic_check.py`, `semantic_corpus.py` | two scalar implementations against one immutable reference; same-contract pairs, every label decided and replayed |
| `tools/checks/curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one hand-authored defect per algorithm family, all of which the finite tests must catch |
| `tools/checks/density.py`, `export_lean_certificates.py` | lexical density accounting over the whole compiler; `--check` fails when `collector_rules()` and `proofs/` have drifted |
| `tools/checks/differential_ownership.py` | generated programs of one shared fragment, rendered as CAIRN source and as Lean `Program` literals, classified identically by the checker and by the Lean `accepts` |
| `bench/suite/harness.py`, `report.py` | the eight kernels of [bench/suite/PREREGISTRATION.md](../bench/suite/PREREGISTRATION.md), every arm built under both compilers with the project's own flags, each baseline once guarded and once not, safety boundaries counted against the build receipt, no result written when a case disagrees with its oracle; the report prints losses beside wins |

`bench/cpu/codegen_only.py` compares code sections by instruction bytes and relocations and implies no timing run. These harnesses write under `results/`, which is ignored and may be replaced on rerun, one subdirectory per kind of output: `native/` (the shared build area), `checks/`, `codegen/`, `timing/`, `gpu/`, `host_regions/`, `semantics/`, `systems/`, `density/`, `context/`, `agent/` and `bench_suite/`.

`training/` holds inherited teaching fixtures: `source/` the 0.3 program lessons, `semantic/` the 0.4 same-contract scalar preferences, obligations and protocol repairs. They are inputs kept for audit, regenerated by `tools/ai/curriculum.py`, `tools/checks/semantic_corpus.py` and `tools/ai/protocol_curriculum.py`, and checked by `curriculum_verify.py` and `mutation_checks.py`. No model was trained, and the answers ship beside the tasks.

A failed, timed-out or interrupted command is recorded as such, and rerunning it unchanged adds a new result without erasing the failure. A task runner needs a good child exit as well as its JSON result. Process limits are not a sandbox.

## Safety and trust

CAIRN is a young compiler. It does not sandbox what it builds, and its toolchain is not verified, so run hostile programs, adapters or compilers under OS isolation. CPU, address-space and core-dump limits do not protect files, credentials, syscalls or network access. `cairn run` caps virtual address space at 1024 MiB by default (64..65536 MiB); the cap is lifted for device programs, because CUDA reserves far more address space than it uses, and it never constrains a shared library loaded by a foreign host.

Outside `unsafe` and `extern`, an accepted program cannot use a moved owner, leak or double-consume a linear value, alias a mutable borrow, keep a borrow past its call, race in a parallel region, touch a place a live task was lent, or index memory of the wrong placement. Arithmetic, bounds, tags, extents and array parts are guarded, and a failed guard aborts the process, on the device as on the host. These rules are implemented in the checker and exercised by rejection tests and by native runs under four sanitizers and device death tests. They are not mechanized: a checker bug is a soundness bug, generic code is checked per instance, and aborts do not run cleanup.

`extern` declarations, `mmio_read`, `mmio_write` and `asm` are usable only inside `unsafe { }`. Their effects (`ffi:symbol`, `io`, `mmio`, `asm`) propagate to every transitive caller, and `unsafe` blocks are counted per function in the receipt, so an audit starts from the receipt rather than from a text search:

```json
"process": { "effects": ["ffi:getpid", "io"], "syntactic_check_sites": { "unsafe_blocks": 1 } }
```

An extern's signature and effects are trusted as written. Callers of exported functions must supply live, initialized, correctly typed storage for every borrow, and a valid tag and active payload for every sum; numerical entry guards cannot prove provenance or exclude concurrent foreign access.

Manifests are data and accept local listed paths only: no hooks, commands, downloads, arbitrary flags, traversal or symlinks. One checker reads every manifest of a build, so a dependency's `cairn.toml` is refused for the same reasons as the root's. Each rule is enforced in `projects/` and pinned in `tests/projects/test_projects.py`.

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

A dependency is source you chose to vendor: it is checked like your own code, its `unsafe` blocks and `extern` declarations show in the effect rows of whatever calls them, and nothing else vouches for it. Native builds use fresh directories and never reuse a stale binary; `--incremental` reuses an object only under a key that hashes everything that went into it and only while the object still matches its digest, which checks integrity and authorizes nothing. Compiler paths and output locations are trusted host choices, and `toolchain.py` is the only place a native flag is chosen.

A model cannot change a pinned reference, domain, signature, effect ceiling, visible dependency set or source outside its authorized range through an edit response, and the whole linked module is rechecked after every edit. Proof and feedback channels fail closed for missing tools or unsupported fragments. The formatter fails closed: same tokens and comments, or no change. The language server analyses the open buffer in-process and executes nothing.

No script here changes the repository's visibility, force-pushes, deletes a remote resource or uploads to a package index. The one scripted remote action, the private publisher below, creates a new private repository and pushes to it after a preflight that scans every reachable committed blob for finite credential patterns and excluded binary and secret paths; it is not an exhaustive detector. The CI workflow has read-only permissions. Report a soundness bug privately, as [SECURITY.md](../SECURITY.md) says.

## Releasing

A release is a tag on `main` whose evidence directory records what ran, on which machine, and what did not. Nothing is published by a test or a script.

One version string is stated in six places, and they must agree: `pyproject.toml`, `src/cairn/version.py` (which the receipt reports as the profile `cairn-native/<version>`), `proofs/lakefile.toml`, `editors/vscode/package.json`, the `profile` of `docs/project/capabilities.json` and the opening of the `base` rule card in `src/cairn/agent/teaching.py`. CHANGELOG.md gets one section per release, in the same categories as the ones before it.

Every change lands with the gates AGENTS.md lists. Then, on a clean committed tree:

```sh
make all native systems                        # lint, the suite, proofs, sanitizers, the systems examples
make gpu embedded                              # where the hardware and the emulator are present
make bench && python3 bench/suite/report.py    # the preregistered CPU suite, hours
python3 tools/release/collect_evidence.py --release v1_3
python3 tools/release/collect_lean_evidence.py --release v1_3
make wheel audit
```

`collect_evidence.py` runs the release gates and writes `evidence/<release>/summary.json`: each gate's command, status, exit code, seconds and last lines, `cairn doctor`, the commit, whether the tree was dirty, and source-line counts. `collect_lean_evidence.py` rebuilds `proofs/` from scratch and writes the build log, the axiom audit, the toolchain versions and a summary under `lean/`. The GPU record is copied from `results/gpu/benchmark.json`, the freestanding transcript from `cairn run examples/embedded`, and the bench tables from `results/bench_suite/`, each into its own subdirectory. `RUN_NOTES.md` beside them names what did not run and why: a gate whose tool is absent is `unavailable`, never passed.

Tag the commit the evidence names, `git tag -a v1.3.0`, and push `main` and the tag. A release that does not pass every gate on a committed tree is not tagged. A release claims only what its evidence directory shows: accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are separate claims, an earlier release's evidence is history, and a speed, a GPU advantage or an AI result is claimed only with an executed run recorded under `evidence/`, with its losses beside its wins. `docs/project/capabilities.json` and [roadmap.md](roadmap.md) are rewritten at each release to match what landed.

`tools/release/publish_private.py` is the one scripted path that touches a remote. It creates a new private personal repository and pushes `main` to it, nothing more:

```sh
python tools/release/publish_private.py SamMausberg/cairn             # local-only dry run
python tools/release/publish_private.py SamMausberg/cairn --execute   # needs an authenticated gh
```

It runs the audit of `tools/release/audit_repository.py` first, requires a clean tree on `main` with no configured remote, creates the repository as the collision check, verifies identity and privacy before and after an exact-commit non-force push, and then registers `origin`. It never asks for a token, installs a credential helper, reuses an existing repository, forces, deletes or changes visibility. A failure after creation can leave an empty private repository, which it does not delete. Its tests use fakes for every remote interaction, so they show which branches it takes and cannot show that a real account accepts the push.

# Compiler ownership and data flow

A project becomes a native artifact in nine stages. Each owns one question, and nothing but the emitter produces text. Paths below are under `src/cairn/`.

| Stage | Module | Entry point | What it produces |
|---|---|---|---|
| Load | `projects/project.py` | `load_project` | one combined source from the manifest's listed inputs, a sha256 per file, a line-to-file map |
| Parse | `compiler/syntax.py` | `Parser.parse` | tokens, spans, a tree that resolves nothing |
| Link modules | `compiler/modules.py` | `link` | the imported `std.*` modules, merged in; nothing else is importable and nothing is fetched |
| Derive recipes | `compiler/expansion.py` | `derive` | each `derive`'s declarations, as ordinary code of the deriving module |
| Specialize families | `compiler/expansion.py` | `specialize` | one function per variant of a family's natural range |
| Check | `compiler/checking.py` | `Checker.bodies` | one typed tree, annotated in place (`Expr.ty`, `Expr.ref`) |
| Judge | `compiler/checking.py` | `Checker.judge` | every effect row, and the rules that need all of them |
| Emit | `compiler/codegen.py` | `Emitter.units` | readable C++20: one shared header, one unit per module |
| Build | `projects/build.py` | `build` | a fresh directory, a hashed native artifact, a `cairn.build/1` receipt |

`compiler/cairnc.py` is the facade. `compile_program` runs parse through judge; `generate` calls `audit_collector` (`verify/linear_certificates.py`) before it emits a line, so the collector's one unchecked store never reaches C++ without its seventeen certificates.

## Where a rule lives

Checking is one pass per function over one typed tree, and a generic instance is checked as ordinary monomorphic code. `effects.py`, `traits.py` and `constants.py` are functions over the checker, not separate passes, so `checking.py` stays the statement and expression rules.

| Rule | File | Function |
|---|---|---|
| Names, types, generic instances | `compiler/checking.py` | `Checker.resolve`, `Checker.expr` |
| Places, second-class borrows | `compiler/checking.py` | `Checker.place` |
| Affine moves, releases and linear values | `compiler/checking.py` | `Checker.consume`, `Checker.leaks`, `Checker.releases`, `Checker.released`, `Checker.branches` |
| Task leases | `compiler/checking.py` | `Checker.leased` |
| Lane race freedom | `compiler/checking.py` | `Checker.region`, `Checker.judge_lane_callbacks` |
| Placement | `compiler/checking.py` | `Checker.host_only`, `Checker.judge` |
| Effect vocabulary, fixed point, operand order | `compiler/effects.py` | `fixed_point`, `audit` |
| Traits, bounds, overlap, dynamic tables | `compiler/traits.py` | `implemented`, `dispatch`, `vtable`, `certify` |
| Constant folding | `compiler/constants.py` | `constant`, `fold` |
| Each primitive's type and cost, beside its lowering | `compiler/builtins.py` | `check_*` and `lower_*` |
| Manifests, vendored dependencies | `projects/project.py` | `read_manifest`, `contained_file`, `claim`, `dependencies` |
| Native flags, the freestanding effect ban | `projects/toolchain.py` | `command`, `flags`, `audit_effects` |

Per-function state lives in one `Scope` that the checker swaps when it checks an instance in the middle of its caller, so instantiation is re-entrant. Signatures are resolved for every concrete function before any body is checked, so a call site never sees an unresolved type.

Three questions have one answer each. *Who may touch this place now?* is `leased`, which every access passes: it enforces task leases and records what a closure captures, and those captures are the closure's borrows at the call it is written in. *Who implements this trait for this type?* is `implemented`: it holds an impl to its trait's declaration, rejects overlap, instantiates generic impls, and serves static calls, `dyn` borrows and `Dyn` values alike. *What may run in a lane?* is one rule applied three times: to the lane's own row, to the rows of the functions it calls, and, through the `lane:f` footprint that the fixed point renames up the call graph, to the closure or function finally passed for a parameter that lanes call. Dynamic call edges are drawn after every body is checked, since a later coercion can add an implementation.

A lane body is one lambda whose entry point, `cr::par::run` or `cr::gpu::launch`, is chosen by placement, so host and device share the emitter. `toolchain.py` chooses the one native command line: clang++/g++, or nvcc under the same strict floating-point and warning contract.

## Runtime headers

| Header in `runtime/` | Owns |
|---|---|
| `cairn_runtime.hpp` | the guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard is host and device callable, and the device trap was chosen by measurement |
| `cairn_owners.hpp` | the movable zeroed `Buf`, `Defer`, borrowed callables, checked parts |
| `cairn_parallel.hpp` | the host lane pool, linear tasks, `Mutex` and `Atomic` with explicit orders |
| `cairn_gpu.hpp` | scoped device, pinned and unified memory, lanes, linear stream tickets, reduction, stable compaction |

Generated code includes only the headers it needs. A freestanding image includes neither concurrent header: `toolchain.audit_effects` rejects every effect that reaches them, and `cairn_parallel.hpp` refuses to compile under `CAIRN_FREESTANDING` so that stays true.

The one piece of global state is that lane pool, and it is visible in the source: the first host `parallel` statement of a process creates it, every later one reuses it. Its size is `std::thread::hardware_concurrency()`, or `CAIRN_LANES` when that names a count from 1 to 1024; anything else traps. A region below 16384 elements (`lanes::CUTOFF`) is the loop it replaces and never touches the pool. Above that it publishes a descriptor on its own stack, engages one lane per 8192 elements (`lanes::GRAIN`) up to the pool's size, and returns when every claimed chunk has run. The thread that starts a region is always one of its lanes and can finish it alone, so a blocked, busy or absent worker delays a region but cannot deadlock one, and regions started at once from several tasks each make progress. The pool is never destroyed: an exit handler stops and joins its workers, after which a region still runs correctly on the thread that starts it, so no static destroyed later finds it gone. Both numbers, and the wake and spin policy behind them, were measured on one machine; `evidence/v1_2/host_regions` records what was measured and what was not.

## Agent and proof paths

`agent/agent_tools.py` supplies typed source sites, the canonical read-only projection of the whole language, and sealed edit sessions whose effect ceiling spans the full effect vocabulary. `agent/sketches.py` binds named choices to host-owned ranges and contracts. `agent/teaching.py` selects short rule cards from lexical tokens. Splicing preserves everything outside the authorized range, and the complete linked module is rechecked, not the displayed packet alone.

`verify/linear_certificates.py` checks exact affine implications; `proofs/` proves that checker sound in Lean, checks the same seventeen certificates there, and proves the collector loop model in bounds and stable. `verify/scalar_semantics.py` and `verify/smt_bridge.py` compare values against a fixed reference source (scalars, floats, records, sums, fixed local arrays) over bounded loops, with concrete replay. `verify/verification.py` owns aggregate coverage and cannot mark a module checked because one function passed. [verification.md](verification.md) has the boundaries.

No agent, test generator or solver may rewrite the authority it is checked against. Effects do not specify functional behavior. Native libraries never import the agent tooling or Z3.

## Packaging

The wheel holds the compiler package, the runtime headers, the target support files, the `std` sources, the typed marker and the CLI metadata. Tests, benchmarks, proofs, evidence and historical specifications stay out of it. Ordinary compilation has no third-party Python dependency; Z3, Lean, CUDA and QEMU are optional local tools, and [testing.md](testing.md) says what their absence does to a gate.

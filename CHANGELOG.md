# Changelog

## Unreleased

### Language

- A call may leave out the extent parameters its views carry. `checksum(frame)` is `checksum(len(frame), frame)`: every `usize` parameter that a later view names as its extent is written in by the checker as `len` of the first view argument that names it, or `hi - lo` for a part, so the emitted C++ and the effect row are those of the call written out. A call passes all such extents or none (`E-ARITY`), and an `extern` takes every argument. The library, the examples and the docs use the short form at 56 call sites whose emitted C++ did not change. `len` of an inline `Array` is now an extent identity, so `f(len(a), a)` of an `Array[T, N]` is accepted too.
- The emitter leaves out a guard the checker proved cannot fail: an index below its extent, `usize` arithmetic that stays in range, a shift or a conversion that fits. The facts come from loop and lane binders, immutable `let`s, conditions and early exits (`compiler/facts.py`); the receipt counts the removed sites as `discharged_check_sites`.
- `reduce op parallel i in n yield e` folds integers on the host lane pool in blocks fixed by the count, and returns what the in-order fold returns; a checked `+` traps exactly when the in-order fold would. Floats are refused (`E-REDUCE-ORDER`).
- A lane may own a block of what lanes write: `out[b * S + j]` with `j` below one constant `S`, an index a loop or condition keeps in `[b * S, b * S + S)`, or a part of that block lent to a helper that writes it. Blocks of one stride never meet, so the race rule stays whole, and an access it cannot place is still `E-PARALLEL-RACE`. A host lane may now call a function that writes through what the lane lends it. The pool sizes its claims by the block, and the facts scale `b < k` to `b * 256 + 256 <= k * 256`, so a per-block histogram's inner loop carries no guard.
- An I/O ring, `let mut q = IoRing(n);`, keeps up to `n` kernel operations in flight from one thread over io_uring. `q.read`, `q.write`, `q.recv`, `q.send` and `q.accept` move the `Buf[u8]` they work on into the ring, and `q.next(tag, result)` hands the next finished one back with its tag and the kernel's result; `std.io.outcome(result)` reads that as a `Result`. A ring records no lease, so it may be lent `rw` to a callee or a task; it is linear, pinned (`E-PINNED`), host only, and `wait(q)` lets every operation finish before it releases anything.
- A ticket or a task group can no longer be a parameter in any mode (`E-PINNED`). 1.3 accepted a group lent `rw` to a callee that spawned into it, and the task's lease ended at the callee's return while the task still wrote.
- A task group keeps every lease any path, or any earlier iteration of a loop, lent it, and a part orders other parts only where every path formed it. This closes three races 1.3 accepted.

### Projects and tools

- An edit packet starts with the target, its effect row and ceiling, and the interfaces of what it calls and what calls it, and grows by `expand` requests the host answers from the pinned program. A host names sessions by short handles (`cairn.edit/2`); `cairn.edit/1` still works. `--scope component` gives the 1.3 packet.
- `cairn explain` shows where each function pays at run time, at its `.cairn` lines: the guards the emitted C++ still checks and the ones the checker discharged, allocations, calls that allocate, spawn, join, lock or do I/O, waits, and clang's verdict on every loop. An agent can ask for it after an edit.
- Signature help in the language server offers the form of a call that leaves its extents out.
- Device code runs only under `make gpu`, in one process, behind a machine-wide lock (`tools/support.py`). The everyday suite never touches the GPU: on the reference machine every run of the device tests reset the display GPU, and repeated resets crashed the host.

### Verification

- Each fault witness in the ownership regression is one decided search over the machine, and each collector invariant step reads its certified obligation through one tactic.
- Tests build emitted C++ through one helper (`tests/emitted.py`) and require refusals through another, which removed 320 lines of repeated scaffolding.

### Documentation

- The agent chapter, the README, the roadmap and the verification chapter were rewritten in plain sentences that state each claim once.

## 1.3.0

### Language

- Task groups await tasks in the order they finish. `let g = Group[T](n);` declares a linear, in-place group of at most `n` tasks in flight and takes its whole storage there (`alloc`, `free`); `spawn f(args) into g;` runs a declared function on its own thread and hands the task to the group, leasing what it borrows to the group until `wait(g)`; `collect(g)` yields the next result to finish and returns no lease; `wait(g)` joins the rest, drops what nobody collected and consumes the group. A full or empty group traps. A group that is stored, passed or returned is `E-PINNED`, one not waited on every path is `E-LINEAR-LEAK` or `E-LINEAR-BRANCH`, a place lent `rw` to a group inside a loop is `E-LEASED`, and a linear result type is `E-LINEAR-STORAGE`.
- A lease names the place that was lent, so two tasks may take two fields of one record: `spawn f(box.a)` and `spawn g(box.b)` run together, and `len(box.a)` still reads while a task holds that field's elements. The same field to two tasks, a field read while the record is lent whole, and a new value landing in a lent field's cell are `E-LEASED` with the narrower subject.
- A `Buf` field may declare an earlier `usize` field of its record as its extent (`struct Chart { rows:usize; price:Buf[f64][rows]; }`). The identity is established by an inline `Buf[T](n)` at construction and held by `E-EXTENT-FIELD` at every place that could break it, so the field goes to a call whole and pays no part guard; `E-EXTENT` names a wrong extent field.
- A recipe may declare a field extent in a record it generates (`$f:Buf[$t][rows]`), held by the same `E-EXTENT` and `E-EXTENT-FIELD` rules as a written record, so a generated column goes to a call whole; `examples/apps/analytics` passes its columns that way.
- `std.text` gains `parse_i64`, `write_i64`, `push_i64`, `starts_with`, `ends_with` and `find_last_byte`; `std.io` gains `print_i64` and `read_stdin`; `std.vec` gains `insert`, `remove`, `swap_remove` and `find`; `std.map` gains `contains`.
- `free` is charged where the release runs, not beside `alloc`: the end of a block or match arm that still holds an owner, a `return` that leaves while one is held, a by-value parameter passed on to nobody, and the place a new value is assigned over. A function that only drops an owner carries `free` alone, and the operand-order audit keys on `alloc` alone.

### Projects and tools

- `cairn verify --all` takes `--assume symbol=expression`, one precondition per function, recorded in the receipt and named in the module's domain; a trip count over a symbolic extent stays unknown until one bounds it.
- Every subcommand of `cairn` says what it does in `--help`.
- `bench/suite/` is the preregistered CPU baseline suite (`make bench`): eight kernels against plain C++, OpenMP and oneTBB, each baseline built once guarded and once not, with safety boundaries counted from the build receipt and losses printed beside wins.
- `tools/release/collect_lean_evidence.py` rebuilds `proofs/` from scratch and records the build, the axiom audit and the toolchain for a release.
- The package declares its license and repository, CI runs on every push and pull request with a separate proofs job, `make lint` type-checks the whole package, `make help` lists the gates, and a test pins the one version every file states.

### Runtime

- CUDA 13: the device runtime builds under CCCL 3, whose deleted `cub` iterators are replaced by `thrust::counting_iterator` and `thrust::transform_iterator`, and a device program's host pass compiles with `-fexceptions` because CCCL 3 cannot parse without it. Nothing throws and guards still abort.

### Verification

- The SMT model admits any tag in storage behind a view, and any tag nested inside a value parameter, as the emitter does; only the top-level tag of a value parameter is guarded at entry, as the emitter guards it. A `match` over a tag that names no variant aborts, as the emitted `default: cr::trap()` does. A tagged view is no longer refused, and an out-of-range tag replays as a counterexample.
- Lean: reaching a field reads that field's header, so the calculus accepts two fields of one record to two tasks and rejects the same field twice (`sameFieldToTwoTasks_races`). A single element, a part of a part and a part with an invisible bound are conservatively the elements, with a case table beside `Place` and regression programs cross-checked against the checker.
- `tools/checks/differential_ownership.py` renders generated programs of one shared fragment as CAIRN source and as Lean `Program` literals and requires `checking.py` and the Lean `accepts` to classify them identically; twenty thousand programs agree, and `make lean` runs two hundred.
- The ownership development is one module per subject under `proofs/Cairn/Ownership/`, and every proof was revisited for a shorter argument.
- The SMT model follows an owner that moves: `take` moves a local owner's storage out and leaves an empty owner behind, `swap` exchanges two, an owner passed by value is storage of any length, and an owner that is returned is observed by its length and its elements. An owner inside a record, a sum or an array stays unknown, named as such; `examples/proof_scope/mixed.cairn` keeps a function taking a callable as the negative coverage fixture.
- The SMT model follows two views of one array through one call: a part is a window into its base storage, a write through either view lands in the base, and the callee's `cr::disjoint` guard is modeled as emitted, so the visibly disjoint `b[0..mid]`, `b[mid..n]` split and read-only aliasing are compared rather than reported unknown.

### Documentation

- The documentation is eleven files under `docs/`: a guide that goes from a fresh checkout to a running project and then through twelve programs, a language reference in three files, the library, the tools and targets, the examples, verification, internals, the agent protocol and the roadmap. Every example compiles, every tour program runs, and no file in the repository is longer than 800 lines.

### Reviews and users

- The whole tree is green on x86-64 with g++ 13, clang 21 and an RTX 5070 Ti under CUDA 13.2; every earlier record was AArch64.

## 1.2.0

### Language

- A generic parameter may promise a kind (`[T:copy]`, `[T:affine]`) or a scalar class (`integer unsigned signed float numeric scalar`) as well as traits, on functions and on generic records. A bound is checked at the call (`E-BOUND`), so misuse is reported in the caller's terms.
- `T(x)` converts or constructs at the instance's `T`. A natural parameter is inferred from the extent it names (`fn say[N:nat](text:ro<u8>[N])` called as `say("ready")`). A constant natural may be a type argument (`Array[u64, N]`, `scale[N](x)`).
- A `const` folds arithmetic, comparison, logic and conversions over literals and other constants, in any order of declaration, and may name a static extent. An `f32` constant rounds every literal, conversion and operation as the machine will.
- `let Conn(sock, sent) = c;` takes a record apart: it consumes the value and binds every field. An owner or a `linear` value kept inside a record now has a way out. Only the declaring module takes a `linear` record apart.
- A trait member may declare a ceiling (`fn less(a:ro<Self>, b:ro<Self>) -> bool pure;`). Every implementation is held to it, and that is what a bound on the trait promises. `std.core` declares `less`, `same` and `hash` pure.
- A generic impl applies exactly where its bounds hold, so `std.core` implements its traits once per scalar class (`impl[T:integer] Ord for T`). One Self type has one `impl` block. A generic impl whose bound asks the question it answers is `E-TRAIT-OVERLAP`.
- Recipes may generate trait `impl`s and `kernel fn`s, and take the name of a function (`recipe fieldwise[F:fn] for R`, `derive fieldwise[half] for P;`). `fold` takes an operator or any function of two operands, qualified or not. A `where` may fold statically, with `min` and `max`. An `each` among arguments may splice several expressions per step. Expansion is hygienic: a name the recipe writes means what it means in the recipe's module. `std.derived` offers `derive eq`, `derive ord` and `derive hash`.
- A `@pinned` or `@unified` view is lent where a `@host` view is asked for, and a `@unified` one where a `@device` view is, so host helpers serve staging buffers. `len("text")` is the literal's length and identifies an extent. The placement words `host device pinned unified` are words only after `@`.
- An observable call (I/O, the machine, atomics and locks, a function value) may not sit beside another call, nor beside an operand whose guard may abort, because C++ leaves operand order open.
- A part's bounds and extent are names, literals and arithmetic; a call is bound to a name first (`E-CALL-SHAPE`). A part written outside an argument list is `E-VIEW-ALIAS`.
- A `linear` value cannot be boxed into `Dyn[Trait]`, whose box would drop it unconsumed (`E-LINEAR-STORAGE`).

### Projects and tools

- `[dependencies] name = "deps/name"` vendors a project inside the root: modules only, `pub` only, loaded first, pinned by hash in the receipt. Nothing is fetched and nothing outside the root is read. Every manifest of a build goes through one checker. A module belongs to one project, `std.*` belongs to the packaged library, and the entry point is searched only in the root project.
- `cairn check --generics` checks each generic function once against its bounds, bodies and whole-program rules alike, and exits 1 unless all certify. Every template of `std` certifies. A program that does not check leaves its templates `unknown`.
- `cairn doc` generates an API reference from the checked program; `docs/guide/std_api.md` is its output for `std`, kept current by a test. `cairn expand` prints what every `derive` generated, as CAIRN source. A diagnostic inside generated code names its derivation.
- A manifest is named by its path, so one tree may hold a second configuration (`cairn run app/gpu.toml`).
- `cairn build --incremental` verifies a cached object against a stored digest before reusing it, writes objects atomically, refuses a symlinked cache, and records a timed-out unit like any other build.
- `cairn lsp` answers completion, signature help, references, rename and definition into `std`, from the last analysis that compiled.
- `cairn fmt` lays out `each { }` splices and `fold` as brackets.

### Runtime

- Host `parallel` regions run on a persistent pool of lanes instead of creating threads per statement; a region below sixteen thousand elements is compiled as the loop it replaces. On the GH200 the size at which a region beats the loop fell from ten million cheap elements to a hundred thousand (`evidence/v1_2/host_regions/`). `CAIRN_LANES` sets the lane count.
- A host `reduce` reads its extent once.

### Verification

- Lean: the ownership calculus now has the places the language has (whole owners, `len`, elements, parts with symbolic bounds and the chain that licenses a K-way split), a defined trap for a backwards part, and a proof of progress. It also covers record field paths and `parallel` regions: no race between two lanes, between a lane and a live task, or between two tasks, for every valuation of the bounds and of the lane count. Rejected witnesses drive the machine into each race.
- The emitter property that proof assumes, that a lent part is guarded on the spawning thread before its task exists, is pinned by a test.
- SMT source equivalence now models array views and their parts, `rw` borrows, function-local heap storage, `compact` and host `reduce`, and observes the final contents of everything a call was lent.

### Reviews and users

- A fifth adversarial review found an out-of-bounds read and write (a part's extent expression was emitted twice, so the guard and the callee could see different values), a `linear` value dropped through `Dyn`, a false `ok` from certification, two merged `impl` blocks, a silent coherence cycle, a checker crash on `impl NotATrait for S`, double-precision folding of `f32` constants, recipe name capture, and nine defects in the dependency loader and the build cache. All are fixed and pinned in the rejection tables.
- A second application, `examples/apps/analytics` (a columnar engine whose table types are generated by its own recipes, with a device path that must equal the host bit for bit), found one checker crash and a dozen rough edges; all are fixed. It was then rewritten against the fixes: 791 lines, no workaround, every template certified.

### Repository

- The tree is organized by concern: `src/cairn/{compiler,projects,verify,agent,editor}`, `tests/{language,soundness,verification,projects,runtime,tooling,agent}`, `tools/{checks,ai,release}`, `bench/{cpu,gpu,host_regions}`, `docs/{guide,internals,project}`.
- The documentation was rewritten for readers, and every CAIRN example in it is checked by the test suite (`cairn`, `cairn rejects E-CODE`, `cairn fragment`).
- `docs/guide/tour.md`: twelve complete programs that the suite builds and runs under sanitizers.

## 1.1.0

- Recipes: generators are library code. `recipe name[K:nat] for R { ... }` holds ordinary function and record declarations with static `each` (over a record's fields or a natural range, at declaration, statement, field-list and call-argument level), `fold`, `where` values, `$name` splices and `require` domains. `derive name[naturals] for Type;` expands it before checking into code of the deriving module. The closed Python generator behind `derive wire` is gone: `std.wire` is twelve lines of CAIRN and produces the same C++ byte for byte. Receipts pin each recipe by the hash of its tokens. `wire` is no longer a reserved word.
- Queued device work: `let t = spawn transfer(dst, src);` and `let k = spawn parallel i in n after t { ... };` put device work on its own stream and return. The ticket leases what the work touches until `wait`, `after` orders work by device events without a host wait, and work queued after a ticket may share what that ticket holds.
- Incremental builds: `cairn build --incremental` compiles one object per module against a shared interface header and reuses an object only when everything that went into it hashes the same. A body-only edit recompiles one module. It is opt-in because it gives up inlining across modules. Device programs and images stay one unit.
- Checked reduction: `reduce +` is offered on unsigned integers, on the host and on the device, and traps exactly when the total does not fit, in any order.
- Evidence: a preregistered fresh-model pilot (`evidence/v1_1/ai_pilot`). Nine of nine tasks were solved from the rule cards alone, eight on the first compile, and every transcript was audited. The cards were then revised with what the subjects had to guess.
- Source equivalence: the SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32` and `f64` under the compiler's strict floating contract, fixed local storage with its bounds guard, and loops with `break` and `continue` unrolled within a sixteen-iteration budget. A value is compared component by component, a sum by its tag and active payload only. Exceeding the budget is an obligation the solver must refute, and a returned NaN is reported unknown.
- Proof: a core ownership and lease calculus in Lean (`proofs/Cairn/Ownership.lean`), with safety including race freedom and with witnesses that rejected programs really fault.
- Tasks: leases are path sensitive. A `wait` on a path that returns no longer ends the lease on the path that goes on, a race three earlier reviews had missed.
- A fourth adversarial review of the 1.1 features found seven defects in recipe expansion and the incremental build. All are fixed and pinned.

## 1.0.0

The language grew from a checked CPU kernel language into a general systems language. The compiler core was rebuilt around one typed tree, and every addition arrived with native behavior tests, rejection tests and an application.

- Types: generic records, sums and functions with inference and on-demand monomorphization; payloads and fields of any value type; inline `Array[T, N]`; `i8` and `i16`; hex, character and string literals; `const`; layout attributes.
- Traits: static dispatch on `Self`, bounds checked per instance, `value.method(...)` resolved in the receiver's module, explicit `ro<dyn Trait>` fat references with a `dispatch` effect, owned `Dyn[Trait]` values for heterogeneous collections.
- Ownership: second-class borrows of values and arrays, checked array parts with visibly disjoint splitting, zeroed `Buf[T]`, affine moves, `take` and `swap`, `linear struct`, `defer`. There are no lifetime annotations.
- Errors: `try` propagates the failure of a two-variant sum. It is the only propagation form.
- Functions: copyable code pointers, and non-escaping closures passed as `ro<fn(...)>`.
- Conversions: float to integer is a checked truncation that traps on NaN or out of range.
- Effects: checked `pure` and `effects(...)` ceilings; `extern` declarations with mandatory effects, `ffi:symbol` propagation and an `unsafe` gate; `mmio_read`, `mmio_write`, `asm`.
- Concurrency: `spawn` and `wait` with linear scope-bound tickets and borrow leases; `Atomic[T]` with explicit memory orders; `Mutex[T]` entered only through a closure. Clean under ThreadSanitizer.
- Data parallelism: `parallel i in n` lanes that are race free by construction, `kernel fn` device helpers, `reduce`, placement types (`@host @pinned @unified @device`), scoped device owners, `transfer`, and the bounded collector as stable stream compaction on the device. One lane body lowers to host threads or CUDA lanes. Guards work in device code and abort the host.
- Modules: `module`, `import` with aliases and name lists, `pub`, and a standard library written in CAIRN and linked from the package.
- Targets: Linux x86-64 and AArch64 hosts from one toolchain table, CUDA through `nvcc` under the same strict floating-point contract, and a freestanding AArch64 profile verified under QEMU.
- Proof: a dependency-free Lean 4 development proves the certificate checker sound, checks the seventeen collector certificates, and proves in-bounds stores and stable selection for the collector loop model. Receipts report it only for the exact checked bundle.
- Agent layer: the canonical projection, edit sessions, effect ceilings and rule cards cover the whole language. An edit cannot add a lane race, a shared write, an allocation or a task past its ceiling.
- Tooling: `cairn fmt` (comment preserving), `cairn lsp`, an editor grammar, optional `#line` source maps, `ruff` formatting and lint for the Python sources.
- Reviews: three adversarial rounds. The first found fourteen accepted but unsound programs (use after free, overflow through extent identity, three lease races, forged linear values, under-reported effects, operand-order holes). The second, with three reviewers, found about forty more, among them closures that freed or raced what their own call lent, lanes whose bodies escaped the rule their callees obey, impls never held to their trait, `try` abandoning an owner mid-expression, privacy holes in `family` and `derive wire`, and a `.gitignore` pattern that had hidden `std/core.cairn` from every fresh checkout. The third attacked only the new rules and found nine more. All are fixed and pinned in the rejection tables, and the fixes made the rules more precise: a closure borrows exactly what it captures, a parallel map may take a closure that writes nothing it captured, K-way part splits are accepted. The first library author's seven compiler bugs are pinned too.
- Compatibility: every 0.6 program, diagnostic code and receipt field is preserved. Newly reserved words: `trait impl dyn const pub linear parallel reduce spawn try as type` (and the placement words, until 1.2). New builtin names (`take swap transfer wait mmio_read mmio_write asm`) yield to a program's own function of the same name. Two 0.6 rejections became legal by design: record payloads in sums and arrays of sums.

## 0.6.0

- Added zeroed lexical heap buffers and fixed stack arrays with nonescaping borrows, explicit storage/effects, and metadata-only len.
- Added monomorphic scalar payload sums, typed error data, exhaustive match, and correct loop break/continue through match arms.
- Added exact integer coefficient certificates for 17 collector arithmetic obligations; trusted Python checker, not Lean.
- Added all-function scalar equivalence coverage and public-type census; unsupported entries block aggregate verification.
- Replaced substring feature selection with lexical cards and shortened common instructions without altering edit authority.
- Fixed finite-task acceptance after abnormal child exit; added address-space limits to CLI execution.
- Added independent decimal/sorting/filtering examples, lifetime observer tests, production sanitizer and abort tests.
- Retained local-only publication policy and explicit gaps in C++ breadth, native proof, GPU support and measured AI proficiency.

## 0.5.0

- Split native syntax, typed expansion, checking, C++ emission and runtime into an installable src/cairn package; retained one implementation behind the compatibility facade.
- Added one CLI for creating, checking, building, running, testing, inspecting and scalar-comparing programs.
- Added explicit ordered multi-file projects, source-origin diagnostics, data-only manifests and native executable entry points.
- Added expression-bodied functions, optional host placement in the CPU profile, and else-if desugaring. Existing arithmetic and safety rules are unchanged.
- Preserved expression/body edit support and source comments for the compact forms.
- Changed the new CLI's default architecture to baseline x86-64; legacy comparison harnesses keep their explicit x86-64-v3 profile.
- Added offline wheel packaging, clean-install tests, fresh build directories, input-path rejection tests, and a private-only first-publication tool.
- Corrected source-audit density accounting to include the complete split package, not merely its small facade.
- Organized earlier specifications and teaching data separately from active compiler code. No model training, new Lean verification, GPU lowering or timing improvement is claimed.

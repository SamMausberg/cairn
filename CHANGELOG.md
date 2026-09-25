# Changelog

## 1.1.0

The release after 1.0.0. Its records are under `evidence/v1_1/` ([index](evidence/v1_1/README.md)), the release gates and what did not run are in `evidence/v1_1/RUN_NOTES.md`, and what is left is the [roadmap](https://github.com/users/SamMausberg/projects/2).

### Writing CAIRN

- A call that writes or allocates may be the only operand of a conversion or unary operator: `let k = usize(next(inp));` is accepted, and a call beside another operand is still `E-EFFECT-ORDER`, whose message now names the call, what it writes and the `let` that binds it.
- A minus on an integer literal is one constant, so a signed type's minimum is a literal (`-9223372036854775808`), and a negated literal carries no overflow guard. 56 programs emit fewer guards, among them `std.text`, `std.zlib` and `std.draw`; none emits more.
- Parts whose bounds are literals and constants are leased as the numbers they fold to, so `p[0..BINS]` and `p[BINS..2 * BINS]` can go to two tasks.
- The refusals first programs meet most often state the fix the compiler knows: the part `b[0..n]` of a `Buf`, the annotation of a `u64` literal, an import that hides `println`, `v.len` of a `Vec`, where a part goes, and what `if`, `match`, `as` and `::` are in CAIRN.
- One check reports every independent refusal: the first exactly as before, the rest in `further`, and `not_judged` counts functions a refusal left without a verdict. A check that stops early on a limit or an internal fault says so in `further_stopped`.
- Every refusal names the rule card that states its rule and, where the compiler can state it, the smallest fix; `cairn rules` prints a card offline.
- `docs/guide.md` and the skill open with a complete program that reads input, a table of the refusals a first program meets and a table of the library calls it uses.

### Kernels

- `load_wide[K](x, i)` and `store_wide` move up to 16 bytes of adjacent elements in one access, with a cache hint named from `Cache` (`E-WIDE`), in host code, lanes and cooperative threads.
- Lanes and cooperative threads update an element atomically (`atomic_add_wrap`, `atomic_min`, `atomic_max`, `atomic_cas`, `atomic_and`, `atomic_or`, `atomic_xor`, `atomic_add_unordered`), never beside a plain access of the same array in one region (`E-ATOMIC-MIXED`).
- A cooperative region may end with a finish that runs once, in one block, after every block, and stays one launch on the device; its block counter is a word of a table in the module's global memory, so it allocates nothing.
- A shared array declared with no initializer is not zeroed, when every element a thread reads was written first (`E-COOP-UNWRITTEN`).
- Warps vote with `warp_ballot`, `warp_any`, `warp_all` and `warp_match`, and `shuffle_up` joins the shuffles.
- The one-writer rule for arrays from outside a cooperative region accepts a write whose conditions pin each block or thread name to one value, such as `if t == 255` or `if tx == 31 && ty == 7`, not only thread 0.
- `reduce op out[k] for i in n yield e;` writes a reduction's total into one element; over `@device` views the total stays in device memory for the next region, with no copy and no wait of its own.
- `examples/reduction` sums f32 in one launch with wide streaming loads, an unzeroed shared array and a finish, with nothing `unsafe`.

### Device execution and performance

- A device function whose effects the host cannot observe waits once when it returns, and a library header adds `cq_NAME(stream, ...)`, which queues it on the caller's stream without waiting and can be captured in a CUDA graph (`E-ENQUEUE` names what keeps a function from it).
- A run of device work waits once, before the next thing the host observes, instead of after each region; a copy to host memory, a release of device memory and a reduction's total wait for held work where they stand. I/O, foreign calls and `@unified` memory keep the earlier behaviour. On the counting host stand-in, a function that copies in, runs four regions around a total and copies back waits five times a call where it waited seven.
- On an RTX 5070 Ti, a one-launch sum of 2^26 f32 written in safe CAIRN ran through `cq_NAME` in 344 and 340 us of GPU time against 342 and 344 us for a hand-written one-pass CUDA kernel, where the owner's earlier CAIRN version took 656 to 679 us. Layer norm, a cooperative stencil and scalar-load reductions remain 11 to 26 percent slower (`evidence/v1_1/device_perf`).
- A device program includes CUB only when it has a device reduce, scan or compact; on the reference machine nvcc builds the others 2.5 to 4 times faster.
- A checked multiply in a device lane tests the product's high half instead of dividing, and typed PTX written in a region's body launches.
- `--emulate` on `build`, `run`, `test` and `validate` judges a device program against its device target and runs its device work on host threads; what the host cannot run as a device would is `E-EMULATE`, and an emulated validation is `finite-tested-emulated`, which `cairn tune` uses only with `--accept-emulated`. A cooperative region on host threads meets its warp's barrier once an exchange, so the emulated reduction runs in 16 s where it took 34 to 45 s.
- `cairn predict` and `cairn tune` price device work on eight packaged cards (A100, H100, H200, B200, L40S, RTX 4090, RTX 5090, RTX 5070 Ti) from NVIDIA's published figures, with `--card NAME`, `--card all` and `cairn cards`. No card has been measured.
- `make gpu` runs every test that runs device code, and a deliberate device trap only when `CAIRN_GPU_TRAPS=1` is also set.
- The device features added after 0.8.3 ran on a GPU for the first time, an RTX 5070 Ti, in the release's one `make gpu` session: 48 of the suite's 52 device-run cases passed, the three that trap on purpose were left out, and Compute Sanitizer cannot instrument that GPU under WSL2, so device validation reports its tools `unavailable` rather than clean or failed. The session found that a `cq_` entry of a function with a finish could not be captured in a CUDA graph, since its counter's claim asked CUDA for the stream's id, which a capture refuses; it names a capturing stream by its handle now, and the host stand-in refuses the id during a capture as CUDA does. `examples/reduction`'s `sum` through `cq_sum` ran in 330.7 us against 329.1 us for the hand-written one-pass kernel at 2^26 f32 (`evidence/v1_1/gpu`).

### Tools

- An edit of one function's body in the edit hosts, `cairn mcp` and `cairn lsp` is checked from the walk the last check kept, with every whole-program rule run again; a differential test holds it to a whole check on every body of every example. An admitted edit lexes only the lines it touches and parses only the edited body. On a generated project of 185 modules such a check takes about a quarter of a whole check. `cairn check` stays whole, and emission is still whole.
- `cairn check` of a cooperative kernel with tensor-core fragments takes less than half as long, `cairn validate` builds its two libraries at once, and `cairn predict --inspect` checks a program once.
- `cairn run --sanitize address|thread` builds and runs the program under the sanitizer, and `cairn build --sanitize` builds it. `cairn build` prints a record of about 2 KB; the full record, with the checker's receipt of every function, stays in `receipt.json`, which the record names.
- `cairn run` and `cairn test` cap a program's data rather than its address space, with an 8 MiB stack per thread, so a correct program of many threads is not stopped by its threads' reservations.
- `cairn doc --std --module M` prints only the modules it names.
- `cairn export`'s run and test of a device export hold the machine-wide device lock and run its test blocks one at a time.
- `cairn tune` chooses an implementation only on a validation at least as strict as the reference's pinned policy, and refuses a candidate whose implementation needs a device feature the target lacks (`E-IMPL-TARGET`), as `cairn export` now does too. Validation records written before 1.1.0 carry no policy, so `cairn tune` ignores them until the implementation is validated again.
- `cairn tune` ranks candidates by one objective over several sizes, generates them lazily, compiles each distinct kernel once and holds its time budget.
- `cairn export --harness sol-execbench|gpumode|kernelbench` writes a function as a submission for that benchmark, bound to PyTorch's current stream through `cq_NAME`, and `cairn new --from-sol-execbench` starts a project from a problem. Nothing is submitted or run on a GPU by these commands.
- The hosts, `cairn state`, `cairn mcp` and the language server share one compile per distinct source. The language server reads nested generic instances and colors and completes a document holding a test block.
- `cairn tune --write` and every session write files with CRLF endings or a byte-order mark in their own form.

### Validation

- Host validation, regression replay, the implementation session and the generated device tests compare floats under one versioned numerical policy, and a Z3 counterexample is replayed natively and fails the validation it breaks.

### Agents

- The 1.1 evaluation of CAIRN with the plugin, CAIRN with its documentation, C++ and Rust stopped early, at 54 of its 156 subjects: every subject solved its task, and per solved task the plugin arm used 5.5 times C++'s tokens and the documentation arm 8.2 times (`evidence/v1_1/ai_eval`, partial). `evidence/v1_1/friction` says where those tokens went: every CAIRN subject's first program that type-checked was correct, and most tokens went to reading the documentation before writing, to refusals and to finding the sanitized build. Replayed on the 69 programs those subjects checked, 1.1.0 refuses 16 where the starting commit refused 30, each naming the change it wants. No model has been run since, so nothing here shows that agents now spend less.

### Repository

- The compiler's modules sit in eight subpackages (`syntax`, `derive`, `check`, `primitives`, `plans`, `cooperative`, `device`, `lower`), and `verify/scalar`, `verify/validation`, `projects/harness`, `editor/lsp`, `agent/hosts`, `agent/mcp` and `perf/tuning` are subpackages too. Emitted C++ is byte-identical for every program the emission-identity check covers. Semantic receipts pin every file under `compiler/` by glob, so each receipt's `implementation_sha256` changed once.
- Test, tool and bench code that was repeated lives in one helper each. `make audit` reads any length of history, admits compressed JSON Lines transcripts under `evidence/` only as the text they decompress to, and fails the suite on any finding.
- CI compiles device code under CUDA 12.9 and 13.2 with both host compilers, tests the oldest and newest supported compilers, every Python from 3.11, an AArch64 host with the freestanding image under QEMU and the installed package, and reports one `ci-passed` check. A pull request runs ten of those jobs; `main` and the weekly run run all eighteen.
- Every change reaches `main` through a pull request that fills in the template and merges when CI passes. Issues are filed through forms, labels are data in `.github/labels.yml`, and `docs/verification.md` has a capability matrix generated from data.
- An adversarial review of everything from 1.0.0 to the 1.1 development version fixed eight defects (`evidence/v1_1/review`); the items it left open are fixed.

## 1.0.0

The first release. The sections below it are the internal milestones that came before it, 0.5.0 to 0.8.3.

### Implementations and tuning

- A function can have alternative implementations beside its reference: `fn g(...) implements f when COND needs(FEATURE) { }`, selected by `plan f use g;`, with the reference running wherever the condition does not hold. An implementation keeps the reference's signature, effect ceiling and numerical contract, and its condition cannot trap (`E-IMPLEMENTS`, `E-IMPL-SIGNATURE`, `E-IMPL-WHEN`, `E-IMPL-EFFECT`, `E-IMPL-NUMERICS`, `E-IMPL-CALL`, `E-IMPL-USE`, `E-IMPL-TARGET`).
- An implementation may take natural parameters, `fn g[K:nat](...) implements f ... tune K in [4, 8]`; each listed value is a checked instance and `plan f use g[8];` selects one (`E-IMPL-PARAM`).
- `cairn validate` tests an implementation against its reference on boundary inputs generated from its contract (tile edges, partial tiles, misaligned views, type edges), each call in its own process. A failing case is shrunk and kept as a regression that `cairn test` replays. It is finite testing, never proof, and SMT equivalence is reported apart where Z3 decides it.
- An implementation session (`cairn.implementation/1`) admits an agent's implementation only when it validates, and refuses any change to the reference, a tolerance, the test policy or the input domain (`E-REFERENCE`, `E-TOLERANCE`, `E-TEST-POLICY`, `E-DOMAIN`, `E-VALIDATION`).
- `cairn tune` searches every plan and every validated implementation the checker accepts within `--budget-compiles`, `--budget-seconds` and `--budget-runs`, compiles each device candidate for its resources, keeps a candidate history in `.cairn/history`, and chooses only candidates whose validation still holds. `--compare` labels each line of a difference as compiler observation, measurement, profiler observation, hypothesis or suggested experiment, and `cairn state --symbol f` lets a fresh agent resume one function's investigation without rerunning what ran (`evidence/v1_0/search`).
- A plan session opens on a function of any module of a project.
- `cairn predict` prices a cooperative region by its blocks, occupancy and the pipeline copies it keeps in flight, and `cairn tune` searches a region's block shape and pipeline depth as natural parameters; the shared memory the checker lays out equals ptxas's figure for every kernel read (`evidence/v1_0/predict_cooperative`).

### Device programming

- One device target, from `--device-target`, the manifest or the installed GPU, is shared by the native build, the device inspector, tune, predict, device timing and the build receipt; `-arch=native` is gone and a record made for another target is refused (`E-TARGET`, `E-TARGET-TOOLKIT`, `E-TARGET-FEATURE`, `E-TARGET-MISMATCH`).
- Generated device code runs on the calling thread's execution context: one stream and one scratch arena, reused, and a synchronous region waits for its own stream rather than the whole device. A C host can hand CAIRN its stream through `NAME_device_stream`. On a host stand-in that counts CUDA calls, a repeated pipeline makes no stream and no allocation after its first pass (`evidence/v1_0/execution`).
- Cooperative regions, `blocks b in G threads t in T { }`, run blocks of threads that share memory, meet at `barrier` and use warp shuffles and sums. A phase rule refuses conflicting accesses between barriers, an omitted barrier and early reuse of shared memory, and every element of an outside array has one writer (`E-COOP-*`). Host runs use real threads at a `std::barrier` and are clean under ThreadSanitizer with both compilers.
- Pipeline stages, `pipeline p:T[N] depth D;`, are resources with compile-time states; a read before the transfer lands or a refill while readers remain is refused (`E-STAGE-UNREADY`, `E-STAGE-BUSY`, `E-STAGE-LOOP`), and the device lowering is `cp.async` with a wait count set by the checker.
- Layouts are declarations the checker evaluates (rows, strides, padding, swizzles, tiles, transposes, spreads over threads), with checked coverage and consumers (`E-LAYOUT`, `E-LAYOUT-GAP`, `E-LAYOUT-OVERLAP`, `E-LAYOUT-CONSUMER`); a plan's `vector` and `stage` take their widths from them.
- Tensor-core fragments (WMMA and `mma.sync` shapes) load, multiply-accumulate and store at warp participation inside a cooperative region; Blackwell `tcgen05` is a separate capability refused on sm_120. Two different tensor-core multiplies are written in CAIRN with the signature and numerical contract of `mma_unordered`, checked on the host under both compilers (`examples/tensor`, `evidence/v1_0/tensor`).
- Typed inline assembly, `asm [volatile] ptx sm_NN | x86_64 | aarch64 "template" (outputs, inputs) clobbers(...) effects(...)`, derives constraints from operand types and runs lane-safe PTX in device lanes (`E-ASM-*`).
- Existing C++ and CUDA enter a project unchanged as foreign implementations of a CAIRN reference through the manifest's `[foreign]` table and `extern ... launch(threads, block)`; `cairn foreign` reports the declared contract, the build, the device inspection and the tests apart (`evidence/v1_0/foreign`).
- Everything in this section compiles for sm_120 and is checked on the host; none of it has run on a GPU.

### Agents

- `skills/cairn/` is an Agent Skill generated from the compiler's rule cards, fixes and commands, and the repository is a Claude Code plugin and marketplace (`claude plugin marketplace add SamMausberg/cairn`): the skill, `cairn` on `PATH`, `cairn lsp` for diagnostics after every edit, and `cairn mcp`. In a six-session smoke comparison, run before `cairn mcp` existed, sessions with the plugin cost 0.51 times as much as sessions without it and every session solved its task (`evidence/v1_0/skill`).
- `cairn mcp` serves check, state and the edit, plan and implementation hosts over MCP, and writes an admitted change back to its files only while they are unchanged and inside the served directory (`E-SESSION`).
- Every new project has an `AGENTS.md` and a `CLAUDE.md` that imports it.
- A rename follows `implements` and `plan f use g`, `cairn fmt` skips the `.cairn` history directory, and an implementation a session writes back stays formatted.

### Language

- A call may leave out the extent parameters its views carry: `checksum(frame)` is `checksum(len(frame), frame)`, with the same C++ and effect row (`E-ARITY`).
- The emitter leaves out a guard the checker proved cannot fail, each one re-derived by an independent audit, and `--keep-guards` writes them all.
- `reduce op parallel` folds integers on the lane pool and returns what the in-order fold returns; floats are refused (`E-REDUCE-ORDER`). `scan OP [exclusive] out for|parallel i in n yield e` writes every prefix.
- A lane may own a block of what lanes write (`out[b * S + j]`, `j < S`), and a host lane may call a function that writes what the lane lends it.
- Plans set how a region runs without changing its result: `grain`, `lanes`, `fuse K`, and on the device `block`, `per_lane`, `unroll`, `vector W` and `stage R` (`E-PLAN`).
- I/O rings, `let mut q = IoRing(n);`, keep up to `n` kernel operations in flight from one thread over io_uring, with owners moving in and out, timeouts and cancellation.
- Tickets and task groups cannot be parameters (`E-PINNED`), a group keeps every lease any path lent it, and an owner passed to a group's task moves into it.
- Shorter forms that emit exactly what they stand for: bare variants (`E-VARIANT-AMBIGUOUS`), one-statement match arms, `_`, compound assignment, call statements (`E-DISCARD`), element loops (`E-ELEMENT-LOOP`), and records that lend a view (`E-LENDS`).
- `test` blocks, `assert` and `assert_eq`; `print`, `println`, `eprint`, `eprintln` and `format` with shortest round-trip floats; `sqrt`, `floor`, `ceil`, `trunc`, `abs` and `to_bits`.
- Storage floats `f16`, `bf16`, `f8e4m3` and `f8e5m2` with one correctly rounded conversion, `quantize`, and `derive grad`, which writes a reverse-mode derivative as ordinary checked code. `mma_unordered` adds a storage-float matrix product into `f32` under a named error bound.

### Tools

- `cairn export` writes the exact program a build compiles, the headers it includes and a record with the command, target and a hash per file; `build`, `run` and `test` take an export and refuse a changed file or compiler (`E-EXPORT-TAMPERED`, `E-EXPORT-TOOLCHAIN`), and an export's record is data: its build runs only the toolchain's own command.
- `cairn predict` prices a function from its checked work and a machine profile, and `cairn explain` shows where each function pays at run time. `cairn diff OLD NEW` classifies every function as identical code, SMT-equivalent, changed with a witness, or unknown, with a semantic version.
- `cairn graph` and `bazel/` rules for other build systems; a program may hold 16 MB of source and 32,768 functions; incremental builds share a precompiled header and write `compile_commands.json` (`evidence/v1_0/scale`).
- `cairn build --header` writes a C header with every layout asserted on both sides; `cairn emit --ctypes` writes a Python binding.
- `cairn new --template cli|lib|service|parallel`, `cairn check --watch`, shell completions, `cairn shot` for headless frames, and a language server that analyses a whole project and renames across files.
- The standard library gains `std.fmt`, `std.fs`, `std.env`, `std.time`, `std.math`, `std.zlib`, `std.image`, `std.draw`, map handles that go stale, and a radix sort.

### Runtime

- A host region gives each lane a home range it claims from first, so a lane reruns the same indices region after region (`proofs/Cairn/Region.lean`).
- Task threads are parked and reused; I/O rings return every submission once with its errno; a function taking views has a checked C entry.

### Verification

- Lean models, each hand-written beside the rule it models: the cooperative phase rule (`Cooperative.lean`: no conflicting access in any interleaving, a result independent of thread order; the checker and the model agreed on every generated region compared), layout coverage (`Layout.lean`), guard elision (`Facts.lean`), the lane pool (`Region.lean`), and the ownership calculus extended to task groups and lane blocks. Every theorem depends on `propext` and `Quot.sound` only.
- An adversarial review of the implementation layer found 21 defects, among them an export that could run any command, validations that stayed current after a helper changed, and three holes in the phase rule; every one is fixed with its regression test, and 78 refused attacks are kept with their codes (`evidence/v1_0/review_implementation_layer`). Two earlier reviews of the tools found nine defects (`evidence/v1_0/review`).
- `bench/ai` is a preregistered equal-budget benchmark of CAIRN, C++ and Rust: every subject solved its task, and CAIRN subjects, who read the documentation inside the budget, used 11.6 times the tokens of C++ subjects (`evidence/v1_0/ai_benchmark`). It ran before the plugin existed.
- A reduction pass kept the C++, effect rows and refusal codes of 1,541 programs identical, and conservative and optimized builds agreed on 174,816 generated cases per compiler.

### Documentation and repository

- The documentation is fourteen files under `docs/`, device programming now a chapter of its own, each written to one style and checked by the suite: every example compiles, every refusal has its code, every documented code, command and option exists in the source, and every relative link resolves.
- `demos/` holds one-command demos of an agent repair, a CPU and GPU numeric workload, a visual app, and the implementation loop (`make demo-implement`): a refused tolerance, a refused candidate with its shrunk input, validated implementations and a timed `cairn tune`.
- Every tracked file is at most 800 lines; the parser, the checker's rule groups, the device layer and the agent tools are split by responsibility, and `AGENTS.md` names the owner of each file.
- CI runs lint, the suite in four shards and the proofs on every push.

## 0.8.3

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

## 0.8.2

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

- Host `parallel` regions run on a persistent pool of lanes instead of creating threads per statement; a region below sixteen thousand elements is compiled as the loop it replaces. On the GH200 the size at which a region beats the loop fell from ten million cheap elements to a hundred thousand (`evidence/v0_8_2/host_regions/`). `CAIRN_LANES` sets the lane count.
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

## 0.8.1

- Recipes: generators are library code. `recipe name[K:nat] for R { ... }` holds ordinary function and record declarations with static `each` (over a record's fields or a natural range, at declaration, statement, field-list and call-argument level), `fold`, `where` values, `$name` splices and `require` domains. `derive name[naturals] for Type;` expands it before checking into code of the deriving module. The closed Python generator behind `derive wire` is gone: `std.wire` is twelve lines of CAIRN and produces the same C++ byte for byte. Receipts pin each recipe by the hash of its tokens. `wire` is no longer a reserved word.
- Queued device work: `let t = spawn transfer(dst, src);` and `let k = spawn parallel i in n after t { ... };` put device work on its own stream and return. The ticket leases what the work touches until `wait`, `after` orders work by device events without a host wait, and work queued after a ticket may share what that ticket holds.
- Incremental builds: `cairn build --incremental` compiles one object per module against a shared interface header and reuses an object only when everything that went into it hashes the same. A body-only edit recompiles one module. It is opt-in because it gives up inlining across modules. Device programs and images stay one unit.
- Checked reduction: `reduce +` is offered on unsigned integers, on the host and on the device, and traps exactly when the total does not fit, in any order.
- Evidence: a preregistered fresh-model pilot (`evidence/v0_8_1/ai_pilot`). Nine of nine tasks were solved from the rule cards alone, eight on the first compile, and every transcript was audited. The cards were then revised with what the subjects had to guess.
- Source equivalence: the SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32` and `f64` under the compiler's strict floating contract, fixed local storage with its bounds guard, and loops with `break` and `continue` unrolled within a sixteen-iteration budget. A value is compared component by component, a sum by its tag and active payload only. Exceeding the budget is an obligation the solver must refute, and a returned NaN is reported unknown.
- Proof: a core ownership and lease calculus in Lean (`proofs/Cairn/Ownership.lean`), with safety including race freedom and with witnesses that rejected programs fault.
- Tasks: leases are path sensitive. A `wait` on a path that returns no longer ends the lease on the path that goes on, a race three earlier reviews had missed.
- A fourth adversarial review of the 0.8.1 features found seven defects in recipe expansion and the incremental build. All are fixed and pinned.

## 0.8.0

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
- Compatibility: every 0.6 program, diagnostic code and receipt field is preserved. Newly reserved words: `trait impl dyn const pub linear parallel reduce spawn try as type` (and the placement words, until 0.8.2). New builtin names (`take swap transfer wait mmio_read mmio_write asm`) yield to a program's own function of the same name. Two 0.6 rejections became legal by design: record payloads in sums and arrays of sums.

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

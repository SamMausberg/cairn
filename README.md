# CAIRN

CAIRN is a systems programming language. Its compiler emits guarded C++20 for CPUs, CUDA for GPUs from the same source, and a freestanding image for bare-metal AArch64. A program allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary only where its source says so, and every function carries an inferred effect row that shows those costs to its callers. Borrows exist only as parameters and arguments, so there are no lifetime annotations.

The language is built for edits made by AI agents. An agent proposes a function body or an expression, and the compiler checks it against a signature, an effect ceiling and a set of visible dependencies that the agent cannot change. The compiler itself is not proved correct. [What is established](#what-is-established) says which parts are proved, which are tested and which are only implemented.

## A first program

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(data[0..mid], 0);                  // a task leases what it borrows until wait
  let right = spawn fill(data[mid..n], u64(mid));
  wait(left);
  wait(right);
}

fn main() -> i32 {
  let mut data = Buf[u64](1000);
  halves(data);                                            // n is len(data): a call may leave extents out
  let total = reduce + for i in len(data) yield data[i];   // a checked sum: it traps if it overflows
  if total != 499500 { return 1; }
  return 0;
}
```

`rw<u64>[n]` is a mutable borrow of `n` elements, and `n` is part of the type. The two tasks may run at once because `data[0..mid]` and `data[mid..n]` visibly meet at `mid` without overlapping. Each part carries one bounds guard, and every index is checked.

A task leases what it borrows until `wait`. Touching the array in between is refused at compile time:

```cairn rejects E-LEASED
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn racy(n:usize, data:rw<u64>[n]) {
  let left = spawn fill(n, data, 0);
  data[0] = 7;
  wait(left);
}
```

```text
error[E-LEASED]: data is lent to left until wait(left).
  --> racy.cairn:5:3
  |
5 |   data[0] = 7;
  |   ^^^^
```

The effect row of `halves` is `spawn`, `join`, `write:data`, `trap` and `ffi_precondition` (the entry guard of its view parameter). A function can cap its row with `pure` or `effects(...)`, and every caller's row includes the rows of what it calls.

The same `parallel` body runs as CUDA lanes when its views are on the device and on a pool of host threads when they are not:

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
```

Whatever a lane writes, it may touch only at element `[i]` or inside its own block of one stride, so two lanes never race.

## What the language has

- Checked integer arithmetic, explicit conversions, wrapping forms that say so by name.
- Records, sums with exhaustive `match` whose variants go bare where the context names the sum, `try` for error propagation, compound assignment, call statements that may not drop an outcome unread, element loops (`for i, x in xs`), and records that lend their live elements as a view (`lends data[0..len]`).
- Owners that move and are released at scope exit, `linear` values that must be consumed exactly once, `take`, `swap`, `defer`.
- Generic types and functions with bounds on traits, kinds (`copy`, `affine`) and scalar classes; traits with static dispatch and explicit `dyn`.
- Closures that borrow what they capture and never escape.
- Tasks with leases, down to one field of a record; task groups collected in completion order; I/O rings that keep kernel operations in flight without a thread each; atomics; mutexes entered through a closure.
- `parallel`, `reduce`, `compact`, `scan`, plans that set how a function's regions run (grain, lanes, fusion, a device block's shape) without changing a result, placement types (`@host @pinned @unified @device`), kernels, transfers, queued device work.
- Storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) that convert by one stated rounding, `quantize` with saturation, and `derive grad`, which writes a reverse-mode derivative as ordinary checked code.
- Test blocks and `assert` in the language, each test run in a process of its own.
- Modules, a standard library written in CAIRN (collections, text, `fmt`, `fs`, `env`, `time`, `math`, sockets, `zlib`, images and 2D drawing), projects with vendored dependencies.
- Recipes: generators written as library code and applied with `derive`.
- `extern` with mandatory effects and an `unsafe` gate, MMIO, inline assembly, a freestanding target, and the other direction: a library's generated C header and ctypes binding, whose checked entries a foreign caller reaches.

## Install

You need Linux on x86-64 or AArch64, Python 3.11 or later, and Clang or GCC with C++20. Compiling has no third-party Python dependency. Optional local tools switch on further gates and are never downloaded: `libz3` (source equivalence), CUDA `nvcc` (device programs), Lean 4 (`proofs/`), `qemu-system-aarch64` (the freestanding target).

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'            # the cairn command, pytest, ruff and mypy
cairn doctor                       # what is installed, and which gates it enables
```

`bin/cairn` is the same command line without installing anything, so `python3 bin/cairn run examples/hello` works in a fresh checkout.

```sh
cairn new my_project && cairn run my_project
cairn new my_tool --template cli            # also lib, service and parallel
cairn run examples/hello
cairn run examples/systems                 # typed-error parser, stack sort, heap pipeline
cairn test examples/systems --cxx g++      # independent finite task contracts
cairn run examples/apps/kvstore            # a crash-safe storage engine
cairn run examples/apps/analytics          # a columnar engine whose table types are generated by recipes
cairn run examples/apps/wordfreq -- examples/apps/wordfreq/tale.txt   # a command-line word counter
cairn run examples/apps/classifier         # trains a network on derived gradients, then runs it quantized
cairn shot examples/apps/panel             # the frames a UI drew, as PNGs with their layout
cairn run examples/apps/gpu_pipeline       # needs CUDA
cairn run examples/embedded                # bare-metal AArch64 under QEMU
```

| Command | What passing means |
|---|---|
| `check`, `emit`, `expand` | The program is accepted: syntax, types, ownership, leases, lanes, placement, effects. `emit` prints the C++, `expand` prints what every `derive` generated. |
| `build`, `run` | A fresh native build (`--debug` maps symbols to the `.cairn` files, `--incremental` reuses objects by content hash), then execution with process limits, under QEMU for the freestanding target. `cairn run app -- ARGS` starts the program with `ARGS`. |
| `build --kind library --header`, `emit --ctypes` | A shared library with the C header of its checked entries, every layout asserted in the header and in the library, and a Python binding that asserts the same layouts when it is imported. |
| `test` | Every test block passes in a process of its own, and so does every independent finite task contract, with each child's exit status checked. |
| `verify --symbol f`, `--all` | Z3 found no input on which two versions of a function differ, within the modeled fragment. Anything outside it is reported unknown and blocks the aggregate result. |
| `diff OLD NEW` | Each function of two versions (paths or git revisions) is identical code, SMT-equivalent, changed with an input that shows it, or unknown with the reason, beside its exact effect, guard and signature changes and a semantic-version verdict; `--require equivalent` makes it a gate. |
| `certificates` | The seventeen arithmetic identities behind the collector check, here in Python and in `proofs/` in Lean. |
| `check --generics` | Every generic function needs only what its bounds promise. |
| `inspect --symbol f` | The packet an AI agent gets for an edit: its source, the interfaces around it, effects, rule cards; `--expand g` adds a body. |
| `explain`, `explain --symbol f` | Where each function pays at run time, read from the emitted C++ and clang's optimization record: guards per line, allocations, waits, loop vectorization. Nothing runs. |
| `predict`, `tune --symbol f` | What each function should cost on this machine, priced from its checked work and a calibrated profile, with what bounds it and how sure the model is; nothing is built. `tune` ranks every legal plan by prediction and times only the best few on the host. |
| `shot` | Runs a program headless and returns each frame `std.draw` captured as a PNG, its layout record and its time, beside the effect rows an edit changed. |
| `state`, `migrate` | The program as it stands under one digest, for an agent to resume from; one authorized signature change carried through every caller, every file or none. |
| `fmt`, `doc`, `lsp` | A comment-preserving formatter, an API reference generated from the checked program, a language server. |
| `new --template`, `check --watch`, `completions` | A starting project the suite builds and tests; the same check again each time a file changes; a bash or zsh completion script from the command line's own parser. |

`make lint test proof` are the everyday gates. `make gpu embedded` need the hardware and the emulator, and `make docs` regenerates the API reference, [docs/std_api.md](docs/std_api.md) and a page per module.

## Documentation

Start with the [guide](docs/guide.md), which takes a fresh checkout to a working project and then walks through twelve complete programs. [docs/](docs/README.md) indexes the reference and everything else. [AGENTS.md](AGENTS.md) has the rules for anyone, human or agent, changing this repository, and [CHANGELOG.md](CHANGELOG.md) is the release history.

## Repository

```
src/cairn/
  compiler/         syntax, modules, recipes and gradients, the checker and its rules, the emitter, the C header
  projects/         manifests and vendored dependencies, git revisions, native builds and their object cache, the toolchain table
  verify/           test blocks and task contracts, the guard-elision audit, SMT source equivalence, diffs, coverage, certificates
  agent/            projections, edit sessions and packets, evidence classes, state, migrations, plan edits, rule cards, sketches
  editor/           formatter, language server, grammars, API reference, terminal output, shell completions
  perf/             the performance model: counted work, machine profiles, calibration, predict and tune
  runtime/          guards, owners, threads, tasks, the lane pool, rings, storage floats, CUDA lanes (C++ headers)
  std/              the standard library, written in CAIRN
  templates/        the starting projects of cairn new --template
  targets/          start-up code and linker script of the freestanding board
proofs/             Lean 4: certificate checker, collector loop model, ownership and lease calculus
examples/           basics/ hello/ systems/ apps/ interop/ embedded/ and inputs for the agent and proof tools
tests/              language/ soundness/ verification/ projects/ runtime/ tooling/ agent/ oracles/ native/
tools/              checks/ ai/ release/
bench/              cpu/ gpu/ host_regions/ host_tasks/ scan/ suite/
editors/            VS Code and Cursor extension, Vim runtime files, both generated from the compiler vocabulary
docs/               the documentation, the generated std_api.md and std/, project data, history/
evidence/           executed results by release, and their limits
```

## What is established

[verification.md](docs/verification.md) has the details. In short:

Proved in Lean 4, with no `sorry` and no axioms beyond `propext` and `Quot.sound`: the collector's certificate checker is sound, its seventeen certificates hold, and its loop model stores in bounds and selects stably. The lane pool runs every index of a host region exactly once, returns only when no worker is inside, and never waits for a worker to arrive. A guard the compiler leaves out because the checker's facts discharge it cannot fail where those facts hold. A core calculus of ownership and leases is proved safe: an accepted program there has no use after move, use after free, double free, leaked ticket or group, use of a group after `wait`, aliased call argument or data race under any interleaving, and never gets stuck. The calculus covers whole owners, record fields, lengths, elements, array parts with symbolic bounds, tasks, task groups and `parallel` regions whose lanes own elements or blocks. It is written by hand beside the checker, and a differential harness requires the two to classify generated programs the same way. The Python checker, the emitter and the native code are not proved.

Tested: placement, effects, closures, what a lane may call, `reduce`, `compact` and `scan`, queued device work, generics, traits, declared field extents, the facts the checker keeps in scope for guard elision, I/O rings, plans and fusion, storage floats over every encoding, derived gradients against finite differences, test blocks, and extents left out of a call. Every guard the emitter leaves out is accepted first by an independent audit, and the conservative and optimized builds of 1,821 generated functions agreed on 174,816 cases per compiler (`evidence/v1_4/guards`). The suite has about 3,500 tests, including rejection tables from seven adversarial reviews, two of them of this release, and native runs under both compilers and four sanitizers; CUDA runs only under `make gpu` and the owner's two device timing targets, and QEMU only on an AArch64 host.

SMT-checked: `cairn verify` asks Z3 whether two versions of a function can differ in their result or in anything they were lent. It trusts its translator and Z3. It follows an owner that moves, and answers `unknown` for an owner held inside a record or an array, a trip count it cannot bound, concurrency, device memory and the foreign boundary.

Measured, on single machines: at equal guards and worker counts a CAIRN parallel region runs level with OpenMP and oneTBB, and beats the guarded sequential loop from ten million elements on four kernels (`evidence/v1_3/bench/`). It loses where the language keeps a reduction or a shared-bin histogram sequential, as the preregistration predicted. Device kernels run far ahead of the host, and transfers often cost more than the kernel (`evidence/v1_3/gpu/`). `cairn predict` was checked against host timings it was not fitted to: a median error of 33 to 44 percent, and a Kendall tau of 0.86 to 0.92 for how it ranks them (`evidence/v1_4/perf_model/`); its device side is a published specification no run has confirmed. Nothing is claimed against tuned C++ or CUDA.

One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests. There was no comparison arm, so it shows that the cards are enough for those tasks and nothing about other languages. The packet rework of this release was measured on scripted transcripts, not on a model (`evidence/v1_4/context/`).

## License

CAIRN is licensed under either the [MIT license](LICENSE-MIT) or the [Apache License, Version 2.0](LICENSE-APACHE), at your option. Contributions are accepted under the same terms; [CONTRIBUTING.md](CONTRIBUTING.md) has the working agreement and [SECURITY.md](SECURITY.md) says how to report a soundness bug.

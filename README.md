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
E-LEASED: data is lent to left until wait(left).
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
- Records, sums with exhaustive `match`, `try` for error propagation.
- Owners that move and are released at scope exit, `linear` values that must be consumed exactly once, `take`, `swap`, `defer`.
- Generic types and functions with bounds on traits, kinds (`copy`, `affine`) and scalar classes; traits with static dispatch and explicit `dyn`.
- Closures that borrow what they capture and never escape.
- Tasks with leases, down to one field of a record; task groups collected in completion order; I/O rings that keep kernel operations in flight without a thread each; atomics; mutexes entered through a closure.
- `parallel`, `reduce`, `compact`, placement types (`@host @pinned @unified @device`), kernels, transfers, queued device work.
- Modules, a standard library written in CAIRN, projects with vendored dependencies.
- Recipes: generators written as library code and applied with `derive`.
- `extern` with mandatory effects and an `unsafe` gate, MMIO, inline assembly, a freestanding target.

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
cairn run examples/hello
cairn run examples/systems                 # typed-error parser, stack sort, heap pipeline
cairn test examples/systems --cxx g++      # independent finite task contracts
cairn run examples/apps/kvstore            # a crash-safe storage engine
cairn run examples/apps/analytics          # a columnar engine whose table types are generated by recipes
cairn run examples/apps/wordfreq -- examples/apps/wordfreq/tale.txt   # a command-line word counter
cairn run examples/apps/gpu_pipeline       # needs CUDA
cairn run examples/embedded                # bare-metal AArch64 under QEMU
```

| Command | What passing means |
|---|---|
| `check`, `emit`, `expand` | The program is accepted: syntax, types, ownership, leases, lanes, placement, effects. `emit` prints the C++, `expand` prints what every `derive` generated. |
| `build`, `run` | A fresh native build (`--debug` maps symbols to the `.cairn` files, `--incremental` reuses objects by content hash), then execution with process limits, under QEMU for the freestanding target. `cairn run app -- ARGS` starts the program with `ARGS`. |
| `test` | Independent finite task cases pass, with the child's exit status checked. |
| `verify --symbol f`, `--all` | Z3 found no input on which two versions of a function differ, within the modeled fragment. Anything outside it is reported unknown and blocks the aggregate result. |
| `certificates` | The seventeen arithmetic identities behind the collector check, here in Python and in `proofs/` in Lean. |
| `check --generics` | Every generic function needs only what its bounds promise. |
| `inspect --symbol f` | The packet an AI agent gets for an edit: its source, the interfaces around it, effects, rule cards; `--expand g` adds a body. |
| `explain`, `explain --symbol f` | Where each function pays at run time, read from the emitted C++ and clang's optimization record: guards per line, allocations, waits, loop vectorization. Nothing runs. |
| `fmt`, `doc`, `lsp` | A comment-preserving formatter, an API reference generated from the checked program, a language server. |

`make lint test proof` are the everyday gates. `make gpu embedded` need the hardware and the emulator, and `make docs` regenerates [docs/std_api.md](docs/std_api.md).

## Documentation

Start with the [guide](docs/guide.md), which takes a fresh checkout to a working project and then walks through twelve complete programs. [docs/](docs/README.md) indexes the reference and everything else. [AGENTS.md](AGENTS.md) has the rules for anyone, human or agent, changing this repository, and [CHANGELOG.md](CHANGELOG.md) is the release history.

## Repository

```
src/cairn/
  compiler/         syntax, modules, recipes, the checker and its rules, the emitter
  projects/         manifests and vendored dependencies, native builds and their object cache, the toolchain table
  verify/           finite task tests, SMT source equivalence, coverage, collector certificates
  agent/            projections, edit sessions and packets, rule cards, sketches
  editor/           formatter, language server, API reference generator
  runtime/          guards, owners, threads, tasks, atomics, the lane pool, CUDA lanes (C++ headers)
  std/              the standard library, written in CAIRN
  targets/          start-up code and linker script of the freestanding board
proofs/             Lean 4: certificate checker, collector loop model, ownership and lease calculus
examples/           basics/ hello/ systems/ apps/ embedded/ and inputs for the agent and proof tools
tests/              language/ soundness/ verification/ projects/ runtime/ tooling/ agent/ oracles/ native/
tools/              checks/ ai/ release/
bench/              cpu/ gpu/ host_regions/ suite/
editors/            VS Code and Cursor extension, Vim runtime files, both generated from the compiler vocabulary
docs/               the documentation, the generated std_api.md, project data, history/
evidence/           executed results by release, and their limits
```

## What is established

[verification.md](docs/verification.md) has the details. In short:

Proved in Lean 4, with no `sorry` and no axioms beyond `propext` and `Quot.sound`: the collector's certificate checker is sound, its seventeen certificates hold, and its loop model stores in bounds and selects stably. The lane pool runs every index of a host region exactly once, returns only when no worker is inside, and never waits for a worker to arrive. A guard the compiler leaves out because the checker's facts discharge it cannot fail where those facts hold. A core calculus of ownership and leases is proved safe: an accepted program there has no use after move, use after free, double free, leaked ticket or group, use of a group after `wait`, aliased call argument or data race under any interleaving, and never gets stuck. The calculus covers whole owners, record fields, lengths, elements, array parts with symbolic bounds, tasks, task groups and `parallel` regions whose lanes own elements or blocks. It is written by hand beside the checker, and a differential harness requires the two to classify generated programs the same way. The Python checker, the emitter and the native code are not proved.

Tested: placement, effects, closures, what a lane may call, `reduce` and `compact`, queued device work, generics, traits, declared field extents, the facts the checker keeps in scope for guard elision, I/O rings, plans and extents left out of a call. The suite has about 2,200 tests, including rejection tables from five adversarial reviews and native runs under both compilers and four sanitizers; CUDA runs only under `make gpu`, and QEMU only on an AArch64 host.

SMT-checked: `cairn verify` asks Z3 whether two versions of a function can differ in their result or in anything they were lent. It trusts its translator and Z3. It follows an owner that moves, and answers `unknown` for an owner held inside a record or an array, a trip count it cannot bound, concurrency, device memory and the foreign boundary.

Measured, on single machines: at equal guards and worker counts a CAIRN parallel region runs level with OpenMP and oneTBB, and beats the guarded sequential loop from ten million elements on four kernels (`evidence/v1_3/bench/`). It loses where the language keeps a reduction or a shared-bin histogram sequential, as the preregistration predicted. Device kernels run far ahead of the host, and transfers often cost more than the kernel (`evidence/v1_3/gpu/`). Nothing is claimed against tuned C++ or CUDA.

One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests. There was no comparison arm, so it shows that the cards are enough for those tasks and nothing about other languages.

## License

CAIRN is licensed under either the [MIT license](LICENSE-MIT) or the [Apache License, Version 2.0](LICENSE-APACHE), at your option. Contributions are accepted under the same terms; [CONTRIBUTING.md](CONTRIBUTING.md) has the working agreement and [SECURITY.md](SECURITY.md) says how to report a soundness bug.

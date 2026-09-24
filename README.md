# CAIRN

CAIRN is a systems language whose compiler rejects data races before a program runs, including races between the threads of a GPU kernel. It compiles to C++20 for the CPU and to CUDA for NVIDIA GPUs, and it is designed to be written by AI agents.

Here is a kernel in which each block of 256 threads reverses one tile of an array through shared memory:

```cairn
// Each block of 256 threads reverses one 256-element tile of x into out.
fn reverse_tiles(g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  blocks b in g threads t in 256 {
    shared tile:f32[256] = zeroed;
    let i = b * 256 + t;
    if i < n { tile[t] = x[i]; }
    barrier;                                       // every thread's element is in place
    if i < n { out[i] = tile[255 - t]; }
  }
}
```

Delete the barrier and the program no longer compiles. The compiler runs the block's body for every thread, finds the pair of threads that conflict, and says where the barrier goes:

```text
error[E-COOP-UNORDERED]: thread t = 255 reads tile[0] at line 7, which thread t = 0 writes at line 6 in the same phase: nothing makes the write happen first. Put a barrier after line 6 and before line 7 runs.
  --> racy.cairn:7:25
  |
7 |     if i < n { out[i] = tile[255 - t]; }
  |                         ^^^^
  = note: the cooperative card states this rule: cairn rules E-COOP-UNORDERED
```

The same checks cover every block of a launch. If every block writes `out[t]`, two blocks write the same element, and the kernel is refused with `E-COOP-GLOBAL`.

## What the compiler refuses

These are compile-time errors, each with a stable code:

- two threads of a block touching one shared element between barriers where either writes (`E-COOP-CONFLICT`, `E-COOP-UNORDERED`, `E-COOP-REUSE`)
- a barrier or warp operation that not every thread reaches (`E-COOP-BARRIER`, `E-COOP-WARP`)
- two blocks writing one element of global memory (`E-COOP-GLOBAL`), or two `parallel` lanes writing one element (`E-PARALLEL-RACE`)
- touching data a running task still holds (`E-LEASED`), using a value after it moved (`E-MOVED`), two mutable borrows of one array (`E-ALIAS`)
- a function doing something its signature does not allow, such as allocating or spawning a thread inside `pure` code (`E-EFFECT-CEILING`)

Integer overflow, division by zero and out-of-bounds indexing are checked at run time instead. A failed check stops the program, on the CPU and inside a GPU kernel, rather than corrupting memory.

## Built for agents, and with them

An agent writing CAIRN gets feedback it can act on without reading the manual:

- One `cairn check` reports every independent error, each with a stable code, a line and column, and a message that says what rule was broken. The first error is always the one a check that stops at the first error would give.
- Every function has an inferred effect row (`alloc`, `spawn`, `io`, `write:out`, `trap`, ...). `cairn doc` prints it, and a signature can cap it, so an edit that adds an allocation where none was allowed is refused.
- A slow function stays as the reference, and a faster version is written beside it as an implementation: `fn g(...) implements f when n % 4 == 0 { ... }`. `cairn validate` tests it against the reference on generated edge cases, and `cairn tune` chooses among validated implementations and plans within compile and run budgets.
- `cairn diff OLD NEW` classifies each function as compiling to identical code, shown equivalent by Z3 within the fragment it models, or changed, with an input that shows the difference.
- The compiler's edit, plan and implementation sessions are available to agents without a shell through `cairn mcp`, and the repository is a Claude Code plugin that adds the skill, the command, the language server and those tools.

CAIRN is also built with agents. Most of its compiler, runtime, tests and documentation were written by AI agents (Claude Code) working to one maintainer's design, and the repository's rules for agents are in [AGENTS.md](AGENTS.md).

Whether CAIRN makes agents cheaper or more successful than C++ or Rust is not yet established. The preregistered v1.0 benchmark, run before the plugin existed, gave `claude-sonnet-5` ten small tasks in each language: every subject solved its task, and CAIRN subjects used 11.6 times the tokens of C++ subjects, most of it reading documentation ([results](evidence/v1_0/ai_benchmark/RESULTS.md)). A larger evaluation with the plugin is in progress, and this README makes no cost claim until it reports.

## GPU work without a GPU

Most of the kernel loop runs on a laptop:

```sh
cairn run examples/cooperative/gpu.toml --emulate --device-target sm_120   # device code on host threads
cairn cards                                                                # the GPUs cairn predict can price
cairn predict examples/cooperative/gpu.toml --card all                     # a time per function on each of them
cairn tune examples/cooperative/tuned.toml --symbol row_totals --card h100 --at rows=64,cols=1e5   # candidates compiled for sm_90a, priced on an H100
```

`--emulate` judges the program against a real device target and runs every device region, collector, cooperative region and transfer on host threads. `cairn test` and `cairn validate` then check a kernel's logic without a GPU, and the host build can run under the address and thread sanitizers. It checks correctness only: an emulated run is not a device run and not a timing.

`cairn predict` prices device work from the published specifications of eight GPUs, from the A100 to the B200 and the RTX 5090. `cairn tune` compiles its candidates for that GPU's target and reads registers and shared memory from ptxas, with nothing launched. These are predictions from datasheets and compiler reports. None of the eight cards has been checked against a measurement.

## A CPU example

The same rules apply to threads on the CPU. Two tasks fill the two halves of an array:

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) {
  for i in 0..n { out[i] = start + u64(i); }     // checked: an overflow traps
}

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(data[0..mid], 0);        // left holds data[0..mid] until wait
  let right = spawn fill(data[mid..n], u64(mid));
  wait(left);
  wait(right);
}

fn main() -> i32 {
  let mut data = Buf[u64](1000);                 // an owner, released at scope exit
  halves(data);                                  // n is len(data)
  let total = reduce + for i in len(data) yield data[i];
  println("total = ", total);
  return 0;
}
```

`rw<u64>[n]` is a mutable borrow of `n` elements, and `n` is part of its type. The two tasks may run at once because `data[0..mid]` and `data[mid..n]` do not overlap. Writing `data[0] = 7;` before `wait(left)` is refused with `E-LEASED`.

A `parallel` body runs as CUDA lanes when its arrays live on the device and on a pool of host threads when they do not. A lane may write only element `[i]` of what it writes, so two lanes never race:

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
```

## Install

You need Linux on x86-64 or AArch64, Python 3.11 or later, and Clang or GCC with C++20. The compiler has no third-party Python dependency.

```sh
git clone https://github.com/SamMausberg/cairn && cd cairn
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                   # '.[dev]' adds pytest, ruff, mypy and setuptools
cairn doctor                       # which optional tools are present
```

For Claude Code, install the plugin. Other agents load `skills/cairn/` (the Agent Skill format) or connect to `cairn mcp` ([agents.md](docs/agents.md#the-skill-and-the-claude-code-plugin)).

```sh
claude plugin marketplace add SamMausberg/cairn
claude plugin install cairn@cairn
```

Optional tools switch on more checks and are never downloaded: CUDA `nvcc` for device code, `libz3` for `verify` and `diff`, Lean 4 for `proofs/`, and `qemu-system-aarch64` for the bare-metal target.

## Run

```sh
cairn run first.cairn              # the CPU example above, saved as first.cairn: total = 499500
cairn doc first.cairn              # each function's signature and effect row
cairn new my_project && cairn run my_project
cairn test examples/systems        # test blocks and task contracts, each in its own process
cairn run examples/apps/kvstore    # a crash-safe storage engine
```

[docs/guide.md](docs/guide.md) goes from a fresh checkout to twelve complete programs, and [docs/tools.md](docs/tools.md) covers every command.

## Demos

Each demo is one command from a fresh checkout, and `tests/projects/test_demos.py` runs all four.

| Demo | What it shows | Command |
|---|---|---|
| [repair](demos/repair/README.md) | An agent fixes a bug through the edit host. The host refuses a debug print (`E-EFFECT-EXPANSION`) and a tidy-up that changes behaviour (`E-PRESERVE`, with the input that shows it). `cairn diff` then reports the fix as a behaviour change, the tidy-up as SMT-equivalent, and a major version bump. | `make demo-repair` |
| [numeric](demos/numeric/README.md) | A 1024 x 1024 heat-plate sweep written once runs on host threads and as CUDA lanes. The host result stays within 0.0000148 of an f64 reference, against a stated bound of 0.0048. The device half compiles for sm_120 and has not run. | `make demo-numeric` |
| [visual](demos/visual/README.md) | An agent asks what a program draws, finds in the layout record that a colour bar covers the plot, moves it, and gets back the new frames. | `make demo-visual` |
| [implement](demos/implement/README.md) | An agent writes faster implementations of a sum of squares through `cairn mcp`. A looser tolerance is refused (`E-TOLERANCE`), a candidate that drops the tail fails validation at `n = 5`, and `cairn tune` times the valid ones and keeps the fastest. | `make demo-implement` |

![the visual demo's plate viewer after 5000 sweeps](demos/visual/frames/after-4.png)

The demo agents are scripted. What the host, the compiler, Z3 and the programs report is computed on each run.

## Limitations and what you trust

CAIRN 1.0 was developed and measured on one machine, and a later major version may still change the language.

The compiler is not proved correct. The checker and the C++ emitter are about 13,100 lines of Python (`src/cairn/compiler`), and the runtime is about 3,700 lines of C++ headers (`src/cairn/runtime`). The Lean proofs cover models written by hand beside that code. Differential tests compare those models with the checker on generated programs, which shows agreement on samples, not that the Python implements the model.

| You trust | For | Checked by |
|---|---|---|
| The Python parser, checker and emitter | every program | about 5,000 tests, rejection tables from eight adversarial reviews, differential runs against the Lean models |
| The runtime headers | owners, threads, the lane pool, rings, device calls | native runs under Clang and GCC with the address, leak, undefined-behaviour and thread sanitizers |
| Clang or GCC, and nvcc | native and device code | nothing in this repository |
| `unsafe` blocks, `extern` declarations, typed `asm` and foreign implementations | the foreign boundary, MMIO, inline assembly, vendored C++ and CUDA | the effects and contracts they declare, taken as written; a foreign implementation is also tested against its reference |
| Z3 and the SMT translator | `cairn verify` and `cairn diff` | tests of the translator; anything outside the modeled fragment is `unknown` |
| The Lean kernel | the proofs in `proofs/` | an axiom audit: `propext` and `Quot.sound`, nothing else |

What has not been validated:

- Most device code has not run on a GPU. Device lanes, transfers and three kernels ran on one RTX 5070 Ti (`evidence/v0_8_3/gpu`). Everything added since compiles for sm_120 and is checked on the host: vector loads, shared staging, device plans, tensor-core fragments and multiplies, cooperative regions, pipeline stages, typed PTX, the device `scan`, foreign CUDA kernels and the execution context. `--emulate` runs that code's logic on host threads, which is not a device run.
- `cairn predict` on the device side uses published specifications, not measurements.
- `cairn validate` is finite testing on generated inputs. The reference is an independent algorithm, but it goes through the same compiler.
- Host performance was measured on one 16-thread x86-64 machine against plain C++, OpenMP and oneTBB at equal guards. Nothing is claimed against tuned C++ or CUDA.
- SMT equivalence covers a fragment. An owner inside a record or an array, concurrency, device memory, the foreign boundary, storage floats and loops it cannot bound are `unknown`, and `unknown` is never reported as success.
- The AI evidence is one model family on small tasks, and that model also wrote much of the language and the tasks.
- Linux only. There is no package registry, and the package is not on PyPI. The bare-metal AArch64 target runs only under QEMU on an AArch64 host.

## What is established

| Claim | Kind | Where |
|---|---|---|
| An accepted program of the ownership and lease calculus has no use after move or free, double free, leaked task, aliased argument or data race, under any interleaving, and never gets stuck. | Lean-checked model | `proofs/Cairn/Ownership/` |
| Two threads of an accepted cooperative region never make conflicting accesses between barriers, in any interleaving, and the result does not depend on thread order. | Lean-checked model, differential-tested | `proofs/Cairn/Cooperative.lean`, `evidence/v1_0/cooperative` |
| The lane pool runs each index of a host region once and returns only when no worker is inside. | Lean-checked model | `proofs/Cairn/Region.lean` |
| A guard the compiler leaves out cannot fail where the checker's facts hold. Each left-out guard is also re-derived by an independent audit, and conservative and optimized builds agreed on 174,816 cases per compiler. | Lean-checked rule, audited, finite-tested | `proofs/Cairn/Facts.lean`, `evidence/v1_0/guards` |
| A declared layout covers its tile exactly once, so writes through it by distinct threads never collide. | Lean-checked model | `proofs/Cairn/Layout.lean` |
| The collector's seventeen arithmetic certificates hold, and its loop model stores in bounds. | Lean-checked | `proofs/Cairn/Collector.lean` |
| A host `parallel` region runs level with OpenMP and oneTBB at equal guards and worker counts. | Benchmarked, one machine | `evidence/v1_0/bench` |
| `cairn predict` ranks held-out host timings with a Kendall tau of 0.87 to 0.92, at a median error of 28 to 44 percent. | Benchmarked, one machine | `evidence/v1_0/perf_model` |
| Every device example runs under `--emulate` with the same results as its host build or reference loop, under Clang and GCC, with the address, leak and thread sanitizers clean. | Finite-tested on the host | `evidence/v1_1/emulation` |

[docs/verification.md](docs/verification.md) says what each proof, model and test covers and what it leaves out.

## The language

- Checked integer arithmetic, explicit conversions, and wrapping forms that say so by name.
- Records, sums with exhaustive `match`, `try` for errors, generics with trait and kind bounds, closures that never escape, modules and projects with vendored dependencies.
- Owners that move and are released at scope exit, `linear` values consumed exactly once, `take`, `swap` and `defer`.
- Tasks with leases down to one field, task groups, I/O rings, atomics and mutexes.
- `parallel`, `reduce`, `compact` and `scan` on host threads or CUDA lanes, plans that change how a region runs without changing its result, and placement in the type (`@host`, `@pinned`, `@unified`, `@device`).
- Cooperative regions with shared memory, barriers, warp shuffles and pipeline stages, checked by the phase rule; layouts with checked coverage; tensor-core fragments.
- Alternative implementations of a function, chosen by a plan, with the reference as the fallback.
- Storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) with one stated rounding, `quantize`, and `derive grad`, which writes a reverse-mode derivative as ordinary checked code.
- Test blocks and `assert` in the language, and a standard library written in CAIRN: collections, text, formatting, files, sockets, `zlib`, images and 2D drawing.
- `extern` with mandatory effects behind `unsafe`, typed inline assembly for x86-64, AArch64 and PTX, vendored C++ and CUDA as foreign implementations, a freestanding AArch64 target, and a generated C header for calling a CAIRN library from C or C++.

## Documentation and repository

[docs/](docs/README.md) is the reference, [AGENTS.md](AGENTS.md) has the rules for changing this repository, and [CHANGELOG.md](CHANGELOG.md) has the release history.

```
src/cairn/     compiler/ runtime/ std/ verify/ agent/ editor/ perf/ projects/ templates/ targets/
.claude-plugin/ the Claude Code plugin and marketplace manifests
skills/        cairn/: the Agent Skill, generated from the compiler's rule cards
proofs/        Lean 4 models and proofs
demos/         the four demos above
examples/      runnable projects and tool inputs
bazel/         rules_cairn: Bazel rules for CAIRN libraries, binaries and tests
tests/         the suite, one folder per subject
tools/         checks, generators and release scripts
bench/         benchmarks and the AI evaluation harness
editors/       VS Code and Vim support, generated from the compiler's vocabulary
docs/          the documentation
evidence/      executed results by release, with their limits
```

## License

CAIRN is licensed under either the [MIT license](LICENSE-MIT) or the [Apache License, Version 2.0](LICENSE-APACHE), at your option. Contributions are accepted under the same terms. [CONTRIBUTING.md](CONTRIBUTING.md) has the working agreement, and [SECURITY.md](SECURITY.md) says how to report a soundness bug.

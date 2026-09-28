# CAIRN

CAIRN is a systems language whose compiler refuses data races before a program runs, including races between the threads of a GPU kernel. It compiles to C++20 for the CPU and to CUDA for NVIDIA GPUs. It is designed to be written by AI agents.

In the kernel below, each block of 256 threads reverses one tile of an array through shared memory, the fast memory that the threads of one block share:

```cairn
// Each block of 256 threads reverses one tile of x into out: 256 elements, or the last few of x.
fn reverse_tiles(g:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  blocks b in g threads t in 256 {
    shared tile:f32[256] = zeroed;
    let i = b * 256 + t;
    let short = b * 256 + 256 - min(n, b * 256 + 256);  // how many of the tile's 256 lie past n
    if t >= short { tile[t] = x[i - short]; }           // a short tile fills the top of tile
    barrier;                                            // every thread's element is in place
    if i < n { out[i] = tile[255 - t]; }
  }
}
```

Delete the barrier and the program no longer compiles. The compiler runs the block's body for every thread, finds the pair of threads that conflict, and says where the barrier goes:

```text
error[E-COOP-UNORDERED]: thread t = 255 reads tile[0] at line 8, which thread t = 0 writes at line 7 in the same phase: nothing makes the write happen first. Put a barrier after line 7 and before line 8 runs.
  --> racy.cairn:8:25
  |
8 |     if i < n { out[i] = tile[255 - t]; }
  |                         ^^^^
  = note: the cooperative card states this rule: cairn rules E-COOP-UNORDERED
```

A region written `blocks ... threads ...` is a cooperative region: CAIRN's form of a CUDA kernel whose threads share memory and meet at barriers. The compiler checks every block of a launch as well. If every block writes `out[t]`, two blocks write the same element, and the kernel is refused with `E-COOP-GLOBAL`.

## What the compiler refuses

The compiler refuses each of these before the program runs, and names each with a stable diagnostic code:

- two threads of a block touching one shared element between barriers, where either of them writes (`E-COOP-CONFLICT`, `E-COOP-UNORDERED`, `E-COOP-REUSE`)
- a barrier or warp operation that some thread of the block does not reach (`E-COOP-BARRIER`, `E-COOP-WARP`)
- two blocks writing one element of global memory (`E-COOP-GLOBAL`), or two lanes of a `parallel` region writing one element (`E-PARALLEL-RACE`)
- touching data a running task still holds (`E-LEASED`), using a value after it moved (`E-MOVED`), passing one array as two mutable borrows (`E-ALIAS`)
- a function doing something its signature does not allow, such as allocating memory or spawning a task inside `pure` code (`E-EFFECT-CEILING`)

Integer overflow, division by zero and indexing out of bounds are checked at run time instead, by guards the compiler writes into the program. A failed guard stops the program, on the CPU and inside a GPU kernel, before it can corrupt memory.

## The same rules on the CPU

In this program two tasks, each on a thread of its own, fill the two halves of one array at once:

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

`rw<u64>[n]` is a mutable borrow of `n` elements, and `n` is part of its type. A task leases what it borrows until its `wait`: until then nothing else may write what the task reads or touch what it writes. The two tasks may run at once because `data[0..mid]` and `data[mid..n]` do not overlap. Writing `data[0] = 7;` before `wait(left)` is refused with `E-LEASED`.

A `parallel` region runs its body once for each index, and each run is a lane. The lanes run as CUDA threads when the region's arrays live on the device, and on a pool of host threads when they do not. A lane may touch an array that any lane writes only at its own index `[i]`, or inside its own block `[i * S + j]` with `j` below a constant `S`, so two lanes never race:

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}
```

## Install

You need Linux on x86-64 or AArch64, Python 3.11 or later, and GCC 11 or later or Clang 13 or later. The compiler has no third-party Python dependency.

```sh
git clone https://github.com/SamMausberg/cairn && cd cairn
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                   # '.[dev]' adds pytest, ruff, mypy and setuptools
cairn doctor                       # which optional tools are present
```

For Claude Code, install the plugin. Other agents load `skills/cairn/`, which is in the Agent Skill format, or connect to `cairn mcp` ([agents.md](docs/agents.md#the-skill-and-the-claude-code-plugin)).

```sh
claude plugin marketplace add SamMausberg/cairn
claude plugin install cairn@cairn
```

Each optional tool turns on more checks, and CAIRN never downloads one: `nvcc` from CUDA 12.9 or later for device code, `libz3` for `verify` and `diff`, Lean 4 for `proofs/`, and `qemu-system-aarch64` for the freestanding AArch64 target, which runs with no operating system.

## Run

```sh
cairn run first.cairn              # the CPU example above, saved as first.cairn: total = 499500
cairn doc first.cairn              # each function's signature and effect row, the list of what it may do
cairn new my_project && cairn run my_project
cairn test examples/systems        # test blocks and task contracts, each in its own process
cairn run examples/apps/kvstore    # a storage engine that recovers from a torn log
```

[docs/guide.md](docs/guide.md) goes from a fresh checkout to twelve complete programs, and [docs/tools.md](docs/tools.md) covers every command.

## Built for agents, and with them

An agent writing CAIRN gets feedback it can act on without reading the manual:

- One `cairn check` reports every independent error. Each has a stable code, a line and column, a message that says which rule was broken, and the name of the rule card that states the rule, which `cairn rules CODE` prints. The first error is always the one a check that stops at the first error would give.
- Every function has an inferred effect row: the list of what it may do, such as `alloc`, `spawn`, `io`, `write:out` or `trap`. `cairn doc` prints it, and a signature can cap it, so an edit that adds an allocation where none was allowed is refused.
- A slow function stays as the reference, and a faster version is written beside it as an implementation: `fn g(...) implements f when n % 4 == 0 { ... }`. `cairn validate` tests it against the reference on generated edge cases. `cairn tune` chooses among validated implementations and plans, which change how a region runs without changing its result, within compile and run budgets.
- `cairn diff OLD NEW` gives each function a class: identical code; SMT-equivalent, where Z3 found no input that tells the two apart within the fragment it models; changed, with an input that shows the difference; or unknown, which is never counted as unchanged.
- The compiler's edit, plan and implementation sessions reach agents without a shell through `cairn mcp`, and the repository is a Claude Code plugin that adds the skill, the `cairn` command, the language server and those tools. After an edit of one function's body, the sessions and the language server check that body alone against the record the last check kept, then run the rules that span the whole program again. A differential test requires the same answer as a whole check.
- `cairn run --sanitize address` or `--sanitize thread` builds the program under that sanitizer and runs it in one command.

CAIRN is also built with agents. AI agents (Claude Code) wrote most of its compiler, runtime, tests and documentation, working to one maintainer's design. The repository's rules for agents are in [AGENTS.md](AGENTS.md).

Whether CAIRN makes agents cheaper or more successful than C++ or Rust is not established, and on the evidence so far it costs them more. The preregistered v1.0 benchmark, run before the plugin existed, gave `claude-sonnet-5` ten small tasks in each language. Every subject solved its task, and CAIRN subjects used 11.6 times the tokens of C++ subjects, most of it reading documentation ([results](evidence/v1_0/ai_benchmark/RESULTS.md)).

The 1.1 evaluation with the plugin stopped early, at 54 of its 156 subjects. Again every subject solved its task, and per solved task the plugin arm used 5.5 times C++'s tokens and the documentation arm 8.2 times ([partial results](evidence/v1_1/ai_eval/RESULTS.md)). The two runs differ in design, so the difference between them is not a measured improvement.

[Where those tokens went](evidence/v1_1/friction/README.md): every CAIRN subject's first program that type-checked was correct. The cost was reading before writing, refusals, and finding the build with the sanitizers. Release 1.1.0 fixes the costliest of those causes, and no model has been run on it yet.

## GPU work without a GPU

Most of the cycle of writing, testing and tuning a kernel runs on a machine with no GPU:

```sh
cairn run examples/cooperative/gpu.toml --emulate --device-target sm_120   # device code on host threads
cairn cards                                                                # the GPUs cairn predict can price
cairn predict examples/cooperative/gpu.toml --card all                     # a time per function on each of them
cairn tune examples/cooperative/tuned.toml --symbol row_totals --card h100 --at rows=64,cols=1e5   # candidates compiled for sm_90a, priced on an H100
```

`--emulate` judges the program against a real device target, such as `sm_120`, and runs all of its device work on host threads: regions, `reduce`, `compact` and `scan`, cooperative regions and transfers. `cairn test` and `cairn validate` then check a kernel's logic without a GPU, and the host build can run under the address and thread sanitizers. An emulated run checks correctness on host threads. It is not a run on the device, and it measures no time. What the host cannot run as the device would, such as a vendored CUDA kernel, is refused with `E-EMULATE`.

`cairn predict` prices device work from the published specifications of eight GPUs, from the A100 to the B200 and the RTX 5090. `cairn tune` compiles its candidates for that GPU's target and reads their registers and shared memory from ptxas, with nothing launched. These are predictions from datasheets and compiler reports. None of the eight cards has been checked against a measurement.

A device library's C header gives each function whose effects the host cannot observe a `cq_NAME(stream, ...)` entry. The entry queues the function's work on the caller's CUDA stream and returns without waiting, so PyTorch or any CUDA program can call it or capture it in a CUDA graph. `cairn export --harness sol-execbench|gpumode|kernelbench` packages a function as a submission for that benchmark, bound to PyTorch's current stream. It writes the files and prints the command, and it never submits or runs anything.

## Demos

Each demo is one command from a fresh checkout, and `tests/projects/test_demos.py` runs all four.

| Demo | What it shows | Command |
|---|---|---|
| [repair](demos/repair/README.md) | An agent fixes a bug through the edit host. The host refuses a debug print (`E-EFFECT-EXPANSION`) and a tidy-up that changes behaviour (`E-PRESERVE`, with the input that shows it). `cairn diff` then reports the fix as a behaviour change, the tidy-up as SMT-equivalent, and a major version bump. | `make demo-repair` |
| [numeric](demos/numeric/README.md) | A sweep of a 1024 x 1024 heat plate, written once, runs on host threads and as CUDA lanes. The host result stays within 0.0000148 of an f64 reference, against a stated bound of 0.0048. On an RTX 5070 Ti the device half gave the host's bits. | `make demo-numeric` |
| [visual](demos/visual/README.md) | An agent asks what a program draws, finds in the layout record that a colour bar covers the plot, moves it, and gets back the new frames. | `make demo-visual` |
| [implement](demos/implement/README.md) | An agent writes faster implementations of a sum of squares through `cairn mcp`. A looser tolerance is refused (`E-TOLERANCE`), a candidate that drops the tail fails validation at `n = 5`, and `cairn tune` times the valid ones and keeps the fastest. | `make demo-implement` |

![the visual demo's plate viewer after 5000 sweeps](demos/visual/frames/after-4.png)

The demo agents are scripted. What the host, the compiler, Z3 and the programs report is computed on each run.

## Limitations and what you trust

CAIRN 1.1 was developed and measured on one machine, and a later major version may still change the language.

The compiler is not proved correct. From parser to C++ emitter it is about 15,300 lines of Python (`src/cairn/compiler`), and the runtime is about 4,300 lines of C++ headers (`src/cairn/runtime`). The Lean proofs cover models written by hand beside that code. Differential tests compare those models with the checker on generated programs, which shows that they agree on samples and does not show that the Python implements the model.

| You trust | For | Checked by |
|---|---|---|
| The Python parser, checker and emitter | every program | about 5,900 tests, rejection tables from nine adversarial reviews, differential runs against the Lean models |
| The runtime headers | owners, threads, the lane pool, rings, device calls | native runs under Clang and GCC with the address, leak, undefined-behaviour and thread sanitizers |
| Clang or GCC, and nvcc | native and device code | nothing in this repository |
| `unsafe` blocks, `extern` declarations, typed `asm` and foreign implementations | the foreign boundary, MMIO, inline assembly, vendored C++ and CUDA | the effects and contracts they declare, taken as written; a foreign implementation is also tested against its reference |
| Z3 and the SMT translator | `cairn verify` and `cairn diff` | tests of the translator; anything outside the modeled fragment is `unknown` |
| The Lean kernel | the proofs in `proofs/` | an axiom audit, which in the 1.1.0 record found `propext` and `Quot.sound` and nothing else |

Device code has run on two GPUs: a rented GH200 for the 0.8.0 to 0.8.2 records, and since 0.8.3 an RTX 5070 Ti under WSL2. In the session that released 1.1.0, 48 of the suite's 52 tests that run device code passed on the RTX 5070 Ti. Three trap on purpose and were left out, and one needs Compute Sanitizer, which cannot instrument that GPU under WSL2.

The tests that passed ran device plans, wide loads and stores, atomics, cooperative regions with their finish, votes and pipeline stages, multiplies on tensor cores that index their tiles through layouts in code, the device `scan` and `compact`, `cq_` entries in a CUDA graph and foreign CUDA kernels, each checked against the host or a reference ([evidence/v1_1/gpu](evidence/v1_1/gpu/README.md)). Storage floats, gradients and asserts in a lane, and a guard that fails on the device, have not run on a GPU.

What else has not been validated:

- `cairn predict` prices the device side from published specifications. No device prediction has been checked against a measurement.
- `cairn validate` is finite testing on generated inputs. The reference is an independent algorithm, but it goes through the same compiler.
- Host performance was measured on one x86-64 machine with 16 threads against plain C++, OpenMP and oneTBB at equal guards. Device kernels were timed on the RTX 5070 Ti alone, against plain CUDA written by hand for the same computations ([evidence/v1_1/device_perf](evidence/v1_1/device_perf/README.md)). Nothing is claimed against tuned C++ or CUDA.
- SMT equivalence covers a fragment. An owner inside a record or an array, concurrency, device memory, the foreign boundary, storage floats and loops it cannot bound are `unknown`, and `unknown` is never reported as success.
- The AI evidence is one model family on small tasks, and that model also wrote much of the language and the tasks.
- CAIRN runs on Linux only. There is no package registry, and the package is not on PyPI. The freestanding AArch64 target runs only under QEMU on an AArch64 host.

## What is established

| Claim | Kind | Where |
|---|---|---|
| An accepted program of the ownership and lease calculus has no use after move or free, double free, leaked task, aliased argument or data race, under any interleaving, and never gets stuck. | Lean-checked model | `proofs/Cairn/Ownership/` |
| Two threads of an accepted cooperative region never make conflicting accesses between barriers, in any interleaving, and the result does not depend on thread order. | Lean-checked model, differential-tested | `proofs/Cairn/Cooperative.lean`, `evidence/v1_0/cooperative` |
| The lane pool runs each index of a host region once and returns only when no worker is inside. | Lean-checked model | `proofs/Cairn/Region.lean` |
| A guard the compiler leaves out cannot fail where the checker's facts hold. An independent audit decides again, from those facts alone, whether each guard may be left out. Builds that keep every guard and builds that leave guards out agreed on 174,816 cases per compiler. | Lean-checked rule, audited, finite-tested | `proofs/Cairn/Facts.lean`, `evidence/v1_0/guards` |
| A declared layout covers its tile exactly once, so writes through it by distinct threads never collide. | Lean-checked model | `proofs/Cairn/Layout.lean` |
| `compact` lowers to the collector loop, whose one store has no bounds check. The loop's seventeen arithmetic certificates hold, and a model of the loop stores only in bounds. | Lean-checked | `proofs/Cairn/Collector.lean` |
| A host `parallel` region runs level with OpenMP and oneTBB at equal guards and worker counts. | Benchmarked, one machine | `evidence/v1_0/bench` |
| `cairn predict` ranks host timings that its calibration never saw with a Kendall tau of 0.87 to 0.92, at a median error of 28 to 44 percent. | Benchmarked, one machine | `evidence/v1_0/perf_model` |
| Every device example that `--emulate` accepts gives the same results under it as its host build or reference loop, under Clang and GCC. Emulated builds are clean under the address, undefined-behaviour and leak sanitizers, and emulated cooperative regions under the thread sanitizer. | Finite-tested on the host | `evidence/v1_1/emulation` |
| After an edit of one function's body, a check from the record the last check kept gives exactly the answer of a whole check. The suite edits the first, middle and last body of every example, and `CAIRN_INCREMENTAL_EVERY=1` edits every body. | Finite-tested | `tests/verification/test_incremental.py` |

[docs/verification.md](docs/verification.md) says what each proof, model and test covers and what it leaves out.

## The language

- Checked integer arithmetic, explicit conversions, and wrapping forms that say so by name.
- Records, sums with exhaustive `match`, `try` for errors, generics with trait and kind bounds, closures that never escape, modules and projects with vendored dependencies.
- Owners that move and are released at scope exit, `linear` values consumed exactly once, `take`, `swap` and `defer`.
- Tasks with leases down to one field, task groups, I/O rings, atomics and mutexes.
- `parallel`, `reduce`, `compact` and `scan` on host threads or CUDA lanes, plans that change how a region runs without changing its result, and placement in the type (`@host`, `@pinned`, `@unified`, `@device`).
- Cooperative regions with shared memory, barriers, warp shuffles and pipeline stages, checked by the phase rule; layouts with checked coverage; fragments for the tensor cores.
- Alternative implementations of a function, chosen by a plan, with the reference as the fallback.
- Storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) with one stated rounding, `quantize`, and `derive grad`, which writes a function's derivative in reverse mode as ordinary checked code.
- Test blocks and `assert` in the language, and a standard library written in CAIRN: collections, text, formatting, files, sockets, `zlib`, images and 2D drawing.
- `extern` with mandatory effects behind `unsafe`, typed inline assembly for x86-64, AArch64 and PTX, vendored C++ and CUDA as foreign implementations, a freestanding AArch64 target, and a generated C header for calling a CAIRN library from C or C++.

## Documentation and repository

[docs/](docs/README.md) is the reference, [AGENTS.md](AGENTS.md) has the rules for changing this repository, and [CHANGELOG.md](CHANGELOG.md) has the release history. The same documentation is published as a website at https://sammausberg.github.io/cairn/.

```text
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

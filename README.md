# CAIRN 1.1

A systems language in which every cost is visible, borrows cannot dangle, and an AI agent's edit is admitted by the compiler rather than trusted. It compiles to readable, guarded C++20 for CPUs, to CUDA for GPUs from the same source, and to a freestanding image for bare metal. It is **not an entirely proved compiler**; what is proved, tested and merely implemented is kept apart everywhere below.

```cairn
import std.core (Option, Ord);

fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }   // CUDA lanes here; host threads if the views are @host
}

fn largest[T: Ord](n:usize, xs:ro<T>[n]) -> Option[usize] {
  if n == 0 { return Option.None; }
  let mut best:usize = 0;
  for i in 1..n { if less(xs[best], xs[i]) { best = i; } }
  return Option.Some(best);
}

fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(mid, data[0..mid], 0);        // each task leases its half until wait
  let right = spawn fill(n - mid, data[mid..n], 500);
  wait(left);
  wait(right);
}
```

Three rules explain most of it. **Costs are visible**: nothing allocates, synchronizes, moves an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row (`alloc`, `write:out`, `par:device`, `ffi:write`, ...) that `pure` and `effects(...)` can cap. **Borrows are second class**: they exist only as parameters and arguments, so there are no lifetime annotations; owners are affine, `linear` values must be consumed exactly once, a task leases what it borrows until `wait`, and a parallel lane may touch only element `[i]` of anything a lane writes. **Short forms are contracts**: `compact`, `reduce`, `parallel`, `try`, `family` and `derive` expand to inspectable code whose obligations travel with the expansion, and generators are library code (a `recipe` is ordinary declarations with static `each`, `where` and `$name` splices; `derive wire` is twelve lines of CAIRN in `std.wire`); the collector's one unchecked store is justified by certificates that are checked before every emission and proved sound in Lean.

## Build and run

Linux on x86-64 or AArch64, Python 3.11+, Clang or GCC with C++20. Ordinary compilation has no third-party Python dependency. Optional local tools switch on further gates and are never downloaded: `libz3` (source equivalence), CUDA `nvcc` (device programs), Lean 4 (`proofs/`), `qemu-system-aarch64` (the freestanding target).

```sh
python3 bin/cairn doctor
python3 bin/cairn run examples/systems                 # typed-error parser, stack sort, heap pipeline
python3 bin/cairn test examples/systems --cxx g++      # independent finite task contracts
python3 bin/cairn run examples/apps/kvstore            # a storage engine written in CAIRN
python3 bin/cairn run examples/apps/gpu_pipeline       # transfer, lanes, device compaction and reduction
python3 bin/cairn run examples/embedded                # bare-metal AArch64 under QEMU, UART over MMIO
python3 bin/cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all
python3 bin/cairn certificates && (cd proofs && lake build)
python3 bin/cairn fmt --check examples && python3 bin/cairn new my_project
```

| Command | Actual acceptance boundary |
|---|---|
| `check`, `emit` | Syntax, types, ownership, leases, lanes, placement and effects; the C++ is inspectable. |
| `build`, `run` | Fresh native build (`--debug` adds symbols that point at the `.cairn` files); explicit execution with process limits, under QEMU for a freestanding target. |
| `test` | Independent finite task cases, with the child's exit status checked. |
| `certificates` | Seventeen exact affine identities for the collector, checked here by trusted Python and in `proofs/` by Lean. |
| `verify --symbol f` / `--all` | Fixed-reference value equivalence through Z3 on the typed tree (scalars, IEEE floats, records, sums, fixed local arrays, bounded loops), seeing through generics, traits and modules; unsupported entries block aggregate success. |
| `inspect --symbol f` | Source, scope, effects and feature-selected rule cards for an AI edit. |
| `fmt`, `lsp` | Comment-preserving formatter that fails closed; a language server (diagnostics, hover types, symbols, formatting). |

## Repository

```
src/cairn/          syntax, modules, expansion, checking, codegen, toolchain, build, CLI
  runtime/          guards, owners, threads/tasks/atomics, CUDA lanes and collectives (C++ headers)
  std/              the standard library, written in CAIRN (core, vec, map, arena, text, sort, io, net, ...)
  targets/          start-up code and linker script of the freestanding AArch64 board
  agent_tools.py ...  edit sessions, sketches, rule cards, scalar SMT, certificates, verification
proofs/             Lean 4: certificate checker soundness, the 17 certificates, the collector loop model, the ownership/lease calculus
examples/           programs with fixed contracts; apps/ (storage engine, TCP service, simulator, GPU pipeline); embedded/
tests/              rejection, native behavior under both compilers and sanitizers, device, QEMU, agent and tooling tests
tools/ bench/       repeatable validation, context accounting, audit, publication, benchmarks
editors/            VS Code / Cursor extension (grammar + language client)
docs/               language, std, architecture, verification, freestanding, tooling, security, roadmap, history
evidence/           versioned executed results and their limits
```

Start with the [tour](docs/tour.md) (twelve programs that the test suite compiles and runs), then [language](docs/language.md), [std](docs/std.md) with its generated [API reference](docs/std_api.md), [architecture](docs/architecture.md) and [verification](docs/verification.md); [tooling](docs/tooling.md) covers `fmt`, `lsp` and the editor extension, [freestanding](docs/freestanding.md) the bare-metal target. [AGENTS.md](AGENTS.md) gives the edit rules; [capabilities.json](docs/capabilities.json) separates what is implemented from what is missing; [roadmap](docs/roadmap.md) states the remaining gates. `make lint test proof` are the everyday gates; `make gpu embedded` need the hardware and emulator.

## What this does not establish

The Lean result covers the certificate checker, its seventeen certificates, a model of the collector loop, and a core ownership and lease calculus over whole places (no use-after-move, use-after-free, double free, leaked ticket, aliased call argument or race, and one release per cell, under any interleaving). It does not cover the Python that mirrors either checker, the emitter's correspondence to the model, or native code. Lane race-freedom, placement, effects, and the ownership rules for array parts, fields and closures are implemented and tested, including under Address, Leak, UndefinedBehavior and Thread sanitizers and device death tests, but they are not mechanized, and generic code is checked per instance. SMT equivalence trusts its translator and Z3 and rejects memory, loops, sums and floats. The foreign boundary is as safe as its declarations are true.

The GPU and host-parallel numbers in `evidence/v1_0/gpu/` are one machine and three kernels: host regions lose to a sequential loop below roughly ten million cheap elements, and device wins depend on transfer cost. No claim is made against tuned C++ or CUDA. No model was trained or evaluated; context measurements count constructed packets, not model proficiency, and byte counts are not frontier tokenizer counts.

## Private by default

No license has been selected and nothing here is published for reuse. The optional [private publisher](docs/private-publication.md) stays opt-in, private-only and non-force, and is never invoked by tests or builds.

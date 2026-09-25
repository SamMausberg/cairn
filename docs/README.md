# CAIRN documentation

These pages teach the CAIRN language and its tools, from a first program to the proofs behind the checker. The [README](../README.md) says what CAIRN is and how to install it. The same pages are published as a website at https://sammausberg.github.io/cairn/.

The groups below are in reading order. A newcomer starts with the guide, then reads the language reference as the need arises.

## Start here

- [guide.md](guide.md): go from a fresh checkout to a project that builds, runs, tests itself and refuses a wrong edit, then read twelve complete programs, one idea each.
- [examples.md](examples.md): see what each project under `examples/` does and prints, and which of its effect rows are worth reading.

## The language

Each reference page gives every rule with a program the compiler accepts and one it refuses, with the refusal's diagnostic code.

- [language.md](language.md): values, control flow, records, sums, `match` and `try`, constants, tests and printing.
- [memory.md](memory.md): views and parts, owners and moves, linear values and `defer`, effect rows, operand order, `extern` and `unsafe`, record layout and the machine with typed assembly, layouts, and foreign implementations.
- [abstractions.md](abstractions.md): generics, bounds and certified templates, traits, `dyn`, function values and closures, modules, projects, dependencies, recipes, and implementations with their natural parameters.
- [concurrency.md](concurrency.md): tasks and leases, task groups, parallel regions, `reduce`, `compact` and `scan`, atomics and mutexes, plans and fusion, and I/O rings.
- [devices.md](devices.md): placement and device memory, queued device work, the execution context device work runs on, cooperative regions with shared arrays, barriers and pipeline stages, wide loads and stores, emulation on the host, what fast kernels use, and a kernel packaged as a SOL-ExecBench, GPU MODE or KernelBench submission.
- [numerics.md](numerics.md): storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) and their one rounding, `quantize`, the tensor-core multiply and its fragments, atomic float addition, `derive grad`, which writes a function's derivative in reverse mode as ordinary checked code, and what an emulated device run computes.

## The standard library

- [library.md](library.md): use the standard library module by module, with a program for each.
- [std_api.md](std_api.md): look up every signature and effect row in the generated reference, one page per module under `std/`.

## Tools

- [tools.md](tools.md): find every command in one table, then read about each one beyond `check`, `build` and `run`: `fmt`, `test`, `validate`, `doc`, `expand`, `explain`, `predict`, `tune`, `diff`, `export`, `foreign`, incremental builds, the C header, `graph` and Bazel, the language server, `cairn mcp`, the editors, and the device and freestanding targets.
- [agents.md](agents.md): connect an AI agent to the compiler: packets and what they establish, rule cards, what a program drew, the program's state, interface migrations, plan edits, the candidate history, implementation sessions, resuming an investigation, named choices, and the skill, the Claude Code plugin and its MCP server.

## Trust and internals

- [verification.md](verification.md): see what each Lean proof covers (the collector, ownership and leases, task groups, the lane pool, guard elision, layouts and the phase rule), what SMT source equivalence, `cairn diff` and `cairn validate` establish, and what each test covers and leaves out; then a table of every feature and what it has shown: compiled, run on a CPU or a GPU, sanitizer-tested, measured.
- [internals.md](internals.md): learn the compiler's stages, which file owns which rule, the test layers, continuous integration, what an accepted program promises, and how a release is cut.

## Plans

- [roadmap.md](roadmap.md): see what is still open, stated as gates. [project/capabilities.json](project/capabilities.json) states, as data, what is implemented and what is not.

## How the examples are checked

The test suite compiles every fenced `cairn` block in these pages (`tests/language/test_docs_examples.py`). A block tagged `cairn rejects E-CODE` must be refused with exactly that code. Early design drafts were never a source of accepted features. They remain in git history, under `docs/history/` at the tag `v0.8.3`.

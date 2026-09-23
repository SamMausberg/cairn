# CAIRN documentation

The [README](../README.md) says what CAIRN is and how to install it. Read the rest in this order.

| Document | What it gives you |
| --- | --- |
| [guide.md](guide.md) | A fresh checkout to a project that builds, runs, tests itself and refuses a wrong edit, then twelve complete programs, one idea each. |
| [language.md](language.md) | Values, control flow, records, sums, constants and tests. Every rule has a program it accepts and one it refuses with its diagnostic code. |
| [memory.md](memory.md) | Views and parts, owners and moves, linear values, record layout, typed assembly, tile layouts, effect rows, operand order and the foreign boundary, in the same form. |
| [abstractions.md](abstractions.md) | Generics, bounds, traits, `dyn`, closures, implementations, modules, projects, dependencies and recipes. |
| [concurrency.md](concurrency.md) | Tasks and leases, task groups, I/O rings, atomics and mutexes, parallel regions, plans and fusion, `reduce`, `compact` and `scan`. |
| [devices.md](devices.md) | Placement and device memory, queued device work, the execution context device work runs on, and cooperative regions with shared arrays, barriers and pipeline stages. |
| [numerics.md](numerics.md) | Storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) and their one rounding, `quantize`, the tensor-core multiply and its fragments, and `derive grad`, which writes a reverse-mode derivative as ordinary checked code. |
| [library.md](library.md) | The standard library, module by module, with a program for each. [std_api.md](std_api.md) indexes the generated reference: every signature and effect row, one page per module under `std/`. |
| [tools.md](tools.md) | Every command beyond check, build and run: `fmt`, `test`, `validate`, `doc`, `expand`, `explain`, `predict`, `tune`, `diff`, `export`, incremental builds, the C header, the language server, the editors, and the device and bare-metal targets. |
| [examples.md](examples.md) | What each project under `examples/` shows, with its output and the effect rows worth reading. |
| [verification.md](verification.md) | The Lean proofs, the guard-elision rule and its audit, the layout rule, SMT source equivalence, what `cairn diff` and `cairn validate` establish, and the tests, each with what it covers and what it does not. |
| [internals.md](internals.md) | The compiler's stages, which file owns which rule, the test layers, what an accepted program promises, and how a release is cut. |
| [agents.md](agents.md) | The AI edit protocol: packets and what they establish, rule cards, the program's state, interface migrations, plan edits, implementation sessions, the candidate history, named choices, what a program drew, and the skill and Claude Code plugin. |
| [roadmap.md](roadmap.md) | What is still open, stated as gates. [project/capabilities.json](project/capabilities.json) holds the same list as data. |

Every fenced `cairn` block in these files is compiled by `tests/language/test_docs_examples.py`, and a block tagged `cairn rejects E-CODE` must be refused with exactly that code. Early design drafts were never a source of accepted features; they remain in git history, under `docs/history/` at the tag `v0.8.3`.

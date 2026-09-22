# CAIRN documentation

The [README](../README.md) says what CAIRN is and how to install it. Read the rest in this order.

| Document | What it gives you |
| --- | --- |
| [guide.md](guide.md) | From a fresh checkout to a project that builds, runs and refuses a wrong edit, then twelve complete programs, one per idea. |
| [language.md](language.md) | The reference for values, control flow, records, sums, memory, ownership and effects. Every rule has a program that is accepted and one that is refused with its diagnostic code. |
| [abstractions.md](abstractions.md) | Generics, bounds, traits, `dyn`, closures, modules, projects, dependencies and recipes. |
| [concurrency.md](concurrency.md) | Tasks and leases, task groups collected in completion order, atomics and mutexes, parallel regions, `reduce` and `compact`, placement and queued device work. |
| [library.md](library.md) | The standard library, module by module, with a program for each. [std_api.md](std_api.md) is the generated signature and effect-row reference. |
| [tools.md](tools.md) | `cairn fmt`, `check --generics`, `doc`, `expand`, `build --incremental`, `lsp`, the editor extension, and the bare-metal AArch64 target under QEMU. |
| [examples.md](examples.md) | What each project under `examples/` shows, with its output and the effect rows worth reading. |
| [verification.md](verification.md) | The Lean proofs, the SMT source equivalence and the tests, each with what it does and does not cover. |
| [internals.md](internals.md) | The compiler's stages and which file owns which rule, the test layers, what an accepted program promises, and how a release is cut. |
| [agents.md](agents.md) | The AI edit protocol: packets, rule cards, named choices and what an edit can never move. |
| [roadmap.md](roadmap.md) | What is still open, stated as gates. [project/capabilities.json](project/capabilities.json) carries the same as data. |

`history/` holds the 0.2, 0.3 and 0.4 specifications and the provenance of the import: earlier proposals, never a source of accepted features. Every fenced `cairn` block in these files is compiled by `tests/language/test_docs_examples.py`, and a block tagged `cairn rejects E-CODE` must be refused with exactly that code.

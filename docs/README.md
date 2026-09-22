# CAIRN documentation

Start with the [README](../README.md) for what CAIRN is and how to install it, then read in this order.

| Document | What it gives you |
| --- | --- |
| [tour.md](tour.md) | Twelve complete programs, one per idea. The suite builds and runs every one. |
| [language/](language/README.md) | The language reference: every rule, with a program that is accepted and one that is refused, and the diagnostic code it is refused with. |
| [library.md](library.md) | The standard library, module by module, with a program for each. [std_api.md](std_api.md) is the generated signature and effect-row reference. |
| [tools.md](tools.md) | `cairn fmt`, `check --generics`, `doc`, `expand`, `build --incremental`, `lsp` and the editor extension. |
| [freestanding.md](freestanding.md) | Building a bare-metal AArch64 image and running it under QEMU. |
| [examples.md](examples.md) | What each project under `examples/` shows, with its output and the effect rows worth reading. |

What is established, and on what evidence:

| Document | What it gives you |
| --- | --- |
| [verification.md](verification.md) | The Lean proofs, the SMT source equivalence and the tests, each with what it does and does not cover. |
| [safety.md](safety.md) | What an accepted program promises, the foreign boundary, builds and agents. |
| [testing.md](testing.md) | The gates, the test layers, the tools under `tools/` and where evidence is recorded. |

For people changing the compiler:

| Document | What it gives you |
| --- | --- |
| [architecture.md](architecture.md) | The nine stages from manifest to native artifact, and which file owns which rule. |
| [agents.md](agents.md) | The AI edit protocol: packets, rule cards, named choices and what an edit can never move. |
| [releasing.md](releasing.md) | How a release is cut: versions, evidence, the tag, and the private publisher. |
| [roadmap.md](roadmap.md) | What is still open, stated as gates. [project/capabilities.json](project/capabilities.json) carries the same as data. |
| [history/](history/) | The 0.2, 0.3 and 0.4 specifications and the provenance of the import. Earlier proposals, never a source of accepted features. |

Every fenced `cairn` block in these files is compiled by `tests/language/test_docs_examples.py`. A block tagged `cairn rejects E-CODE` must be refused with exactly that code.

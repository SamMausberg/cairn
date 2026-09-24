# CAIRN 1.0.0 run notes

The release gates and the Lean build ran on 2026-09-24 at commit `5e5cfe5`, on a clean tree, with no other agent on the machine: an AMD Ryzen 7 7800X3D (16 threads) under WSL2 (Linux 6.18), Python 3.12.3, g++ 13.3, clang++ 21.1.8, CUDA 13.2 with an RTX 5070 Ti (compute capability 12.0), libz3, oneTBB, Lean 4.34.0 through elan 4.2.1.

## The gates

`summary.json` records eight gates, and all passed: `ruff format --check`, `ruff check`, mypy over `src/cairn`, `cairn fmt --check` over the examples and the library, the suite (4,864 passed, 54 skipped), the collector's seventeen certificates, the Lean certificate bundle in sync, and the SMT module equivalence of `examples/proof_scope`. The skipped tests are the device runs that only `make gpu` starts, the freestanding target, which needs an AArch64 host and QEMU, and tests whose tool is absent. `source_lines` counts tracked files only from this release on; earlier records also counted caches under `tests/`.

## The Lean build

`lean/` is a from-scratch build of `proofs/`: 20 files, 112 declarations audited, 90 depending on `propext` and `Quot.sound` and 21 on `propext` alone, no `sorry` and no `native_decide`, and the ownership regression passing. The build ran right after the gates, so its record marks the tree dirty: the only change was the gates' own `summary.json`.

## What 1.0.0 added, and how each was checked here

Each record under this directory says what ran for its subject; these are the ones this release adds after the September 22 candidate (`gates_2026_09_22/`).

- Alternative implementations, parameterized implementations, `cairn validate` and the implementation session: rejection tables for every `E-IMPL-*` code, native runs under both compilers with the address and undefined sanitizers, generated boundary cases against the reference (`implementations/`).
- `cairn tune`'s bounded search, its candidate history and `--compare` (`search/`); cooperative regions priced by `cairn predict`, with the checker's shared memory equal to ptxas's on eleven kernels (`predict_cooperative/`).
- Cooperative regions and pipeline stages: host runs under ThreadSanitizer with both compilers, a removed barrier reported as a race, the Lean phase rule compared with the checker on generated regions (`cooperative/`).
- Layouts and tensor-core fragments, and two tensor-core multiplies checked on the host against an f64 reference under both compilers (`tensor/`); typed assembly run natively on x86-64 and foreign C++ validated against its reference (`foreign/`).
- The device target and the execution context, counted on a host stand-in for the CUDA runtime (`execution/`); `cairn export` refusing a tampered tree.
- The Agent Skill, the Claude Code plugin and `cairn mcp`, with a six-session smoke comparison (`skill/`).
- An adversarial review of all of it: 21 defects, each fixed with its regression test, and 78 refused attacks kept (`review_implementation_layer/`).
- A fresh clone of this tree ran the suite (4,864 passed, 54 skipped), every demo, every template, the wheel installed into a clean environment, and the plugin installed from its marketplace into an empty Claude Code configuration, with its language server and MCP server answering.

## What was not done

No device code ran for this release. Every device feature added since 0.8.3 compiles for sm_120 and is checked on the host; `make gpu`, which runs the device tests, has not run since the 0.8.3 record. GitHub's CI could not start jobs during the release (an account billing block), so these local gates are the release's verification. The AI benchmark was not repeated with the plugin, and the plugin's `claude plugin eval` suite (`bench/skill`) has not run. The preregistered CPU suite was not rerun for this commit; `bench/` is its run of 2026-09-22 at `a090489`. Typed AArch64 assembly is checked and lowered but was not built, and the freestanding target did not run, since this machine is x86-64 without QEMU. Three cooperative rules (arrays from outside a region, pipeline stages, warp collectives) are checked by finite tests only, with no Lean model.

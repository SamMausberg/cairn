# CAIRN 1.1.0 run notes

The release gates ran on 25 September 2026 at commit `ec0a1dd` (main after #123), on a clean tree, with nothing else running on the machine: an AMD Ryzen 7 7800X3D (16 threads) under WSL2 (Linux 6.18), Python 3.12.3, g++ 13.3.0, clang++ 21.1.8, CUDA 13.2 with an RTX 5070 Ti (compute capability 12.0, Windows driver 596.49), libz3, Lean 4.34.0 through elan 4.2.1. The same commit passed every job of CI's run 36175764565 on GitHub: the suite, CUDA 12.9 and 13.2 device builds under both host compilers, GCC 11 with Clang 13 and GCC 15 with Clang 23, Pythons 3.11 to 3.14, an AArch64 host with the freestanding image under QEMU, and the installed package.

## The gates

`summary.json` records eight gates, and all passed: `ruff format --check`, `ruff check`, mypy over `src/cairn` (163 files), `cairn fmt --check` over the examples and the library, the suite (5,856 passed, 68 skipped, in 4 min 52 s at 16 workers), the collector's certificates, the Lean certificate bundle in sync, and the SMT module equivalence of `examples/proof_scope`. The skipped tests are the device runs that only `make gpu` starts and tests whose tool is absent here, such as `qemu-system-aarch64`.

`summary.json`'s `compiler_core` counts `src/cairn/compiler/*.py` and `src/cairn/projects/*.py`, a pattern written before 1.1 moved the compiler into subpackages, so at this commit it counts only the facade, the compile cache and `projects/`: 2,921 lines. The compiler is 15,283 lines (`src/cairn/compiler/**/*.py`), 18,889 with `projects/`. The evidence commit changes the collector's pattern.

## Beside the gates

- `make native`: every check of `tools/checks/verify.py --gcc --sanitize` passed, 14 commands under both compilers with the sanitizers, in 34 min; `bench/codegen/codegen_only.py` found identical function sections and relocations for 8 of 9 kernels.
- `make systems`: the systems programs validated, and `examples/systems` ran and tested.
- `make proof`: the Lean build, the ownership differential against the checker, the certificates and the module equivalence.
- `make audit`: 7,357 distinct blobs of every reachable commit scanned, no finding.
- `make wheel`: `cairn_language-1.1.0-py3-none-any.whl`, sha256 `f9617970a0964781e2e3e46ca5339bebbc4f7cf047732ad57baa8b0839a6e5a3`.
- The demos, `make demo`, `demo-repair`, `demo-numeric`, `demo-visual` and `demo-implement`: each ran and printed what its README says. The repair host refused the debug print (`E-EFFECT-EXPANSION`) and the tidy-up (`E-PRESERVE`); the numeric plate stayed 0.0000148 from its f64 reference against a bound of 0.0048; the visual demo's test passed; the implement host refused the looser tolerance (`E-TOLERANCE`), validation shrank the dropped tail to `n = 5`, and `cairn tune` ranked the valid implementations.
- `lean/`: a from-scratch build of `proofs/` (the build directory removed first, 21 modules built), 112 declarations audited, depending on `propext` and `Quot.sound` and nothing else, no `sorry`.

## The device session

`gpu/` records the release's one `make gpu` session on the RTX 5070 Ti, at `23f4c98`, before #121 to #123. #121 changed version strings and documentation, #122 changed only the export path, which no device test reaches, and #123 is the fix for two of the session's three failures; the three tests that failed ran again from #123's branch. 48 of the suite's 52 device-run cases passed; the three that trap on purpose were left out, and Compute Sanitizer cannot instrument the GPU under WSL2.

## What 1.1.0 added, and how each was checked

Each record under this directory ([index](README.md)) says what ran for its subject. The work of the 1.1 release campaign landed through pull requests #66 to #123, each merged only when CI's `ci-passed` check was green; CHANGELOG.md's 1.1.0 section lists what each changed. Emitted C++ was held byte-identical by `tools/checks/emission_identity.py` across the package layout (#67, #69), the incremental check (#101, #114, #119) and every refactor that said so; the changes that meant to change emission say which programs changed (#94, #98, #104).

## What was not done

- The 1.1 evaluation stopped at 54 of its 156 subjects (`ai_eval/`), and no model ran on 1.1.0, so nothing here shows that agents spend fewer tokens on it. `friction/` measures the compiler on the programs the evaluation's subjects wrote, not an agent.
- `make bench`, the preregistered CPU suite, and `make scale` did not run for this release; no host performance claim changes.
- `make embedded` did not run: `qemu-system-aarch64` is not installed here. CI's AArch64 job ran the freestanding image under QEMU.
- On the GPU: Compute Sanitizer, the tests that trap on purpose, pipeline stages, layouts in code, asserts and gradients in a lane, `compact`, `bench/gpu/parallel_gpu.py`, and every `bench/device` pair but `reduction` (`gpu/`).
  Corrected on 2026-09-25: pipeline stages, layouts in code and the device `compact` did run in that session, in tests that passed (`gpu/make_gpu_tests.txt`). What did not run on the GPU is Compute Sanitizer, the tests that trap on purpose, storage floats, asserts and gradients in a lane, `bench/gpu/parallel_gpu.py`, and every `bench/device` pair but `reduction`.
- `make calibrate-device` and `make tune-device` did not run; no device card is measured.

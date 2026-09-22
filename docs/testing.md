# Testing


Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

## Everyday gates

```sh
make lint          # ruff format --check, ruff check, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, the Lean export drift check, lake build, the differential run, scalar module equivalence
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make proof` needs `lake` on `PATH`; elan installs it in `~/.elan/bin`. Its Lean half prints the axioms behind every theorem, and only `propext` and `Quot.sound` are allowed:

```text
info: Cairn/Audit.lean:21:0: 'Cairn.check_sound' depends on axioms: [propext, Quot.sound]
```

`make proof` runs only the positive coverage case. Run the negative one by hand: `cairn verify examples/proof_scope/mixed.cairn examples/proof_scope/mixed.cairn --all` must come back incomplete and nonzero, because a function that moves an owner cannot inherit a scalar pass. Record both outcomes. Neither implies a successful Lean build; [verification](verification.md) has the boundaries.

## What each folder establishes

Run one folder with `python -m pytest -q tests/soundness -n auto`.

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted breadth of the language, built and run natively under both compilers; the twelve tour programs; every `cairn` block in README.md and under `docs/` |
| `soundness/` | every hole an audit found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, `checking.py` and the Lean calculus classifying generated programs alike, the SMT translator against concrete replay, coverage that no single function can confer |
| `projects/` | manifests, vendored dependencies, incremental builds, the five applications, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the self-checking binaries in `tests/native/`, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus whitespace and comment fuzz, a real `cairn lsp` subprocess, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets, rule cards, sketches, guarded edits, and the canonical projection round-tripping every sample and `std` module to identical native code |
| `oracles/`, `native/` | not pytest modules: the Python oracles `tools/checks/verify.py` drives, and the C++ and CUDA fixtures `runtime/` compiles |

Sanitizers run where they bite: the ownership program under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. Death tests must abort the host, one per invocation: a guard that fires inside a CUDA lane, an unawaited ticket. A task whose child exits abnormally has failed, whatever it printed. Examples are gates too, and `cairn run examples/systems --memory-mib 1024` with `cairn test examples/systems --cxx g++` drives a typed decimal parser and a stack and heap sorting and filter pipeline whose contracts inspect meaningful outputs rather than a successful compilation.

## Rejection and behaviour tables

A rejection table maps a sentence naming the rule to a diagnostic code and a program. One parametrized test compiles each entry and requires exactly that code, so a rule that stops biting fails by name. `tests/soundness/test_soundness.py` holds 92 entries over 25 codes; smaller tables sit beside the feature they guard. Every safety rule has one.

A native behaviour table maps a sentence to an expected process exit status and a program. The test emits C++ for that entry point alone, builds under clang++ with `-fsanitize=address,undefined`, runs it, and requires exactly that status: `0` where the program judges itself, `-6` where a guard must abort. Fifteen entries follow the second audit.

## The tools

```sh
python tools/checks/verify.py --gcc --sanitize
python tools/checks/validate_systems.py
python tools/checks/validate_semantics.py --gcc
make bench                                  # the preregistered CPU baseline suite, hours; --smoke takes seconds
```

| Script under `tools/checks/` | What it checks |
|---|---|
| `verify.py` | rebuilds the native artifacts and drives the oracles in `tests/oracles/`: equal boundary checks, strict floating flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an O0 observer counting allocation and release across returns, loops and match exits |
| `validate_semantics.py` | the translator and trap-aware interpreter against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and input-pinned SMT |
| `semantic_check.py`, `semantic_corpus.py` | two scalar implementations against one immutable reference; same-contract pairs, every label decided and replayed |
| `curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one hand-authored defect per algorithm family, all of which the finite tests must catch |
| `check_compact_forms.py`, `native_scalar.py` | complete definitions, not generated expansions; a test-only trap observer that is not the production runtime |
| `density.py`, `export_lean_certificates.py` | lexical density accounting; `--check` fails when `collector_rules()` and `proofs/` have drifted |
| `differential_ownership.py` | generated programs of one shared fragment, rendered as CAIRN source and as Lean `Program` literals, and required to be classified identically by `checking.py` and by the Lean `accepts` |
| `bench/suite/harness.py` | the eight kernels of [bench/suite/PREREGISTRATION.md](../bench/suite/PREREGISTRATION.md), every arm built under both compilers with the project's own flags, each baseline once guarded and once not, safety boundaries counted against the build receipt, equal worker counts, and no result written when a case disagrees with its sequential or Python oracle |
| `bench/suite/report.py` | reads one of those runs and prints its tables, applying the preregistered acceptance rule; it measures nothing and prints losses beside wins |

Production sanitizer and SIGABRT fixtures are separate from that O0 observer, `validate_systems.py` proves nothing about allocation or lifetime safety for arbitrary programs, and a trusted translator or oracle can still hold a bug. `bench/cpu/codegen_only.py` compares code sections by instruction bytes and relocations and implies no fresh timing run: five of its nine selected function sections were byte-identical to the C++ references on AArch64 at 1.0, where the 0.6 figure of eight of nine was x86-64 under another compiler. These harnesses write under `results/`, which is ignored and may be replaced on rerun. One subdirectory per kind of output, and nothing at the top:

| Directory | What lands there |
|---|---|
| `results/native/` | the one shared build area: generated `.cpp`, every runtime header, receipts, objects, libraries, the section dumps, the `sanitize` and `benchmark` executables, and `verification_run.json`. `bench/cpu/reference.cpp` and `family_template.cpp` include its `cairn_runtime.hpp`, so every harness that compiles them builds here |
| `results/checks/` | the child-harness output `verify.py` captures, one file per command |
| `results/codegen/` | `codegen.json` from `bench/cpu/codegen_only.py` |
| `results/timing/` | the paired timing run: summary, raw CSV, section equivalence and the environment it was measured in |
| `results/gpu/` | `benchmark.json` and its executable from `bench/gpu/parallel_gpu.py` |
| `results/host_regions/` | where `bench/host_regions/host_regions.py` builds; its record goes to `--out` |
| `results/semantics/`, `results/systems/`, `results/density/`, `results/context/` | one record each from `validate_semantics.py`, `validate_systems.py`, `density.py` and `measure_context.py` |
| `results/agent/` | the agent and curriculum records from `tools/ai/` plus `mutation_checks.py` and `curriculum_verify.py` |
| `results/bench_suite/` | every arm's build and the raw timings of `bench/suite/harness.py`, and the tables `report.py` prints from them |

`tools/ai/measure_context.py` keeps its counterfactual honest: the same current full JSON packet for each legacy-profile function, with only the rule-card text swapped for the preserved 0.5 text, and new-feature packets reported apart because they have no executable 0.5 baseline. Its counts are exact plain ByT5 bytes with no special tokens; `--tiktoken o200k_base` needs a separately installed package and vocabulary and is reported only if it ran. Packet sizes are not logged conversations, training gains or comprehension scores, and wider feature coverage can enlarge the full card while common packets shrink.

## Evidence

`python tools/release/collect_evidence.py --release v1_1` runs the release gates (format, lint, mypy, `cairn fmt --check`, the suite, certificates, the Lean drift check, module equivalence) and writes `evidence/<release>/summary.json`: each gate's command, status, exit code, seconds and last three output lines, plus `cairn doctor`, the commit, whether the worktree was dirty, and source-line counts. A missing tool is `unavailable`, a timeout `timed-out`, and nothing is retried. It lists `lean/`, `gpu/` and `embedded/` under `separately_recorded` but writes none of them: `tools/release/collect_lean_evidence.py` rebuilds `proofs/` from scratch and records the build, the axiom audit and the toolchain, the embedded transcript comes from `cairn run examples/embedded`, and the GPU record is copied into the release directory from `results/gpu/benchmark.json`, since `bench/gpu/parallel_gpu.py` writes only there and never into `evidence/`.

`evidence/` holds one directory per release. `summary.json` is the entry point of each, `v1_2/RUN_NOTES.md` says what ran, on which machine, and what was not done, and every directory keeps the names and source identity it was recorded under. `v1_0/` also holds `lean/`, `gpu/` and `embedded/` records, `v1_1/` the preregistered `ai_pilot/`, and `v1_2/host_regions/` a later measurement of host parallel regions that supersedes the host-parallel column of `v1_0/gpu/benchmark.json`. Earlier releases are history, not fresh measurements, and no old result confers verification on new source: `collect_evidence.py` writes a new directory rather than reusing one. `evidence/v0_5/verification-run.json` is a retained child-command log from the 0.5 layout, run on an x86-64 host.

`docs/history/BASELINE.json` and `UPSTREAM.json` name the archives this source was built from, each by sha256, with the commit and tag the import started from and the standing policy that no prior result is a new measurement.

Distribution is its own gate. Build the wheel offline, install it outside the source checkout, run the native examples, the independent contracts and both verification modes, and compare every packaged source and runtime byte with the repository. Verify the Git bundle, extract the final ZIP, repeat the unit and example gates. A worktree success is not an installed-package success.

## Failure policy

A failed, timed-out or interrupted command is recorded as such. An unchanged rerun is a new result, not erasure of the failure. A task runner needs a good child exit as well as its JSON result. Process limits are not a sandbox. Whole-compiler correctness, native refinement, model proficiency, GPU performance and C++-breadth completeness each need evidence that these finite gates do not give.

## Inherited teaching fixtures

`training/` holds the inherited lessons. `source/` holds the 0.3 program lessons that 0.4 retained. `semantic/` holds the 0.4 same-contract scalar preferences, obligations and protocol repairs. These are inputs kept for audit, not new results. Historical compiler hashes, solver results, packet IDs and version labels may still refer to their original release, so re-run the generator and checker for a new admission record rather than relabelling an old receipt.

`tools/ai/curriculum.py` regenerates `source/`; `tools/checks/semantic_corpus.py` and `tools/ai/protocol_curriculum.py` regenerate `semantic/`. `tools/checks/curriculum_verify.py` and `tools/checks/mutation_checks.py` check `source/` against independent finite oracles.

No model was trained. Answers and oracles are included here and are not secret held-out data. Some older static contrast lessons change their API or meaning, so do not reward them as contract-preserving repairs.

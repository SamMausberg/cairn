# Testing

Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

## Everyday gates

```sh
make lint          # ruff format --check, ruff check, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, the Lean export drift check, lake build, scalar module equivalence
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make proof` needs `lake` on `PATH`; elan installs it in `~/.elan/bin`. Its Lean half prints the axioms behind every theorem, and only `propext` and `Quot.sound` are allowed:

```text
info: Cairn/Audit.lean:21:0: 'Cairn.check_sound' depends on axioms: [propext, Quot.sound]
```

`make proof` runs only the positive coverage case. Run the negative one by hand: `cairn verify examples/proof_scope/mixed.cairn examples/proof_scope/mixed.cairn --all` must come back incomplete and nonzero, because a function that moves an owner cannot inherit a scalar pass. Record both outcomes. Neither implies a successful Lean build; [verification.md](verification.md) has the boundaries.

## What each folder establishes

Run one folder with `python -m pytest -q tests/soundness -n auto`.

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted breadth of the language, built and run natively under both compilers; the twelve tour programs; every `cairn` block in README.md, `docs/guide/` and `docs/internals/` |
| `soundness/` | every hole an audit found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, the SMT translator against concrete replay, coverage that no single function can confer |
| `projects/` | manifests, vendored dependencies, incremental builds, the five applications, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the self-checking binaries in `tests/native/`, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus whitespace and comment fuzz, a real `cairn lsp` subprocess, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets, rule cards, sketches, guarded edits, and the canonical projection round-tripping every sample and `std` module to identical native code |
| `checks/`, `native/` | not pytest modules: the Python oracles `tools/checks/verify.py` drives, and the C++ and CUDA fixtures `runtime/` compiles |

Sanitizers run where they bite: the ownership program under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. Death tests must abort the host, one per invocation: a guard that fires inside a CUDA lane, an unawaited ticket. A task whose child exits abnormally has failed, whatever it printed. Examples are gates too, and `cairn run examples/systems --memory-mib 1024` with `cairn test examples/systems --cxx g++` drives a typed decimal parser and a stack and heap sorting and filter pipeline whose contracts inspect meaningful outputs rather than a successful compilation.

## Rejection and behaviour tables

A rejection table maps a sentence naming the rule to a diagnostic code and a program. One parametrized test compiles each entry and requires exactly that code, so a rule that stops biting fails by name. `tests/soundness/test_soundness.py` holds 82 entries over 25 codes; smaller tables sit beside the feature they guard. Every safety rule has one.

A native behaviour table maps a sentence to an expected process exit status and a program. The test emits C++ for that entry point alone, builds under clang++ with `-fsanitize=address,undefined`, runs it, and requires exactly that status: `0` where the program judges itself, `-6` where a guard must abort. Fifteen entries follow the second audit.

## The tools

```sh
python tools/checks/verify.py --gcc --sanitize
python tools/checks/validate_systems.py
python tools/checks/validate_semantics.py --gcc
```

| Script under `tools/checks/` | What it checks |
|---|---|
| `verify.py` | rebuilds the native artifacts and drives the oracles in `tests/checks/`: equal boundary checks, strict floating flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an O0 observer counting allocation and release across returns, loops and match exits |
| `validate_semantics.py` | the translator and trap-aware interpreter against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and input-pinned SMT |
| `semantic_check.py`, `semantic_corpus.py` | two scalar implementations against one immutable reference; same-contract pairs, every label decided and replayed |
| `curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one hand-authored defect per algorithm family, all of which the finite tests must catch |
| `check_compact_forms.py`, `native_scalar.py` | complete definitions, not generated expansions; a test-only trap observer that is not the production runtime |
| `density.py`, `export_lean_certificates.py` | lexical density accounting; `--check` fails when `collector_rules()` and `proofs/` have drifted |

Production sanitizer and SIGABRT fixtures are separate from that O0 observer, `validate_systems.py` proves nothing about allocation or lifetime safety for arbitrary programs, and a trusted translator or oracle can still hold a bug. `bench/cpu/codegen_only.py` compares code sections by instruction bytes and relocations and implies no fresh timing run. These harnesses write under `results/`, which is ignored and may be replaced on rerun.

`tools/ai/measure_context.py` keeps its counterfactual honest: the same current full JSON packet for each legacy-profile function, with only the rule-card text swapped for the preserved 0.5 text, and new-feature packets reported apart because they have no executable 0.5 baseline. Its counts are exact plain ByT5 bytes with no special tokens; `--tiktoken o200k_base` needs a separately installed package and vocabulary and is reported only if it ran. Packet sizes are not logged conversations, training gains or comprehension scores, and wider feature coverage can enlarge the full card while common packets shrink.

## Evidence

`python tools/release/collect_evidence.py --release v1_1` runs the release gates (format, lint, mypy, `cairn fmt --check`, the suite, certificates, the Lean drift check, module equivalence) and writes `evidence/<release>/summary.json`: each gate's command, status, exit code, seconds and last three output lines, plus `cairn doctor`, the commit, whether the worktree was dirty, and source-line counts. A missing tool is `unavailable`, a timeout `timed-out`, and nothing is retried. The `lean/`, `gpu/` and `embedded/` records come from the harnesses that produce them; see [evidence/README.md](../../evidence/README.md).

Distribution is its own gate. Build the wheel offline, install it outside the source checkout, run the native examples, the independent contracts and both verification modes, and compare every packaged source and runtime byte with the repository. Verify the Git bundle, extract the final ZIP, repeat the unit and example gates. A worktree success is not an installed-package success.

## Failure policy

A failed, timed-out or interrupted command is recorded as such. An unchanged rerun is a new result, not erasure of the failure. A task runner needs a good child exit as well as its JSON result. Process limits are not a sandbox. Whole-compiler correctness, native refinement, model proficiency, GPU performance and C++-breadth completeness each need evidence that these finite gates do not give.

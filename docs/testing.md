# Reproduce CAIRN 1.0 evidence

Results for this release live in evidence/v1_0; evidence/v0_6 and earlier are historical. Historical teaching labels and old benchmarks are not new tests. All commands below run locally and perform no publication or credential setup.

## Everyday gates

```sh
make lint          # ruff format --check, ruff check, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, scalar module equivalence, export drift check and `lake build`
make gpu embedded  # CUDA runtime/lanes and the QEMU board, where available
```

The suite runs every accepted 1.0 construct natively under clang++ and g++, the ownership program under Address/Leak/UndefinedBehavior sanitizers, the task program under ThreadSanitizer, the runtime headers' own self-checking binaries (including death tests: a guard that fires inside a CUDA lane must abort the host, an unawaited ticket must trap), the freestanding image under QEMU with an exact UART transcript, the formatter over every `.cairn` file plus randomized whitespace/comment fuzz, and a real `cairn lsp` subprocess. Every safety rule has a rejection test naming its diagnostic code. The canonical projection must round-trip every sample and `std` module to identical native code.

## Fast and example gates

```sh
python3 -m pytest -q tests
python3 bin/cairn run examples/hello
python3 bin/cairn test examples/hello
python3 bin/cairn run examples/systems --memory-mib 1024
python3 bin/cairn test examples/systems --cxx g++
```

The fast suite covers native syntax, source spans, project validation, effect boundaries, local owners, exhaustive results, loop targeting, edits, scalar coverage, exact certificates and mocked private publication. `examples/systems` has a typed decimal parser and a stack/heap sorting/filter pipeline; its tests inspect meaningful outputs rather than successful compilation alone.

## Independent systems tests

```sh
python3 tools/validate_systems.py
```

This checks decimal values and first error offsets against a Python oracle, sorts against sorted(), filters against a list oracle, verifies unchanged inputs and output tails, and runs both compilers. A separate O0 observer counts allocation/release across returns, loops and match exits. Production-runtime sanitizer and SIGABRT fixtures are separate from that observer. It also verifies identity edits across the new syntax. It does not prove allocation/lifetime safety for arbitrary programs.

## Existing native and arithmetic gates

```sh
python3 tools/verify.py --gcc --sanitize
python3 bench/codegen_only.py
python3 tools/validate_semantics.py --gcc
```

The old native harness preserves equal boundary checks, strict floating flags, both compilers, independent codec/template cases and exhaustive small collectors. Code-section comparison counts instruction bytes and relocations; no fresh timing run is implied. Arithmetic validation uses a test-only trap observer, independent concrete arithmetic and input-pinned SMT checks. A trusted translator or oracle can still contain a bug. All harness-generated files under results/ are ignored and may be replaced on rerun.

## Certificate and coverage gates

```sh
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
python3 bin/cairn verify examples/proof_scope/mixed.cairn \
  examples/proof_scope/mixed.cairn --all
```

The first module comparison should succeed in the scalar source model, views and all. The second must return incomplete/nonzero, because a function that moves an owner cannot inherit a scalar pass. Both outcomes are recorded. No successful Lean build is implied by either gate. verification.md states the full boundaries.

## Context accounting

```sh
python3 tools/measure_context.py
python3 tools/density.py
```

measure_context.py uses the same current full JSON packet for each legacy-profile function and changes only rule-card text to the preserved 0.5 text for its counterfactual. All other source, type/effect, task, hash and transport fields remain identical. It separately reports new-feature packets, which have no executable 0.5 comparison. The default tokenizer is exact plain ByT5 byte mapping with no special tokens. With a separately installed package/vocabulary, `--tiktoken o200k_base` measures that encoding instead. No such optional result is reported unless it ran. Packet sizes are not logged AI conversations, training gains or comprehension scores. Widening feature coverage can enlarge the full card even while common packets shrink.

## Distribution, history and failure policy

Build the wheel offline, install outside the source checkout, run native examples/independent contracts and both verification modes, and compare all packaged source/runtime bytes with the repository. Verify the Git bundle, extract the final ZIP and repeat unit/example gates. A worktree success is not an installed-package success.

A failed, timed-out or interrupted command must be recorded as such. An unchanged rerun is a new result, not erasure of the failure. Task runners require a good child exit as well as their JSON result. Limits are not a sandbox. Whole-compiler correctness, native refinement, model proficiency, GPU performance and C++-breadth completeness each require evidence absent from these finite gates.

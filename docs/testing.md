# Reproducing checks

The executable results for this revision are in evidence/. Do not promote old training labels or old report numbers to new measurements.

## Fast development gate

```sh
python3 -m pytest -q tests
python3 bin/cairn check examples/hello
python3 bin/cairn run examples/hello
python3 bin/cairn test examples/hello
```

Tests exercise parse/type rejection, alias/effect restrictions, sealed edits, scalar comparison, project path validation, source mapping, fresh output directories, both native compilers, and fail-closed publication. Publication tests use fake GitHub responses and temporary local Git repositories, never a live remote.

## Native regression gate

```sh
python3 tools/verify.py --gcc --sanitize
python3 bench/codegen_only.py
```

This retained measurement harness deliberately writes generated fixtures under results/ and can overwrite those ignored results. It is separate from the normal fresh-directory CLI builder. It tests ordinary functions and families, abort cases, exhaustive short collectors, wire encoding/decoding, independent C++ template values, and ASan/UBSan. Code-section comparison includes relocation entries. Timing is optional and is not implied by byte equality.

The aggregate harness can take longer than an interactive command limit. If a command is interrupted, record the successful completed children, run the remaining children explicitly, and never rewrite the interruption as a pass. evidence/RUN_NOTES.md records the actual continuation for this release.

## Scalar verification gate

```sh
python3 bin/cairn verify examples/sketch/reference.cairn   examples/hello/src/math.cairn --symbol average
python3 tools/validate_semantics.py --gcc
```

The first query compares all declared-width scalar inputs under a fixed total reference. The larger arithmetic validator uses test-only trap instrumentation for repeated observation; it is not a proof of native abort behavior. Memory, loops and floating point are outside scalar equivalence. Missing solver or undecided obligations are unknown.

## Distribution gate

Build the wheel offline with installed build tools, install it in a fresh environment outside this checkout, and execute `cairn new`, `check`, `run`, `test` and `verify`. The wheel must include the runtime header. Clone the Git bundle into a fresh directory; verify its object database and repeat the fast gate. Source-tree tests alone do not establish that an installed package works.

## Meaning of results

A typecheck checks the prototype's implemented static rules. A finite test observes supplied inputs only. A sanitizer run is dynamic checking, not unconditional memory safety. SMT equivalence trusts the translator and solver within its explicit fragment. Identical native sections under named flags are neither a benchmark nor proof of equivalence on a different target. No tested stage upgrades itself into a Lean proof or an AI proficiency result.

# CAIRN 0.6

An executable CPU language prototype with explicit memory, typed error results, a C++20 backend, and compiler-guided AI edits. This release adds scoped heap/stack buffers, exhaustive matching, loop control, exact arithmetic certificates, and whole-module scalar-verification coverage. It is **not a full C++ replacement or an entirely proved compiler**.

```cairn
enum Division { Value(u64); ZeroDivisor; }

fn divide(x:u64, y:u64) -> Division {
  if y == 0 { return Division.ZeroDivisor; }
  return Division.Value(x / y);
}

fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);
```

A returned error is data, not an abort. Match every variant explicitly. The arithmetic identity avoids intermediate overflow; the restricted scalar checker can compare it with a fixed reference. Neither its result nor a compiler typecheck proves the complete native toolchain.

## Build and run

Tested host: Linux x86-64, Python 3.11+, Clang 17 or GCC 14.2 with C++20. Ordinary compilation has no third-party Python runtime dependency. Scalar equivalence additionally needs a locally installed Z3 shared library. There are no automatic downloads, model endpoints, or credentials.

```sh
python3 bin/cairn doctor
python3 bin/cairn run examples/systems
python3 bin/cairn test examples/systems --cxx g++
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
python3 bin/cairn new my_project
```

`examples/systems` is a real multi-file executable: a decimal parser returns typed error offsets, a byte sorter uses a fixed stack histogram, and a filtering pipeline uses one explicit heap buffer. It has independent expected outputs. It does not depend on an unimplemented language I/O library. Exit zero denotes the example's success.

Install the supplied wheel with `python3 -m pip install --no-index --no-deps /path/to/cairn_language-0.6.0-py3-none-any.whl`. An installed `cairn` command exposes the same interface as `python3 bin/cairn`.

## Explicit storage, compact source

```cairn
fn sorted_even(n:usize, out:rw<u8>[n], input:ro<u8>[n]) -> usize {
  buffer scratch:u8[n] = zeroed;
  sort_bytes(n,scratch,input);
  let used = compact out for i in len(scratch)
    where (scratch[i] & 1) == 0 yield scratch[i];
  return used;
}
```

`sort_bytes` is implemented in `examples/systems/src/sort.cairn`, not an invented library call. `buffer` allocates and initializes storage; `stack counts:usize[256] = zeroed;` reserves fixed local storage. `len` reads extent metadata. Owners cannot escape, be copied, or be returned in this profile. Normal scope exit, return, break and continue release scoped heap storage. Aborts do not promise cleanup. All reads and writes retain the native checks; the collector's structurally bounded output store is the existing specialized exception.

## Commands and evidence

| Command | Actual acceptance boundary |
|---|---|
| `check`, `emit` | Native syntax/types/effects; C++ emission is inspectable. |
| `build`, `run` | Fresh native library/executable; explicit execution with process limits. |
| `test` | Independent finite task cases, with child exit status checked. |
| `certificates` | Seventeen exact linear identities for bounded-collector arithmetic, checked by trusted Python. |
| `verify ... --symbol f` | Selected pure integer/Boolean function, fixed-reference equivalence through Z3. |
| `verify ... --all` | Every declared function and public type census must satisfy the scalar policy; unsupported entries block aggregate success. |
| `inspect ... --symbol f` | Source, scope, effects, and feature-selected instructions for an AI edit. |

Use `--cxx g++` for GCC. Baseline `x86-64` is the CLI default; `--arch x86-64-v3` is explicit. `run` defaults to a 1024 MiB virtual-address-space limit, configurable with `--memory-mib`. Limits are not a security sandbox. Shared-library users must impose their own execution limits.

## Repository

```
src/cairn/       syntax, expansion, checking, emission, CLI, editing and scalar SMT
  runtime/      guarded views, arithmetic and scoped storage
examples/       programs and fixed behavioral test contracts
tests/          rejection, independent behavior, protocol and distribution tests
tools/          repeatable validation, context accounting, audit and publication
bench/          ordinary C++ references; no expert-baseline label
docs/           current language/verification guides and explicitly historical specs
training/       inherited teaching fixtures; no trained model is shipped
evidence/       versioned executed results and limitations
```

Start with [language](docs/language.md), [verification](docs/verification.md), and [testing](docs/testing.md). [AGENTS.md](AGENTS.md) gives the edit rules. The capability ledger at [docs/capabilities.json](docs/capabilities.json) distinguishes implemented features from missing ones. `make test`, `make systems`, `make proof`, and `make native` are independent gates.

## Local and private

All changes in this delivery remain local on `work/v0.6`; the previous `main` and `v0.5.0` are retained. No GitHub repository was created, no code was uploaded, and no remote workflow was run. The optional [private publisher](docs/private-publication.md) remains opt-in, private-only, and non-force. It is not invoked by tests or builds. No public fallback or license was selected.

## What this does not establish

Exact arithmetic certificates do not establish parser/type soundness, lifetime safety, stable-selection correctness, or native refinement. SMT equivalence trusts its translator and solver and rejects memory, loops, sums, floating point, and concurrency. There is no successful Lean build or axiom audit. General owners/containers, generic results, namespaces/separate compilation, OS libraries, CPU concurrency and GPU lowering remain unimplemented.

No model was trained or evaluated. Context measurements count complete constructed packets, not model proficiency. Byte-token counts are not frontier BPE counts; no general 100x compression is claimed. Native section equality is not a timing benchmark or universal C++ performance guarantee.

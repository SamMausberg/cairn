# CAIRN 0.5

A runnable CPU language prototype with a checked frontend, a C++20 backend, and compiler-guided AI editing. This repository turns the 0.4 research artifact into an installable developer tool. It is not a full C++ replacement or a verified compiler.

```cairn
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

fn select_even(n:usize, out:rw<u64>[n], input:ro<u64>[n]) -> usize {
  let used = compact out for i in n where (input[i] & 1) == 0 yield input[i];
  return used;
}
```

`average` avoids intermediate overflow. `select_even` writes into caller-owned storage, allocates nothing, preserves order, and leaves the unused output tail unchanged. The shorter syntax uses the same checked AST and runtime as its explicit form.

## Build and run now

Requirements: Python 3.11 or newer, Linux x86-64, and Clang or GCC with C++20 support. The compiler has no third-party Python runtime dependencies. Optional scalar equivalence requires a locally installed Z3 shared library. Nothing downloads a compiler, solver, model, or dependency automatically.

From a checkout, without installing Python packages:

```sh
python3 bin/cairn doctor
python3 bin/cairn check examples/hello
python3 bin/cairn run examples/hello
python3 bin/cairn test examples/hello
python3 bin/cairn new my_project
```

The example is a native executable whose exit code is zero on success. There is no language I/O library yet. `cairn new` refuses any existing destination, including an empty directory.

To install the supplied wheel, use `python3 -m pip install --no-index --no-deps /path/to/cairn_language-0.5.0-py3-none-any.whl`. For editable development, with the pinned build tools already installed: `python3 -m pip install --no-build-isolation --no-deps -e .`. Both expose the `cairn` command; `python3 bin/cairn` remains available without installation.

## One CLI

| Command | Meaning |
|---|---|
| `check [path]` | Parse and check a file or ordered project. Does not run it. |
| `emit [path]` | Print readable C++ to stdout. Does not overwrite source. |
| `build [path]` | Build a library or executable into a fresh directory. |
| `run [path]` | Explicitly build and execute `fn main() -> i32`. |
| `test [path]` | Build and run the manifest's independent JSON test contracts. |
| `verify reference candidate --symbol name` | Scalar source equivalence through Z3; not a native proof. |
| `inspect [path] --symbol name` | Compiler-generated context for an agent edit. |

Build receipts contain exact source and artifact hashes, compiler flags and identity, exit status, and the verification boundary. Baseline architecture is `x86-64`; `--arch x86-64-v3` is explicit. Use `--cxx g++` to select GCC. Failed builds never reuse a previous CLI artifact.

## Repository map

```
src/cairn/       parser, checking, expansion, code generation, CLI, agent/SMT tools
  runtime/      the explicit C++ runtime header, packaged with the compiler
examples/       compilable programs, projects, and task contracts
tests/          frontend, project, protocol, native and publication tests
tools/          development, fixture generation, audit and private publication
bench/          independent C++ references and measurement harnesses
docs/           current language/architecture/security guides and historical specs
training/       inherited, auditable teaching fixtures, not a trained model
evidence/       this revision's executed checks and limitations
```

The ordered project loader is not an import system or separate compiler/linker. The library still lacks owners, allocation, modules/namespaces, traits, closures, recoverable errors, OS libraries, CPU concurrency, and GPU code generation. Historical specifications describe proposals, not extra accepted syntax.

## Develop and verify

Run `make test` for the fast suite and `make native` for native regressions, GCC, sanitizers and code-section comparisons. `make wheel` builds an offline wheel using the installed pinned setuptools. See [testing](docs/testing.md) for each acceptance level and [architecture](docs/architecture.md) for source ownership. Follow [AGENTS.md](AGENTS.md) before AI-assisted changes.

## Private GitHub publication

This delivery creates a local Git repository only. The connected GitHub interface could read repositories and write existing repository content, but could not create a repository; the environment had no authenticated GitHub CLI. No code was uploaded, no existing repository was changed, and no remote privacy claim is made.

`tools/publish_private.py OWNER/REPO` performs a local-only preflight. With an already authenticated GitHub CLI, adding `--execute` creates a **new private personal repository**, checks identity and privacy before uploading, pushes without force, and checks the result. It refuses existing local remotes and name conflicts. Read [private publication](docs/private-publication.md) first. There is no public fallback, automatic authentication, release publication, or license selection.

## Evidence boundary

The native compiler and C++ toolchain remain trusted. Scalar `smt-equivalent` trusts its translator and Z3 and excludes memory, loops and floating point. No Lean theorem covers this compiler; historical Lean work remains unchecked. No new model training, model proficiency result, GPU speed claim, general 100x density result, or universal C++ performance claim is made.

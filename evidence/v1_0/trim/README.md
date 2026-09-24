# Trimming src/cairn, as it landed

A pass on 2026-09-23 over the package under `src/cairn` that removed duplicated logic without changing what the compiler, the editor or the tools do. It started from `67d5c46` and landed as eleven commits on main, from `14aba42` to `586a217` (the list is below). The result is small: 0.41 percent of the package's o200k tokens. Earlier reduction passes had already taken the easy repetition, so what was left is logic written twice in different files.

## What changed

| Directory | Lines | Non-blank lines | o200k tokens | Share of the directory's tokens at 67d5c46 |
|---|---:|---:|---:|---:|
| `compiler/` | -16 | -45 | +19 | +0.02% |
| `verify/` | -41 | -42 | -28 | -0.06% |
| `editor/` | -11 | -19 | -220 | -0.52% |
| `perf/` | -61 | -69 | -1,031 | -2.51% |
| `agent/` | -31 | -30 | -153 | -0.40% |
| `projects/` | -23 | -23 | -33 | -0.32% |
| `cli.py`, `version.py` and the rest of the top level | -4 | -3 | -45 | -0.53% |
| `runtime/`, `std/`, `templates/`, `targets/` | 0 | 0 | 0 | 0 |
| **all of `src/cairn`** | **-187** | **-231** | **-1,491** | **-0.41%** |

These are the sums of the eleven commits, each against its parent, so other work in the same files is left out. At `67d5c46` the package held 27,164 lines and 367,747 tokens. At `586a217`, with every agent's changes included, it holds 27,028 lines and 366,926 tokens. Tokens are `tiktoken`'s `o200k_base`, counted over whole files, comments and docstrings included.

What was shared:

- One `write_program` writes a program beside the runtime headers for every build and timer: builds, test contracts, explain, the loop reader, the device reader and both timers. The host and device timers share one driver, one timed-function lookup and one reading of the timing program's answer. Predict prices a reduction and a scan through one path, and an `if` and a `match` through one rule.
- The checker reads a layout's parts one way, and printing and calls rewrite a record that lends a view one way. Traits refuse a member dyn cannot call in one place. Plans and fusion find a planned function the same way, and element loops are written out by one routine. The parser takes a token one way, and the lowering lists a statement's nested statements one way. `derive grad` writes a loop head and a reduction's backward loop once.
- `cairn run`, test blocks and task contracts set a native child's limits in one place.
- The editor walks a body's binders once, for both its rename rule and its semantic tokens. The TextMate grammar is written through one pattern builder and one region builder. The workspace reads open buffers one way.
- One rule maps a compiler's symbol back to its function, for `cairn explain`, predict's loop reader and the device reader. An edit session reads a written name without a forwarding method. Thirteen signatures the formatter had spread one parameter to a line are packed to 120 columns.

Two things stayed duplicated on purpose. `verify/elision.py` repeats rules of `compiler/facts.py` because it is the independent audit of every guard lowering leaves out, and it must not call `facts.py`. `verify/scalar_concrete.py` and `scalar_symbolic.py` are two interpreters of one semantics because the concrete one checks the symbolic one's counterexamples. The runtime headers and `std` were not touched: a runtime change needs `make native`, and a `std` change would alter the emitted code of every program that imports it.

## One behaviour that changed

Before this pass, three functions mapped a compiler symbol back to a CAIRN function, each in its own way. For a function whose own name starts with `ci_`, `perf/native.py` and `perf/device.py` stripped both prefixes of `cf_ci_x` and could attribute its loops or kernels to a function named `x`. `cairn explain` already kept it as `ci_x`. The shared rule, `compiler/codegen.py: demangled`, keeps it as `ci_x` everywhere. `tests/agent/test_explain.py::test_a_compiler_symbol_is_read_back_to_the_function_it_belongs_to` pins it. No example, test program or doc block names such a function, so no recorded output changed.

## How it was checked

- **Emission identity.** `tools/checks/emission_identity.py` snapshotted the 1,541 programs it collects (every example project and file, one program importing all of `std`, every CAIRN program written into a test and every `cairn` block of the docs) with the `67d5c46` compiler. It then compared them against the compiler at `586a217`. Every emitted C++ text and effect row was byte-identical, `changed` and `new` were empty, and so were `fewer_guards` and `more_guards` under `--normalize guards`.
- **A wider behaviour snapshot**, taken after every step and for the last time at `586a217` against the `67d5c46` compiler. It covered 1,508 programs: the default C++, the C++ with every guard kept, the per-module units of an incremental build, the receipt, every refusal's full diagnostic, the formatter's output and the predict report. It found no difference.
- **An editor snapshot** over the same 1,508 programs, at `a44c6f8` against the commit before it, and at `586a217` against `67d5c46`. It covered diagnostics, document symbols, semantic tokens, inlay hints, code actions and lenses. At up to 40 identifiers in each program it also covered hover, definition, highlights, prepare-rename, completion, signature help and a rename. It found no difference. Each program ran in a fresh fork with a fixed hash seed, and the package's own location was written as `<SRC>`, so two runs of one tree agree.
- **The suite and lint.** `make lint` and `python -m pytest tests -n 4` passed twice. The first run was at `e67df55`, the head of the commits through `742e403`: 3,815 passed and 47 skipped. The second was at `d7328c0`, the last three commits and this record over the main of that moment: 3,817 passed and 47 skipped. Main then gained five commits to `docs/` and `bench/ai/`, and `tests/language/test_docs_examples.py` and `tests/tooling/test_repository.py` passed again over them. The skips are tests that need a tool this machine lacks or the owner's device target: `CAIRN_GPU_TESTS` was never set. The generated grammars are current (`python -m cairn.editor.grammar --check`), and so are the Lean collector certificates (`tools/checks/export_lean_certificates.py --check`).
- **The device timer.** `perf/on_device.py` runs device code only under the owner's make targets, and it was never run here. `tests/tooling/test_on_device.py` compiles its program with nvcc for `sm_120` for a device view and a pinned one. A test added in this pass, `test_a_timed_run_builds_beside_the_runtime_headers_and_runs_under_the_lock`, follows `time_device` past its gate with every process it would start replaced. It checks that the program is written beside the runtime headers, built with nvcc, run under the device lock and counted, and nothing reaches the device. The unified placement is one entry of the allocator table and was not compiled.

## Commits

`14aba42` write_program and one timing driver; `6b28814` layout parts, `take`, nested statements, the lent part; `f24d6b4` a native child's limits; `447010c` packed signatures; `89e208b` grammar builders; `a44c6f8` one binder walk; `b63c6b8` traits, plans, element loops, `derive grad`; `742e403` one symbol rule and no forwarding method; `dbb61cb` the timers' lookup and answer; `f41c762` a written type, storage literals, `if` and `match` pricing; `586a217` open buffers.

The machine was one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2, shared with five other agents throughout. Nothing here is a timing, so the load changes none of these numbers.

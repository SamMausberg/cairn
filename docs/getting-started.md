# Getting started

Ten minutes from a fresh checkout to a project that builds, runs, passes its contract and refuses a wrong edit. Every command prints JSON, so the output reads the same in a terminal, in a script and in an agent's transcript.

## Install

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cairn doctor
```

`doctor` names the compilers it found and which optional tools are present. Clang or GCC with C++20 is required; `libz3`, `nvcc`, Lean and QEMU each switch on one more gate and are never downloaded. In a checkout without an install, `python3 bin/cairn` is the same command.

## A project

```sh
cairn new demo
```

A project is a directory with a manifest, ordered sources and task contracts. The manifest is data: it lists files and names a build kind, and it can run nothing.

```toml
[project]
name = "demo"
sources = ["src/math.cairn", "src/main.cairn"]
tests = ["tests/average.json"]

[build]
kind = "exe"
arch = "baseline"
```

The two sources are compiled together, in manifest order, as one program:

```cairn
// Floor average without overflowing the intermediate sum.
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

fn main() -> i32 {
  if average(10, 20) == 15 { return 0; }
  return 1;
}
```

`u64` is a fixed-width integer, `+` traps on overflow, `&` and `^` are unsigned, `shr` takes a count below the width, and `= expression;` is a one-return body. `main` returns the process exit status.

## Check, run, test

```sh
cairn check demo
```

```json
{"status": "typed", "functions": 2, "formal_status": "not-verified"}
```

`typed` means the program passed every static rule: syntax, types, ownership, leases, lanes, placement and effects. `formal_status` is `not-verified` here and everywhere else, because acceptance is not a proof.

```sh
cairn run demo
```

```json
{"status": "program-exited", "exit_code": 0, "build_directory": "demo/build/demo-38_uge7b", "memory_limit_mib": 1024}
```

`run` builds a native executable in a fresh directory under `build/` and runs it under an address-space cap. The directory holds the generated `program.cpp`, the runtime headers it includes and `receipt.json`, which records what was compiled, with what, and the effect row of every function:

```json
"average": {"effects": ["trap"], "calls": [], "syntactic_check_sites": {"shift": 1, "overflow": 1}}
```

`trap` says the function carries a guard that can abort, and the two sites are the shift count and the checked `+`. A function that allocated, wrote through a borrow, started a task or crossed to the device would say so in the same list, and so would everything that calls it.

```sh
cairn test demo
```

```json
{"status": "passed-finite-tests", "tests": [{"contract": "average.json", "cases": 81, "execution_exit_code": 0}]}
```

A contract names a symbol and finite cases, and `test` builds a shared library and calls the symbol for each one from Python. A case that disagrees, or a child that exits abnormally, fails the run whatever was printed.

```json
{"schema": "cairn.task/1", "symbol": "average",
 "cases": [{"args": {"x": 0, "y": 0}, "return": 0}, {"args": {"x": 18446744073709551615, "y": 18446744073709551615}, "return": 18446744073709551615}]}
```

## A wrong edit

Change the annotation of a local and the compiler refuses the program with a code, a message and a position:

```cairn rejects E-TYPE-MISMATCH
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);
fn main() -> i32 {
  let mean:u32 = average(10, 20);
  return 0;
}
```

```json
{"status": "rejected", "code": "E-TYPE-MISMATCH", "message": "Expected u32, got u64.", "line": 3, "column": 18}
```

Nothing converts on its own; `u32(average(10, 20))` says the narrowing and checks it. The rules that make the language safe are refused the same way. A heap array is an owner, using it as a value moves it, and the old name is dead:

```cairn rejects E-MOVED
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);
fn main() -> i32 {
  let mut data = Buf[u64](4);
  let first = data;
  return i32(average(data[0], 1));
}
```

```text
E-MOVED: data was moved.
```

Every diagnostic code has a paragraph in the [language reference](language/README.md) with a program that is refused with it.

## Format, document, edit

```sh
cairn fmt demo                 # rewrite every .cairn in place; --check and --diff write nothing
cairn doc demo                 # a Markdown reference from the checked program, effect rows included
cairn emit demo/src/math.cairn # the C++ one file lowers to
```

`fmt` refuses rather than risk a change of meaning: it re-lexes its own output and leaves a file alone unless the tokens and comments are unchanged. `doc` prints each public function with its signature, its comment and its inferred effects. The [editor extension](tools.md#the-editor-extension) gives diagnostics as you type, hover types and effect rows, completion and rename over the same language server.

## Where to go next

The [tour](tour.md) is twelve complete programs, one per idea, and the [language reference](language/README.md) states every rule with a program that is accepted and one that is refused. [library.md](library.md) is the standard library, [examples.md](examples.md) has real programs from a storage engine to a bare-metal image, and [verification.md](verification.md) says which claims are proved, which are SMT-checked and which are only tested.

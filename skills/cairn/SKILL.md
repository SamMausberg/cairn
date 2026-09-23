---
name: cairn
description: "Write, check, test and tune programs in CAIRN, a checked systems language that compiles to C++20 for the CPU and CUDA for NVIDIA GPUs. Use when a task involves .cairn files, a cairn.toml project or the cairn command, or asks for a CAIRN program. Gives the rules the compiler enforces (checked arithmetic, owners that move, second-class borrows, leases, race-free lanes, effect rows), the fix for each diagnostic code, and the check, test and run loop."
license: MIT OR Apache-2.0
compatibility: "Linux on x86-64 or AArch64 with Python 3.11+ and Clang or GCC with C++20. Needs the cairn command: bin/cairn of a CAIRN checkout, pip install of it, or the Claude Code plugin, which puts it on PATH. nvcc for device code."
metadata:
  version: "0.9.0"
  generated-by: "python -m cairn.agent.skill"
---

# CAIRN

CAIRN is its own language, not Rust, C++ or Python with different spelling. The compiler refuses races, uses of moved values and unchecked effects before anything runs, traps on overflow and out-of-bounds access, and infers what each function costs. Read the core rules below before writing code, and a card before using what it covers. This file's directory holds `codes.md` and `cards/`; in Claude Code it is `${CLAUDE_SKILL_DIR}`, so a card is `${CLAUDE_SKILL_DIR}/cards/owners.md`.

## Loop

1. Write the program. One file with `fn main() -> i32` runs as is; `cairn new NAME` makes a project (a data-only `cairn.toml`, `src/`, a test).
2. Run `cairn check PATH --format json` until it prints `"status": "typed"`. A refusal names one `code`, a line and a column. Look the code up in [codes.md](codes.md), read the card it names, and change the code the rule is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe` or delete a check to get past a refusal.
3. `cairn test PATH` runs every `test` block in a process of its own; `cairn run PATH` builds and runs, with arguments after `--`.
4. Before tuning, read the costs instead of guessing: `cairn doc PATH` prints each signature with its effect row, `cairn explain PATH` the guards, allocations and waits left at run time, `cairn predict PATH` a time per function from a machine profile, without running anything.

## Core rules

```text
CAIRN 0.9 is a checked systems language, not Rust or Python. Braces, semicolons, typed signatures, explicit return on every path; no tail expression. fn inc(x:u64)->u64 = add_wrap(x,1); is one return, not a closure. let is immutable, let mut mutable, parameters immutable; no shadowing, no implicit conversion; let x:u32 = 7; annotates.

for i in lo..hi is sequential and half-open, bounds evaluated once, lo first; for i, x in xs is for i in 0..len(xs) with let x = xs[i] (copyable elements); ranges are not lists; only compact/parallel/reduce write for i in n. if/else if/else and while use braces; break/continue target the nearest loop, also from match arms. while/recursion may diverge; no stack bound is proved. Precedence rises || && | ^ & (== != < <= > >=) (+ -) (* / %); && and || stop early. reg/each are old spellings of let mut/for.

No inheritance, overloading, exceptions or hidden allocation; indentation is insignificant. Other features have cards, sent when used. Preserve the fixed task. Typed, tested, SMT-equivalent and Lean-checked are different claims.
```

```text
Types: bool, u8/u16/u32/u64, usize (64-bit), i8/i16/i32/i64. +,-,* trap on overflow in every build, and so does x += e; add_wrap/sub_wrap/mul_wrap are unsigned and modular, so x+1 and add_wrap(x,1) differ at the maximum. /,% trap on zero or signed min/-1; signed remainder truncates toward zero, unlike Python. shl_wrap(x,k), shr(x,k): unsigned x, usize k below the width. &,|,^,~ are unsigned; min/max integer-only. Conversions are explicit calls, u64(x), range checked: narrowing traps outside the target; float to integer truncates toward zero, trapping on NaN or out of range. Literals take the expected type, else u64/f64. Never weaken arithmetic or the trap/domain policy to pass a check.
```

```text
Call declared functions and listed primitives, never invented libraries; expand an undisclosed function before calling it. A call that writes through a borrow or allocates is a whole statement (f(x); drops a result), initializer or condition, never a nested operand (E-EFFECT-ORDER); one that only releases may nest, since the drop runs where C++ ends the scope. Rows substitute the caller's arguments, recursive calls included. What a callee's interface establishes, and what it leaves to its body, is the packet's evidence.
```

## A program

Tasks, leases, a test block and a checked reduction; `cairn run` prints `total = 499500`.

```cairn
// Two tasks fill the two halves of an array, then main adds it up.
fn fill(n:usize, out:rw<u64>[n], start:u64) {
  for i in 0..n { out[i] = start + u64(i); }     // checked: an overflow traps
}

fn halves(n:usize, data:rw<u64>[n]) {
  let mid = n / 2;
  let left = spawn fill(data[0..mid], 0);        // left holds data[0..mid] until wait
  let right = spawn fill(data[mid..n], u64(mid));
  wait(left);
  wait(right);
}

test halves_count_up {
  let mut data = Buf[u64](10);
  halves(data);                                  // n is len(data)
  assert_eq(data[9], 9);
}

fn main() -> i32 {
  let mut data = Buf[u64](1000);                 // an owner, released at scope exit
  halves(data);
  let total = reduce + for i in len(data) yield data[i];
  println("total = ", total);
  return 0;
}
```

## Cards

Each card states one part of the language and the codes of its rules. The compiler sends the same cards to an agent it hosts, picked by the words below.

| Card | Read it when the program uses |
|---|---|
| [views](cards/views.md) | `buffer`, `len`, `stack` |
| [compact](cards/compact.md) | `compact` |
| [scan](cards/scan.md) | `scan` |
| [floats](cards/floats.md) | `f32`, `f64` |
| [math](cards/math.md) | `abs`, `ceil`, `floor`, `sqrt`, `to_bits`, `trunc` |
| [storage](cards/storage.md) | `bf16`, `f16`, `f8e4m3`, `f8e5m2`, `from_bits`, `mma_unordered`, `quantize`, `quantize_stochastic` |
| [gradients](cards/gradients.md) | `grad` |
| [records](cards/records.md) | the forms it describes |
| [generators](cards/generators.md) | `derive`, `family`, `recipe` |
| [memory](cards/memory.md) | `buffer`, `stack` |
| [sums](cards/sums.md) | `match`, `try` |
| [generics](cards/generics.md) | `impl`, `trait` |
| [owners](cards/owners.md) | `Array`, `Buf`, `defer`, `linear`, `swap`, `take` |
| [assembly](cards/assembly.md) | `asm` |
| [effects](cards/effects.md) | `effects`, `extern`, `pure`, `unsafe` |
| [parallel](cards/parallel.md) | `device`, `parallel`, `pinned`, `reduce`, `transfer`, `unified` |
| [tasks](cards/tasks.md) | `Atomic`, `Group`, `Mutex`, `collect`, `spawn`, `wait` |
| [rings](cards/rings.md) | `IoRing` |
| [closures](cards/closures.md) | `dyn` |
| [tests](cards/tests.md) | `assert`, `assert_eq`, `test` |
| [implementations](cards/implementations.md) | `implements` |
| [printing](cards/printing.md) | `eprint`, `eprintln`, `format`, `print`, `println` |
| [layouts](cards/layouts.md) | `layout` |
| [lends](cards/lends.md) | `lends` |
| [modules](cards/modules.md) | `import`, `module`, `pub` |

## Mistakes that cost the most

- Habits from Rust or C++: there are no `&`/`&mut` references, lifetimes, `::` paths, `as` casts (write `u64(x)`) or tail-expression returns. `impl` is only `impl Trait for T`; `value.f(args)` calls a plain `fn f(v, args)` from the type's module. Text is `ro<u8>[n]` or `Vec[u8]`; there is no `String`.
- Invented libraries: only what a file declares, the builtins the cards name and the `std.*` modules exist; `cairn doc --std` lists every `std` signature.
- Guessing a fix: each diagnostic code has one rule behind it, and its card says what that rule accepts.

## Commands

Every command takes `--format json`, the default when output is piped.

| Command | What it does |
|---|---|
| `cairn doctor` | Report local tools; never downloads them. |
| `cairn new` | Create a project from a template; it is data only. |
| `cairn check` | Accept or refuse a program: syntax, types, ownership, leases, lanes, placement, effects. |
| `cairn emit` | Print the C++ the program lowers to. |
| `cairn expand` | Print what every derive generated, as CAIRN source. |
| `cairn build` | Build a native artifact in a fresh directory, with a receipt. |
| `cairn run` | Build, then run under process limits, or under the target's emulator; ARGS after -- go to the program. |
| `cairn shot` | Run headless and collect every frame std.draw captured: its PNG, its layout record and its time. |
| `cairn test` | Run the project's test blocks, each in a process of its own, and its finite task contracts. |
| `cairn inspect` | Print the packet an editing agent gets for one symbol. |
| `cairn state` | Print the program's state for an agent: every signature and effect row by module, under a digest. |
| `cairn migrate` | Change one function's interface through every caller, in all the files or in none. |
| `cairn explain` | Where each function pays at run time: guards, allocations, waits and loop vectorization. |
| `cairn predict` | How long each function will take, from its checked work and a machine profile; nothing runs. |
| `cairn tune` | Choose a function's plan by prediction, and with --measure time only the best-ranked few on this host. |
| `cairn validate` | Test one implementation against its reference on boundary inputs its contract gives; finite, not proof. |
| `cairn doc` | Generate the API reference of the checked program, as Markdown. |
| `cairn graph` | Print the module graph: each file's modules, each module's imports, exports and dependents, hashes. |
| `cairn verify` | SMT source equivalence, not native or Lean verification. |
| `cairn diff` | What changed between two versions, function by function, and on what evidence. |
| `cairn certificates` | Check collector arithmetic certificates; not a Lean/compiler proof. |
| `cairn fmt` | Format CAIRN sources in place; refuses any change to the token stream. |
| `cairn lsp` | Speak the Language Server Protocol over stdin/stdout. |
| `cairn completions` | Print the completion script of a shell: bash or zsh. |

## More

The reference is the `docs/` directory of the CAIRN repository: `language.md`, `memory.md`, `abstractions.md`, `concurrency.md` and `numerics.md` for the language, `library.md` and `std/` for the standard library, `tools.md` for the commands, `agents.md` for the edit host. From a checkout or the Claude Code plugin it is `${CLAUDE_SKILL_DIR}/../../docs/`; otherwise https://github.com/SamMausberg/cairn/tree/main/docs. Every example there compiles, so copy from it rather than from memory of another language.

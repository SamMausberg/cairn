---
name: cairn
description: "Write, check, test and tune programs in CAIRN, a checked systems language that compiles to C++20 for the CPU and CUDA for NVIDIA GPUs. Use when a task involves .cairn files, a cairn.toml project or the cairn command, or asks for a CAIRN program. Gives the rules the compiler enforces (checked arithmetic, owners that move, second-class borrows, leases, race-free lanes, effect rows), the fix for each diagnostic code, and the check, test and run loop."
license: MIT OR Apache-2.0
compatibility: "Linux on x86-64 or AArch64 with Python 3.11+ and Clang or GCC with C++20. Needs the cairn command: bin/cairn of a CAIRN checkout, pip install of it, or the Claude Code plugin, which puts it on PATH. nvcc for device code."
metadata:
  version: "1.0.0"
  generated-by: "python -m cairn.agent.skill"
---

# CAIRN

CAIRN is its own language, not Rust, C++ or Python with different spelling. The compiler refuses races, uses of moved values and unchecked effects before anything runs, and traps on overflow and out-of-bounds access. Read the core rules below before writing code, and a card before using what it covers. A card is `cards/NAME.md` beside this file (`${CLAUDE_SKILL_DIR}/cards/owners.md` in Claude Code), and `cairn rules NAME` prints it.

## Loop

1. Write the program. One file with `fn main() -> i32` runs as is; `cairn new NAME` makes a project (a data-only `cairn.toml`, `src/`, a test).
2. Run `cairn check PATH --format json` until it prints `"status": "typed"`. A refusal gives a `code`, a line and a column, the `card` that states its rule (`cairn rules CODE` prints it) and, when the compiler can state one, a `repair_hint`; `further` lists every other refusal the check could judge on its own, each the same way, so fix them all before checking again. Change the code the rule is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe` or delete a check to get past a refusal.
3. `cairn test PATH` runs every `test` block in a process of its own; `cairn run PATH` builds and runs, with arguments after `--`.
4. Read the costs instead of guessing: `cairn doc PATH` prints each signature with its effect row, `cairn explain PATH` the guards, allocations and waits left at run time, `cairn predict PATH` a time per function from a machine profile, without running anything.
5. To make a function faster, leave it as the reference and write an implementation beside it: `fn g(...) implements f when COND { }`, with natural parameters to search if useful. `cairn validate PATH --symbol g` tests it against the reference on generated boundary inputs, `cairn tune PATH --symbol f` searches plans and validated implementations within its budgets and `--write` selects the winner (`plan f use g;`). Never edit the reference, a tolerance or a test to make an implementation pass. When CAIRN cannot say the design you want, keep the design: write that function in CUDA or C++ as a foreign implementation, `fn g(...) implements f` calling vendored code ([foreign](cards/foreign.md)), which `cairn foreign` inspects and `cairn validate` holds to the reference, never a slower or narrower workaround.
6. An agent without a shell gets the same through `cairn mcp`: `check`, `state`, and the edit, plan and implementation sessions, which write an admitted change back to its files.

`cairn --help` lists every command, and a command that reports prints JSON when piped.

## Core rules

```text
CAIRN 1.0 is a checked systems language, not Rust or Python. Braces, semicolons, typed signatures, explicit return on every path; no tail expression. fn inc(x:u64)->u64 = add_wrap(x,1); is one return, not a closure. let is immutable, let mut mutable, parameters immutable; no shadowing, no implicit conversion; let x:u32 = 7; annotates.

for i in lo..hi is sequential and half-open, bounds evaluated once, lo first; for i, x in xs is for i in 0..len(xs) with let x = xs[i] (copyable elements); ranges are not lists; only compact/parallel/reduce write for i in n. if/else if/else and while use braces; break/continue target the nearest loop, also from match arms. while/recursion may diverge; no stack bound is proved. Precedence rises || && | ^ & (== != < <= > >=) (+ -) (* / %); && and || stop early. reg/each are old spellings of let mut/for.

No inheritance, overloading, exceptions or hidden allocation; indentation is insignificant. Other features have cards, sent when used. Preserve the fixed task. Typed, tested, SMT-equivalent and Lean-checked are different claims.
```

```text
Types: bool, u8/u16/u32/u64, usize (64-bit), i8/i16/i32/i64. +,-,* and x += e trap on overflow in every build; add_wrap/sub_wrap/mul_wrap are unsigned and modular, so x+1 and add_wrap(x,1) differ at the maximum. /,% trap on zero or signed min/-1; signed remainder truncates toward zero, unlike Python. shl_wrap(x,k), shr(x,k): unsigned x, usize k below the width. &,|,^,~ are unsigned; min/max integer-only. Conversions are explicit calls, u64(x), range checked: narrowing traps outside the target; float to integer truncates toward zero, trapping on NaN or out of range. Literals take the expected type, else u64/f64. const K:u64 = 4 * 1024; folds at compile time. Never weaken arithmetic or the trap/domain policy to pass a check.
```

```text
Call declared functions and listed primitives, never invented libraries; expand an undisclosed function before calling it. A call that writes through a borrow or allocates is a whole statement (f(x); drops a result), initializer or condition, never a nested operand (E-EFFECT-ORDER); one that only releases may nest, since the drop runs where C++ ends the scope. Rows substitute the caller's arguments, recursive calls included. What a callee's interface establishes, and what it leaves to its body, is the packet's evidence.
```

Their codes: base E-BUILTIN-NAME E-DUPLICATE E-ELEMENT-LOOP E-EXPRESSION-BODY E-IMMUTABLE E-LEX E-LOOP-CONTROL E-LVALUE E-NAME E-PARAM E-PARSE E-RETURN E-SHADOW E-TYPE E-TYPE-MISMATCH E-UNBOUND E-UNREACHABLE; integers E-CAST E-CONST E-LITERAL-RANGE E-MINMAX E-OPERATOR E-WRAP-TYPE; calls E-ARITY E-CALL E-CALLEE E-DISCARD E-EFFECT-ORDER.

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

Each card states one part of the language and the codes of its rules. A host sends an agent the cards its program's words select, below, and `cairn rules FILE` names them.

- [views](cards/views.md): ro rw buffer len stack
- [compact](cards/compact.md): compact
- [scan](cards/scan.md): scan
- [floats](cards/floats.md): f32 f64
- [math](cards/math.md): abs ceil floor sqrt to_bits trunc
- [storage](cards/storage.md): bf16 f16 f8e4m3 f8e5m2 from_bits mma_unordered quantize quantize_stochastic
- [gradients](cards/gradients.md): grad
- [records](cards/records.md): struct enum
- [generators](cards/generators.md): derive family recipe
- [memory](cards/memory.md): buffer stack
- [sums](cards/sums.md): enum match try
- [generics](cards/generics.md): impl trait
- [owners](cards/owners.md): Array Buf defer linear swap take
- [assembly](cards/assembly.md): asm
- [foreign](cards/foreign.md): launch
- [effects](cards/effects.md): effects extern pure unsafe
- [parallel](cards/parallel.md): device parallel pinned reduce transfer unified
- [cooperative](cards/cooperative.md): barrier pipeline shuffle shuffle_down shuffle_xor warp
- [tasks](cards/tasks.md): Atomic Group Mutex collect spawn wait
- [rings](cards/rings.md): IoRing
- [closures](cards/closures.md): dyn
- [tests](cards/tests.md): assert assert_eq test
- [implementations](cards/implementations.md): implements
- [printing](cards/printing.md): eprint eprintln format print println
- [layouts](cards/layouts.md): layout
- [fragments](cards/fragments.md): MmaA MmaAcc MmaB TmemAcc WmmaA WmmaAcc WmmaB
- [lends](cards/lends.md): lends
- [modules](cards/modules.md): import module pub

A refusal from a host or the command line may name [hosts](cards/hosts.md), [migrations](cards/migrations.md), [sketches](cards/sketches.md), [validation](cards/validation.md), [commands](cards/commands.md), [limits](cards/limits.md).

## Mistakes that cost the most

- Habits from Rust or C++: there are no `&`/`&mut` references, lifetimes, `::` paths, `as` casts (write `u64(x)`) or tail-expression returns. `impl` is only `impl Trait for T`; `value.f(args)` calls a plain `fn f(v, args)` from the type's module. Text is `ro<u8>[n]` or `Vec[u8]`; there is no `String`.
- Invented libraries: only what a file declares, the builtins the cards name and the `std.*` modules exist; `cairn doc --std` lists every `std` signature.
- Guessing a fix: each diagnostic code has one rule behind it, and its card says what that rule accepts.

## More

The reference is `docs/` of the CAIRN repository, `${CLAUDE_SKILL_DIR}/../../docs/` from a checkout or the Claude Code plugin, else https://github.com/SamMausberg/cairn/tree/main/docs: `language.md`, `memory.md`, `abstractions.md`, `concurrency.md`, `devices.md` and `numerics.md` for the language, `library.md` for the standard library, `tools.md` for the commands, `agents.md` for the hosts. Every example there compiles, so copy from it rather than from memory of another language.

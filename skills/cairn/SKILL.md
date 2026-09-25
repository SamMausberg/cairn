---
name: cairn
description: "Write, check, test and tune programs in CAIRN, a checked systems language that compiles to C++20 for the CPU and CUDA for NVIDIA GPUs. Use when a task involves .cairn files, a cairn.toml project or the cairn command, or asks for a CAIRN program. Gives the rules the compiler enforces (checked arithmetic, owners that move, second-class borrows, leases, race-free lanes, effect rows), the fix for each diagnostic code, and the check, test and run loop."
license: MIT OR Apache-2.0
compatibility: "Linux on x86-64 or AArch64 with Python 3.11+ and Clang or GCC with C++20. Needs the cairn command: bin/cairn of a CAIRN checkout, pip install of it, or the Claude Code plugin, which puts it on PATH. nvcc for device code."
metadata:
  version: "1.1.0"
  generated-by: "python -m cairn.agent.skill"
---

# CAIRN

CAIRN is its own language, not Rust, C++ or Python with different spelling. The compiler refuses races, uses of moved values and unchecked effects before anything runs, and traps on overflow and out-of-bounds access. Read the core rules below before writing code, and a card before using what it covers. A card is `cards/NAME.md` beside this file (`${CLAUDE_SKILL_DIR}/cards/owners.md` in Claude Code), and `cairn rules NAME` prints it.

## Loop

1. Write the program. One file with `fn main() -> i32` runs as is; `cairn new NAME` makes a project (a data-only `cairn.toml`, `src/`, a test).
2. Run `cairn check PATH --format json` until it prints `"status": "typed"`. A refusal gives a `code`, a line and a column, the `card` that states its rule (`cairn rules CODE` prints it) and, when the compiler can state one, a `repair_hint`; `further` lists every other refusal the check could judge on its own, so fix them all before checking again. Change the code the rule is about. Never widen an effect ceiling, turn `ro` into `rw`, add `unsafe` or delete a check to get past a refusal.
3. `cairn run PATH < input` builds and runs, with arguments after `--`; `--sanitize address` or `--sanitize thread` runs the build a sanitizer checks. `cairn test PATH` runs every `test` block in a process of its own.
4. For speed, read costs instead of guessing (`cairn explain PATH`, `cairn predict PATH`), then leave the function as the reference and write an implementation beside it, `fn g(...) implements f when COND { }`, which `cairn validate` holds to the reference and `cairn tune` selects ([implementations](cards/implementations.md)); a design CAIRN cannot say is a foreign implementation ([foreign](cards/foreign.md)), never a slower or narrower workaround. `cairn mcp` serves the same to an agent without a shell.

`cairn --help` lists every command, and a command that reports prints JSON when piped.

## Core rules

```text
CAIRN 1.1 is a checked systems language, not Rust or Python. Braces, semicolons, typed signatures, explicit return on every path; no tail expression. fn inc(x:u64)->u64 = add_wrap(x,1); is one return, not a closure. let is immutable, let mut mutable, parameters immutable; no shadowing, no implicit conversion; let x:u32 = 7; annotates.

for i in lo..hi is sequential and half-open, bounds evaluated once, lo first; for i, x in xs is for i in 0..len(xs) with let x = xs[i] (copyable elements); ranges are not lists; only compact/parallel/reduce write for i in n. if/else if/else and while use braces; break/continue target the nearest loop, also from match arms. while/recursion may diverge; no stack bound is proved. Precedence rises || && | ^ & (== != < <= > >=) (+ -) (* / %); && and || stop early. reg/each are old spellings of let mut/for.

No inheritance, overloading, exceptions or hidden allocation; indentation is insignificant. Other features have cards, sent when used. Preserve the fixed task. Typed, tested, SMT-equivalent and Lean-checked are different claims.
```

```text
Types: bool, u8/u16/u32/u64, usize (64-bit), i8/i16/i32/i64. +,-,* and x += e trap on overflow in every build; add_wrap/sub_wrap/mul_wrap are unsigned and modular. /,% trap on zero or signed min/-1; signed remainder truncates toward zero. shl_wrap(x,k), shr(x,k): unsigned x, usize k below the width. &,|,^,~ are unsigned; min/max integer-only. Conversions are explicit calls, u64(x), range checked: narrowing traps outside the target; float to integer truncates toward zero, trapping on NaN or out of range. Literals take the expected type, else u64/f64, and -1 alone is an i64; a signed minimum is a literal, -9223372036854775808. const K:u64 = 4 * 1024; folds at compile time. Never weaken arithmetic or the trap/domain policy to pass a check.
```

```text
Call declared functions and listed primitives, never invented libraries; expand an undisclosed function before calling it. A call that writes through a borrow or allocates is a whole statement (f(x); drops a result), initializer, condition or a conversion's one operand (usize(next(inp))), never beside a second operand (E-EFFECT-ORDER); one that only releases may nest, since the drop runs where C++ ends the scope. Rows substitute the caller's arguments, recursive calls included. What a callee's interface establishes, and what it leaves to its body, is the packet's evidence.
```

Their codes: base E-BUILTIN-NAME E-DUPLICATE E-ELEMENT-LOOP E-EXPRESSION-BODY E-IMMUTABLE E-LEX E-LOOP-CONTROL E-LVALUE E-NAME E-PARAM E-PARSE E-RETURN E-SHADOW E-TYPE E-TYPE-MISMATCH E-UNBOUND E-UNREACHABLE; integers E-CAST E-CONST E-LITERAL-RANGE E-MINMAX E-OPERATOR E-WRAP-TYPE; calls E-ARITY E-CALL E-CALLEE E-DISCARD E-EFFECT-ORDER.

## A program

Standard input, integers, a Vec and two tasks; `printf '3 -1 4 -9' | cairn run .` prints `count 4 negative 2`.

```cairn
// Reads integers from standard input; prints how many, and how many are negative, counted on two tasks.
import std.core (Option, Result);
import std.io as io;
import std.text as text;
import std.vec (Vec);

fn negatives(n:usize, xs:ro<i64>[n]) -> usize {
  let mut k:usize = 0;                              // an unannotated 0 would be a u64
  for x in xs { if x < 0 { k += 1; } }
  return k;
}

fn main() -> i32 {
  let mut input = vec.new[u8]();                    // a growable owner, freed at scope exit
  stack chunk:u8[4096] = zeroed;
  let mut more = true;
  while more {
    match io.read_stdin(4096, chunk) {
      Ok(got) => { if got == 0 { more = false; } else { vec.extend_from(input, got, chunk[0..got]); } }
      Err(_) => more = false;
    }
  }
  let mut values = vec.new[i64]();
  let mut lo:usize = 0;
  while lo < input.len {
    let mut hi = lo;
    while hi < input.len && input.data[hi] > 32 { hi += 1; }
    if hi > lo {
      match text.parse_i64(hi - lo, input.data[lo..hi]) {   // a part is written where it is passed
        Ok(v) => vec.push(values, v);
        Err(_) => return 1;
      }
    }
    lo = hi + 1;
  }
  let n = values.len;
  let halves = Group[usize](2);
  spawn negatives(values.data[0..n / 2]) into halves;   // each task reads its own part; n is len of it
  spawn negatives(values.data[n / 2..n]) into halves;
  let first = collect(halves);                      // a call that joins or writes is its own statement
  let second = collect(halves);
  wait(halves);
  println("count ", n, " negative ", first + second);
  return 0;
}
```

## Cards

Each card, `cards/NAME.md`, states one part of the language and the codes of its rules. A host sends an agent the cards its program's words select, below, and `cairn rules FILE` names them.

- views: ro rw buffer len stack
- compact: compact
- scan: scan
- floats: f32 f64
- math: abs ceil floor sqrt to_bits trunc
- storage: bf16 f16 f8e4m3 f8e5m2 from_bits mma_unordered quantize quantize_stochastic
- gradients: grad
- records: struct enum
- generators: derive family recipe
- memory: buffer stack
- sums: enum match try
- generics: impl trait
- owners: Array Buf defer linear swap take
- assembly: asm
- foreign: launch
- effects: effects extern pure unsafe
- parallel: device parallel pinned reduce transfer unified
- wide: Cache load_wide store_wide
- atomics: atomic_add_unordered atomic_add_wrap atomic_and atomic_cas atomic_max atomic_min atomic_or atomic_xor
- cooperative: barrier pipeline shuffle shuffle_down shuffle_up shuffle_xor warp warp_all warp_any warp_ballot warp_match
- tasks: Atomic Group Mutex collect spawn wait
- rings: IoRing
- closures: dyn
- tests: assert assert_eq test
- implementations: implements
- printing: eprint eprintln format print println
- layouts: layout
- fragments: MmaA MmaAcc MmaB TmemAcc WmmaA WmmaAcc WmmaB
- lends: lends
- modules: import module pub

A refusal from a host or the command line may name hosts, migrations, sketches, validation, commands, harness, limits.

## Mistakes that cost the most

- Habits from Rust or C++: no `&`/`&mut`, lifetimes, `::` paths, `as` casts (write `u64(x)`), tuples (a `struct`), tail-expression returns, or `if` and `match` as values (`let mut x = b; if c { x = a; }`). `impl` is only `impl Trait for T`; `value.f(args)` calls a plain `fn f(v, args)` from the type's module. Text is `ro<u8>[n]` or `Vec[u8]`, never a `String`.
- An integer literal is a `u64` unless something expects another type: `let mut i:usize = 0;` for an index. A signed minimum is a literal, `-9223372036854775808`.
- A `Buf[T](n)` is `len(b)` long, which the checker does not tie to `n`: pass `f(b)` and the call supplies `len(b)`, or pass the part `b[0..n]`. A part `xs[lo..hi]` is written only as a call's argument. A `Vec`'s length is `v.len` and its elements `v.data[i]`.
- Invented libraries: only what a file declares, the builtins the cards name and the `std.*` modules exist; `cairn find parse integer` or `cairn find --takes 'ro<u8>[n]' --returns i64` names the function to call, and `cairn doc --std --module std.text` prints one module.
- Guessing a fix: each diagnostic code has one rule behind it, and its card says what that rule accepts.

## More

The reference is `docs/` of the CAIRN repository, `${CLAUDE_SKILL_DIR}/../../docs/` from a checkout or the Claude Code plugin, else https://github.com/SamMausberg/cairn/tree/main/docs: `language.md`, `memory.md`, `abstractions.md`, `concurrency.md`, `devices.md` and `numerics.md` for the language, `library.md` for the standard library, `tools.md` for the commands, `agents.md` for the hosts. Every example there compiles, so copy from it rather than from memory of another language.

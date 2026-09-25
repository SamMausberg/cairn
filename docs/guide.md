# Guide

This guide takes you from a fresh checkout to a CAIRN project that builds, runs, tests itself and refuses a wrong edit, and then through twelve complete programs, one idea each. It opens with a program that reads input, counts on two tasks and prints, followed by the refusals a first program meets most often and the library calls it needs.

At a terminal every `cairn` command prints lines for a person. Piped, or given `--format json`, it prints the JSON record that a script or an agent reads, and `CAIRN_FORMAT` sets the default.

## Write a program

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

```sh
cairn check first.cairn                                     # typed, or a refusal with its code, line and fix
printf '3 -1 4' | cairn run first.cairn                     # count 3 negative 1
printf '3 -1 4' | cairn run first.cairn --sanitize address  # the same, checked by the address sanitizer
```

These are the refusals a first program meets most often, taken from the programs that the subjects of the 1.1 evaluation wrote:

| written | CAIRN wants | code |
|---|---|---|
| `let x = next(inp) + 1;`, where `next` writes `inp` | `let v = next(inp);` then `v + 1`; `usize(next(inp))` alone is fine | `E-EFFECT-ORDER` |
| `f(n, b)`, where `b` is `Buf[u64](n)` | `f(b)`, which passes `len(b)`, or `f(n, b[0..n])` | `E-TYPE-MISMATCH` |
| `let mut i = 0;` then `xs[i]` | `let mut i:usize = 0;`: a literal is a `u64` when nothing expects another type | `E-TYPE-MISMATCH` |
| `let x = if c { a } else { b };` | `let mut x = b; if c { x = a; }` | `E-NAME` |
| `let s = xs[lo..hi];` | the part in the call itself: `f(xs[lo..hi])` | `E-VIEW-ALIAS` |
| `len(v)` of a `Vec` | `v.len`, and `v.data[i]` for an element | `E-LEN` |
| `x as u64`, `i64::MIN`, `(a, b)` | `u64(x)`, `-9223372036854775808`, a `struct` | `E-PARSE` |
| `add_wrap(x, y)` on `i64` | wrapping is unsigned only; test first, `y > 0 && x > MAX - y` | `E-WRAP-TYPE` |

These are the library calls a program like this uses. `cairn find parse integer` or `cairn find --takes 'ro<u8>[n]' --returns i64` names the call for a need the table leaves out, and `cairn doc --std --module std.text` prints the signatures of one module:

| need | call |
|---|---|
| read standard input | `io.read_stdin(n, into)`: bytes read, 0 at the end |
| numbers from text | `text.parse_u64(n, s)`, `text.parse_i64(n, s)`: `Ok(v)` or `Err(ParseError)`; `text.find_byte(n, s, byte, from)` |
| a growing list | `vec.new[T]()`, `vec.push(v, x)`, `vec.extend_from(v, n, part)`, `v.len`, `v.data[i]` |
| a fixed array | `Buf[T](n)`, `n` zeroed elements; `stack chunk:u8[4096] = zeroed;` |
| output | `println(...)` and `print(...)`: integers, bools, `'c'`, strings and `u8` views; `eprintln` to standard error |
| threads | `let t = spawn f(args);` and `wait(t)`; `Group[T](k)`, `spawn f(args) into g;`, `collect(g)`, `wait(g)` |

## Install

From a checkout of the repository:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cairn doctor
```

`cairn doctor` names the C++ compilers it found and which optional tools are present. You need Clang or GCC with C++20. `libz3`, `nvcc`, Lean and QEMU each turn on one more check, and CAIRN never downloads them. Without an install, `python3 bin/cairn` runs the same command.

## A project

```sh
cairn new demo
```

A project is a directory with a manifest, `cairn.toml`, its sources and its task contracts. A task contract is a JSON file of finite test cases for one function. The manifest is data: it lists files and names a build kind, and it cannot run anything.

```toml
[project]
name = "demo"
sources = ["src/math.cairn", "src/main.cairn"]
tests = ["tests/average.json"]

[build]
kind = "exe"
arch = "baseline"
```

The sources are compiled together, in manifest order, as one program:

```cairn
// Floor average without overflowing the intermediate sum.
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

test average {
  assert_eq(average(10, 20), 15);
  assert_eq(average(1, 2), 1, "rounds down");
}

// Prints the average it checks, and exits 0 only when it is right.
fn main() -> i32 {
  let mean = average(10, 20);
  println("average(10, 20) = ", mean);
  if mean != 15 { return 1; }
  return 0;
}
```

`u64` is a 64-bit unsigned integer, and `+` traps on overflow. `&` and `^` work on unsigned integers only, and `shr` takes a count below the width. A body written `= expression;` returns that expression.

`test average { ... }` runs only under `cairn test`, and `assert_eq` traps when its two values differ, printing both. A `let` is immutable, a call that returns nothing is a statement, and `main` returns the process exit status.

That project is the default template. Four more templates are starting points for real programs, and each carries a test block:

```sh
cairn new tool --template cli        # counts the files named after --, as wc does: std.env, std.fs, std.fmt
cairn new crc --template lib         # a library; cairn build crc --header writes its C header too
cairn new echo --template service    # a server that keeps every client's receive in flight on one I/O ring
cairn new squares --template parallel  # a host region, a checked reduce and a plan that splits it
```

The test suite creates, builds, runs and tests every template. Every new project also gets an `AGENTS.md` of under 25 lines. It tells a coding agent the loop of check, test and run, where the rules are (the CAIRN skill and `cairn doc --std`), and never to widen an effect ceiling or weaken a test to get past a refusal. A `CLAUDE.md` of one line imports it for Claude Code, which reads that file instead.

## Check, run, test

```sh
cairn check demo
```

```text
typed: 3 functions
```

```json
{"status": "typed", "functions": 3, "library_functions": 0, "formal_status": "not-verified",
 "project": {"name": "demo", "manifest_sha256": "e029...", "sources": [...], ...}}
```

`typed` means the program passed every rule the compiler checks before it runs: syntax, types, ownership, leases, lanes, placement and effects. The three functions are the program's own, counting the test. When a program imports library modules, the library functions it reaches are checked with it and counted in `library_functions`. `formal_status` is always `not-verified`, because acceptance is not a proof.

```sh
cairn run demo
```

`run` builds a native executable in a fresh directory under `build/` and runs it with its data memory capped, at 1024 MiB unless `--memory-mib` says otherwise. At a terminal the program writes to the terminal, and `cairn` exits with the program's status:

```text
average(10, 20) = 15
```

Piped, the record carries what it printed:

```json
{"status": "program-exited", "exit_code": 0, "stdout": "average(10, 20) = 15\n", "stderr": "",
 "build_directory": "/home/you/demo/build/demo-38_uge7b", "security_sandbox": false, "memory_limit_mib": 1024, "emulator": null}
```

`security_sandbox` is `false` because the memory cap stops a runaway program and isolates nothing. `cairn run demo -- one two` passes `one` and `two` to the program, which reads them through `std.env`.

The build directory holds the generated `program.cpp`, the runtime headers, the executable and `receipt.json`. The receipt records what was compiled, with which compiler, and every function's effect row, the list of what the function may do:

```json
"average": {"effects": ["trap"], "calls": [], "syntactic_check_sites": {"shift": 1, "overflow": 1}, "discharged_check_sites": {"shift": 1},
            "heap_allocations": 0, "allocation_count_kind": "syntactic-sites-not-dynamic-bound", "local_storage": [],
            "implicit_synchronization": 0, "status": "prototype-checked-not-proved"}
```

A guard is a check the program makes at run time. `syntactic_check_sites` counts the guards the source asks for, here the shift count and the checked `+`. `discharged_check_sites` counts those the checker proved cannot fail, which the C++ leaves out: the shift count is the literal 1, below the width. Nothing bounds `x` and `y`, so the guard of `+` stays, and `trap` in the row says the function has a guard that can abort.

A function that allocates, writes through a borrow, starts a task or uses the device says so in the same list, and so does every function that calls it.

```sh
cairn test demo
```

```text
passed-finite-tests: 1 test, 1 contract, 81 cases
```

`test` runs each test block in a process of its own, so a failed assert fails only that test. Then it runs the manifest's contracts. A contract names a symbol and a finite list of cases, and `test` builds a shared library and calls the symbol once per case from Python. A wrong answer, or a child that exits abnormally, fails the run whatever it printed.

```json
{"schema": "cairn.task/1", "symbol": "average",
 "cases": [{"args": {"x": 0, "y": 0}, "return": 0}, {"args": {"x": 18446744073709551615, "y": 18446744073709551615}, "return": 18446744073709551615}]}
```

## A wrong edit

Change the type of a local in `src/main.cairn`, and the compiler refuses the program with a code, a message and a position:

```cairn rejects E-TYPE-MISMATCH
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

// Prints the average it checks, and exits 0 only when it is right.
fn main() -> i32 {
  let mean:u32 = average(10, 20);
  println("average(10, 20) = ", mean);
  if mean != 15 { return 1; }
  return 0;
}
```

```text
error[E-TYPE-MISMATCH]: Expected u32, got u64.
  --> src/main.cairn:3:18
  |
3 |   let mean:u32 = average(10, 20);
  |                  ^^^^^^^
  = help: convert explicitly, u32(x), which traps outside u32's range, or compute in u32.
  = note: the base card states this rule: cairn rules E-TYPE-MISMATCH
```

```json
{"protocol": "cairn.diagnostic/2", "status": "rejected", "code": "E-TYPE-MISMATCH", "message": "Expected u32, got u64.",
 "line": 3, "column": 18, "trust": "prototype-not-verified", "expected_type": "u32", "actual_type": "u64", "file": "src/main.cairn",
 "card": "base", "repair_hint": "Convert explicitly, u32(x), which traps outside u32's range, or compute in u32."}
```

Nothing converts implicitly. `u32(average(10, 20))` writes the narrowing out and checks it at run time. The safety rules refuse a program the same way: a heap array is an owner, so using it after `let first = data;` is `E-MOVED`.

Every refusal names the rule card that states its rule, and `cairn rules E-TYPE-MISMATCH` prints that card. A refusal carries the fix in `repair_hint` when the compiler can state one without guessing. Every code the compiler emits has a rule card.

## Format, document, edit

```sh
cairn fmt demo                 # rewrite every .cairn in place; --check and --diff write nothing
cairn doc demo                 # a Markdown reference from the checked program, effect rows included
cairn emit demo/src/math.cairn # the C++ one file lowers to
```

`fmt` lexes its output again and rewrites a file only if the tokens and comments come out unchanged. The [editor extension](tools.md#the-editor-extension) shows diagnostics as you type, types and effect rows on hover, completion and rename, all from the same language server.

## The tour in twelve programs

Each program below shows one idea and checks its own results, returning 0 only when they hold. The suite builds and runs every one under the address and undefined-behaviour sanitizers (`tests/language/test_tour.py`), and each must exit 0.

### 1. Values, checked arithmetic, explicit conversions

Integer arithmetic traps on overflow in every build. The wrapping forms, such as `add_wrap`, say so by name. A conversion is a call such as `u32(x)`, and one that narrows checks the range.

```cairn
fn mean(a:u32, b:u32) -> u32 = u32((u64(a) + u64(b)) / 2);   // widen, then narrow with a check

fn main() -> i32 {
  let big:u32 = 4000000000;
  if mean(big, big) != big { return 1; }              // big + big would have trapped in u32
  if add_wrap(big, big) != 3705032704 { return 2; }   // modular on purpose
  let mut steps:u64 = 0;
  for i in 0..10 { steps += u64(i); }
  if steps != 45 { return 3; }
  return 0;
}
```

### 2. Views: borrowed arrays whose length is part of the type

A view is a borrowed array. `ro<T>[n]` borrows `n` elements to read, and `rw<T>[n]` borrows them to read and write. The length `n` is the view's extent, and it is an earlier parameter, a literal or a constant. Indexing is bounds checked, two `rw` views passed to one call cannot overlap, and a part `xs[lo..hi]`, a view of elements `lo` through `hi - 1`, costs one guard.

```cairn
const N:usize = 4 * 2;

fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for x in xs { t += x; } return t; }
fn last(xs:ro<u64>[N]) -> u64 = xs[N - 1];

fn main() -> i32 {
  stack cells:u64[N] = zeroed;                      // fixed storage on the stack, zero-initialized
  fill(N, cells, 1);
  if total(N, cells) != 36 || last(cells) != 8 { return 1; }
  if total(3, cells[2..5]) != 3 + 4 + 5 { return 2; }
  return 0;
}
```

### 3. Records, sums, `match` and `try`

A `struct` is a record, and an `enum` is a sum whose variants each carry at most one payload. `match` must give every variant an arm, and it has no wildcard arm. `try` yields the success payload, or returns the failure from the enclosing function. It is the only way to pass an error up.

```cairn
struct Point { x:i64; y:i64; }
enum Parsed { Ok(Point); Err(u8); }
enum Checked { Ok(i64); Err(u8); }

fn parse(x:i64, y:i64) -> Parsed { if x < 0 || y < 0 { return Err(7); } return Ok(Point(x, y)); }
fn manhattan(x:i64, y:i64) -> Checked { let p = try parse(x, y); return Ok(p.x + p.y); }

fn main() -> i32 {
  match manhattan(3, 4) {
    Ok(d) => { if d != 7 { return 1; } }
    Err(_) => return 2;
  }
  match manhattan(-1, 4) {
    Ok(_) => return 3;
    Err(code) => { if code != 7 { return 4; } }
  }
  return 0;
}
```

### 4. Generics and what a parameter promises

The checker checks each instance of a generic function or record at the types it is used with. A bound on a type parameter is a promise checked at the call: a trait, a kind (`copy`, `affine`), or a closed class of scalars that allows operators, such as `numeric`. A `family` makes one function for each value of a natural parameter.

```cairn
struct Pair[T:copy] { a:T; b:T; }

fn largest[T:numeric](a:T, b:T) -> T { if a < b { return b; } return a; }
fn twice[T:copy](x:T) -> Pair[T] = Pair(x, x);
fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);
family times = scale[2..5];                         // times_2, times_3, times_4

fn main() -> i32 {
  let p = twice(21);
  if largest(3, 9) != 9 || largest(2.5, 1.5) != 2.5 || p.a + p.b != 42 || times_3(7) != 21 { return 1; }
  return 0;
}
```

### 5. Traits, static and dynamic

A trait call is resolved at compile time from the type of `Self`. A `dyn` reference is an explicit borrow of two words, the object and a static table of its functions. A call through it adds `dispatch`, and the effect rows of every implementation, to the caller's effect row.

```cairn
trait Shape { fn area(self:ro<Self>) -> u64; }
struct Square { side:u64; }
struct Rect { w:u64; h:u64; }
impl Shape for Square { fn area(self:ro<Square>) -> u64 = self.side * self.side; }
impl Shape for Rect { fn area(self:ro<Rect>) -> u64 = self.w * self.h; }

fn both[A:Shape, B:Shape](a:ro<A>, b:ro<B>) -> u64 = area(a) + area(b);   // static
fn measure(s:ro<dyn Shape>) -> u64 = area(s);                                // through a table

fn main() -> i32 {
  let sq = Square(3);
  let r = Rect(2, 5);
  if both(sq, r) != 19 || measure(sq) != 9 || r.area() != 10 { return 1; }
  return 0;
}
```

### 6. Owners: moved, never copied, released at scope exit

An owner is a value that holds memory and releases it when it goes out of scope. `Buf[T]` is a zeroed heap array and an ordinary affine value: it moves, and it is never copied. `take` and `swap` are the only ways to get a value out of a place such as a field. A `linear struct` must be consumed exactly once on every path, and `defer` schedules one visible call for the end of the block.

```cairn
linear struct Token { id:u64; }
fn open(id:u64) -> Token = Token(id);
fn close(t:Token, closed:rw<u64>) { closed += 1; }

struct Slot { data:Buf[u64]; }
fn grow(s:rw<Slot>, n:usize) { let mut bigger = Buf[u64](n); swap(bigger, s.data); }   // the old array is freed here

fn main() -> i32 {
  let mut closed:u64 = 0;
  let mut slot = Slot(Buf[u64](2));
  {
    let token = open(1);
    defer close(token, closed);                     // runs at this block's exit, and counts as the consumption
    grow(slot, 8);
  }
  let data = take(slot.data);                        // leaves an empty Buf behind
  if closed != 1 || len(data) != 8 || len(slot.data) != 0 { return 1; }
  return 0;
}
```

### 7. Closures borrow exactly what they capture

A closure exists only as a `ro<fn(...)>` argument, so it never escapes or allocates. It borrows what it captures, for that call only.

```cairn
fn apply(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }

fn main() -> i32 {
  let mut cells = Buf[u64](4);
  let bias:u64 = 10;
  let mut calls:u64 = 0;
  apply(cells, |x:u64| -> u64 { calls += 1; return x + bias; });
  if cells[3] != 10 || calls != 4 { return 1; }
  return 0;
}
```

### 8. Tasks lease what they borrow

`spawn` runs a declared function as a task on a thread of its own and returns a ticket. The ticket is linear, so every path must `wait` for it. Until that `wait` the task leases what it borrows, and nothing else may touch what the task writes. Parts that are visibly disjoint may go to different tasks.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for x in xs { t = add_wrap(t, x); } return t; }

fn main() -> i32 {
  let n:usize = 900;
  let a:usize = 300;
  let b:usize = 600;
  let mut data = Buf[u64](n);
  let t1 = spawn fill(a, data[0..a], 0);
  let t2 = spawn fill(b - a, data[a..b], 300);
  let t3 = spawn fill(n - b, data[b..n], 600);
  wait(t1);
  wait(t2);
  wait(t3);
  let sum = spawn total(data);
  let answer = wait(sum);
  if answer != 404550 { return 1; }
  return 0;
}
```

### 9. Lanes are race free by construction

A lane may touch what any lane writes only at its own index `[i]`, or inside its own block of a constant size ([concurrency.md](concurrency.md#parallel-regions)). Lanes combine their values through `reduce`, into one value, and `scan`, into every prefix. The checked `+` is allowed where no order of evaluation can change whether it traps. A lane may call a closure that writes nothing it captured.

```cairn
fn map(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }

fn main() -> i32 {
  let n:usize = 10000;
  let k:u64 = 3;
  buffer squares:u64[n] = zeroed;
  buffer upto:u64[n] = zeroed;
  map(n, squares, |x:u64| -> u64 { return x * x * k; });
  let total = reduce + for i in n yield squares[i];
  let largest = reduce max for i in n yield squares[i];
  let whole = scan + upto parallel i in n yield squares[i];      // every prefix, in two passes over the lanes
  if total != 999850005000 || largest != 299940003 { return 1; }
  if whole != total || upto[2] != 0 + 3 + 12 || upto[n - 1] != total { return 2; }
  return 0;
}
```

### 10. Generators are library code

A recipe generates declarations for a type, and `derive R for T;` applies it. It is written as ordinary declarations with static `each` and `where` clauses and `$name` splices. `derive wire`, `derive eq`, `derive ord` and `derive hash` are recipes in the standard library.

```cairn
import std.core (Eq, Ord);
import std.sort as sort;

recipe sums for R {
  each f in R { require integer(f), "sums adds integer fields."; }
  pub fn sum_$R(n:usize, rows:ro<R>[n]) -> R = R(each f in R { total_$f(n, rows) });
  each f in R where t = typeof(f) {
    fn total_$f(n:usize, rows:ro<R>[n]) -> $t { let mut s:$t = 0; for row in rows { s += row.$f; } return s; }
  }
}

struct Trade { shares:u32; cents:u64; }
derive sums for Trade;
derive eq for Trade;
derive ord for Trade;
derive wire for Trade;

fn main() -> i32 {
  let mut book = Buf[Trade](3);
  book[0] = Trade(5, 700);
  book[1] = Trade(2, 900);
  book[2] = Trade(2, 100);
  let all = sum_Trade(book);
  if !same(all, Trade(9, 1700)) { return 1; }
  sort.sort(book);                                  // lexicographic, by the derived Ord
  if book[0].cents != 100 || book[2].shares != 5 { return 2; }
  stack bytes:u8[12] = zeroed;
  encode_Trade(bytes, book[2]);
  if !same(decode_Trade(bytes), book[2]) || wire_size_Trade() != 12 { return 3; }
  return 0;
}
```

### 11. Modules and the library

`module` names the module the declarations after it belong to, `pub` exports a declaration, and `import` brings a module, or listed names, into view. Growable arrays, maps and I/O are library code written in CAIRN, so their costs show in every caller's effect row.

```cairn
module inventory;
import std.core (Option);
import std.map as map;
import std.vec as vec;

pub struct Stock { names:map.Map[u64, u64]; log:vec.Vec[u64]; }
pub fn empty() -> Stock {
  let names = map.new[u64, u64]();                   // a call that allocates is a statement of its own,
  let log = vec.new[u64]();                          // never an operand: the cost stays where it can be seen
  return Stock(names, log);
}
pub fn add(s:rw<Stock>, item:u64, count:u64) {
  let seen = map.find(s.names, item);
  match seen {
    Some(slot) => s.names.vals[slot] += count;
    None => map.insert(s.names, item, count);
  }
  vec.push(s.log, item);
}
pub fn count(s:ro<Stock>, item:u64) -> u64 {
  match map.find(s.names, item) {
    Some(slot) => return s.names.vals[slot];
    None => return 0;
  }
}

module app;
import inventory;

pub fn main() -> i32 {
  let mut stock = inventory.empty();
  inventory.add(stock, 7, 2);
  inventory.add(stock, 7, 3);
  inventory.add(stock, 9, 1);
  if inventory.count(stock, 7) != 5 || inventory.count(stock, 8) != 0 || stock.log.len != 3 { return 1; }
  return 0;
}
```

### 12. The foreign boundary states its effects

The checker cannot read an `extern` function's body, so the declaration states what the function may do. A call to it needs `unsafe`, and its effect reaches every caller. `pure` and `effects(...)` on a signature are ceilings: the checker refuses a body whose effect row goes past them.

```cairn
extern fn getpid() -> i32 effects(io);

fn process() -> i32 { unsafe { return getpid(); } }
fn double(x:u64) -> u64 pure = x * 2;               // a ceiling: this body may trap and nothing else

fn main() -> i32 {
  if process() <= 0 || double(21) != 42 { return 1; }
  return 0;
}
```

Three example projects show the device half of the language. [examples/apps/gpu_pipeline](examples.md#examplesappsgpu_pipeline) has placement types and a device `reduce` and `compact`, [examples/apps/simulator](examples.md#examplesappssimulator) the same lanes on the host and on the device, and the `gpu.toml` of [examples/apps/analytics](examples.md#examplesappsanalytics) a `kernel fn` and queued work with `spawn ... after`. Their device runs happen only under `make gpu`.

## Where to go next

[language.md](language.md), [memory.md](memory.md), [abstractions.md](abstractions.md), [concurrency.md](concurrency.md), [devices.md](devices.md) and [numerics.md](numerics.md) are the reference: each rule with a program the compiler accepts and one it refuses. The [index](README.md) lists the rest in reading order.

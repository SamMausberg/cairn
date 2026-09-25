# Guide

Start with the program below: it reads input, computes on two tasks and prints, and the tables after it give the refusals a first program meets and the library calls it needs. The rest of the guide sets up a project that builds, runs, tests itself and refuses a wrong edit, then walks through twelve complete programs. At a terminal every command prints lines for a person; piped, or given `--format json`, it prints the JSON record a script or an agent reads.

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

The refusals a first program meets most often, from the programs the 1.1 evaluation's subjects wrote:

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

The library calls a program like this uses; `cairn doc --std --module std.text` prints one module's signatures:

| need | call |
|---|---|
| read standard input | `io.read_stdin(n, into)`: bytes read, 0 at the end |
| numbers from text | `text.parse_u64(n, s)`, `text.parse_i64(n, s)`: `Ok(v)` or `Err(ParseError)`; `text.find_byte(n, s, byte, from)` |
| a growing list | `vec.new[T]()`, `vec.push(v, x)`, `vec.extend_from(v, n, part)`, `v.len`, `v.data[i]` |
| a fixed array | `Buf[T](n)`, `n` zeroed elements; `stack chunk:u8[4096] = zeroed;` |
| output | `println(...)` and `print(...)`: integers, bools, `'c'`, strings and `u8` views; `eprintln` to standard error |
| threads | `let t = spawn f(args);` and `wait(t)`; `Group[T](k)`, `spawn f(args) into g;`, `collect(g)`, `wait(g)` |

## Install

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cairn doctor
```

`doctor` names the compilers it found and the optional tools that are present. Clang or GCC with C++20 is required. `libz3`, `nvcc`, Lean and QEMU each switch on one more check, and none is ever downloaded. Without an install, `python3 bin/cairn` is the same command.

## A project

```sh
cairn new demo
```

A project is a directory with a manifest, sources and task contracts. The manifest is data: it lists files and names a build kind, and it cannot run anything.

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

`u64` is a fixed-width integer and `+` traps on overflow. `&` and `^` are unsigned, and `shr` takes a count below the width. A body written `= expression;` returns that expression. `test average { ... }` runs only under `cairn test`, and `assert_eq` traps when its two values differ, printing both. A `let` is immutable, a call that returns nothing is a statement, and `main` returns the process exit status.

That project is the default template. Four more are starting points for real programs, and each carries a test block:

```sh
cairn new tool --template cli        # counts the files named after --, as wc does: std.env, std.fs, std.fmt
cairn new crc --template lib         # a library; cairn build crc --header writes its C header too
cairn new echo --template service    # a server that keeps every client's receive in flight on one I/O ring
cairn new squares --template parallel  # a host region, a checked reduce and a plan that splits it
```

The test suite creates, builds, runs and tests every template. Every new project also gets an `AGENTS.md` of under 25 lines. It tells a coding agent the check, test and run loop, where the rules are (the CAIRN skill and `cairn doc --std`), and never to widen a ceiling or weaken a test to get past a refusal. A one-line `CLAUDE.md` imports it for Claude Code, which reads that file instead.

## Check, run, test

```sh
cairn check demo
```

```text
typed: 3 functions
```

```json
{"status": "typed", "functions": 3, "library_functions": 0, "formal_status": "not-verified"}
```

`typed` means the program passed every static rule: syntax, types, ownership, leases, lanes, placement and effects. The three functions are the program's own, counting the test. When a program imports library modules, the library functions it reaches are checked with it and counted in `library_functions`. `formal_status` is always `not-verified`, because acceptance is not a proof.

```sh
cairn run demo
```

`run` builds a native executable in a fresh directory under `build/` and runs it with its memory capped. At a terminal the program writes to the terminal, and `cairn` exits with the program's status:

```text
average(10, 20) = 15
```

Piped, the record carries what it printed:

```json
{"status": "program-exited", "exit_code": 0, "stdout": "average(10, 20) = 15\n", "build_directory": "demo/build/demo-38_uge7b", "memory_limit_mib": 1024}
```

`cairn run demo -- one two` passes `one` and `two` to the program, which reads them through `std.env`.

The build directory holds the generated `program.cpp`, the runtime headers it includes, and `receipt.json`, which records what was compiled, with which compiler, and every function's effect row:

```json
"average": {"effects": ["trap"], "calls": [], "syntactic_check_sites": {"shift": 1, "overflow": 1}, "discharged_check_sites": {}}
```

`trap` means the function has a guard that can abort: here the shift count and the checked `+`. `discharged_check_sites` lists the guards the checker proved cannot fail, which the C++ leaves out. Nothing bounds `x` and `y`, so both guards stay. A function that allocates, writes through a borrow, starts a task or uses the device says so in the same list, and so does every function that calls it.

```sh
cairn test demo
```

```text
passed-finite-tests: 1 test, 1 contract, 81 cases
```

`test` runs each test block in its own process, so a failed assert fails only that test. Then it runs the manifest's contracts. A contract names a symbol and a finite list of cases, and `test` builds a shared library and calls the symbol once per case from Python. A wrong answer, or a child that exits abnormally, fails the run whatever it printed.

```json
{"schema": "cairn.task/1", "symbol": "average",
 "cases": [{"args": {"x": 0, "y": 0}, "return": 0}, {"args": {"x": 18446744073709551615, "y": 18446744073709551615}, "return": 18446744073709551615}]}
```

## A wrong edit

Change the type of a local and the compiler refuses the program with a code, a message and a position:

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
{"status": "rejected", "code": "E-TYPE-MISMATCH", "message": "Expected u32, got u64.", "line": 3, "column": 18, "file": "src/main.cairn",
 "card": "base", "repair_hint": "Convert explicitly, u32(x), which traps outside u32's range, or compute in u32."}
```

Nothing converts implicitly. `u32(average(10, 20))` writes the narrowing out and checks it at run time. The safety rules refuse a program the same way: a heap array is an owner, so using it after `let first = data;` is `E-MOVED`.

Every refusal names the rule card that states its rule, which `cairn rules` prints, and carries the fix when the compiler can state one without guessing. Every diagnostic code also has a paragraph in the reference, with a program it refuses.

## Format, document, edit

```sh
cairn fmt demo                 # rewrite every .cairn in place; --check and --diff write nothing
cairn doc demo                 # a Markdown reference from the checked program, effect rows included
cairn emit demo/src/math.cairn # the C++ one file lowers to
```

`fmt` re-lexes its output and rewrites a file only if the tokens and comments come out unchanged. The [editor extension](tools.md#the-editor-extension) shows diagnostics as you type, types and effect rows on hover, completion and rename, all from the same language server.

## The tour in twelve programs

The suite builds and runs every program below under the address and undefined-behaviour sanitizers (`tests/language/test_tour.py`), and each must exit 0.

### 1. Values, checked arithmetic, explicit conversions

Integers trap on overflow in every build, the wrapping forms say so by name, and a narrowing conversion is a range check.

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

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements, where `n` is an earlier parameter, a literal or a constant. Indexing is bounds checked, two `rw` views passed to one call cannot overlap, and a part `xs[lo..hi]` costs one guard.

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

A sum has one payload per variant. `match` is exhaustive and has no wildcard. `try` yields the success payload or returns the failure from the enclosing function; it is the only way to propagate an error.

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

Each instance of a generic is checked. A bound is a promise checked at the call: a trait, a kind (`copy`, `affine`), or a closed class of scalars that allows operators.

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

Dispatch is static on the type of `Self`. A `dyn` reference is an explicit two-word borrow, and a call through it adds `dispatch` and the rows of every implementation to the effect row.

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

`Buf[T]` is a zeroed heap array and an ordinary affine value. `take` and `swap` are the only ways out of a place, a `linear struct` must be consumed exactly once on every path, and `defer` schedules one visible call.

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

`spawn` runs a declared function on its own thread and hands back a linear ticket. Until `wait`, nobody else may touch what the task writes. Visibly disjoint parts may go to different tasks.

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

A lane may touch what it writes only at `[i]`. Lanes combine through `reduce`, into one value, and `scan`, into every prefix. The checked `+` is allowed where no order of evaluation can change whether it traps. A lane may call a closure that writes nothing it captured.

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

A recipe is ordinary declarations with static `each`, `where` and `$name` splices. `derive wire`, `derive eq`, `derive ord` and `derive hash` are recipes in the standard library.

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

`module` names what follows, `pub` exports, and `import` brings a module, or listed names, into view. Growable arrays, maps and I/O are library code written in CAIRN, so their costs show in every caller's effect row.

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

The checker cannot read an `extern` function's body, so the declaration states what it may do, a call to it needs `unsafe`, and the effect reaches every caller. `pure` and `effects(...)` are checked ceilings.

```cairn
extern fn getpid() -> i32 effects(io);

fn process() -> i32 { unsafe { return getpid(); } }
fn double(x:u64) -> u64 pure = x * 2;               // a ceiling: this body may trap and nothing else

fn main() -> i32 {
  if process() <= 0 || double(21) != 42 { return 1; }
  return 0;
}
```

The device half of the language (placement types, `kernel fn`, device `reduce` and `compact`, queued work with `spawn ... after`) is shown by [examples/apps/gpu_pipeline](examples.md#examplesappsgpu_pipeline) and [examples/apps/simulator](examples.md#examplesappssimulator). Their device runs happen only under `make gpu`.

## Where to go next

[language.md](language.md), [memory.md](memory.md), [abstractions.md](abstractions.md), [concurrency.md](concurrency.md), [devices.md](devices.md) and [numerics.md](numerics.md) are the reference: every rule with a program it accepts and one it refuses. The [index](README.md) gives the rest in reading order.

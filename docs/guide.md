# Guide

From a fresh checkout to a project that builds, runs and refuses a wrong edit, then twelve complete programs, one per idea. Every command prints JSON, so its output reads the same in a terminal, in a script and in an agent's transcript.

## Install

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cairn doctor
```

`doctor` names the compilers it found and which optional tools are present. Clang or GCC with C++20 is required. `libz3`, `nvcc`, Lean and QEMU each enable one more gate and are never downloaded. In a checkout without an install, `python3 bin/cairn` is the same command.

## A project

```sh
cairn new demo
```

A project is a directory with a manifest, ordered sources and task contracts. The manifest is data: it lists files and names a build kind, and it cannot run anything.

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

`typed` means the program passed every static rule: syntax, types, ownership, leases, lanes, placement and effects. `formal_status` is `not-verified` here and everywhere, because acceptance is not a proof.

```sh
cairn run demo
```

```json
{"status": "program-exited", "exit_code": 0, "build_directory": "demo/build/demo-38_uge7b", "memory_limit_mib": 1024}
```

`run` builds a native executable in a fresh directory under `build/` and runs it under an address-space cap. The directory holds the generated `program.cpp`, the runtime headers it includes and `receipt.json`, which records what was compiled, with what, and the effect row of every function:

```json
"average": {"effects": ["trap"], "calls": [], "syntactic_check_sites": {"shift": 1, "overflow": 1}, "discharged_check_sites": {}}
```

`trap` says the function carries a guard that can abort, and the two sites are the shift count and the checked `+`. `discharged_check_sites` lists the guards the checker proved cannot fail and the C++ leaves out; nothing bounds `x` and `y` here, so both guards stay. A function that allocated, wrote through a borrow, started a task or crossed to the device would say so in the same list, and so would everything that calls it.

```sh
cairn test demo
```

```json
{"status": "passed-finite-tests", "tests": [{"contract": "average.json", "cases": 81, "execution_exit_code": 0}]}
```

A contract names a symbol and finite cases. `test` builds a shared library and calls the symbol for each case from Python. A case that disagrees, or a child that exits abnormally, fails the run whatever was printed.

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

Nothing converts on its own; `u32(average(10, 20))` says the narrowing and checks it. The safety rules are refused the same way. A heap array is an owner, using it as a value moves it, and the old name is dead:

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

Every diagnostic code has a paragraph in the language reference with a program that is refused with it.

## Format, document, edit

```sh
cairn fmt demo                 # rewrite every .cairn in place; --check and --diff write nothing
cairn doc demo                 # a Markdown reference from the checked program, effect rows included
cairn emit demo/src/math.cairn # the C++ one file lowers to
```

`fmt` re-lexes its own output and leaves a file alone unless the tokens and comments are unchanged. `doc` prints each public function with its signature, its comment and its inferred effects. The [editor extension](tools.md#the-editor-extension) gives diagnostics as you type, hover types and effect rows, completion and rename over the same language server.

## The tour in twelve programs

The suite compiles and runs every program here under the address and undefined-behaviour sanitizers (`tests/language/test_tour.py`): a program exits 0 or the build fails.

### 1. Values, checked arithmetic, explicit conversions

Integers trap on overflow in every build. The wrapping forms say so by name. Nothing converts implicitly, and a narrowing conversion is a range check.

```cairn
fn mean(a:u32, b:u32) -> u32 = u32((u64(a) + u64(b)) / 2);   // widen, then narrow with a check

fn main() -> i32 {
  let big:u32 = 4000000000;
  if mean(big, big) != big { return 1; }              // big + big would have trapped in u32
  if add_wrap(big, big) != 3705032704 { return 2; }   // modular on purpose
  let mut steps:u64 = 0;
  for i in 0..10 { steps = steps + u64(i); }
  if steps != 45 { return 3; }
  return 0;
}
```

### 2. Views: borrowed arrays whose length is part of the type

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements, where `n` is an earlier parameter, a literal or a constant. Indexing is bounds checked, two `rw` views of one call cannot overlap, and a part `xs[lo..hi]` carries one guard.

```cairn
const N:usize = 4 * 2;

fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = t + xs[i]; } return t; }
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

A sum has one payload per variant. `match` is exhaustive and has no wildcard. `try` yields the success payload or returns the failure from the enclosing function, and it is the only propagation form.

```cairn
struct Point { x:i64; y:i64; }
enum Parsed { Ok(Point); Err(u8); }
enum Checked { Ok(i64); Err(u8); }

fn parse(x:i64, y:i64) -> Parsed { if x < 0 || y < 0 { return Parsed.Err(7); } return Parsed.Ok(Point(x, y)); }
fn manhattan(x:i64, y:i64) -> Checked { let p = try parse(x, y); return Checked.Ok(p.x + p.y); }

fn main() -> i32 {
  match manhattan(3, 4) {
    Checked.Ok(d) => { if d != 7 { return 1; } }
    Checked.Err(code) => { return 2; }
  }
  match manhattan(-1, 4) {
    Checked.Ok(d) => { return 3; }
    Checked.Err(code) => { if code != 7 { return 4; } }
  }
  return 0;
}
```

### 4. Generics and what a parameter promises

An instance is what is checked. A bound is a promise checked at the call: a trait, a kind (`copy`, `affine`) or a closed class of scalars that licenses operators.

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

Dispatch is static on the type of `Self`. A `dyn` reference is an explicit two-word borrow, and a call through it shows `dispatch` in the effect row together with the rows of every implementation.

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
fn close(t:Token, closed:rw<u64>) { closed = closed + 1; }

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
  apply(cells, |x:u64| -> u64 { calls = calls + 1; return x + bias; });
  if cells[3] != 10 || calls != 4 { return 1; }
  return 0;
}
```

### 8. Tasks lease what they borrow

`spawn` runs a declared function on its own thread and hands back a linear ticket. Until `wait`, nobody else may touch what the task writes. Visibly disjoint parts may go to different tasks.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 { let mut t:u64 = 0; for i in 0..n { t = add_wrap(t, xs[i]); } return t; }

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

A lane may touch what it writes only at `[i]`. `reduce` is how lanes combine. The checked `+` is offered where no order of evaluation can change whether it traps, and a lane may call a closure that writes nothing it captured.

```cairn
fn map(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }

fn main() -> i32 {
  let n:usize = 10000;
  let k:u64 = 3;
  buffer squares:u64[n] = zeroed;
  map(n, squares, |x:u64| -> u64 { return x * x * k; });
  let total = reduce + for i in n yield squares[i];
  let largest = reduce max for i in n yield squares[i];
  if total != 999850005000 || largest != 299940003 { return 1; }
  return 0;
}
```

### 10. Generators are library code

A recipe is ordinary declarations with static `each`, `where` and `$name` splices. `derive wire`, `derive eq`, `derive ord` and `derive hash` are recipes of the packaged library.

```cairn
import std.core (Eq, Ord);
import std.sort as sort;

recipe sums for R {
  each f in R { require integer(f), "sums adds integer fields."; }
  pub fn sum_$R(n:usize, rows:ro<R>[n]) -> R = R(each f in R { total_$f(n, rows) });
  each f in R where t = typeof(f) {
    fn total_$f(n:usize, rows:ro<R>[n]) -> $t { let mut s:$t = 0; for i in 0..n { s = s + rows[i].$f; } return s; }
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

`module` names what follows, `pub` exports, `import` brings a module (or listed names) into view. Growth, maps and I/O are library code written in CAIRN, so their costs show in every caller's effect row.

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
    Option.Some(slot) => { s.names.vals[slot] = s.names.vals[slot] + count; }
    Option.None => { map.insert(s.names, item, count); }
  }
  vec.push(s.log, item);
}
pub fn count(s:ro<Stock>, item:u64) -> u64 {
  match map.find(s.names, item) {
    Option.Some(slot) => { return s.names.vals[slot]; }
    Option.None => { return 0; }
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

An `extern` declaration has no body the checker can read, so it declares what it may do, and calling it needs `unsafe`. The effect travels to every caller. `pure` and `effects(...)` are checked ceilings.

```cairn
extern fn getpid() -> i32 effects(io);

fn process() -> i32 { unsafe { return getpid(); } }
fn double(x:u64) -> u64 pure = x * 2;               // a ceiling: this body may trap and nothing else

fn main() -> i32 {
  if process() <= 0 || double(21) != 42 { return 1; }
  return 0;
}
```

The device half of the language (placement types, `kernel fn`, device `reduce` and `compact`, queued work with `spawn ... after`) is shown by [examples/apps/gpu_pipeline](examples.md#examplesappsgpu_pipeline) and [examples/apps/simulator](examples.md#examplesappssimulator), which the suite runs when a GPU is present.

## Where to go next

[language.md](language.md), [abstractions.md](abstractions.md) and [concurrency.md](concurrency.md) are the reference: every rule with a program that is accepted and one that is refused. [library.md](library.md) is the standard library, [examples.md](examples.md) has real programs from a storage engine to a bare-metal image, and [verification.md](verification.md) says which claims are proved, which are SMT-checked and which are only tested.

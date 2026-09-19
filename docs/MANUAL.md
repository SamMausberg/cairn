# The CAIRN manual

CAIRN is a systems programming language whose compiler emits guarded C++20 for CPUs, CUDA for GPUs from the same source, and a freestanding image for bare-metal AArch64. This manual is the language, the library, the tools, the tests and the proofs in one file. [README.md](../README.md) says what the project is and how to install it; [AGENTS.md](../AGENTS.md) has the rules for working on it.

- [A tour in twelve programs](#a-tour-in-twelve-programs)
- [Language reference](#language-reference)
- [The standard library](#the-standard-library)
- [Tooling](#tooling)
- [Projects and the freestanding target](#projects-and-the-freestanding-target)
- [Examples](#examples)
- [Verification](#verification)
- [Compiler architecture](#compiler-architecture)
- [Testing](#testing)
- [Safety and trust](#safety-and-trust)
- [The AI edit protocol](#the-ai-edit-protocol)
- [Publication](#publication)
- [Remaining gates](#remaining-gates)

Every signature and effect row of the library is in [std_api.md](std_api.md), which `cairn doc --std` generates.

## A tour in twelve programs

Twelve complete programs. The test suite compiles and runs every one of them under the address and undefined-behaviour sanitizers (`tests/language/test_tour.py`): a program here exits 0 or the build fails.

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
  apply(len(cells), cells, |x:u64| -> u64 { calls = calls + 1; return x + bias; });
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
  let sum = spawn total(len(data), data);
  let answer = wait(sum);
  if answer != 404550 { return 1; }
  return 0;
}
```

### 9. Lanes are race free by construction

A lane may touch what it writes only at `[i]`. `reduce` is how you combine. The checked `+` is offered where no order of evaluation can change whether it traps, and a lane may call a closure that writes nothing it captured.

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
  let all = sum_Trade(len(book), book);
  if !same(all, Trade(9, 1700)) { return 1; }
  sort.sort(len(book), book);                       // lexicographic, by the derived Ord
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

The device half of the language (placement types, `kernel fn`, device `reduce` and `compact`, queued work with `spawn ... after`) is shown in [examples/apps/gpu_pipeline](#examplesappsgpu_pipeline) and [examples/apps/simulator](#examplesappssimulator), which the suite runs when a GPU is present.

## Language reference

This reference states implemented behavior. `docs/history/` holds the 0.2, 0.3 and 0.4 specifications, which record earlier proposals and must not be used to infer accepted features. Under `src/cairn/`, grammar lives in `compiler/syntax.py`, types, ownership and effects in `compiler/checking.py`, lowering in `compiler/codegen.py`, guards in `runtime/*.hpp`. Every construct below is executed natively by the test suite; none of it is a whole-compiler proof ([verification](#verification)).

Three rules explain most of the language.

Costs are visible. Nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row.

Borrows are second class. A borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references.

Short forms are contracts. `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

### Values and arithmetic

Identifiers are ASCII, comments (`//` to the end of the line) are UTF-8. Parameters and `let` locals are immutable, `let mut` is mutable, and no name may shadow another. `reg x = 0;` and `each i in n { }` are older spellings of `let mut x = 0;` and `for i in 0..n { }`.

The scalars are `bool`, the unsigned `u8 u16 u32 u64 usize` (`usize` is 64-bit), the signed `i8 i16 i32 i64`, and `f32 f64`. Literals are decimal and `0x` integers, floats with a point or an exponent, `true` and `false`, `'c'` (one byte, an integer literal compatible with `u8`) and `"text"`, a static `ro<u8>[n]` view with the escapes `\n \t \r \0 \\ \" \' \xNN`. A literal takes the type expected of it, `u64` or `f64` when nothing expects one.

`+ - *` abort on overflow in every build. `/` and `%` reject a zero divisor and the signed minimum over `-1`, and a signed remainder truncates toward zero. `add_wrap sub_wrap mul_wrap` are modular, `shl_wrap` and `shr` take a `usize` count below the width, `& | ^ ~` are unsigned, and `min` and `max` are integer-only.

```cairn
fn payload(total:u32, header:u32) -> u32 = total - header;   // aborts if header > total
fn share(part:u32, whole:u32) -> f64 = f64(part) / f64(whole);

fn main() -> i32 {
  let total:u32 = 1500;
  if payload(total, 20) != 1480 { return 1; }
  let seq:u32 = 4294967290;
  if add_wrap(seq, 10) != 4 { return 2; }                    // modular, and it says so
  let drift:i32 = -7;
  if drift / 2 != -3 || drift % 2 != -1 { return 3; }        // toward zero
  if shr(0xff00, 8) != 0xff || (0xf0 & 0x3c) != 0x30 { return 4; }
  let ratio = share(payload(total, 20), total);
  if u32(ratio * 100.0) != 98 || u8(255) != 255 { return 5; }  // truncates toward zero
  return 0;
}
```

Conversions are explicit type calls. Nothing converts on its own, and an integer target is range checked: narrowing aborts outside the target, and float to integer aborts on NaN or on a value the target cannot hold.

```cairn rejects E-TYPE-MISMATCH
fn payload(total:u32, header:u32) -> u32 = total - header;
fn main() -> i32 { let header:u64 = 20; return i32(payload(1500, header)); }
```

```text
Expected u32, got u64.
```

Floats compile with `-ffp-contract=off -fno-fast-math` (and `--fmad=false` on the device): no contraction and no reassociation is ever authorized. A failed guard aborts the process. It does not unwind, and it rolls nothing back.

### Functions and control flow

A block body needs explicit `return` statements and every path of a non-void function must return one. There is no block-tail return. An expression body, such as `fn payload(total:u32, header:u32) -> u32 = total - header;` above, is that one return.

The control forms are `if / else if / else`, `while`, `for i in lo..hi`, `break`, `continue` (to the nearest loop, also from a match arm) and nested `{ }` blocks. A `for` evaluates `lo` and then `hi` once, and an empty or reversed range does nothing. `&&` and `||` short-circuit. No loop implies parallelism.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n {
    if bytes[i] == 0 { continue; }                  // padding carries no checksum
    sum = add_wrap(sum, u32(bytes[i]));
  }
  return sum;
}

fn main() -> i32 {
  let frame = "GET /\0\0";
  if checksum(len(frame), frame) != 71 + 69 + 84 + 32 + 47 { return 1; }
  return 0;
}
```

```cairn rejects E-RETURN
fn kind(first:u8) -> u8 { if first == 71 { return 1; } }
```

```text
Not all paths of kind return.
```

### Records and sums

`struct` is a record, `enum` a tagged sum with zero or one payload per variant. Fields and payloads may be any value type (scalars, records, sums, owners), never a borrow and never `void`, and never their own type by value, directly or through an inline `Array`. A record is copyable when all of its fields are. A tag-only enum may be compared with `==`.

```cairn
struct Header { kind:u8; size:u32; }
struct Frame { head:Header; body:Buf[u8]; }      // a Buf makes Frame an owner
enum Op { Read; Write; }                         // tag-only: equality is allowed

fn empty(kind:u8) -> Frame = Frame(Header(kind, 0), Buf[u8](0));

fn main() -> i32 {
  let f = empty(7);
  if f.head.kind != 7 || len(f.body) != 0 || Op.Read == Op.Write { return 1; }
  return 0;
}
```

A record reaches itself through a `Buf`.

```cairn rejects E-RECORD-TYPE
struct Frame { head:u8; next:Frame; }
```

```text
Record fields cannot contain their own type by value; reach it through a Buf.
```

### match and try

`match` evaluates its subject once and needs exactly one arm per variant. There is no wildcard. A payload arm binds one fresh immutable value, and matching an owner consumes it.

```cairn
struct Header { kind:u8; size:u32; }
enum Parsed { Ok(Header); Short(usize); }

fn parse(n:usize, bytes:ro<u8>[n]) -> Parsed {
  if n < 5 { return Parsed.Short(n); }
  return Parsed.Ok(Header(bytes[0], u32(n) - 5));
}

fn main() -> i32 {
  match parse(len("\x07abcdefg"), "\x07abcdefg") {
    Parsed.Ok(head) => { if head.kind != 7 || head.size != 3 { return 1; } }
    Parsed.Short(got) => { return 2; }
  }
  match parse(2, "hi") {
    Parsed.Ok(head) => { return 3; }
    Parsed.Short(got) => { if got != 2 { return 4; } }
  }
  return 0;
}
```

```cairn rejects E-MATCH-COVERAGE
enum Op { Read; Write; Flush; }
fn cost(op:Op) -> u64 { match op { Op.Read => { return 1; } Op.Write => { return 2; } } }
```

```text
Every variant must have exactly one arm; missing Op.Flush.
```

`try e` takes a two-variant sum, success first and failure second. It yields the success payload, or returns the failure from the enclosing function or closure, whose return type must be a two-variant sum with the same failure payload. The families may differ, so a `Done[E]` failure propagates out of a function returning `Result[T, E]`. It is the only propagation form, and it is always written out.

```cairn
struct Header { kind:u8; size:u32; }
enum Read { Ok(Header); Err(u8); }
enum Sized { Ok(u32); Err(u8); }

fn head(n:usize, bytes:ro<u8>[n]) -> Read {
  if n < 5 { return Read.Err(1); }
  return Read.Ok(Header(bytes[0], u32(n) - 5));
}
fn body_size(n:usize, bytes:ro<u8>[n]) -> Sized {
  let h = try head(n, bytes);                     // or return Sized.Err(1) from here
  return Sized.Ok(h.size);
}

fn main() -> i32 {
  match body_size(7, "\x07abcdef") { Sized.Ok(size) => { if size != 2 { return 1; } } Sized.Err(e) => { return 2; } }
  match body_size(2, "hi") { Sized.Ok(size) => { return 3; } Sized.Err(e) => { if e != 1 { return 4; } } }
  return 0;
}
```

Inside a larger expression, a `try` may not sit beside an operand that already owns something: leaving from there would abandon it. Bind the `try` first.

```cairn rejects E-EFFECT-ORDER
struct Frame { body:Buf[u8]; size:usize; }
enum Sized { Ok(usize); Err(u8); }
fn size(v:u8) -> Sized { if v == 0 { return Sized.Err(1); } return Sized.Ok(2); }
fn build(v:u8, body:rw<Buf[u8]>) -> Sized { let f = Frame(take(body), try size(v)); return Sized.Ok(f.size); }
```

```text
Bind this try first: leaving from here would abandon an owner that another operand already holds.
```

### Arrays, views and parts

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements; `ro<T>` and `rw<T>` borrow one value, which the callee reads and assigns like the value itself. A borrow argument names a place (`frame`, `f.body`, `grid`), and an `ro<T>` parameter also accepts a temporary. An extent is a literal or an earlier immutable `usize` parameter, and `len(view)` reads that metadata.

```cairn
fn fill(n:usize, out:rw<u8>[n], value:u8) { for i in 0..n { out[i] = value; } }
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}
fn bump(seen:rw<u64>) { seen = seen + 1; }        // a single borrow, assigned by name

fn main() -> i32 {
  stack frame:u8[16] = zeroed;
  let mut seen:u64 = 0;
  fill(len(frame), frame, 3);
  bump(seen);
  if checksum(len(frame), frame) != 48 || seen != 1 { return 1; }
  return 0;
}
```

Extents agree by name and literal identity, not by value, and `len(v)` supplies the identity of `v`. `len("ready")` is the literal's byte count, so nothing is counted by hand.

```cairn rejects E-TYPE-MISMATCH
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 = u32(bytes[0]);
fn main() -> i32 { let a = Buf[u8](8); let b = Buf[u8](8); return i32(checksum(len(a), b)); }
```

```text
Expected ro<u8>[len(a)]@host, got ro<u8>[len(b)]@host.
```

A part `bytes[lo..hi]` goes wherever an array borrow is expected and carries one dynamic guard: `lo <= hi <= len`, and `hi - lo` equal to the callee's extent, which for a part may be any `usize` arithmetic. A part of a part guards once per level. The guard names the same expressions the call does, so bounds and extents are written from names, literals, fields, elements, operators, `len` and the arithmetic builtins. Bind a call to a name first (`E-CALL-SHAPE`), as a computed capacity is bound.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}

fn main() -> i32 {
  let frame = "\x07\x00\x00\x00hello";
  let n = len(frame);
  if checksum(n - 4, frame[4..n]) != 532 { return 1; }     // the payload, after a four-byte header
  if checksum(2, frame[4..n][0..2]) != 104 + 101 { return 2; }
  return 0;
}
```

```cairn rejects E-CALL-SHAPE
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 = u32(bytes[0]);
fn header_size() -> usize = 4;
fn main() -> i32 { let frame = "\x07\x00\x00\x00hi"; return i32(checksum(2, frame[header_size()..6])); }
```

```text
A part's bounds and extent are names, literals and arithmetic; bind a call first.
```

Read-only borrows may alias. A mutable borrow must not overlap any other argument of the same call (`E-ALIAS`): distinct fields of one record are disjoint, and two parts of one array are disjoint only when they visibly share a boundary, as `bytes[0..mid]` and `bytes[mid..n]` do. The entry guards check null, alignment, length and overlap numerically as well.

```cairn rejects E-ALIAS
fn swap_ends(n:usize, a:rw<u8>[n], b:rw<u8>[n]) { swap(a[0], b[0]); }
fn main() -> i32 { let mut frame = Buf[u8](8); swap_ends(4, frame[0..4], frame[2..6]); return 0; }
```

```text
A mutable view cannot be passed to overlapping call arguments.
```

Borrows cannot be stored, returned or bound to a local, and a part is written only as an argument (`E-VIEW-ALIAS`). The one exception is `let frame = "text";`, whose storage is static.

```cairn rejects E-VIEW-ALIAS
fn main() -> i32 { let mut frame = Buf[u8](8); let payload = frame[4..8]; return 0; }
```

```text
A part xs[lo..hi] is a borrow: it exists only as a view argument of a call.
```

### Owners and moves

Four forms of storage hold elements. Every type has an all-zero value, so all four are zero-initialized.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}

fn main() -> i32 {
  let n:usize = 4;
  buffer scratch:u8[n] = zeroed;      // lexical heap array, extent identity n
  stack header:u8[4] = zeroed;        // fixed local storage, at most 65536 bytes per function
  let mut body = Buf[u8](n);          // first-class, movable; its extent is len(body)
  let mut tag = Array[u8, 4]();       // an inline fixed array value
  body[0] = 9;
  tag[3] = 1;
  if checksum(n, scratch) + checksum(4, header) != 0 || checksum(len(body), body) != 9 { return 1; }
  if checksum(4, tag) != 1 { return 2; }
  return 0;
}
```

Owners are affine. Using one as a value (binding it, passing it by value, returning it, putting it in a field) moves it, and its name is dead afterwards (`E-MOVED`). Release at scope exit is implicit, and the `free` effect is charged where that release runs: the end of a block or match arm that still holds the owner, a `return` that leaves while it is still held, a function that was handed one by value and passed it on to nobody, and the place a new value is assigned over. A function that only drops an owner it was given carries `free` alone; one that hands the same owner on carries neither `free` nor `alloc`. An outer owner cannot be moved inside a loop (`E-MOVE-IN-LOOP`), a closure or a lane.

```cairn
fn sink(b:Buf[u8]) {}                                  // its row is free
fn hand_on(b:Buf[u8]) -> Buf[u8] = b;                  // its row is empty

fn main() -> i32 {
  let first = Buf[u8](4);
  sink(first);
  let second = Buf[u8](4);
  let same = hand_on(second);
  sink(same);
  return 0;
}
```

```cairn rejects E-MOVED
fn send(body:Buf[u8]) -> usize = len(body);
fn main() -> i32 { let body = Buf[u8](4); let once = send(body); let twice = send(body); return 0; }
```

```text
body was moved.
```

An owner cannot be moved out of a place. `take(place)` moves the value out and leaves the zero value behind, and `swap(a, b)` exchanges two places. Growth is library code, not a builtin: `std.vec` reallocates with `Buf`, `swap` and an assignment, so its allocation shows in every caller's row.

```cairn
struct Ring { slots:Buf[u8]; used:usize; }

fn grow(r:rw<Ring>, room:usize) {
  let mut bigger = Buf[u8](room);
  for i in 0..r.used { swap(bigger[i], r.slots[i]); }
  swap(bigger, r.slots);                            // the old array is released here
}

fn main() -> i32 {
  let mut ring = Ring(Buf[u8](2), 2);
  ring.slots[1] = 5;
  grow(ring, 8);
  let old = take(ring.slots);                       // leaves an empty Buf in the field
  if len(old) != 8 || old[1] != 5 || len(ring.slots) != 0 { return 1; }
  return 0;
}
```

```cairn rejects E-PARTIAL-MOVE
struct Ring { slots:Buf[u8]; used:usize; }
fn main() -> i32 { let mut ring = Ring(Buf[u8](2), 0); let slots = ring.slots; return 0; }
```

```text
An owner cannot be moved out of a place; use take() or swap().
```

A whole record is taken apart the way it was built. `let Ring(slots, used) = r;` consumes `r` and binds every field in declaration order (`let mut Ring(...)` to bind them mutably), which is the way out for an owner or a linear value kept inside a record. Anything that is not that record by value with one name per field is `E-UNPACK`.

A record of another module must be `pub`, and a `linear` record is taken apart only by the module that declares it (`E-PRIVATE`), so a protocol cannot be ended from outside.

```cairn
struct Ring { slots:Buf[u8]; used:usize; }

fn drain(r:Ring) -> usize {
  let Ring(slots, used) = r;                        // r is gone; slots is an owner again
  return len(slots) + used;
}

fn main() -> i32 {
  let mut ring = Ring(Buf[u8](4), 0);
  ring.used = 3;
  if drain(ring) != 7 { return 1; }
  return 0;
}
```

### Linear values and defer

A `linear struct` must be consumed exactly once on every path: leaving one unconsumed is `E-LINEAR-LEAK`, consuming it on some paths only is `E-LINEAR-BRANCH`. `defer call(...);` schedules one visible call for every normal exit of its block and counts as that consumption. An abort promises no cleanup.

```cairn
linear struct Lease { id:u64; }

fn acquire(id:u64) -> Lease = Lease(id);
fn release(l:Lease, freed:rw<u64>) { let Lease(id) = l; freed = freed + id; }

fn main() -> i32 {
  let mut freed:u64 = 0;
  {
    let lease = acquire(7);
    defer release(lease, freed);                    // runs at this block's exit
  }
  if freed != 7 { return 1; }
  return 0;
}
```

```cairn rejects E-LINEAR-BRANCH
linear struct Lease { id:u64; }
fn acquire(id:u64) -> Lease = Lease(id);
fn release(l:Lease) { let Lease(id) = l; }
fn main() -> i32 { let lease = acquire(7); if true { release(lease); } return 0; }
```

```text
lease is consumed on some paths only.
```

### Generics

A function takes type parameters and natural parameters. Arguments for them are inferred from the arguments, the literals and the expected type, or written out where nothing else says them (`largest[u64](3, 9)`, `scale[4](x)`, `Option[u64].None`). A literal argument takes its type from the other arguments and then from the expected result. `T(x)` converts, or constructs, at the instance's `T`.

Every instance is monomorphized on demand and checked as ordinary code, so an instance, not its template, is what typechecks. Arguments are typed where they are written: the template's module decides what its own text means, never what the caller's expressions mean.

```cairn
struct Column[T] { values:Buf[T]; used:usize; }

fn mean[T:numeric](n:usize, xs:ro<T>[n]) -> T {
  let mut total:T = 0;
  for i in 0..n { total = total + xs[i]; }
  return total / T(n);                          // T(n) converts at the instance's T
}
fn take_first[T:copy](c:ro<Column[T]>) -> T = c.values[0];

fn main() -> i32 {
  stack cents:u64[4] = zeroed;
  stack load:f64[2] = zeroed;
  for i in 0..4 { cents[i] = u64(4 + i * 4); }  // 4, 8, 12, 16
  load[0] = 1.5;
  load[1] = 2.5;
  let mut column = Column(Buf[u32](1), 1);
  column.values[0] = 7;
  let started:u32 = take_first(column);         // the literal 7 took u32 from the expected type
  if mean(4, cents) != 10 || mean(2, load) != 2.0 || started != 7 { return 1; }
  return 0;
}
```

A natural parameter may be a view's static extent, and is then inferred from a literal, a fixed array or a literal extent passed there. `family` names a bounded range of instances of one natural template.

```cairn
fn label[N:nat](text:ro<u8>[N]) -> usize = N;         // N comes from the literal
fn scale[K:nat](x:usize) -> usize = mul_wrap(x, K);
family gain = scale[1..4];                            // gain_1, gain_2, gain_3

fn main() -> i32 {
  stack row:u8[3] = zeroed;
  if label("retry after") != 11 || label(row) != 3 { return 1; }
  if scale[4](2) != 8 || gain_3(7) != 21 { return 2; }
  return 0;
}
```

### Bounds

A parameter promises more with a bound: a trait, a kind, or a closed class of scalars. `[T:copy]` may be used many times and dropped. `[T:affine]` may be dropped and kept in zeroed storage. Saying nothing admits `linear` values too, which must be consumed exactly once. The classes are `integer unsigned signed float numeric scalar`, and a class licenses the operators and literals of its types. Bounds combine with `+`, and generic records take them too.

A bound is checked where the instance is requested, so misuse is reported at the call, in the caller's terms.

```cairn
import std.core (Ord);

struct Span[T:copy] { low:T; high:T; }

fn span[T:Ord + copy](n:usize, xs:ro<T>[n]) -> Span[T] {
  let mut least:usize = 0;
  let mut most:usize = 0;
  for i in 1..n {
    if less(xs[i], xs[least]) { least = i; }
    if less(xs[most], xs[i]) { most = i; }
  }
  return Span(xs[least], xs[most]);
}

fn main() -> i32 {
  stack sizes:u32[4] = zeroed;
  for i in 0..4 { sizes[i] = u32(40 + i * 20); }
  sizes[2] = 12;
  let seen = span(4, sizes);
  if seen.low != 12 || seen.high != 100 { return 1; }
  return 0;
}
```

```cairn rejects E-BOUND
struct Window[T:copy] { first:T; last:T; }
fn widest[T:copy](x:T) -> Window[T] = Window(x, x);
fn main() -> i32 { let body = Buf[u8](2); let w = widest(body); return 0; }
```

```text
Buf[u8] is affine, not copy; widest needs [T:copy].
```

### Certifying a template

`cairn check --generics` checks each template once, at opaque witness types that offer only what its bounds promise and must be consumed exactly once (the strictest kind); a parameter bounded by a scalar class is checked at every type of that class.

`ok` means every instance whose arguments satisfy the bounds will check. Any other verdict names the first thing the body needed beyond its bounds: an operator on a bare `T`, a second use of a `T`, dropping one, zeroed storage of one. Where a parameter promised no kind, the verdict says so.

```cairn
fn smaller[T:numeric](a:T, b:T) -> T { if a < b { return a; } return b; }
fn count[T](x:T) -> u64 = 1;                          // drops x, which may be linear

fn main() -> i32 {
  if smaller(3, 9) != 3 || count(7) != 1 { return 1; }
  return 0;
}
```

```sh
cairn check --generics frame.cairn
```

```json
"generics": {
  "smaller": "ok",
  "count": "E-LINEAR-LEAK: x is linear: consume it, or defer its consumer, on every path. T may be linear here; [T:affine] or [T:copy] promises more."
}
```

The verdict covers the rules that need every row as well: ceilings, operand order and what a lane may reach. At a witness, a bounded member may do what its trait's ceiling allows; a member without a ceiling may do anything, which shows as `bound:Trait.member` in the row and which no `pure` template and no lane can absorb. A program that does not check leaves its templates `unknown`, never `ok`.

The command exits 1 unless every template certifies, so a project may hold itself to that, and every template of `std` certifies. Without the flag, a program's own unbounded templates are still accepted per instance, and a template nobody instantiates is listed in the receipt as `uninstantiated_templates`, never silently trusted.

### Traits

Dispatch is static, on the type of the `Self` argument, and a bound such as `[K:Hash + Eq]` is checked when the instance is made. An impl defines exactly its trait's members, with exactly their declared modes and types and `Self` replaced by the implementing type (`E-TRAIT-IMPL`), whether or not they are ever used, in one `impl` block. Two blocks for one `Self` type are `E-TRAIT-OVERLAP`, however they split the members. There is no inheritance and no implicit boxing.

```cairn
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
struct Beacon { seq:u16; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload) + 5; }
impl Frame for Beacon { fn size(self:ro<Beacon>) -> u64 = 8; }

fn bandwidth[A:Frame, B:Frame](a:ro<A>, b:ro<B>) -> u64 = size(a) + size(b);

fn main() -> i32 {
  let packet = Packet(40);
  let beacon = Beacon(3);
  if bandwidth(packet, beacon) != 53 || packet.size() != 45 { return 1; }
  return 0;
}
```

```cairn rejects E-TRAIT-IMPL
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u32 = self.payload; }
```

```text
Frame.Packet.size does not match Frame.size(ro<Packet>).
```

A ceiling on a trait's member (`fn size(self:ro<Self>) -> u64 pure;`) is part of the contract: every implementation is held to it (`E-EFFECT-CEILING`), and one that declares more is `E-TRAIT-IMPL`. That is what lets a bound promise it. `std.core` declares `less`, `same` and `hash` pure.

Every generic parameter of an impl appears in its `Self` type, so an instance is made from the type alone, and a generic impl applies exactly where its bounds hold. That is how `std.core` covers every scalar class at once (`impl[T:integer] Ord for T`).

One type has one implementation. A generic impl and a concrete impl that both match one type are refused, and so is a generic impl whose bound asks the question it answers (`impl[T:Frame] Frame for T`). Static calls, `dyn` borrows and `Dyn` values all resolve through that one answer.

```cairn rejects E-TRAIT-OVERLAP
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload); }
impl[T] Frame for T { fn size(self:ro<T>) -> u64 = 0; }
fn main() -> i32 { let packet = Packet(1); return i32(size(packet)); }
```

```text
Two impls of Frame match Packet: one Self type means one implementation.
```

Coherence is judged over the whole program, which is always in hand, since dependencies are vendored sources. Any module may implement a trait it can name for a type it can name, by hand or by `derive`, and two modules that both do are told so (`E-TRAIT-OVERLAP`). There is no orphan rule.

`value.f(args)` means `f(value, args)`, looked up first in the module that declares the receiver's type and before the builtins, so a type may have its own `len`. An unqualified `len(x)` is still the builtin; only the names that became builtins in 1.0 (`take swap transfer wait mmio_read mmio_write asm`) yield to a function the calling module can name. Privacy is judged from where the call is written. When two traits the type implements declare one member name, write the trait (`Frame.size(packet)`) or the call is `E-TRAIT-AMBIGUOUS`.

```cairn
module frames;
pub struct Ring { slots:Buf[u8]; used:usize; }
pub fn ring(room:usize) -> Ring = Ring(Buf[u8](room), 0);
pub fn len(r:ro<Ring>) -> usize = r.used;                 // a type may have its own len
pub fn add(r:rw<Ring>, byte:u8) { r.slots[r.used] = byte; r.used = r.used + 1; }

module app;
import frames;

pub fn main() -> i32 {
  let mut ring = frames.ring(4);
  ring.add(9);
  if ring.len() != 1 || len(ring.slots) != 4 { return 1; }  // the method, then the builtin
  return 0;
}
```

### dyn and Dyn

`ro<dyn Frame>` takes any named place whose type implements the trait. The call site builds a two-word reference (the object and a static table); members called on it go through the table, add the `dispatch` effect, and contribute the effect rows of every implementation. Dynamic references are borrows, so they are never values and nothing escapes through them. By-value owners pass through the table by move.

A member is dyn-compatible when only its receiver is a borrow or mentions `Self`, which excludes returning `Self`. A trait goes behind `dyn` only if every member is, because the table has a slot for each.

```cairn rejects E-DYN
trait Frame { fn size(self:ro<Self>) -> u64; fn dup(self:ro<Self>) -> Self; }
struct Packet { payload:u32; }
impl Frame for Packet {
  fn size(self:ro<Packet>) -> u64 = u64(self.payload);
  fn dup(self:ro<Packet>) -> Packet = Packet(self.payload);
}
fn total(f:ro<dyn Frame>) -> u64 = size(f);
fn main() -> i32 { let packet = Packet(4); return i32(total(packet)); }
```

```text
Frame.dup is not dyn-compatible: only its receiver may be a borrow or mention Self.
```

The owned form is explicit. `Dyn[Frame](Packet(40))` moves a value of any implementing type that is not `linear` to the heap (`alloc`, `free`). The box drops what it holds, so a `linear` value may not go in one (`E-LINEAR-STORAGE`); it may only be consumed.

A `Dyn[Frame]` is an ordinary affine value that can live in a `Vec` or a record, its members dispatch, and it lends itself wherever a `dyn Frame` reference is expected. An empty one, moved from or out of zeroed storage, is a guard failure when it is lent, never a null call.

```cairn
trait Frame { fn size(self:ro<Self>) -> u64; }
struct Packet { payload:u32; }
struct Beacon { seq:u16; }
impl Frame for Packet { fn size(self:ro<Packet>) -> u64 = u64(self.payload) + 5; }
impl Frame for Beacon { fn size(self:ro<Beacon>) -> u64 = 8; }

fn total(f:ro<dyn Frame>) -> u64 = size(f);              // a borrowed two-word reference

fn main() -> i32 {
  let mut queue = Buf[Dyn[Frame]](2);
  queue[0] = Dyn[Frame](Packet(40));                     // on the heap, released at scope exit
  queue[1] = Dyn[Frame](Beacon(3));
  if total(queue[0]) + total(queue[1]) != 53 { return 1; }
  if queue[1].size() != 8 { return 2; }
  return 0;
}
```

### Function values and closures

`fn(u64) -> u64` is a copyable code pointer to a plain declared function of values; an instantiated generic such as `ascending[u64]` qualifies. It can be stored in a record, its zero value is legal and calling it is a guard failure, and whoever calls through one inherits the effects of every function whose address is taken.

```cairn
struct Stage { apply:fn(u64) -> u64; }

fn twice(x:u64) -> u64 = x * 2;
fn drop_low(x:u64) -> u64 = x & 0xfffffff0;

fn run(n:usize, stages:ro<Stage>[n], x:u64) -> u64 {
  let mut value = x;
  for i in 0..n { let step = stages[i].apply; value = step(value); }
  return value;
}

fn main() -> i32 {
  let mut pipeline = Buf[Stage](2);
  pipeline[0] = Stage(twice);
  pipeline[1] = Stage(drop_low);
  if run(len(pipeline), pipeline, 21) != 32 { return 1; }
  return 0;
}
```

`ro<fn(u64) -> u64>` is a borrowed callable: pass a declared function, or write a closure in place. A closure captures its enclosing scope by reference, exists only as that argument, and therefore never allocates and never escapes. Its effects belong to the function that wrote it, and the callee shows `indirect_call`. Function types carry values and single borrows, not array views (`E-FN-TYPE`).

```cairn
fn scale(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }

fn main() -> i32 {
  let mut samples = Buf[u64](4);
  let gain:u64 = 3;
  let mut calls:u64 = 0;
  for i in 0..len(samples) { samples[i] = u64(i); }
  scale(len(samples), samples, |x:u64| -> u64 { calls = calls + 1; return x * gain; });
  if samples[3] != 9 || calls != 4 { return 1; }
  return 0;
}
```

What a closure captures it borrows for that call, `rw` where it writes. The same call cannot also lend, move or write a place the closure touches (`E-ALIAS`), so a callee never sees a closure reach anything it was given.

```cairn rejects E-ALIAS
fn scale(n:usize, xs:rw<u64>[n], f:ro<fn(u64) -> u64>) { for i in 0..n { xs[i] = f(xs[i]); } }
fn main() -> i32 {
  let mut samples = Buf[u64](4);
  scale(len(samples), samples, |x:u64| -> u64 { return x + samples[0]; });
  return 0;
}
```

```text
A mutable view cannot be passed to overlapping call arguments.
```

### Effects

Every function carries a row, the least fixed point of its own local effects and its callees' rows, with borrowed footprints renamed to the caller's arguments. A row says what may happen, never what is computed. The build receipt has it under `functions.<name>.effects`.

| effect | appears when |
| --- | --- |
| `read:x`, `write:x` | the borrow `x` is read, written |
| `local_read`, `local_write` | the function's own storage is read, written |
| `alloc`, `zero_init` | heap storage is taken, is zeroed |
| `free` | a release runs: a scope ends holding an owner, or a value lands on one |
| `stack_storage` | a `stack` array is declared |
| `gpu_alloc`, `gpu_free` | device scratch is taken, released |
| `transfer:h2d`, `d2h`, `d2d`, `h2h` | elements cross a placement boundary |
| `par:host`, `par:device` | a region runs as host threads, as CUDA lanes |
| `spawn`, `join` | a task starts, a ticket is awaited |
| `atomic`, `lock` | an atomic is accessed, a mutex entered |
| `indirect_call`, `dispatch` | a call goes through a function value, a `dyn` table |
| `lane:f` | lanes call the `fn` parameter `f` |
| `ffi:symbol`, `io` | a foreign symbol is called, under the labels its `extern` declares |
| `mmio`, `asm` | the machine is reached |
| `trap`, `diverge` | a guard may abort, the call graph has a cycle |
| `ffi_precondition` | the caller must supply live, initialized storage for a borrow |

`pure` and `effects(read:x, trap)` declare a ceiling, which is checked (`E-EFFECT-CEILING`). `pure` still allows `trap`, `diverge`, `local_read`, `local_write`, `stack_storage`, `zero_init`, `ffi_precondition` and the reads of what the function was lent, so the `checksum` below keeps `read:bytes` in its row.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 pure {
  let mut sum:u32 = 0;
  for i in 0..n { sum = add_wrap(sum, u32(bytes[i])); }
  return sum;
}

fn main() -> i32 {
  if checksum(len("abc"), "abc") != 294 { return 1; }
  return 0;
}
```

```cairn rejects E-EFFECT-CEILING
fn fill(n:usize, out:rw<u8>[n], value:u8) pure { for i in 0..n { out[i] = value; } }
```

```text
fill exceeds its declared effects.
```

### Operand order

A call that writes through a borrow or allocates cannot be a nested operand (`E-EFFECT-ORDER`). Bind it to a name first, so the cost is a statement of its own. A call that only releases stays an ordinary operand: a drop runs where C++ ends the scope, and it writes no place another operand can name.

```cairn rejects E-EFFECT-ORDER
fn fill(n:usize, out:rw<u8>[n], value:u8) -> usize { for i in 0..n { out[i] = value; } return n; }
fn main() -> i32 { let mut frame = Buf[u8](4); let done = fill(len(frame), frame, 1) + len(frame); return 0; }
```

```text
Bind a writing call to its own statement before using its result.
```

C++ leaves the order of operands open, so a call the outside world can observe (I/O, the machine, atomics and locks, a function value) may not sit beside another call in one expression, nor beside an operand whose own guard may abort: an element, a part, checked arithmetic. `&&`, `||` and a call's own arguments are sequenced, and are not affected.

```cairn rejects E-EFFECT-ORDER
extern fn putchar(c:i32) -> i32 effects(io);
fn say(c:i32) -> i32 { unsafe { return putchar(c); } }
fn main() -> i32 { return say(65) + say(66) - 131; }
```

```text
Bind this call first: it can be observed from outside, and the operand beside it could run, or abort, before or after it.
```

### extern and unsafe

An `extern` declaration names a C symbol, a signature and the effects the body may have; `extern "close" fn close_fd(fd:i32) -> i32 effects(io);` binds a symbol under another name. The body is invisible to the checker, so the effects are mandatory, and `ffi:write` propagates to every transitive caller. An extern's extent may name a later parameter, which is how C orders a pointer and its length.

Foreign calls, `mmio_read`, `mmio_write` and `asm` are legal only inside `unsafe { }`, which the receipt counts per function. A caller must supply live, initialized, correctly typed storage for each borrow: the numerical entry guards cannot establish provenance, and that obligation is the `ffi_precondition` in the row.

```cairn
extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);

fn say(n:usize, text:ro<u8>[n]) { unsafe { let sent = write(1, text, n); } }

fn main() -> i32 {
  say(len("frame sent\n"), "frame sent\n");
  return 0;
}
```

```json
"say": ["ffi:write", "ffi_precondition", "io", "read:text", "trap"]
```

### Tasks and leases

`let t = spawn f(args);` runs a declared function on its own thread. The arguments are evaluated at the spawn and carried by value, so a task never reads the spawner's locals. `t` is a linear ticket bound to its scope. `wait(t)` consumes it and returns `f`'s result, and it must do so on every path of the same function; the ticket cannot be stored, passed or returned.

Until the `wait`, every place lent to the task is leased: nobody may write what the task reads or touch what it writes (`E-LEASED`), including by moving the owner. Read-only lending is shared freely. Visibly disjoint parts of one array may be lent mutably to different tasks. A part ends where the next begins, the bounds are literals or names that cannot change, and because every lent part was guarded `lo <= hi`, the order of the bounds chains through the parts in between, so a K-way split works.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum = add_wrap(sum, xs[i]); }
  return sum;
}

fn main() -> i32 {
  let n:usize = 900;
  let a:usize = 300;
  let b:usize = 600;
  let mut samples = Buf[u64](n);
  let first = spawn fill(a, samples[0..a], 0);              // three disjoint parts, three threads
  let second = spawn fill(b - a, samples[a..b], 300);
  let third = spawn fill(n - b, samples[b..n], 600);
  wait(first);
  wait(second);
  wait(third);
  let sum = spawn total(len(samples), samples);            // a view lends the elements: len stays readable
  if wait(sum) != 404550 { return 1; }
  return 0;
}
```

An owner lent whole (`rw<Buf[T]>`) lends its `len` too. A temporary given to a task's single borrow rides along by value. A closure cannot follow a task to another thread (`E-SPAWN`).

```cairn rejects E-LEASED
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn main() -> i32 {
  let mut samples = Buf[u64](8);
  let t = spawn fill(len(samples), samples, 0);
  samples[0] = 9;
  wait(t);
  return 0;
}
```

```text
samples is lent to t until wait(t).
```

A lease names the place that was lent, not the local it is rooted in. Lending `box.a` leases `box.a`, so another task may take `box.b` at the same time, and `len(box.a)` still reads while a task holds that field's elements. Lending the record itself leases every field inside it, and a field of a record a task holds is not readable.

```cairn
struct Pair { left:Buf[u64]; right:Buf[u64]; }
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn main() -> i32 {
  let n:usize = 64;
  let mut pair = Pair(Buf[u64](n), Buf[u64](n));
  let left = spawn fill(len(pair.left), pair.left, 0);      // two fields, two threads
  let right = spawn fill(len(pair.right), pair.right, 100);
  let k = len(pair.left);                                   // the field's header, which its elements do not cover
  wait(left);
  wait(right);
  if k != n || pair.left[1] != 1 || pair.right[1] != 101 { return 1; }
  return 0;
}
```

The same field twice is one piece of storage twice, and replacing a lent field's cell (`pair.left = Buf[u64](2)`) is refused for the same reason: the task's view lives in that cell.

```cairn rejects E-LEASED
struct Pair { left:Buf[u64]; right:Buf[u64]; }
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn main() -> i32 {
  let n:usize = 64;
  let mut pair = Pair(Buf[u64](n), Buf[u64](n));
  let left = spawn fill(len(pair.left), pair.left, 0);
  let right = spawn fill(len(pair.left), pair.left, 100);
  wait(left);
  wait(right);
  return 0;
}
```

```text
pair.left is lent to left until wait(left).
```

### Atomics and mutexes

`Atomic[T]` (the integers and `bool`) and `Mutex[T]` are declared in place and shared by `ro` borrow. They are the only interior mutability in the language, and they are never stored in a record, passed by value or returned (`E-PINNED`). Every atomic access names its memory order: `load`, `store`, `swap`, `fetch_add`, `fetch_sub`, `fetch_and`, `fetch_or`, `fetch_xor` and `compare_exchange(expected, desired, Order.seq_cst, Order.seq_cst)`. The effects are `spawn`, `join`, `atomic` and `lock`.

```cairn
fn count_live(n:usize, xs:ro<u64>[n], live:ro<Atomic[u64]>) {
  for i in 0..n { if xs[i] > 0 { let before = live.fetch_add(1, Order.relaxed); } }
}

fn main() -> i32 {
  let mut samples = Buf[u64](4);
  samples[1] = 7;
  samples[3] = 9;
  let live = Atomic[u64](0);
  let t = spawn count_live(len(samples), samples, live);
  wait(t);
  if live.load(Order.seq_cst) != 2 { return 1; }
  return 0;
}
```

A mutex has one operation, and it may return a value. No guard object exists to escape, the closure cannot name the mutex it holds (`E-ALIAS`), and a thread that reaches the same mutex again through another borrow traps instead of relocking. Lanes may use atomics and mutexes.

```cairn
fn main() -> i32 {
  let bytes_sent = Mutex[u64](0);
  bytes_sent.with(|total:rw<u64>| { total = total + 1480; });
  let seen = bytes_sent.with(|total:rw<u64>| -> u64 { return total; });
  if seen != 1480 { return 1; }
  return 0;
}
```

### Parallel regions

`parallel i in n { body }` runs one lane per index and completes before the next statement.

Lanes are race free by construction. Whatever any lane writes may be touched only at element `[i]` (`E-PARALLEL-RACE`), a shared scalar cannot be assigned (`E-PARALLEL-WRITE`: use `reduce`), and lanes cannot return, nest or move an outer owner. A lane's own row, and the row of everything it calls, must be pure-like (`E-PARALLEL-CALL`); a host lane may also allocate, use atomics and lock.

```cairn
fn shade(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }

fn main() -> i32 {
  let n:usize = 10000;
  let gain:u64 = 3;
  buffer squares:u64[n] = zeroed;
  shade(n, squares, |x:u64| -> u64 { return x * x * gain; });   // reads its captures, writes none
  if squares[99] != 29403 { return 1; }
  return 0;
}
```

```cairn rejects E-PARALLEL-RACE
fn shade(n:usize, out:rw<u64>[n]) { parallel i in n { out[0] = u64(i); } }
```

```text
out is written by lanes, so every lane may touch only out[i].
```

A lane may call, or hand on to a helper, a `fn` parameter of its function. That leaves `lane:f` in the row, and in a declared ceiling, renamed up the call graph like `read:x`.

Whatever is finally passed is judged where it is written: the closure above is accepted, a closure that writes what it captured is not, and a stored `fn` value counts as any function of its type whose address was taken. Dispatch from a host lane, from a function it calls, or from such a closure is judged against every implementation.

```cairn rejects E-PARALLEL-CALL
fn shade(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }
fn main() -> i32 {
  let n:usize = 64;
  buffer squares:u64[n] = zeroed;
  let mut calls:u64 = 0;
  shade(n, squares, |x:u64| -> u64 { calls = calls + 1; return x; });
  return 0;
}
```

```text
shade calls f from parallel lanes, where it cannot write:calls.
```

Host lanes are a pool. The first host region of a process creates them and every later one reuses them, so a region costs a hand-off rather than a thread, and a region of fewer than sixteen thousand elements is compiled as the ordinary loop it replaces and starts nothing at all. The pool holds one thread per core, or the number `CAIRN_LANES` names.

How many lanes there are is never observable in a result, only in the time a region takes. Start-up makes host regions pay off only for large `n`; `evidence/v1_0/gpu/benchmark.json` records measured break-even points.

### reduce and compact

`reduce` combines with one of `add_wrap mul_wrap & | ^ min max` on integers, or `+ *` on floats. On the host it is an in-order fold. Over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats.

Checked `+` is offered on unsigned integers, where no partial sum can overflow unless the total does, so the trap cannot depend on the order; on the device the sum carries an overflow flag through the reduction and the host traps. Signed `+` and integer `*` are not offered, because a partial result can overflow alone.

```cairn
fn main() -> i32 {
  let n:usize = 10000;
  buffer weights:u64[n] = zeroed;
  parallel i in n { weights[i] = u64(i % 7); }
  let total = reduce + for i in n yield weights[i];       // checked: unsigned, order cannot matter
  let largest = reduce max for i in n yield weights[i];
  if total != 29994 || largest != 6 { return 1; }
  return 0;
}
```

`compact` writes the stable selected prefix into existing storage of capacity exactly `n`. It evaluates the predicate once per input and the projection only when selected, never reads its output, leaves the tail unchanged and allocates nothing on the host. Over a `@device` output it is stable stream compaction whose scan needs device scratch, which shows as `gpu_alloc` and `gpu_free`; a device `reduce` likewise.

Its one unchecked store is justified by seventeen affine certificates, checked before every emission and proved sound in Lean together with in-bounds stores and stable selection for the loop model ([verification](#verification)).

```cairn
fn keep_live(n:usize, out:rw<u64>[n], xs:ro<u64>[n]) -> usize {
  let used = compact out for i in n where xs[i] > 0 yield xs[i];
  return used;
}

fn main() -> i32 {
  let n:usize = 4;
  buffer samples:u64[n] = zeroed;
  buffer live:u64[n] = zeroed;
  samples[1] = 7;
  samples[3] = 9;
  let kept = keep_live(n, live, samples);
  if kept != 2 || live[0] != 7 || live[1] != 9 || live[2] != 0 { return 1; }   // the tail is untouched
  return 0;
}
```

### Placement and device memory

A view's placement is part of its type: `@host` (the default), `@pinned`, `@unified`, `@device`. A region whose body indexes a `@device` view runs as CUDA lanes, otherwise on host threads; the emitted lane body is the same lambda either way.

Host code cannot index `@device` memory and device lanes cannot index host memory (`E-PLACEMENT`); `@unified` is visible to both. A view may be lent as what its memory also is: `@pinned` or `@unified` where a `@host` view is asked for, `@unified` where a `@device` view is, never the other way, so host helpers serve page-locked staging buffers unchanged.

`transfer(dst, src)` is the only way elements cross a placement boundary. Extents agree by identity, parts such as `transfer(a[0..k], b[2..6])` by one length guard, and the two sides may not overlap.

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}

fn run(n:usize, host_x:ro<f32>[n], host_out:rw<f32>[n]) {
  buffer x:f32[n]@device = zeroed;                      // a scoped device owner
  buffer y:f32[n]@device = zeroed;
  buffer out:f32[n]@device = zeroed;
  transfer(x, host_x);
  saxpy(n, out, x, y, 2.0);
  transfer(host_out, out);
}
```

Whatever a device lane reaches is device code. A helper with a host view parameter, or a host-only construct in its body, is refused there (`E-PLACEMENT`), while plain pure helpers run on either side. String literals and host owners are host memory and stay out of device code. A device `compact` checks its predicate and its projection as device lanes.

```cairn rejects E-PLACEMENT
fn peek(m:usize, host_side:ro<u64>[m]) -> u64 = host_side[0];
fn go(n:usize, d:rw<u64>[n]@device, m:usize, host_side:ro<u64>[m]) {
  parallel i in n { d[i] = peek(m, host_side); }
}
```

```text
A device lane reaches peek, where host_side is a host view.
```

`kernel fn` declares a device helper. Its body is device code, it may be called only from device lanes and other kernels (`E-PLACEMENT` anywhere else), and it obeys the device lane rules.

```cairn
kernel fn at(w:usize, n:usize, grid:ro<f32>[n]@device, x:usize, y:usize) -> f32 = grid[y * w + x];

fn blur(w:usize, n:usize, out:rw<f32>[n]@device, grid:ro<f32>[n]@device) {
  parallel i in n { out[i] = 0.5 * at(w, n, grid, i % w, i / w); }
}
```

### Queued device work

Device work can be queued instead of awaited. `spawn transfer(...)` and `spawn parallel ... after t { }` put a transfer or a device region on a stream of its own and return at once, so the host and other queued work go on. The ticket is the same linear, scope-bound value (its type is `Ticket[void]@device`): it holds a lease on every view the work touches, `rw` where it writes, until `wait`, and only completion restores ordinary access.

`after a, b` orders the new work behind live tickets by device events, never by stopping the host. Work queued after a ticket may touch what that ticket, and whatever it was itself queued after, holds; nothing else may. Only device work is queued this way (`E-SPAWN`). Host work and blocking I/O become asynchronous by spawning the function that does them, which leases their buffers the same way. A failed enqueue or a lost device traps, and there is no cancellation.

```cairn
fn stage(n:usize, host_x:ro<f32>[n], x:rw<f32>[n]@device, out:rw<f32>[n]@device) {
  let up = spawn transfer(x, host_x);
  let work = spawn parallel i in n after up { out[i] = 2.0 * x[i]; };
  wait(up);
  wait(work);
}
```

### Constants

A `const` folds at compile time, from literals, from other constants in any order of declaration, from arithmetic, comparison, `&&`, `||`, `!` and the scalar conversions. The folding is exact: integer division and remainder go toward zero as they do at run time, and in an `f32` constant every literal, conversion and operation rounds once, as the machine will, with `f64` as the operand of a conversion. The result must fit its type and be finite. Division by zero and a constant defined through itself are `E-CONST`.

A constant natural may stand wherever a natural is written: a static extent in a declaration or a signature, `Array[u64, N]`, `scale[N](x)`.

```cairn
const WIDTH:usize = 8;
const CELLS:usize = WIDTH * HEIGHT;                 // order of declaration does not matter
const HEIGHT:usize = 4;
const MARGIN:i64 = -7 / 2;                          // toward zero, as at run time

fn last(cells:ro<u64>[CELLS]) -> u64 = cells[CELLS - 1];

fn main() -> i32 {
  stack grid:u64[CELLS] = zeroed;
  let mut tile = Array[u64, WIDTH]();
  grid[CELLS - 1] = 9;
  tile[WIDTH - 1] = 1;
  if last(grid) != 9 || MARGIN != -3 || len(tile) != WIDTH { return 1; }
  return 0;
}
```

```cairn rejects E-CONST
const HEAD:u32 = TAIL + 1;
const TAIL:u32 = HEAD;
```

```text
HEAD is defined in terms of itself.
```

### Modules

`module net.http;` names the module of the declarations that follow; a file without one shares the root namespace. `pub` exports a declaration, and impl members are always public. `import net.http;` lets you write `http.get(...)` and `http.Request`, `import a.b as c;` renames, and `import std.core (Option, Result);` also brings those names in unqualified. An import may not hide a name the importing module declares (`E-DUPLICATE`). A private record's fields are as private as the record.

A `family` over another module's template needs that template to be `pub`, and its instances belong to the module that wrote the family (`pub family` exports them). `derive wire for R;` is written in the module that declares `R`.

```cairn
module codec;
import std.core (Option);

struct Limits { largest:u32; }                            // private: so are its fields
pub fn accepts(size:u32) -> Option[u32] {
  let limits = Limits(1480);
  if size > limits.largest { return Option.None; }
  return Option.Some(size + 5);
}

module app;
import codec as packet;
import std.core (Option);

pub fn main() -> i32 {
  match packet.accepts(40) { Option.Some(total) => { if total != 45 { return 1; } } Option.None => { return 2; } }
  match packet.accepts(9000) { Option.Some(total) => { return 3; } Option.None => {} }
  return 0;
}
```

```cairn rejects E-DUPLICATE
module lib;
pub const LIMIT:usize = 99;
module app;
import lib (LIMIT);
const LIMIT:usize = 1;
pub fn main() -> i32 { return i32(LIMIT); }
```

```text
import (LIMIT) collides with app.LIMIT; drop one or use the qualified name.
```

A project's files are compiled together in manifest order. The `std.*` modules ship inside the package and are linked on demand. Only project modules and `std.*` can be imported, and nothing is downloaded.

### Projects

`cairn.toml` lists ordered sources and independent task files. It is data, never a build script. A command takes a source file, a project directory or a manifest by path, so one tree may hold a second configuration (`cairn run app/gpu.toml`).

```toml
[project]
name = "gateway"
sources = ["src/frames.cairn", "src/main.cairn"]

[dependencies]
geometry = "deps/geometry"

[build]
kind = "exe"            # or "library"
arch = "baseline"       # or a named profile of the host family (x86-64, AArch64)
target = "hosted"       # or a board such as "aarch64-virt"
```

A freestanding `target` refuses any program whose effect rows need a hosted runtime ([the freestanding profile](#projects-and-the-freestanding-target)). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device), and builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible. A library exports every function; an executable contains only what its `main` reaches, and `main` may live in a module, while the receipt still covers everything that was checked.

`--debug` adds symbols and `#line` maps to the authored files. `--incremental` compiles one object per module against a shared interface header and reuses an object only when its unit, that header, the command line, the runtime headers and the compiler version hash to the same key, and the stored object still matches the digest written beside it in `build/objects`. It gives up inlining across modules, and device programs and freestanding images stay one unit either way ([tooling](#tooling) has the measured sessions).

### Dependencies

`[dependencies] geometry = "deps/geometry"` names a project vendored inside this one's root, with its own `cairn.toml` and its own dependencies loaded first, at most 16 per manifest and 4 deep, a diamond loaded once. A dependency contributes modules only, and only what it marks `pub` is reachable. Nothing is fetched, no path leaves the root, no path has a `.` or `..` segment, no symbolic link is followed, and the receipt pins each dependency's manifest and sources by hash.

A dependency's manifest is read by the same checker as yours, so an unknown table or option is refused there too, and its `[build]`, which the build ignores, must still name a known kind, architecture and target. One directory is one project under one name: a second name for it is an error, not a diamond, and one name is one project of the build, the root's own included.

A module belongs to exactly one project. No project declares a `std.*` module, reopening a module another project declared names both projects and fails, and so does a file with no `module` header that would silently continue a dependency's. An executable's entry point is searched only in the sources this manifest lists, so a dependency neither supplies `main` nor denies you yours.

### Recipes

A recipe is a generator written as library code: ordinary declarations (functions, records, trait `impl`s, `kernel fn`) over a record schema (`for R`), naturals (`recipe tiles[W:nat, H:nat]`) and names of functions (`recipe fieldwise[F:fn] for R`). `derive name[arguments] for Type;` applies one.

Inside a recipe, `each f in R { }` iterates statically over a record's fields and `each k in lo..hi { }` over a natural range, at declaration level, at statement level, in a record's field list, or among the arguments of a call, where it splices one or several expressions per step (`each f in R { lo.$f, hi.$f }`). `fold | each ... { e }` joins the expansions with one operator, or with any function of two operands (`fold add_wrap each ...`, `fold lib.chain each ...`).

`where a = offset(f), t = typeof(f)` names static values. They are computed from naturals, comparisons, `min`, `max`, a static `fold` over a static `each` (`where width = fold + each f in R { bytes(f) }`) and the facts `bytes bits offset index count typeof unsigned signed integer float scalar record`. Static values are naturals and booleans; a negative result or a division by zero is `E-RECIPE-STATIC`.

`$name` splices one into an identifier (`encode_$R`, `value.$f`, `shift_$k`), and a whole `$name` is that natural or that type. The longest static name wins, so `$R_columns` is `$R` then `_columns`. Only the bare `for` parameter `R` is the type itself: every other static needs its `$`, so an ordinary identifier that happens to share a `where` name is left alone. `require condition, "message";` states the admissible inputs (`E-DERIVE-DOMAIN`, or the code the message opens with).

```cairn
module layout;

pub recipe columns for R {                                  // R: the record it is derived for
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
  pub fn $R_columns_new(rows:usize) -> $R_columns = $R_columns(each f in R where t = typeof(f) { Buf[$t](rows) });
  pub fn $R_get(c:ro<$R_columns>, i:usize) -> R = R(each f in R { c.$f[i] });
  pub fn $R_set(c:rw<$R_columns>, i:usize, row:R) { each f in R { c.$f[i] = row.$f; } }
}

module app;
import layout;

struct Particle { x:f32; mass:f64; }
derive layout.columns for Particle;                         // Particle_columns, _new, _get, _set

pub fn main() -> i32 {
  let mut columns = Particle_columns_new(4);
  Particle_set(columns, 2, Particle(1.5, 3.0));
  let row = Particle_get(columns, 2);
  if row.mass != 3.0 || row.x != 1.5 { return 1; }
  return 0;
}
```

```cairn rejects E-DERIVE-DOMAIN
module layout;
pub recipe columns for R {
  each f in R { require scalar(f), "columns holds scalar fields."; }
  pub struct $R_columns { each f in R where t = typeof(f) { $f:Buf[$t]; } }
}
module app;
import layout;
struct Frame { body:Buf[u8]; }
derive layout.columns for Frame;
```

```text
columns holds scalar fields.
```

A function name is spliced as the deriving module wrote it (`$F(v.$f)` calls it; inside a longer identifier, `$F_$R`, it gives its last segment) and means what it means there, so one generic function serves fields of different types and privacy is judged from the deriving module. Recipes take types and naturals, not expressions: behavior reaches a generated function as a `fn` value or a closure. A recipe over a natural range generates one function per step, as `family` does for one template.

Expansion happens before checking and reads nothing but the recipe and the schema, so it is a function of its inputs; `cairn expand` prints what it produced, as source. Expansion is hygienic: a function, type, trait or constant the recipe names means what it means in the recipe's own module and is spelled out in full where the code lands (`helper(x)` becomes `lib.helper(x)`, so the deriving module's own `helper` cannot capture it, and a private one is `E-PRIVATE` from there). Only `$` splices, and the names they build, belong to the deriving module.

What a recipe generates is ordinary code of the deriving module, checked like any other. Its signatures and effect ceilings are its contract, and privacy is judged where `derive` is written, though a recipe reaches its own module's helpers by their public path.

A generated name that already exists is `E-DERIVE-COLLISION`, and static iteration is bounded per level and in total (`E-EXPANSION-LIMIT`). A derivation for a record that another derivation generates waits for it, whatever order they are written in. The receipt pins every recipe by the hash of its tokens (`recipes`) beside the list of `derivations`.

A bare recipe name the program does not declare falls back to the packaged `std.<name>`, then to `std.derived`. `derive wire` emits fixed-width unsigned little-endian codecs in declaration order with no padding; it is no longer compiler code but the packaged recipe `std.wire`. `std.derived` holds `derive eq`, `derive ord` (lexicographic) and `derive hash`: impls of `std.core`'s traits for any record whose fields already have them, checked like impls written by hand.

```cairn
import std.core (Eq, Ord);

struct Header { kind:u8; size:u32; }
derive wire for Header;
derive eq for Header;
derive ord for Header;

fn main() -> i32 {
  stack bytes:u8[5] = zeroed;
  let head = Header(7, 1480);
  encode_Header(bytes, head);
  if !same(decode_Header(bytes), head) || wire_size_Header() != 5 { return 1; }
  if !less(Header(7, 1), head) { return 2; }
  return 0;
}
```

### Layout, the machine and freestanding targets

`packed` removes a record's padding, and `align(n)` raises its alignment to a power of two from 8 to 65536 (`E-ALIGN`).

```cairn
struct Header packed { kind:u8; size:u32; }        // five bytes, no padding
struct Slot align(64) { hits:u64; }                // one cache line apiece

fn main() -> i32 {
  let head = Header(7, 1480);
  let slot = Slot(1);
  if head.size != 1480 || slot.hits != 1 { return 1; }
  return 0;
}
```

`mmio_read[u32](address)`, `mmio_write[u32](address, value)` and `asm("wfi")` reach the machine from inside `unsafe { }`, and add `mmio` and `asm` to the row.

```cairn
fn wake(base:usize) {
  unsafe {
    let status = mmio_read[u32](base);
    mmio_write[u32](base + 4, status | 1);
    asm("wfi");
  }
}
```

A freestanding build produces one ELF image with no operating system, C library or C++ runtime under it; [the freestanding profile](#projects-and-the-freestanding-target) has the target table and what the profile guarantees.

### What the language does not have

* No inheritance and no implicit boxing.
* No lifetime annotations: a borrow cannot outlive the call it is written in.
* No implicit conversion, no operator overloading, no shadowing, no block-tail return.
* No wildcard arm, and no propagation form other than `try`.
* No exception, no unwinding and no rollback: a failed guard aborts.
* No orphan rule, because coherence is judged over the whole program.
* No cancellation of a task or of queued device work.
* No loop that implies parallelism.
* No downloads: dependencies are vendored sources.

### Scope of proof

Typed, native-built, finite-tested, SMT-equivalent and Lean-checked are distinct claims; see [verification](#verification). Generic instances, owners, lanes and the foreign boundary are native-implemented and tested, not mechanized.

## The standard library

Twelve modules, written in CAIRN, shipped inside the package and linked on demand. `import std.map (Map);` brings in `map.insert(...)` and the bare name `Map`. Nothing is downloaded and nothing is implicit: a module you do not import is not in your program. An executable keeps only what `main` reaches, a library build keeps every function of the modules it imports, and a generic function exists only at the types it is used with.

[std_api.md](std_api.md) holds every signature and every effect row, generated from these sources by `cairn doc --std`. What follows is the working guide: what each module is for, a program that uses it, and where it bites.

Three habits explain most of the API shape. A lookup answers with an index, never a borrow: `map.find` and `arena.find` return `Option[usize]` and the caller reads `m.vals[slot]` itself, which is a place and can therefore be passed on, borrowed, `take`n or assigned. Every array parameter carries its length, `f(n, xs)` against a callee's `xs:ro<u8>[n]`; a whole view or buffer matches by name identity, and a part `v.data[lo..hi]` matches whatever `usize` expression you pass, at the cost of one bounds guard. Costs are in the signature: a function that allocates says `alloc` in its effect row and so does everyone who calls it. `cairn doc` prints each row, and `cairn build` writes it into the receipt at `frontend.functions.<name>.effects`.

| module | what it is for | allocates |
| --- | --- | --- |
| `std.core` | `Option`, `Result`, the `Ord`/`Eq`/`Hash` traits | no |
| `std.vec` | the growable owner `Vec[T]` | yes |
| `std.text` | integers to and from bytes, comparison, search, hashing | only `push_u64` |
| `std.io` | files, standard streams, a monotonic clock | only `read_file` |
| `std.map` | open-addressed `Map[K, V]` | yes |
| `std.derived` | `derive eq`, `derive ord`, `derive hash` | no |
| `std.sort` | in-place heapsort and search | no |
| `std.wire` | `derive wire`: fixed-width records to bytes | no |
| `std.arena` | generational `Arena[T]` with stale-handle detection | yes |
| `std.mem` | `fill`, `copy`, `equal` over views | no |
| `std.net` | blocking TCP | no |
| `std.sys` | the C library, declared once | no |

### std.core

`Option[T]`, `Result[T, E]` and the three traits everything else dispatches on: `Ord` (`less`, a strict weak order), `Eq` (`same`, an equivalence) and `Hash` (`hash`). `Option` is absence, not a null. `Result` is the only failure carrier, and `try` is the only thing that propagates it.

```cairn
import std.core (Option, Result, Ord);

struct Frame { stream:u32; bytes:u32; }
impl Ord for Frame { fn less(a:ro<Frame>, b:ro<Frame>) -> bool pure = a.bytes < b.bytes; }

fn widest(n:usize, frames:ro<Frame>[n]) -> Option[usize] {
  if n == 0 { return Option.None; }
  let mut best:usize = 0;
  for i in 1..n { if less(frames[best], frames[i]) { best = i; } }
  return Option.Some(best);
}

fn under(n:usize, frames:ro<Frame>[n], limit:u32) -> Result[u32, u32] {
  match widest(n, frames) {
    Option.Some(at) => {
      if frames[at].bytes > limit { return Result.Err(frames[at].stream); }
      return Result.Ok(frames[at].bytes);
    }
    Option.None => { return Result.Ok(0); }
  }
}

fn headroom(n:usize, frames:ro<Frame>[n]) -> Result[u32, u32] {
  let used = try under(n, frames, 1500);   // on failure this leaves with the stream number
  return Result.Ok(1500 - used);
}

fn main() -> i32 {
  stack frames:Frame[2] = zeroed;
  frames[0] = Frame(1, 512);
  frames[1] = Frame(2, 1400);
  match headroom(2, frames) { Result.Ok(spare) => { if spare != 100 { return 1; } } Result.Err(s) => { return 2; } }
  frames[1] = Frame(2, 9000);
  match headroom(2, frames) { Result.Ok(spare) => { return 3; } Result.Err(s) => { if s != 2 { return 4; } } }
  return 0;
}
```

`std.core` implements `Ord` and `Eq` for every integer type, `Eq` for `bool` and `Hash` for the unsigned integers, one bounded impl per class; `hash` is Fibonacci hashing, one wrapping multiply whose high bits carry the mix. Write your own impl, or generate one with [std.derived](#stdderived). Every trait member is declared `pure` and every implementation is held to it, so `[T:Ord]` promises a comparison that only reads: a `less` that prints is `E-EFFECT-CEILING`, "Ord.Frame.less exceeds its declared effects."

### std.vec

`struct Vec[T:affine] { data:Buf[T]; len:usize; }`, a growable owner. Both fields are public, because passing `v.data[0..n]` to a view parameter is the normal way to hand a Vec to anything.

```cairn
import std.vec as vec;
import std.text as text;

fn main() -> i32 {
  let mut line = vec.new[u8]();              // nothing is allocated until the first push
  for i in 1..4 {
    text.push_u64(line, u64(i) * 11);
    vec.push(line, 32);                      // a space
  }
  vec.truncate(line, line.len - 1);          // release the trailing space now, not at scope exit
  if line.len != 8 { return 1; }             // "11 22 33"
  if line.data[0] != 49 || line.data[7] != 51 { return 2; }
  if vec.capacity(line) < line.len { return 3; }
  return 0;
}
```

`reserve` doubles and moves elements with `swap`, so an owner is never copied; `push` is amortized O(1) and puts `alloc` in every caller's row. `pop` moves the element out as an `Option[T]`. `get`, `set` and `extend_from` take only copyable elements, because an owner would have to be moved out of a place, and `get` and `set` trap on an index at or past `len`. `clear` and `truncate` release elements now and keep the capacity.

### std.text

Numbers to bytes and back, plus the searching a line protocol needs. A substring cannot be returned, so every search answers with an index into the input and the caller passes `s[lo..hi]` onwards.

```cairn
import std.core (Option, Result);
import std.text as text;

// The decimal field that starts at `from` and ends at the next comma or at the end of the line.
fn field(n:usize, line:ro<u8>[n], from:usize) -> Result[u64, text.ParseError] {
  match text.find_byte(n, line, 44, from) {
    Option.Some(at) => { return text.parse_u64(at - from, line[from..at]); }
    Option.None => { return text.parse_u64(n - from, line[from..n]); }
  }
}

fn main() -> i32 {
  let line = "23,19,x9";
  match field(len(line), line, 0) {
    Result.Ok(value) => { if value != 23 { return 1; } }
    Result.Err(why) => { return 2; }
  }
  match field(len(line), line, 6) {
    Result.Ok(value) => { return 3; }
    Result.Err(why) => {
      match why {
        text.ParseError.Invalid(at) => { if at != 0 { return 4; } }   // offset of the byte at fault
        text.ParseError.Overflow(at) => { return 5; }
        text.ParseError.Empty => { return 6; }
      }
    }
  }
  stack out:u8[4] = zeroed;
  let used = text.write_hex(len(out), out, 48879, 4);   // a writing call gets its own statement
  if used != 4 || out[0] != 98 { return 7; }            // "beef"
  return 0;
}
```

`write_u64` and `write_hex` answer with the number of bytes used, and 0 when the value does not fit; `write_hex` writes exactly `width` lowercase digits and loses whatever is above them. `compare` and `equal` are lexicographic, shorter first. `find` is naive, because a line protocol's needles are short. `hash_bytes` is FNV-1a with no table. `push_u64` is the one function here that allocates.

### std.io

A `File` is `linear`: the type system, not a convention, is what closes a descriptor. A failure is an errno inside an `IoError`, because CAIRN cannot express the `int*` the C library keeps its error behind.

```cairn
import std.core (Option, Result);
import std.io as io;

const PATH:usize = 13;   // "readings.log" and its NUL

fn record(path:ro<u8>[PATH], n:usize, data:ro<u8>[n]) -> Result[usize, io.IoError] {
  let f = try io.open(PATH, path, io.TRUNCATE);
  defer io.close(f);                       // runs on every exit, including the ones `try` takes
  let wrote = try io.write(f, n, data);
  let flushed = try io.sync(f);
  return Result.Ok(wrote);
}

fn lines(path:ro<u8>[PATH]) -> Result[u64, io.IoError] {
  let bytes = try io.read_file(PATH, path);
  let n = bytes.len;
  let mut count:u64 = 0;
  for i in 0..n { if bytes.data[i] == 10 { count = count + 1; } }
  return Result.Ok(count);
}

fn main() -> i32 {
  let path = "readings.log\x00";
  match record(path, 14, "23,19\n31,7\n42\n") {
    Result.Ok(wrote) => { if wrote != 14 { return 1; } }
    Result.Err(why) => { return 2; }
  }
  match lines(path) {
    Result.Ok(count) => { if count != 3 { return 3; } }
    Result.Err(why) => { return 4; }
  }
  match io.remove(PATH, path) { Result.Ok(done) => {} Result.Err(why) => { return 5; } }
  return 0;
}
```

`defer io.close(f)` is the idiom, not a nicety: `try` refuses to leave a function while a linear value is unconsumed, so a File that is not deferred cannot be used with `try` at all (`E-LINEAR-LEAK`, "f is linear: consume it, or defer its consumer, on every path"). `close` reports nothing because consuming a linear value requires a function that never reaches a `return`, and `return` demands that every linear value already be consumed. Report through a borrow if you need the status.

Open flags are `READ`, `WRITE`, `APPEND` and `TRUNCATE`; `seek` takes `SET`, `CUR` or `END`; `sync` and `truncate` answer `Ok(0)`. A path ends in a NUL byte, since C reads a pointer and not a length. `read` is one syscall and answers 0 at end of file; `read_full` and `write` loop, and a short write is an error here even though it is not one to the kernel. Nothing buffers: n bytes written is n bytes of syscall. `print`, `println`, `eprintln`, `newline` and `print_u64` are best effort and return nothing. `monotonic_ns` reads CLOCK_MONOTONIC.

### std.map

`Map[K, V]`: open addressing, linear probing, tombstones. The map owns its keys and values, so `Map[u64, Vec[u8]]` is ordinary.

```cairn
import std.core (Option);
import std.map as map;
import std.text as text;

fn bump(counts:rw<map.Map[u64, u64]>, key:u64) {
  match map.find(counts, key) {
    Option.Some(slot) => { counts.vals[slot] = counts.vals[slot] + 1; }
    Option.None => { map.insert(counts, key, 1); }
  }
}

// One count per distinct word, keyed by the word's FNV digest: a Vec has no Hash of its own.
fn tally(n:usize, line:ro<u8>[n], counts:rw<map.Map[u64, u64]>) {
  let mut start:usize = 0;
  while start < n {
    let mut stop = n;
    match text.find_byte(n, line, 32, start) { Option.Some(at) => { stop = at; } Option.None => {} }
    if stop > start { bump(counts, text.hash_bytes(stop - start, line[start..stop])); }
    start = stop + 1;
  }
}

fn main() -> i32 {
  let mut counts = map.new[u64, u64]();
  let line = "put get put del get put";
  tally(len(line), line, counts);
  if map.count(counts) != 3 { return 1; }
  match map.find(counts, text.hash_bytes(3, "put")) {
    Option.Some(slot) => { if counts.vals[slot] != 3 { return 2; } }
    Option.None => { return 3; }
  }
  let mut seen:u64 = 0;
  for slot in 0..map.slots(counts) { if map.live(counts, slot) { seen = seen + counts.vals[slot]; } }
  if seen != 6 { return 4; }
  return 0;
}
```

`find` answers with the slot, not the value. `insert` moves key and value in and releases the old value when it replaces one; `remove` moves the value out. Operations are expected O(1), and `insert` rehashes past three quarters full, so `alloc`, `free` and `zero_init` are in every caller's row. `K` must implement `Hash` and `Eq`, checked where the instance is made: a key with `derive eq` and no `derive hash` is `E-TRAIT-IMPL`, "Route does not implement Hash; std.map.insert needs [K:Hash+Eq+affine]."

### std.derived

Three recipes that generate trait implementations: `derive eq` (field-wise `same`), `derive ord` (lexicographic `less`, declaration order) and `derive hash` (FNV-1a over the fields' own hashes). Each writes an ordinary `impl` of the `std.core` trait, so the record then satisfies `[T:Ord]` for `std.sort` and `[K:Hash + Eq + affine]` for `std.map`.

```cairn
import std.core (Option, Eq, Hash);
import std.map as map;

struct Route { source:u32; port:u16; }
derive eq for Route;
derive hash for Route;

fn main() -> i32 {
  let mut hits = map.new[Route, u64]();
  map.insert(hits, Route(10, 80), 4);
  map.insert(hits, Route(10, 443), 9);
  map.insert(hits, Route(10, 80), 5);      // replaces, and releases the old value
  if map.count(hits) != 2 { return 1; }
  let wanted = Route(10, 80);
  match map.find(hits, wanted) {
    Option.Some(slot) => { if hits.vals[slot] != 5 { return 2; } }
    Option.None => { return 3; }
  }
  if !same(Route(10, 80), wanted) || same(Route(10, 81), wanted) { return 4; }
  return 0;
}
```

A field whose type lacks the trait is named: `derive ord` over a record with a `bool` field is `E-TRAIT-IMPL`, "bool does not implement std.core.Ord." `cairn expand` prints what a `derive` generated, as source.

### std.sort

Heapsort: the one O(n log n) order that needs no recursion (no `diverge` from a call cycle), no scratch buffer (no `alloc`) and moves elements only with `swap`, so it sorts owners too. Equal elements are not kept in order.

```cairn
import std.core (Option, Ord, Eq);
import std.sort as sort;

struct Trade { venue:u32; cents:u64; }
derive ord for Trade;   // lexicographic in declaration order: venue, then cents
derive eq for Trade;

fn main() -> i32 {
  let mut book = Buf[Trade](4);
  book[0] = Trade(2, 900);
  book[1] = Trade(1, 700);
  book[2] = Trade(2, 100);
  book[3] = Trade(1, 50);
  sort.sort(len(book), book);
  if book[0].venue != 1 || book[0].cents != 50 || book[3].cents != 900 { return 1; }
  let wanted = Trade(2, 100);
  match sort.search(len(book), book, wanted) {
    Option.Some(at) => { if at != 2 { return 2; } }
    Option.None => { return 3; }
  }
  sort.sort_by(len(book), book, |a:ro<Trade>, b:ro<Trade>| -> bool { return a.cents > b.cents; });
  if book[0].cents != 900 { return 4; }
  return 0;
}
```

`sort_by` takes a closure or a declared function; `sort` is `sort_by` with the trait's `less`. `search` binary-searches an already sorted view and answers the index of an element equal to the key, or `None`. The closure is a borrowed callable: it captures by reference, exists only as that argument, and cannot allocate or escape. The caller's row gains `indirect_call`.

### std.wire

One recipe, and the first piece of the compiler to become library code. `derive wire for Header;` generates `encode_Header`, `decode_Header` and `wire_size_Header` for a record of fixed-width unsigned fields: little-endian, declaration order, no padding, extents known statically.

```cairn
import std.core (Eq);

struct Header { check:u32; kind:u32; key_len:u32; val_len:u32; }
derive wire for Header;   // no import: a bare recipe name falls back to std.<name>
derive eq for Header;

fn main() -> i32 {
  stack bytes:u8[16] = zeroed;
  let head = Header(3735928559, 1, 4, 9);
  encode_Header(bytes, head);
  if bytes[0] != 239 || bytes[3] != 222 { return 1; }   // little-endian, low byte first
  if !same(decode_Header(bytes), head) { return 2; }
  if wire_size_Header() != 16 { return 3; }             // the fields, with no padding
  return 0;
}
```

It infers no framing, no authentication and no validation. Any other kind of field is `E-DERIVE-FIELD`, "wire/1 supports only fixed-width unsigned scalar fields." `src/cairn/std/wire.cairn` is the worked example of `each`, `where`, `fold` and `$` splices; its output is pinned byte for byte against the closed generator it replaced.

### std.arena

The language's answer to graphs and cycles. Values live in one owned array and are named by a copyable `Handle { slot; generation }`. Removing a value bumps its slot's generation, so every handle to it stops resolving: a use after free becomes a `None`, not a dangling pointer.

```cairn
import std.core (Option);
import std.arena as arena;
import std.vec as vec;

struct Step { cost:u64; needs:vec.Vec[arena.Handle]; }   // a cycle of handles, not of owners

fn main() -> i32 {
  let mut plan = arena.new[Step]();
  let no_needs = vec.new[arena.Handle]();     // a call that allocates cannot be a nested operand
  let fetch = arena.insert(plan, Step(3, no_needs));
  let empty = vec.new[arena.Handle]();
  let parse = arena.insert(plan, Step(5, empty));
  match arena.find(plan, parse) {
    Option.Some(slot) => { vec.push(plan.items[slot].needs, fetch); }
    Option.None => { return 1; }
  }
  match arena.remove(plan, fetch) {
    Option.Some(dropped) => { if dropped.cost != 3 { return 2; } }
    Option.None => { return 3; }
  }
  match arena.find(plan, fetch) { Option.Some(slot) => { return 4; } Option.None => {} }  // stale
  return 0;
}
```

`insert` is amortized O(1) and reuses removed slots; `find` and `remove` are O(1), and `remove` moves the value out. Iterate with `for slot in 0..a.slots()` and `a.alive(slot)`; `a.handle(slot)` gives the handle a live slot currently answers to, and `a.count()` how many are live.

### std.mem

`fill`, `copy` and `equal` over views, one pass each. All three instantiate only for copyable elements, because an owner would have to be moved out of a place.

```cairn
import std.mem as mem;

fn main() -> i32 {
  stack pattern:u8[8] = zeroed;
  buffer frame:u8[16] = zeroed;
  mem.fill(len(pattern), pattern, 255);
  mem.copy(8, frame[0..8], pattern);
  if !mem.equal(8, frame[0..8], len(pattern), pattern) { return 1; }
  if mem.equal(len(frame), frame, len(pattern), pattern) { return 2; }   // lengths differ
  return 0;
}
```

`copy` takes two views of one extent, so it is not a memmove: overlapping parts of one array are refused at the call site with `E-ALIAS`, "A mutable view cannot be passed to overlapping call arguments", and the entry guard checks it numerically as well. Shift a buffer down with an ordinary loop. `equal` takes two extents, since a comparison is the one place where the lengths may legitimately differ, and it stops at the first difference.

### std.net

Blocking TCP. A `Socket` is linear for the same reason a `File` is. There is no thread, poll or timeout in the language yet, so one connection is served at a time. An address is four bytes, so a string literal is a perfectly good IPv4 address.

```cairn
import std.core (Result);
import std.io (IoError);
import std.net as net;

// One process plays both ends: connect_to completes into the listener's backlog, so the
// accept that follows returns without a second thread.
fn echo_once(port:u16) -> Result[usize, IoError] {
  let server = try net.listen_on("\x7f\x00\x00\x01", port, 16);
  defer net.close(server);
  let client = try net.connect_to("\x7f\x00\x00\x01", port);
  defer net.close(client);
  let session = try net.accept(server);
  defer net.close(session);
  let sent = try net.send(client, 5, "ping\n");
  stack heard:u8[5] = zeroed;
  let got = try net.recv(session, len(heard), heard);
  let back = try net.send(session, got, heard[0..got]);
  stack echoed:u8[5] = zeroed;
  let last = try net.recv(client, len(echoed), echoed);
  if echoed[0] != 112 { return Result.Err(IoError(71)); }   // 'p'
  return Result.Ok(last);
}

fn main() -> i32 {
  match echo_once(39812) {
    Result.Ok(n) => { if n != 5 { return 1; } }
    Result.Err(why) => { return 2; }
  }
  return 0;
}
```

`listen_on` sets `SO_REUSEADDR`, so a restart does not lose to the previous listener's TIME_WAIT. `accept` and `connect_to` block. `send` loops until the whole view is gone; `recv` is one syscall, and a count of 0 means the peer closed its end, never an error. Every failure path inside the module closes the raw descriptor before a `Socket` exists, and reads errno first, because `close` would overwrite it.

### std.sys

Every libc binding the library uses, declared exactly once: `read`, `write`, `open`, `close`, `lseek`, `fsync`, `ftruncate`, `unlink`, `rename`, `clock_gettime`, `getpid`, the BSD socket calls, `__errno_location`, and `errno()` on top of it. Prefer `std.io` and `std.net`. This module exists because an extern's C symbol is its CAIRN name, so two modules cannot both declare `close`.

```cairn
import std.sys as sys;

// Your own binding, with the two conventions std.sys uses. An extent may name a later
// parameter, which is how C orders (pointer, length).
extern fn getrandom(into:rw<u8>[n], n:usize, flags:u32) -> i64 effects(io);

fn main() -> i32 {
  stack seed:u8[8] = zeroed;
  unsafe {
    let got = getrandom(seed, len(seed), 0);   // a writing call gets its own statement
    if got != 8 || sys.getpid() <= 0 { return 1; }
  }
  return 0;
}
```

The second convention is for C strings: a pointer is not a view, so `open` is declared `path:ro<u8>[1]` and called as `sys.open(path[0..1], flags, 420)` with the NUL byte inside `path`. `errno` is a macro over a thread-local `int*`, which no CAIRN signature can return, so `std.sys.errno` declares `__errno_location` as a `usize` and does one `mmio_read[u32]` of that address inside `unsafe`. It is the only place in the library that needs the `mmio` effect.

### Known sharp edges

A linear value inside a record leaves by taking the record apart. `take` cannot forge the zero a `File` would leave behind (`E-LINEAR-STORAGE`, "take would leave a forged linear value behind; swap two places instead"), so a wrapper is consumed whole: `let Conn(f, sent) = c;` binds every field and `c` is gone. A `File` or a `Socket` may therefore live inside your own state.

One failure family per function. `try` requires the enclosing function to return the same sum family with the same failure payload, so everything fallible here is `Result[_, IoError]` and the void-ish ones answer `Ok(0)`. Mixing families is `E-TRY`, "try returns the failure of std.core.Result[u64, std.io.IoError], which Broken cannot carry."

A call that allocates or writes through a borrow cannot be a nested operand. Bind it first: `let empty = vec.new[Handle](); arena.insert(plan, Step(1, empty));`. Nesting it is `E-EFFECT-ORDER`, "Bind a writing call to its own statement before using its result."

## Tooling

Everything here ships with the compiler and depends on nothing outside the standard library. None of it changes what the compiler accepts: `fmt` and `lsp` are layout and presentation, `doc` and `expand` print what the checker already saw, and `--incremental` only changes how the same program reaches the linker. The sessions below were run in this checkout against `examples/hello` and `examples/apps/analytics`.

### `cairn fmt`

```sh
cairn fmt src/                 # rewrite every *.cairn under src/ in place
cairn fmt --check src/ tests/  # write nothing; exit 1 if anything would change
cairn fmt --diff src/a.cairn   # write nothing; print a unified diff
```

Paths may be files or directories, and a directory is searched for `*.cairn`. In place is the default. The exit code is 1 when a file fails to lex, or when `--check` or `--diff` found something to change; otherwise 0.

```text
$ cairn fmt --diff sloppy.cairn
--- sloppy.cairn
+++ sloppy.cairn (formatted)
@@ -1,10 +1,7 @@
 // Rolling checksum over a frame.
-fn checksum( n : usize , frame : ro < u8 > [ n ] ) -> u32
-{
-    let mut sum : u32 = 0 ;
+fn checksum(n:usize, frame:ro<u8>[n]) -> u32 {
+  let mut sum:u32 = 0;
 
-
-
-    for i in 0 .. n { sum = add_wrap ( sum , u32 ( frame [ i ] ) ) ; }
-    return sum ;
+  for i in 0..n { sum = add_wrap(sum, u32(frame[i])); }
+  return sum;
 }
```

The layout it produces:

* two-space indentation, one statement per line, one trailing newline;
* canonical spacing: `fn f(n:usize, x:ro<u8>[n]) -> u64`, `a + b`, `x[i]`, `f(a, b)`, `key:Type` with no space after the colon, no space before `;` `,` `[`, and `@device` attached to its extent;
* a block the author wrote on one line stays on one line if it still fits in 100 columns (`if x { return 1; }`), and otherwise breaks over lines; a block written over several lines is never collapsed;
* long parameter lists and call arguments wrap inside their brackets, long binary chains wrap before an operator, both greedily filled to 100 columns;
* every comment is kept, at the end of its line or on its own line;
* one blank line between declarations or statements is kept, a run of blank lines collapses to one, and blank lines next to a brace are dropped.

It refuses rather than risk a change of meaning. Before writing anything the formatter re-lexes its own output and compares the token stream and the comment list with the input. If either differs, or if the input does not lex at all, the file is left exactly as it was and listed under `not_formatted` with the reason. Everything but a `--diff` patch is a JSON report:

```json
{
  "status": "would-change",
  "mode": "check",
  "changed": ["sloppy.cairn"],
  "not_formatted": [{"file": "broken.cairn", "reason": "Unexpected character '\"'."}]
}
```

Formatting is a fixed point: `format_source(format_source(x)) == format_source(x)`. The API is `cairn.editor.formatting.format_source(text) -> str`, which returns `text` unchanged when it refuses, and `format_report(text) -> (text, reason)` when the reason matters.

### `cairn check --generics`

`cairn check` types the program. `--generics` also checks each generic function once against its bounds and fails if one needs more, so misuse is reported at the call rather than at the instance. The answer is one entry per template of the project's own modules:

```json
{"status": "typed", "functions": 159, "formal_status": "not-verified",
 "generics": {"analytics.agg.run_static": "ok", "analytics.agg.bins_new": "ok",
              "analytics.query.map_par": "ok", "analytics.query.map_loop": "ok"}}
```

A template that reaches past its bounds is named with what it needed, and the command exits 1. `fn widest[T:affine](a:ro<T>, b:ro<T>) -> bool = less(a, b);` compares two values of a type that promised only to be affine:

```json
{"generics": {"ranking.widest": "E-TRAIT-IMPL: ?ranking.widest.T does not implement std.core.Ord."}}
```

### `cairn doc`

`cairn doc [path] [--module m]` prints a Markdown reference taken from the checked program: every public type, recipe and function of the project's modules with its bounds, the `//` comment written above it, and the effect row the checker inferred.

    $ cairn doc examples/hello
    # root module

    ```cairn
    fn average(x:u64, y:u64) -> u64
    ```
    source: src/math.cairn Floor average without overflowing the intermediate sum.
    Effects: `trap`.

A template's row is the one at its witnesses, that is, what it may do for any arguments within its bounds, besides what their own trait members do; a template that needs more than its bounds is listed with what it needed. `cairn doc --std` documents the packaged library. [std_api.md](std_api.md) is that output, regenerated by `make docs` and compared with the compiler's answer by the test suite, so it cannot drift.

### `cairn expand`

`cairn expand [path]` prints what the program's `derive` statements generated, as CAIRN source in the canonical projection, module by module: the records, functions and `impl` blocks a recipe made, with every `$name` spliced and every static value folded.

```text
$ cairn expand examples/apps/kvstore
fn encode_Header(out:rw<u8>[16]@host, value:Header) {
  out[0] = u8((shr(value.check, 0) & 255));
  out[1] = u8((shr(value.check, 8) & 255));
  ...
```

Generated code is ordinary code of the deriving module, so this is exactly what the checker sees. When a diagnostic points into a recipe, this is where to read the instance it is about. `cairn inspect --symbol` still refuses a generated entry, because an edit belongs in the recipe: `E-SYMBOL`, "Edit an authored function, not a generated entry."

### `cairn build --incremental`

One object per module, compiled against a shared interface header (`program.hpp`: types, tables and prototypes) and cached under `build/objects/`. It is opt-in because separate objects give up inlining across modules; device programs and freestanding images are always one unit. The cache is safe to delete.

An object's key is the hash of everything that went into it: the unit, the header, the command line (which carries the compiler, the architecture, the build kind and `--debug`), the runtime headers and the compiler version. Nothing stale can be linked, because a change to any of those is a different key. Missing objects compile concurrently, and the build receipt lists every unit and whether it was reused. On this machine, the fifteen units of `examples/apps/analytics`, its own eight modules and the seven `std` modules it links:

```text
cairn build --incremental examples/apps/analytics    1.34 s   15 compiled   cold cache
cairn build --incremental examples/apps/analytics    0.14 s    0 compiled   nothing changed
cairn build examples/apps/analytics                  1.17 s             whole program, one unit
```

A body-only edit recompiles one module: one statement added to `analytics.query.above_loop` took 0.87 s and rebuilt `analytics_query.cpp` alone. A signature or layout change recompiles all, because the shared header is in every key; renaming one parameter of `analytics.report.micros` rebuilt all fifteen.

A key names an object; only a digest identifies it. `<key>.o` is published by a rename, and the sha256 of its bytes is written beside it as `<key>.sha256` by a second rename, so an interrupted compile leaves at worst an object with no digest. Before an object is reused its bytes are hashed and compared with that digest: truncate a cached object and the next build compiles that unit again rather than linking it. This is an integrity check against interrupted, corrupted or shared caches, not a defence against anyone who can write into `build/`, who can rewrite the digest too.

`build/objects`, an object and its digest must each be a plain entry of the project's own build output. A symbolic link, or a file where the directory belongs, is refused under the same fail-closed rule the build directory itself has, so nothing is ever written through a link out of the project. A unit whose compile times out or is killed leaves no object under its key, and still produces the `cairn.build/1` record and the `receipt.json` that a whole-program build produces.

### A manifest is named by its path

`check`, `build`, `run`, `test`, `doc` and `expand` take a directory holding `cairn.toml`, a single `.cairn` file, or the path of a manifest with any name. One project directory can therefore hold several configurations:

```sh
cairn run examples/apps/analytics            # cairn.toml, the host engine
cairn run examples/apps/analytics/gpu.toml   # the same sources plus the device entry
```

[examples/apps/analytics](#examplesappsanalytics) says what its second manifest changes, and why a machine without CUDA must still be able to build the host engine.

### `cairn lsp`

```sh
cairn lsp      # speaks JSON-RPC with Content-Length framing on stdin/stdout
```

Supported: `initialize`, `initialized`, `shutdown`, `exit`; full-text `textDocument/didOpen`, `didChange`, `didClose`; and

| Request | What it answers |
| --- | --- |
| `publishDiagnostics` | the compiler's diagnostic for the buffer: its code, its message, the `repair_hint` from `agent_tools.explain`, and the exact token range |
| `textDocument/hover` | the smallest checked expression covering the position: its type, the type expected of it, and for a name whether the binding is mutable; on a function name, its signature and its inferred effect row |
| `textDocument/documentSymbol` | functions, structs, enums, traits, consts and impls with ranges; trait and impl members nest as children |
| `textDocument/definition` | a declaration of the name under the cursor: this document, or the packaged `src/cairn/std/*.cairn` file the name comes from (`vec.push` and a name brought in by `import std.core (Option);` alike) |
| `textDocument/completion` | see below; the trigger character is `.` and the client filters by the prefix already typed |
| `textDocument/signatureHelp` | the innermost call still open before the cursor: its signature, its parameters and the index of the one being written (commas at depth zero; method syntax counts the receiver). Triggers are `(` and `,` |
| `textDocument/references` | every place in **this document** that names what the cursor stands on, under the rule below |
| `textDocument/prepareRename`, `textDocument/rename` | the same set as one `WorkspaceEdit`, or a refusal |
| `textDocument/formatting` | one whole-document edit from `cairn fmt`, or no edit when the buffer is already formatted |

Positions are UTF-16 code units, as the protocol requires, so non-ASCII comments and astral characters do not shift a range. Hovering `average` in `examples/hello/src/math.cairn`, and asking for help inside its call, answers this:

```json
{"id": 2, "result": {"contents": {"kind": "markdown",
  "value": "\n```cairn\nfn average(x:u64, y:u64) -> u64\n```\n\nEffects: `trap`."},
  "range": {"start": {"line": 1, "character": 3}, "end": {"line": 1, "character": 10}}}}
{"id": 3, "result": {"signatures": [{"label": "fn average(x:u64, y:u64) -> u64",
  "parameters": [{"label": "x:u64"}, {"label": "y:u64"}]}],
  "activeSignature": 0, "activeParameter": 1}}
```

**The last good analysis.** A buffer being typed usually does not compile, so each feature takes its context from the current tokens, which always exist because the scan never fails, and its meaning from the last analysis that did compile. The two are matched by name, not by position: the old analysis' offsets are stale after an edit, the name of the enclosing function and of its locals is not. After `let y = p.` the fields of `p` are still offered. The locals on offer are those the last good analysis saw in this function, plus the names the current tokens bind before the cursor (`let`, `let mut`, `reg`, `for`, `each`, `parallel`, `buffer`, `stack`, parameters, closure parameters and `match` payload binders), so a binding typed one edit ago completes too. A buffer that has never compiled still completes its own keywords, builtins, types, declarations and token-visible locals; it is never renamed.

After `name.`, completion offers the fields of the record `name` holds (through `ro<>`/`rw<>`, with a generic container's arguments substituted, so `Vec[u64]` has `data:Buf[u64]`) and every function `name.f(...)` resolves to: those of the receiver type's module and the members implementing a trait you can see for it, both taking the receiver first. If `name` is an enum or sum, its variants; if it is an import alias or a module path, that module's public declarations. Elsewhere it offers the locals with their types, the declarations visible in the cursor's module (its own, imported names, import aliases), the builtins the checker knows, the intrinsic and scalar types, and the reserved words. After `import ` it offers the packaged modules and the document's own; after `derive ` the recipes it can reach; inside a generic bound after `:` the traits in scope, the kinds and the scalar classes. A private name of another module is never offered.

**References and rename answer only what one document can prove.** For a top-level declaration: every identifier token equal to its name, unless that name is ever written after a `.` or after `import` (a field, a method, a variant or a module path, which one document cannot tell apart), or a local of that name is bound somewhere, or two declarations share it. Another declaration's own name is never touched. For a local: the identifiers from its binder to the end of its declaration, provided it is bound exactly once there (blocks are scopes, so a sibling block may bind the name again) and is not also a declaration, an import alias or an imported name. Nothing can shadow it inside that range, because CAIRN rejects shadowing (`E-SHADOW`). Where the rule does not hold, `references` answers nothing and `prepareRename` answers null. A rename is refused as well for a reserved word, a builtin, a name of another module, a buffer with no good analysis, and a new name that is not a free identifier of this document. The suite applies the returned edit and recompiles, so an edit that would break a program that compiled is a test failure.

The server analyses the open buffer. `std.*` imports are linked by the compiler itself, and sites from a linked module are not offered as hovers of the file you are editing. A buffer that does not compile still gets its outline, and any compiler failure becomes a diagnostic rather than an exception: the server answers every request it accepted and keeps running. Every handler answers an empty list or null for any position in any buffer, and costs far less than the analysis it reads from.

Known limits: only one document is analysed at a time, so a name declared in a sibling file of the same project is neither hovered, completed nor jumped to, and references and rename stop at the edge of the buffer; the first declaration with a matching name wins in `definition`, and `Enum.Variant` resolves to the enum; completion after `derive ` lists the recipes the program already links; there is no workspace symbol, code action or formatting-on-type support; diagnostics stop at the first compiler error, because the compiler does.

### The editor extension

`editors/vscode/` is a VS Code and Cursor extension, version 1.2.0: a TextMate grammar, bracket and comment configuration, and a client that starts `cairn lsp` over stdio. It has no build step. `node_modules/` is not vendored; `vscode-languageclient` is declared in `package.json` and resolved when the extension is packaged or installed.

#### Install from this checkout

Symlink the directory into the editor's extension folder and reload the window:

```sh
ln -s "$PWD/editors/vscode" ~/.cursor/extensions/cairn-1.2.0          # Cursor
ln -s "$PWD/editors/vscode" ~/.vscode/extensions/cairn-1.2.0          # VS Code
```

Syntax highlighting works immediately. The language server needs its dependency present, so run `npm install --omit=dev` inside `editors/vscode/` once. That is the only step that touches the network; without it the grammar still loads and the client reports a missing module.

#### Install as a package

```sh
cd editors/vscode
npm install
npx --yes @vscode/vsce package          # writes cairn-1.2.0.vsix
code --install-extension cairn-1.2.0.vsix
```

#### Server executable

The client runs `cairn`, with `lsp` as its argument. When `cairn` is not on `PATH`, a source checkout for instance, point the setting at the checkout's entry script:

```json
{
  "cairn.server.command": "/path/to/cairn/bin/cairn",
  "cairn.server.arguments": ["lsp"]
}
```

`bin/cairn` needs a Python 3.11 or later interpreter on `PATH`. To pin one, set the command to that interpreter and the arguments to `["/path/to/cairn/bin/cairn", "lsp"]`.

#### What the extension gives you

Diagnostics as you type, hover types and effect rows, a document outline, completion (fields, methods, module members, variants, locals and words), signature help, go to definition inside the open file and into the packaged `std`, references and rename within the file, and `Format Document`, which is the same formatter as `cairn fmt`. The client negotiates all of it from the server, so nothing here changes when the server learns something new. `cairn lsp` above says what each of those covers and what it does not.

The grammar's keyword list is checked against the compiler's `RESERVED` set by `tests/tooling/test_lsp.py`, so a new keyword fails the suite until the grammar learns it.

## Projects and the freestanding target

A freestanding build produces one ELF image that runs on bare hardware: no operating system, no C library, no C++ runtime, no dynamic loader, no start files and no unwinder. `[build] target` in `cairn.toml` selects it, `--target` on `cairn build` and `cairn run` overrides it, and the default is `"hosted"`, which is byte for byte the build this compiler has always produced.

```toml
[project]
name = "embedded"
sources = ["src/uart.cairn", "src/parse.cairn", "src/main.cairn"]

[build]
kind = "exe"
target = "aarch64-virt"
```

```sh
cairn build examples/embedded   # the ELF image plus a receipt, in a fresh directory
cairn run   examples/embedded   # the same image, under the target's emulator
```

`cairn run` reports the UART transcript as `stdout` and the emulator's exit status as `exit_code`. Here is the deliberate guard violation in `examples/embedded/trap/`, which reads one element past a four-element array:

```json
{"status": "program-exited", "exit_code": 134,
 "stdout": "trap demo: reading window[4] of 4\n",
 "emulator": ["/usr/bin/qemu-system-aarch64", "-M", "virt", "-cpu", "cortex-a72",
              "-nographic", "-semihosting", "-kernel", ".../trapdemo.elf"]}
```

### What the profile guarantees

The whole language, minus what a host provides. Checked `+ - * / %`, bounds, extent, null, alignment, overlap and tag guards, `stack` storage, records, sums, `match`, generics, traits, closures, `compact`, `reduce` and `derive wire` all behave exactly as they do hosted. `f32` and `f64` work, because the start-up code enables FP and SIMD at EL1.

A refusal instead of a link error. The build reads each function's effect row out of the frontend receipt and rejects the program by name if any row holds an effect a hosted runtime would have to supply: `alloc`, `free`, `io`, `gpu_*`, `transfer:*`, `par:*` or `ffi:*`. That is every `buffer`, `Buf`, `std.vec`, `parallel`, device region and `extern` call, caught before a compiler runs. `mmio_read`, `mmio_write` and `asm` remain available inside `unsafe`.

```json
{"status": "unknown", "code": "E-PROJECT-OR-ENVIRONMENT",
 "message": "A freestanding target has no hosted runtime: main has effect 'alloc'."}
```

No undefined symbols. The only symbols the C++ needs from outside the translation unit, `memset`, `memcpy` and the exit path, are defined in the target's `start.S`, and `cr::trap()` no longer calls `std::abort`.

```text
$ nm -u examples/embedded/build/embedded-*/embedded.elf     # nothing is undefined
$ size examples/embedded/build/embedded-*/embedded.elf
   text	   data	    bss	    dec	    hex	filename
   5411	      0	      0	   5411	   1523	embedded.elf
```

A trap is distinguishable from success. `fn main() -> i32` returns its value as the emulator's exit status, and a failed guard leaves through the same door with status 134, the status a hosted shell reports for `std::abort`.

One command, one fresh directory. The image, the generated C++, every runtime header and the receipt land together; nothing is reused between builds and nothing is downloaded.

### What it does not guarantee

No allocator, so no growth. `buffer`, `Buf` and `std.vec` are rejected, not emulated. Storage is `stack` arrays, statics and `ro<u8>[n]` string views.

No concurrency and no device: `parallel` and every `@device` placement are rejected.

No MMU, no caches, no interrupts, no timer. The image runs in the state QEMU hands it, flat physical memory at EL1 with the MMU off, so every access is Device-nGnRnE memory. The build therefore passes `-mstrict-align`, since an unaligned access to Device memory is not architecturally allowed, and performance numbers from this profile are not comparable to hosted ones. Nothing here installs a vector table, so a hardware exception hangs the machine rather than reporting; the language's own guards are what stop a bad index or an overflow.

A trap does not unwind, log or roll back. It stops the machine. `defer` and owner release do not run, exactly as a hosted abort does not.

Cross compilation is still not offered. A target names a host family and is refused on any other host, rather than guessing at a sysroot.

`cairn test` still runs on the host. A task contract is a finite scalar comparison of one symbol, so it builds a hosted shared library whatever `target` says. What runs on the machine is `cairn run` and `tests/projects/test_freestanding.py`.

The backend is not verified. `formal_status` stays `not-verified` here as everywhere else.

### The `aarch64-virt` target

QEMU's `virt` machine with a Cortex-A72, the board CAIRN's evidence is captured on.

| Address | What |
| --- | --- |
| `0x0900_0000` | PL011 UART0 data register; `0x0900_0018` is the flag register, bit 5 = TX FIFO full |
| `0x4000_0000` | DRAM base, 128 MiB by default |
| `0x4008_0000` | image load address: `.text`, `.rodata`, `.data`, `.bss` |
| `0x4100_0000` | stack top, growing down; 16 MiB clear of the image and of the device tree QEMU writes just past it |

`src/cairn/targets/aarch64-virt/start.S` sets `sp`, zeroes `.bss`, enables FP and SIMD through `CPACR_EL1`, calls `cf_main` and branches to `cr_exit`. `cr_exit` issues ARM semihosting `SYS_EXIT` (`0x18`) with `ADP_Stopped_ApplicationExit` and the status, which QEMU turns into its own exit status; that is why the emulator is started with `-semihosting`. PSCI `SYSTEM_OFF` would also stop the machine, but it always exits 0, so a trap could not be told from a success, which is the experiment that settled the choice. `start.S` also defines `memset` and `memcpy`, byte at a time, because the compiler lowers aggregate copies to those names whatever `-ffreestanding` says.

The build is one command line, from the receipt of `cairn build examples/embedded`:

```text
clang++ -std=c++20 -O3 -ffp-contract=off -fno-fast-math -fno-exceptions -fno-rtti
  -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-variable -Wno-unused-but-set-variable
  -DCAIRN_FREESTANDING=1 -ffreestanding -nostdlib -static -fno-stack-protector
  -fno-threadsafe-statics -fno-PIC -fno-PIE -fno-unwind-tables -fno-asynchronous-unwind-tables
  -Wl,--build-id=none -Wno-unused-command-line-argument -mstrict-align -march=armv8-a
  -Wl,-T,<target>/link.ld <build>/program.cpp <target>/start.S -o <build>/embedded.elf
```

`-Wno-unused-command-line-argument` is there because the C++ options are unused on the `start.S` job and `-Werror` would otherwise reject them.

### Running it under QEMU

```sh
qemu-system-aarch64 -M virt -cpu cortex-a72 -nographic -semihosting -kernel <image>.elf
echo $?      # fn main()'s return value, or 134 for a failed guard
```

`cairn run --target aarch64-virt <project>` runs exactly that command. QEMU loads the ELF by its program headers and enters at `_start`, so no raw binary and no bootloader is needed. `examples/embedded/` is the worked application and `examples/embedded/trap/` the guard violation; `evidence/v1_0/embedded/` holds a captured transcript, `size`, `nm` and tool versions.

### Adding a target

1. Create `src/cairn/targets/<name>/` with `link.ld` and `start.S`. The start-up code owns the stack, `.bss`, the call to `extern "C" cf_main()`, `memset`, `memcpy`, and `extern "C" [[noreturn]] void cr_exit(int)`, the one exit path, which must carry a status out so a trap (134) is distinguishable from any value `fn main() -> i32` can return.
2. Add one row to `TARGETS` in `src/cairn/projects/toolchain.py` naming the host `family`, the `-march` `arch`, any extra `flags`, and the `run` command that executes an image, ending in the option that takes the image path.
3. Add the directory's `*.S` and `*.ld` to `package-data` in `pyproject.toml` if the glob does not already cover it, and extend `tests/projects/test_freestanding.py`.

Nothing else in the compiler knows about targets: the profile is a flag set, a linker script, a start-up file and an effect refusal, not a second code generator.

## Examples

Every project under [examples/](../examples) is built and run by the test suite. Commands are written as `cairn ...`; from a source checkout that is `python3 bin/cairn ...`.

Start with `hello` for the smallest complete project and `apps/kvstore` to see how a real program is shaped.

| project | what it is | command |
| --- | --- | --- |
| [hello](#exampleshello) | the smallest project: a manifest, two modules, one task contract | `cairn run examples/hello` |
| [systems](#examplessystems) | a decimal parser and a counting sort, with contracts for both | `cairn test examples/systems` |
| [basics](#examplesbasics) | three single-file sources with no `main`, used by the harnesses | `cairn check examples/basics/native.cairn` |
| [apps/kvstore](#examplesappskvstore) | a crash-safe log-structured store with an in-memory index | `cairn run examples/apps/kvstore` |
| [apps/service](#examplesappsservice) | a TCP key/value service on a line protocol | `cairn run examples/apps/service` |
| [apps/analytics](#examplesappsanalytics) | a columnar engine whose table types are generated by recipes | `cairn run examples/apps/analytics` |
| [apps/simulator](#examplesappssimulator) | one Jacobi stencil, three back ends, bitwise agreement | `cairn run examples/apps/simulator` |
| [apps/gpu_pipeline](#examplesappsgpu_pipeline) | upload, map, compact, reduce, download, checked against the host | `cairn run examples/apps/gpu_pipeline` |
| [embedded](#examplesembedded) | a bare-metal AArch64 image under QEMU, and a guard violation | `cairn run examples/embedded` |
| [proof_scope](#examplesproof_scope) | what `cairn verify` covers and what it cannot reach | `cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all` |
| [sketch](#examplessketch) | a host-bound sketch settled by SMT, with no model in the loop | `python3 tools/ai/sketch_demo.py` |
| [agent](#examplesagent) | the fixture behind the edit and repair loop | `python3 tools/ai/demo.py` |

`apps/simulator`, `apps/gpu_pipeline` and `apps/analytics/gpu.toml` need nvcc and a CUDA device. `embedded` needs `qemu-system-aarch64` on an AArch64 host. Everything else needs only a C++20 compiler.

### examples/hello

The smallest complete project: a manifest, two modules and one finite task contract.

```sh
cairn run  examples/hello     # "status": "program-exited", "exit_code": 0
cairn test examples/hello     # "status": "passed-finite-tests", "cases": 81
```

`src/math.cairn` is the whole point: `average` computes the floor of the mean without overflowing the intermediate sum. `tests/average.json` pins it at 81 boundary pairs, including `u64` maxima, and `cairn test` builds a shared library and calls the symbol for each one.

### examples/systems

Two systems idioms in one project: a decimal parser that reports the offset of the byte at fault, and a counting sort whose input and output views stay disjoint.

```sh
cairn run  examples/systems     # "status": "program-exited", "exit_code": 0
cairn test examples/systems     # decimal_kind: 8 cases, sorted_even: 7 cases, both passed
```

`src/parse.cairn` answers with a sum (`Value(u64)`, `Invalid(at)`, `Overflow(at)`, `Empty`) and never a sentinel, and it detects overflow before it happens. `src/sort.cairn` keeps its histogram in `stack counts:usize[256]`, so nothing is allocated and the `rw` output cannot alias the `ro` input.

### examples/basics

Three single-file sources with no `main`, read by `cairn check`, by the density harness (`tools/checks/density.py`) and by the codegen benchmarks.

```sh
cairn check examples/basics/native.cairn   # "functions": 23
cairn check examples/basics/family.cairn   # "functions": 256
cairn check examples/basics/wire.cairn     # "functions": 3
```

`native.cairn` is the breadth sample: records, sums, `each` loops, `compact`, checked arithmetic, explicit conversions and every guard site. `family.cairn` is five lines that produce 256 typed specializations, which is what `family gain = scale[1..257];` means. `wire.cairn` is one record and one `derive wire`; read what it generated with `cairn expand examples/basics/wire.cairn`.

### examples/apps/kvstore

A crash-safe log-structured store: an append-only log of checksummed records, the index in memory, put/get/delete, reopen by replay, and compaction. `main` runs the whole scenario against a file of its own and returns 0 only if every check passes.

```sh
cairn run examples/apps/kvstore
```

```text
kvstore: self-check passed
```

#### What it demonstrates

`derive wire` for the 16-byte record header. `struct Header { check:u32; kind:u32; key_len:u32; val_len:u32; }` plus `derive wire for Header;` gives little-endian `encode_Header` and `decode_Header` with no padding and no hand-written shifting. `cairn expand examples/apps/kvstore` prints them.

A linear `File` beside the state, not inside it. `Store` holds only the index and the valid log length; the File is a separate local with `defer io.close(f)`. A linear value inside a struct could never be consumed, because `take` leaves a shell that still demands consumption, so this is the shape the type system asks for.

`try` with `defer`. Every step is `let x = try ...;`, and the deferred close runs on the failing path too. `try` refuses to leave a function while a linear value is live, which is what makes the whole style legal.

`Map[u64, Record]` with owner values. The index owns both the key and the value bytes (`Record { key:Vec[u8]; value:Vec[u8]; }`). Replacing a key releases the old value inside the map, and `remove` moves it out.

Recovery that is a loop, not a promise. `replay` stops at the first record that is short, truncated or fails its checksum, `ftruncate`s the file back to the last whole record and leaves the descriptor there. The scenario tears the log twice, nine bytes of a header and then a complete record with a wrong checksum, and expects both to vanish.

#### How the store is laid out

The index is keyed by the 64-bit FNV digest of the key and also stores the key bytes, so every hit is verified byte for byte and a digest collision is a miss rather than a wrong answer. Values live in memory; the log is the durability layer, replayed on open and rewritten by compaction.

`compact_log` writes the live records to `<path>.tmp` and renames it over the log. The caller must reopen afterwards, because the descriptor it still holds names the replaced file.

#### Effect rows worth noticing

From the build receipt, minus the `ffi_precondition`, `diverge`, `local_read`, `local_write`, `stack_storage` and `ffi:__errno_location` that every caller of `std.io` carries:

```text
put          alloc, ffi:fsync, ffi:write, free, io, mmio, read:f, read:key, read:s, read:value,
             trap, write:s, zero_init
replay       alloc, ffi:ftruncate, ffi:lseek, ffi:read, free, io, mmio, read:f, read:s, trap,
             write:s, zero_init
compact_log  ffi:close, ffi:fsync, ffi:open, ffi:rename, ffi:write, io, mmio, read:path, read:s,
             read:temp, trap, zero_init
```

`put` says `ffi:fsync`, so durability is visible in the row of everything that calls it: there is no way to make a write durable without it showing up. `compact_log` has no `alloc`, because rewriting the log copies bytes through one stack header and the existing Vec storage. `mmio` comes from `std.sys.errno`, which reads errno's address with one volatile load.

### examples/apps/service

A TCP key/value service on 127.0.0.1, one client at a time, shut down by the client.

```text
put <key> <value>   ->  +ok
get <key>           ->  = <value>  |  -missing
del <key>           ->  +ok        |  -missing
quit                ->  +bye, and the service exits 0
anything else       ->  -error
```

```sh
cairn run examples/apps/service                                   # blocks: "service: listening on 39800"
printf 'put a hello\nget a\ndel a\nget a\nquit\n' | nc 127.0.0.1 39800
```

```text
+ok
= hello
+ok
-missing
+bye
```

The port is `const PORT:u16` in `src/main.cairn`. `tests/projects/test_apps.py` copies the project and rewrites it with a free port, so parallel test workers never collide.

#### What it demonstrates

A linear `Socket` per connection: `let c = try net.accept(server); defer net.close(c);` inside the accept loop. The deferred close runs at the end of each iteration, and the server socket's own `defer` outlives it. Forgetting either is `E-LINEAR-LEAK`, not a descriptor leak.

Parsing without substrings. A borrow cannot be returned, so `word_end` answers with an index and the request is sliced at the call site: `find(t, k, line[key_lo..key_hi])`. The parts carry one bounds guard each, and nothing is copied until a key is actually stored.

One buffer, no allocation on the request path. `serve` keeps a 4096-byte `buffer`, finds a newline in the filled prefix, answers, then shifts the remainder down with an ordinary loop. `mem.copy` would be rejected here (`E-ALIAS`), which is the right answer for a memmove.

State that outlives connections: the `Table` lives in `run`, so the second client sees what the first one stored.

#### Effect rows worth noticing

From the build receipt, minus the `ffi_precondition`, `diverge`, `local_read`, `local_write`, `stack_storage` and `ffi:__errno_location` that every caller of `std.io` carries:

```text
respond  alloc, ffi:send, free, io, mmio, read:c, read:line, read:t, trap, write:t, zero_init
serve    alloc, ffi:recv, ffi:send, free, io, mmio, read:c, read:t, trap, write:t, zero_init
run      + ffi:socket, ffi:bind, ffi:listen, ffi:accept, ffi:setsockopt, ffi:close, ffi:write
```

`respond` reads the connection and the request and writes only the table, and the row says which argument each read and write belongs to. The calls that open and close a socket (`ffi:socket`, `ffi:bind`, `ffi:listen`, `ffi:accept`, `ffi:setsockopt`, `ffi:close`) appear only in `run`, so the protocol layer provably cannot open or close a connection behind the loop's back. `alloc` is there because `get` builds its answer in a `Vec`; a reply of a fixed shape would not allocate at all.

#### Known limits

Blocking and sequential: one `accept` at a time, no timeout and no poll. A client that opens a connection and says nothing stops the service until it goes away.

### examples/apps/analytics

A million wire-encoded trades on disk, read back into a structure of arrays, filtered, aggregated and grouped four different ways. No table type here is one the compiler knows about: `Trade_table`, `Trade_get`, `Trade_summarize`, `Trade_unrolled_price`, the `Ord` that sorts a row and the `Aggregator` impls behind the query plan are all generated by recipes, four of this program's own in `src/cols.cairn` and `src/agg.cairn` plus `wire`, `eq` and `ord` from the packaged library.

Every query is answered twice, and `main` returns 0 only if the two answers agree: row-wise against column-wise, the contracted collector against the loop, a sequential pass against host lanes, one pass against four tasks, a bound against a table lookup. The device configuration adds host against device.

791 lines of CAIRN in ten modules; 21 bytes a row, 21 MB on disk, 10^6 rows.

```sh
cairn run examples/apps/analytics --timeout 240
```

```text
analytics: rows ingested     1000000
analytics: rows above limit  523214
analytics: price total       8389514683164
analytics: kept total        6482140393436
analytics: distinct venues   8
analytics: ingest            25132 us
analytics: row vs column     2704 us
analytics: statistics        4570 us
analytics: filter            1954 us
analytics: derived columns   5717 us
analytics: group by venue    2513 us
analytics: every cross-check passed on the host
```

The host configuration needs no GPU and builds in 1.2 s under clang++, 1.4 s under g++. The device one is `gpu.toml`, which swaps `src/main.cairn` for `src/device_main.cairn` and adds `src/device.cairn`, because one `@device` view sends the whole program through nvcc and a machine without CUDA must still be able to build and run the host engine. A manifest is named by its path, so both live in one directory.

```sh
cairn run examples/apps/analytics/gpu.toml --timeout 300
```

```text
analytics: rows on the GPU   1000000
analytics: rows above limit  523214
analytics: price total       8389514683164
analytics: host queries      2244 us
analytics: upload            69 us
analytics: device queries    975 us
analytics: download          1928 us
analytics: queued pipeline   1123 us
analytics: the device agrees with the host bit for bit
```

nvcc takes 6.3 s of the 7 s that build spends.

#### What it demonstrates

Recipes as the schema layer. `columns/1` generates the table record (one `Buf` per field plus the row count), the row accessors, a row digest folded over the fields with `fold ^`, and the static facts `count(R)` and the wire width `fold + each f in R { bytes(f) }`, which is the sum of the fields and not the size of the record, because the codecs pack with no padding. `stats/1` generates a summary record laid out column by column (`min_f`, `max_f`, `sum_f` per field, spliced three at a time by one `each` in the field list and again in the constructor call) and four functions per column. `unrolled[K]/1` takes a natural and generates a K-accumulator kernel per column, joining them with `fold add_wrap each k in 0..K { acc_$k }`; `derive cols.unrolled[4] for Trade;` and `derive cols.unrolled[8] for sensor.Reading;` differ only in that number. `device_columns/1` generates the same reductions over `@device` views, under the same names, in a different module.

Generated implementations. A recipe writes `impl`s, so the schema no longer hand-forwards them: `derive eq for Trade;` and `derive ord for Trade;` are what let `sort.sort` order whole rows and `same(row, want)` compare a decoded row with the one the generator wrote. `analytics.agg` derives its own: an aggregator is a fold over one accumulator field, so `folded[F:fn]` takes the function that folds and writes the `Aggregator` impl. `derive folded[add_wrap] for SumAgg;`, `derive folded[max] for MaxAgg;`, `derive folded[counting] for CountAgg;`. A fourth aggregator is one line.

Generated code is ordinary code. The generated names belong to the module that wrote the `derive`, so the rest of the program says `schema.Trade_summarize(...)`. They carry effect rows (`Trade_total_qty` traps, `Trade_key` reads its argument and nothing else), and one of the two records derived in `analytics.schema` is declared in `analytics.sensor`. `cairn expand examples/apps/analytics` prints all of it.

Two totals, because they are two different claims. `reduce +` over an unsigned column traps if the total does not fit, in any association order; `reduce add_wrap` is exact under any association and is therefore the one the device tree reduction is compared against. The program computes both and insists they agree on this dataset.

A K-way task split. `venue_sums_tasks` lends four visibly disjoint row ranges and four disjoint blocks of one scratch buffer to four tasks at once, waits for all four and merges. Overlap the blocks by one element and the checker says `E-LEASED` at the second `spawn`.

Templates that say what they need. `cairn check --generics examples/apps/analytics` exits 0: all seven of this program's templates certify against their bounds, so misuse is reported at the call. `Bins[T:Ord + affine]` says what the bins do with a key, order it and keep it in zeroed storage, and no more; `map_par[T:copy, U:affine]` says that reading `src[i]` by value is a copy while the output is only ever assigned over.

Lanes that call the caller's closure. `map_par` is generic, its lanes call `f`, and its row carries `lane:f`. Whatever is passed is judged where it is written, so `|x:u64| -> u64 { return mul_wrap(x, factor); }` is fine and a closure that assigned `factor` would be `E-PARALLEL-CALL`.

Pluggable aggregators both ways. `run_static[A:Aggregator]` is monomorphised per aggregator, `run_dyn(a:rw<dyn Aggregator>, ...)` dispatches, and `Vec[Dyn[Aggregator]]` is a query plan assembled at run time. All three answer the same numbers.

Placement in the name. `host device pinned unified` are ordinary identifiers except after `@`, so the device half is `module analytics.device` and its functions drop the suffix they used to carry: `device.wrapping_total` over `@device` views beside `query.wrapping_total` over host ones, and `device.Trade_wrapping_price` beside `schema.Trade_wrapping_price`.

A queued device pipeline over pinned staging. `spawn transfer`, `spawn parallel ... after up_price, up_qty` and `spawn transfer ... after work` put two uploads, a region and a download on streams. Pinned memory is host memory, so `mem.copy` fills the staging buffers and `mem.equal` and `query.wrapping_total` read them back, with no helper rewritten for the placement; and because a read-only lease is shared, the host folds its own copy of the product straight out of the same staging buffers while the device works.

Labels nobody counts. `report.line` takes `ro<u8>[LABEL]`, so the caller passes the text and the compiler checks its width. A label one space short is `E-TYPE-MISMATCH` at the call.

#### Effect rows worth noticing

```text
cols.chain                              (empty)
agg.counting                            (empty)
schema.Trade_key                        read:row, trap
schema.Trade_total_qty                  read:c, trap                    checked `reduce +`
schema.std.core.Ord....Trade.less       read:a, read:b                  derived, not written
agg.Aggregator....SumAgg.absorb         read:self, write:self           derived from add_wrap
query.notional_loop                     read:price, read:qty, write:out, trap
query.notional_par                      ... par:host
query.map_par[u64, u64]                 ... indirect_call, lane:f, par:host
query.venue_sums_tasks                  ... spawn, join, alloc, free
agg.run_static[SumAgg]                  read:a, read:xs, write:a, trap
agg.run_dyn                             ... dispatch
device.queued_notional                  ... spawn, join, par:device, transfer:h2d, transfer:d2h
```

The two derived-column functions differ in exactly one effect. The helpers the recipes splice in have empty rows, so a generated digest or aggregator costs nothing but arithmetic, and the derived `less` reads its two arguments and does not even trap.

#### What the timings say

Measured 2026-09-19 on a GH200 with CUDA 12.8 and clang 15.0.7. Ingest dominates: 25 ms to encode, write, read and decode a million 21-byte rows, against 2 ms for a full pass over a column. Host lanes do not pay off here, because `notional_par` over 10^6 cheap elements is no faster than the loop. On the device the three queries take 1 ms against 2.2 ms on the host, but the download of the kept prefix costs 1.9 ms on its own: as in `gpu_pipeline`, moving the answer is dearer than computing it, which is why the language makes you write the `transfer`.

#### What is still awkward

A recipe's run-time `fold` takes an operator or a bare function name, never a qualified one, so `fold cols.chain each f in R { ... }` is `E-PARSE` and the digest folds with `^` over per-field calls instead. Generated code lives in the deriving module, which is exactly where a recipe's own helper needs its full path.

A view parameter's extent cannot be inferred from a literal (`fn say[N:nat](t:ro<u8>[N])` called as `say("...")` is `E-INFER`), so a label is a fixed width or the literal is written twice inside `len(...)`. Fixed width happens to suit a report; it would not suit anything else.

`notional_of` is still written by hand. A recipe iterates over all of a record's fields, so arithmetic between two named ones, price times quantity, has no shape a recipe can take, even though a recipe may now generate a `kernel fn`.

### examples/apps/simulator

A 2-D Jacobi heat sweep over a 1024x1024 grid, 16 sweeps, run three ways: an ordinary loop, host lanes (`parallel` over `@host` views) and device lanes (`parallel` over `@device` views). The program then compares all three element by element and fails if any bit differs.

```sh
cairn run examples/apps/simulator --timeout 240
```

```text
simulator: 1024x1024 grid, 16 Jacobi sweeps
simulator: sequential 38291 us
simulator: threads    3628 us
simulator: device up  283130 us
simulator: device     448 us
simulator: all three back ends agree bit for bit
```

Needs nvcc and a CUDA device: any `@device` view sends the whole program through nvcc.

#### What it demonstrates

Placement is the only difference. `step_loop`, `step_threads` and `step_device` have the same body. The third one's views say `@device`, and that alone makes its lanes a kernel.

Bitwise agreement is a claim the language can keep. The arithmetic lives in one function, `blend(up, down, left, right) = 0.25 * (up + down + left + right)`, compiled for both host and device from one definition. The build forbids contraction and reassociation on both sides (`-ffp-contract=off`, `--fmad=false`), so the same expression really is the same operations in the same order. `blend` and `interior` are pure, which is what lets a device lane call them.

Ping-pong without moving an owner. Each round runs two sweeps, `a -> b` then `b -> a`, so the two `buffer`s never have to be swapped (a scoped buffer is a view, not a movable owner) and both keep the extent identity `m` that the callee's `[m]` demands.

`transfer` is the only crossing: two explicit copies, one in and one out. The effect row shows `transfer:h2d` and `transfer:d2h`, and nothing else touches the boundary.

#### Effect rows worth noticing

```text
blend         (empty)
interior      trap
step_loop     ffi_precondition, read:src, trap, write:out
step_threads  ffi_precondition, par:host, read:src, trap, write:out
step_device   ffi_precondition, par:device, read:src, trap, write:out
main          ... gpu_alloc, gpu_free, transfer:h2d, transfer:d2h, par:host, par:device
```

`blend` has an empty row: it reads nothing, writes nothing and cannot trap. The three sweeps differ in exactly one effect. `trap` on the others is the bounds and division guards, which stay in the device build too.

#### What the timings say

Measured 2026-09-19 on a GH200, 64 cores, CUDA 12.8, clang 15.0.7. Host threads beat the sequential loop by about 10x. Sixteen sweeps are sixteen regions, and they are cheap because the first one builds the lane pool and the other fifteen reuse it; under the 1.1 runtime, which created and joined a thread team per statement, the same program measured 24.0 ms instead of 3.6 ms, for about 1.6x. It is not about 64x because the kernel is memory bound. The device sweeps are about 85x faster than the sequential loop, but the first device allocation pays 283 ms to create the CUDA context, which the program reports separately rather than hiding inside the measurement.

### examples/apps/gpu_pipeline

Four device stages over 2^22 values, each one statement, verified against the same four stages run on the host. Every phase is timed with `io.monotonic_ns`, which is CLOCK_MONOTONIC through `clock_gettime`.

```sh
cairn run examples/apps/gpu_pipeline --timeout 240
```

```text
gpu_pipeline: 4194304 values, kept 524288
gpu_pipeline: host pipeline   3485 us
gpu_pipeline: upload          125 us
gpu_pipeline: device map      305 us
gpu_pipeline: device compact  600 us
gpu_pipeline: device reduce   155 us
gpu_pipeline: download        1647 us
gpu_pipeline: device result matches the host
```

Needs nvcc and a CUDA device.

#### What it demonstrates

The contracted forms run on the device unchanged. `compact out for i in m where ... yield ...` becomes CUB stream compaction when its output is a `@device` view and a plain loop when it is not, from the same source, and the stable-prefix contract holds on both. `reduce add_wrap ...` becomes a device tree reduction or a host in-order fold.

Why `add_wrap` and not `+`. The device folds in an unspecified association order, so only an operator that is exact under any association may be offered; checked `+` would make the trap depend on the order. The host and device sums are therefore equal by construction, and the program asserts it.

Compacted prefixes are parts. `keep_device` returns how many values it selected, and the sum of the selection is `sum_device(dev_used, dev_kept[0..dev_used])`, a part with one dynamic guard, since the buffer's own extent is the capacity and not the count.

Verification is elementwise. The kept prefix is downloaded and compared with the host's, and the two counts and the two sums must agree. The mixing function `mix` is pure and shared by the host loop and the device lanes.

#### Effect rows worth noticing

```text
map_device   ffi_precondition, par:device, read:src, trap, write:out
keep_device  ffi_precondition, gpu_alloc, gpu_free, par:device, read:src, trap, write:out
sum_device   ffi_precondition, gpu_alloc, gpu_free, par:device, read:src, trap
main         ... alloc, free, gpu_alloc, gpu_free, io, par:device, transfer:h2d, transfer:d2h
```

The three stages differ only in what they read and write. `keep_device` and `sum_device` also say `gpu_alloc` and `gpu_free`, which is CUB's own temporary storage for the scan and the tree reduction: the device buffers the program itself asks for are not the only device memory in the row, and the row says so. `main` carries no `par:host`, because the host `reduce` emits a sequential fold.

#### What the timings say

Measured 2026-09-19 on a GH200 with CUDA 12.8. The device does map, compact and reduce in about 1 ms against 3.5 ms for the host pipeline, but the download of the whole capacity costs 1.6 ms on its own. On this machine, moving results is more expensive than computing them, which is exactly the cost the language insists you write down as a `transfer`.

### examples/embedded

A sensor log arrives over a wire as comma-terminated decimal fields, some of them malformed. This program reports every bad field with the offset of the byte at fault, summarises the good ones with checked arithmetic, sorts them through a fixed histogram and prints all of it over a PL011 UART, on a machine with no operating system, no C library and no allocator.

```sh
cairn run   examples/embedded     # builds the image and runs it under QEMU
cairn build examples/embedded     # the ELF alone, plus the receipt beside it
```

```text
cairn freestanding: qemu virt, pl011 uart
log 23,19,31,7,42,19,x9,,99999999999999999999,
  bad digit at 17
  empty field at 20
  too large at 40
readings 6 total 141 max 42 mean 23
sorted 7 19 19 23 31 42
ok
```

The image exits with `fn main()`'s return value, 0 here, and `size` reports 5411 bytes of text with no data and no bss.

#### What each file shows

`src/uart.cairn` is the driver, and `mmio_read[u32]` and `mmio_write[u32]` inside `unsafe { }` are the whole of it: poll the flag register until the transmit FIFO has room, then write the data register. `putu` formats a `u64` as decimal into twenty bytes of `stack` storage, the widest a `u64` can be, with every write bounds checked and nothing allocated.

`src/parse.cairn` answers with `enum Reading { Value(u64); Invalid(usize); Overflow(usize); Empty; }`. A field that is not a number is a value, not an errno or a sentinel, and it carries the offset of the first byte at fault. Overflow is detected before it happens, so the parser never wraps.

`src/main.cairn` walks the input once in `ingest` and hands each field to the parser as `text[start..i]`, a part of the one borrowed view: no copy, no allocation, one dynamic guard that the part is inside the string. The `match` has an arm per variant and no wildcard, so adding a variant would break the build rather than silently fall through. The totals use checked `+`, so a log that overflows a `u64` stops the machine instead of reporting a smaller number. `sort_small` counting-sorts through a `stack counts:usize[64]` histogram; a reading outside `0..63` trips the range-checked `u8` conversion or the bounds check rather than corrupting a neighbouring bucket.

There is no `buffer`, no `Buf`, no `parallel` and no `extern` anywhere, and there could not be: the freestanding build reads the effect row of every function and refuses the program by name if one of them needs something a hosted runtime would have to provide. See [the freestanding profile](#projects-and-the-freestanding-target).

#### The guard violation

`trap/` is the same machine and the same driver, reading one element past a four-element array with an index that comes from storage rather than from a literal. There is no MMU here, so the read would succeed and return whatever follows the array. The language's own bounds check is the only thing that stops it.

```sh
cairn run examples/embedded/trap
```

```text
trap demo: reading window[4] of 4
```

`unreachable` is never printed and QEMU exits 134, the status a hosted shell reports for `std::abort`. A guard on bare metal is as loud as a guard on a host.

### examples/proof_scope

What `cairn verify` covers, and what it cannot reach. `reference.cairn` is the specification, `candidate.cairn` a different implementation of the same five functions, and `mixed.cairn` the case the value model has no account of.

```sh
cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all
# "status": "smt-module-equivalent", covered: average, extent, shifted, smaller, total

cairn verify examples/proof_scope/reference.cairn examples/proof_scope/mixed.cairn --all
# "status": "incomplete", missing: extent, shifted; extra: moved; uncovered: extent, moved, shifted
```

`moved` allocates a `Buf` and `take`s it, which the value model cannot express, so it is reported as uncovered rather than as equivalent. `native_proof` and `lean_proof` are `false` in both answers: this is SMT equivalence of two sources under a restricted value model, not a proof about the emitted machine code.

### examples/sketch

A host-bound sketch. The signature, the task and the reference belong to the host; the only thing searched is one named choice. `before.cairn` returns `(x + y) / 2`, which overflows, and `after.cairn` is what the deterministic search picks.

```sh
python3 tools/ai/sketch_demo.py --out /tmp/sketchdemo
# {"status": "passed", "native_cases": 81, "model_used": false}
# searches: 4 candidates, 4 semantic checker calls, 8 SMT queries; then 3 calls and 6 queries from cache
```

`average_0_reference-totality.smt2` and `average_1_equivalence.smt2` are the two queries that settled it, in that order. `choices.json` holds the winning expression and `finite_task.json` the 81 cases the result is then run against. No model is called: the search is over a fixed list of four expressions.

### examples/agent

The fixture behind the edit and repair loop: a task contract, a source with a deliberate off-by-one (`>=` where the task says strictly greater), the repaired source, and an adapter whose three answers are hard-coded.

```sh
python3 tools/ai/demo.py --out /tmp/agentdemo
# {"status": "passed-reserved-finite-tests", "attempts": 3,
#  "public_cases": 3, "reserved_cases": 5, "adapter_kind": "scripted-fixture"}
```

`scripted_adapter.py` is a fixture, not a model: it replies with a type error, then a behavioural error, then the correct body, which is what makes the transcript reproducible. `task.json` names the symbol, the allowed effects and the cases; some of them are reserved, so the adapter never sees what it is finally judged on. `prefix_sum.cairn` is a second, unrelated symbol the harnesses use.

## Verification

There is no whole-compiler proof. Three mechanisms establish three different things about three different objects, and none of them transfers to the others: Lean 4 proofs about hand-written models, Z3 queries about a modeled source fragment, and executed tests. Accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are separate claims, and `unknown` is never any of them.

### Summary

| Claim | Established by | Where | What it does not cover |
|---|---|---|---|
| The certificate checker is sound. | Lean, `check_sound` | `proofs/Cairn/Affine.lean` | That `linear_certificates.py` implements the Lean `check`; it is a reviewed transliteration. |
| The seventeen collector certificates pass that checker. | Lean kernel `decide`, `all_checked` | `proofs/Cairn/CollectorCertificates.lean`, generated | Anything about the emitted loop. |
| The collector loop model stores in bounds, keeps both cursors representable and selects stably. | Lean, `store_index_lt_capacity`, `increments_fit`, `collect_spec` | `proofs/Cairn/Collector.lean` | That the emitted C++ is this loop. The model uses `Int`/`Nat`, not machine words. |
| An accepted program of the ownership and lease calculus has no use-after-move, use-after-free, double free, leaked ticket, aliased argument or race, under every interleaving and every valuation; never gets stuck; and releases every cell exactly once on normal termination. | Lean, `accepted_no_fault` and the named faults, `accepted_threads_disjoint`, `accepted_frees_each_allocation_once`, `accepted_progress` | `proofs/Cairn/Places.lean`, `proofs/Cairn/Ownership.lean` | Any link to `checking.py`. Single elements, parts of parts, invisible bounds, closures, `lane:f`, placement, `reduce`/`compact`, streams. The part guard and the region join are assumed of the emitter. |
| Two versions of one function agree on the result and on everything they were lent, for every admitted input. | Z3 over a modeled source fragment, `smt-equivalent` | `src/cairn/verify/scalar_semantics.py` | A moving owner, recursion, tasks, lanes, device placement, closures, `dyn`, the foreign boundary, an unbounded trip count, an observed NaN, a tag inside a view, two views of one array in one call: each is `unknown`. Trusts the translator and Z3. |
| Every declared function and public type of two modules was compared that way. | `cairn verify --all`, `smt-module-equivalent` | `src/cairn/verify/verification.py` | One uncovered function keeps the module incomplete. Size, count and solver budgets apply. |
| Accepted programs build and run under both compilers, four sanitizers, CUDA and QEMU, and pass their finite task contracts. | Executed tests | `tests/` | Finite inputs only. |

```sh
cd proofs && lake build                       # about four seconds, no dependencies
cd proofs && lake env lean Cairn/Audit.lean   # the axiom audit on its own
python3 -m pytest -q tests/verification       # 195 tests, including that build
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

[the Lean development](#the-lean-development) has the Lean file layout, the pinned toolchain, the axiom audit and the drift check. [testing](#testing) has the test layers.

### The Lean project

`proofs/` is a dependency-free Lean 4 project. The toolchain is pinned in `proofs/lean-toolchain`, Mathlib is not used, and `lake build` checks it from scratch in about four seconds. There is no `sorry`, no `native_decide` and no added axiom: every audited declaration depends on `propext` and `Quot.sound` at most, and `Classical.choice` appears nowhere, the ownership calculus included.

The certificate module is generated from the Python rules by `tools/checks/export_lean_certificates.py`. A test fails if the two drift, and the compiler receipt reports `lean_verified: true` only while the live bundle hashes to the bundle Lean checked.

None of this establishes that the Python checker implements the Lean `check` (it is a transliteration, reviewed, not extracted), that the emitter's C++ corresponds to the loop model, or anything about the parser, the type checker, placement, effects, the C++ compiler or the machine. What the ownership calculus leaves open has its own list below. SMT results still trust their translator and Z3. Proving one relation does not confer correctness on the rest.

### The collector certificates and the loop model

`compact` lowers to a loop whose one store carries no dynamic bounds check:

```c
k = 0;
for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
used = k;
```

`out` has capacity exactly `n`, and `pred` and `proj` never read `out`. What licenses the unchecked store is the cursor invariant `0 <= k <= i <= n <= M`, where k is emitted outputs, i visited inputs, n capacity and M the largest representable cursor. While `i < n`, the store at k is in range and both increments fit. Emit substitutes `k'=k+1` and `i'=i+1`, skip substitutes `k'=k` and `i'=i+1`, initialization substitutes `i=k=0`, and on exit `k<=n`.

`src/cairn/verify/linear_certificates.py` writes an affine form as five exact integer coefficients over `(1,k,i,n,M)`, read as "this form is nonnegative". A rule fixes premises a_j and a conclusion g. Its certificate gives nonnegative integer multipliers c_j and a nonnegative integer c_0, and the checker accepts only when, coefficient by coefficient:

    g = c_0 + sum_j c_j * a_j

Under any assignment satisfying a_j>=0 every summand is nonnegative, so g>=0 by distributivity and equality of coefficients. The argument is universal over integer assignments, not an enumeration of example states. The implementation rejects Boolean, floating and negative multipliers, malformed dimensions and excessive integer sizes.

The store bound is the exact identity `n-k-1 = (i-k) + (n-i-1)`. The emitted-cursor bound adds `M-n`, giving `M-k-1 = (i-k) + (n-i-1) + (M-n)`.

Seventeen obligations cover initialization, nonnegative and in-range stores, both increments, emit and skip preservation, and output-count boundedness. Some initialization obligations are trivial identities. Seventeen is an obligation count, not seventeen independent research theorems.

The compiler checks the fixed bundle before every emission, and mutation tests confirm that a corrupted bundle blocks compilation. There is no user-editable assumption channel in the source or in the edit protocol. The receipt pins the rules and the checker source by SHA-256; hashes bind bytes, not correctness.

Trusted here: Python's exact integer arithmetic, this small checker's implementation, and the connection between the compiler's cursor operations and the transitions above. There is no mechanized proof of that connection. The certificates do not prove stable-selection behaviour, alias or lifetime safety, memory initialization, the absence of compiler bugs, or native machine behaviour, and they do not authorize arbitrary bounds-check removal.

Lean adds three results. `Cairn.check_sound`: if the checker accepts a rule, the rule's conclusion is nonnegative for every integer assignment that satisfies its assumptions. `Cairn.Collector.all_checked`: all seventeen certificates pass that checker inside Lean, and each named obligation becomes a Lean theorem by applying its certificate. And for an executable model of the loop: the invariant is preserved by emit and skip steps using the certified inequalities, every store index is below the capacity (`store_index_lt_capacity`), both increments stay representable (`increments_fit`), and the result is stable selection, so the written prefix equals `(inputs.filter pred).map proj`, its length is returned and the tail is unchanged (`collect_spec`).

```sh
python3 bin/cairn certificates
python3 -m pytest -q tests/verification/test_linear_certificates.py
```

### Ownership and leases: a mechanized core calculus

`proofs/Cairn/Places.lean` and `proofs/Cairn/Ownership.lean` are a second, independent development: a small ownership-and-lease calculus with an executable checker, an interleaving small-step machine with explicit error states, and checked safety and progress theorems. It is written by hand beside `src/cairn/compiler/checking.py`, not extracted from it, and it is evidence that the rules hang together, not that the implementation obeys them.

#### Locals and places

A local is the unit of ownership: it is allocated, moved, dropped and released. A place is what one borrow names, with one constructor per shape `checking.py:path` produces and one per case `checking.py:lend` chooses between.

| Lean | source | what it is |
| --- | --- | --- |
| `whole x` | `d` | the owner itself, header and elements: what an `rw<Buf[T]>` parameter lends, and the only borrow through which a task can replace the cell (`swap`) |
| `hdr x` | the `len(d)` read | the header alone: the length and the identity of the cell (`leased(..., elements=False)`) |
| `elems x` | `d[]` | every element: what an array view `rw<T>[n]` of a whole owner lends |
| `part x lo hi` | `d[lo..hi]` | those elements |

Each place carries a root: a local plus a field path, which is what `path` writes as `r.a.b`. `r.xs[lo..hi]`, `r.xs[]` and `len(r.xs)` are the same four shapes at the root `r.xs`.

A bound is an integer literal or the name of an immutable natural, which is what `path` keeps. Anything else becomes `?` there and is not modelled. The names are read by an arbitrary valuation, and every theorem below quantifies over every valuation, so no result depends on the numbers. Single elements (`a[i]`) and parts of parts are not modelled.

#### Disjointness, decided twice

The checker decides overlap syntactically, as `checking.py:overlaps` does. Places of different locals never overlap. The header and the elements do not overlap, which is why `len(d)` stays readable while a view of `d`'s elements is lent, and does not while `d` itself is lent. Two roots meet exactly when they are the same local and one field path is a prefix of the other, so distinct fields of one record are apart and a field and its record are not (`checking.py`'s dotted-prefix test). Two parts of one root are disjoint exactly when one visibly ends at or before the other begins. Everything else overlaps, and `whole x` overlaps every place of `x`.

"Visibly ends at or before" is `reaches`: the same bound, a literal comparison, or a chain through the `lo <= hi` facts of the parts in play. That is the reflexive-transitive closure `checking.py` searches depth-first, saturated one round at a time here.

The machine decides overlap under the valuation: two accesses collide when both touch the header or their index ranges meet. `Ownership.ovl_sound` is the bridge between the two, and the whole part story rests on it.

#### The guard, and the trap

A slice is formed by `cr::part(p, lo, hi, n, want)`, which traps unless `lo <= hi`. The real guard also checks `hi <= n` and the length the callee declared; only `lo <= hi` matters to the alias rules, so only it is modelled. The machine performs that guard for every part a statement names, on the spawner's thread, before the call is made or the task is started.

A failed guard is a trap: a fourth configuration beside running, finished and faulted, and a defined abort of the whole configuration, live tasks included, which is what `cr::trap()` does to the process. It is not a fault and not a race. The invariant `TasksGuarded` states that every part a live task holds was guarded, and `inPlay_guarded` adds the place being touched, whose slice is formed now. That is the explicit form of "every chaining fact corresponds to a guard that has already run".

It is also exactly what makes `d[6..3]` safe to accept. That slice orders `d[0..6]` before `d[3..9]`, which overlap, and the program aborts at its spawn before either of those tasks exists (`tests/soundness/test_soundness.py::test_a_backwards_part_used_to_order_two_others_aborts_at_its_spawn`).

#### Which facts each rule may chain

The two Python rules use different fact sets, and the model keeps them apart. `leased` chains through every part the live tasks hold, plus the place being touched. `disjoint` chains through the parts of the one argument list it is looking at.

So handing a single call `d[0..a]` and `d[b..n]` is rejected even while `d[a..b]` is lent to a live task, because that argument list contains nothing to order `a` before `b`, while spawning the same two ends one at a time is accepted. Both the Lean checker and `checking.py` behave that way. It is conservative, not unsound.

#### Syntax

A program is one scope: a list of declared locals plus a body. Statements are `alloc x` (a fresh heap cell lands in `x`), `mkScalar x` (a copyable scalar), `copy y x`, `move y x`, `drop x` (the implicit release at the exit of an inner scope), `call args` where `args` is a list of `(place, ro|rw)` borrows lent for the duration of the call, `spawn t args` (the borrows stay lent until `wait t`), `wait t`, and `ite thn els` with both branches.

A read of a scalar is `call [(whole x, ro)]`, a write of a part is `call [(part x lo hi, rw)]`, and `let k = len(x);` is `call [(hdr x, ro)]`. Borrows are second class by construction, since a borrow exists only as an entry in one argument list.

#### The checker

`Ownership.accepts` is an executable Lean function, not a relation, and its decisions can be evaluated. It carries the state `checking.py` carries in its `Scope`: which locals hold a scalar, which hold an owner, which are dead, and a lease map from a ticket to the borrows it holds.

It enforces: only declared locals; no use of a dead local (moved, dropped, or killed by a join); `copy` only of a scalar; no move, drop or rebinding of a local any of whose places is leased; no read of what a live task writes and no write of what a live task reads or writes, overlap decided by the rule above; no two overlapping borrows with an `rw` among the arguments of one call; a fresh ticket per `spawn`; at a branch join, a local moved on one path is dead afterwards and both branches must agree on which tickets are live; and no live ticket where the scope ends.

#### The machine

A small-step semantics over a heap of allocation ids, a liveness map and a per-cell release counter. It is deliberately undefensive, so that the faults are actually reachable for programs the checker rejects. `copy` duplicates whatever bits a local holds, which is how a mis-accepted copy of an owner reaches a double free. Binding a local releases what it held before. A move leaves a ghost mark whose only purpose is to let a later use be named `UseAfterMove`. Nothing in the machine prevents a fault; only the checker does.

The error configurations are `UseAfterMove`, `UseAfterFree`, `DoubleFree`, `Leak` (a live ticket where the scope ends), `Race` and `AliasedArgs`. `Trap` is not one of them.

Concurrency is interleaving, not a sequential approximation. A task's body is abstracted to its footprint: while its ticket is live it may touch any place it was lent, in the mode it was lent, between any two steps of the spawner, any number of times. A task holding the whole owner `rw` may also replace the cell, which is what `swap` through a lent owner does; one holding only elements or a part cannot, since a view cannot replace the storage it views.

`Race` is reached when the spawner and a live task, or two live tasks, touch a common location of one local with a write among them; for two parts that means their index ranges meet under the valuation. `if` has an opaque condition, so both branches are always reachable and the join is what makes a one-sided move dead.

#### Parallel regions

`parallel i in n { body }` is a statement whose body is a list of lane accesses, which is exactly what `checking.py:region` keeps in `Lanes.accesses`: for each place of the enclosing scope the body touches, its root, whether the index written there is the binder itself, and whether it writes.

Four shapes are modelled: `x[i]` at the lane's own index; `x[e]` for any other index expression, and any part or view lent on to a call, whose footprint is modelled as every element, the worst case, since nothing bounds where the index lands; the place itself (a shared scalar read or assigned, or a single borrow lent on); and `len(x)`.

The rule is `checking.py`'s: whatever any lane writes may be touched only at `x[i]`, keyed on the root local, not the field path, and a `len` read is not recorded at all (`check_len` never touches `Lanes.accesses`, and the header is not an element). Assigning a shared scalar is `E-PARALLEL-WRITE` there; here it fails the same rule, because such an access writes its local and is not at the binder. Every lane access is checked against the leases of the enclosing scope under the place `where` writes, which for any index is `x[]`, so a region beside a task holding any part of the same array is rejected, conservatively, exactly as `checking.py` rejects it.

What a lane cannot do is an absence rather than a rule: a body is a list of accesses, so a region cannot nest, `return`, move an outer owner or run a collector, and a local the body declares is not a place of the enclosing scope and does not appear.

`lanesOf n body` builds one thread per index below `n`, holding `part x [k, k+1)` where the body says `x[i]` and the worst case elsewhere. Lanes have the same shape as tasks and step by the same rule, so `stepThread` serves both, and the invariant's three clauses about live threads range over `tasks ++ lanes` together. That single `Pairwise` is what says no two threads conflict, whether they are two tasks, a task and a lane, or two lanes.

The spawner is blocked while a region runs, since `stepHost` offers it nothing but ending the region, which is what "completes before the next statement" means. Tasks spawned earlier keep running throughout and their leases are respected, because a lane's accesses were checked against them at the region. Race freedom between two lanes is `meets_own_element`: what any lane writes, every lane touches only at its own index, and two indices differ.

#### The theorems

Every statement below is proved in `proofs/Cairn/`, audited in `proofs/Cairn/Audit.lean` and required by `tests/verification/test_lean_proofs.py`. `Reach ρ p.scope (Cfg.start p) cfg` means "`cfg` is reachable from the initial configuration of `p` under the interleaving semantics, when the immutable bounds are worth what `ρ` says". The valuation is implicit in the theorems and universally quantified: an accepted program is safe for every one.

```lean
theorem reaches_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts)
    {x goal : Bound} (h : reaches facts x goal = true) : x.eval ρ ≤ goal.eval ρ

theorem ovl_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) {r s : Place}
    (h : ovl inPlay r s = false) : meets ρ r s = false

theorem accepted_no_fault {p : Program} (hp : accepts p = true) {ρ : Valuation} {cfg : Cfg}
    (hr : Reach ρ p.scope (Cfg.start p) cfg) (e : Err) : cfg ≠ Cfg.err e

theorem accepted_no_use_after_move {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.useAfterMove q)

theorem accepted_no_use_after_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.useAfterFree a)

theorem accepted_no_double_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.doubleFree a)

theorem accepted_race_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.race q)

theorem accepted_threads_disjoint {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {code : List Stmt} {tasks lanes : List Task} {st : State}
    (hr : Reach ρ p.scope (Cfg.start p) (.run code tasks lanes st)) :
    (tasks ++ lanes).Pairwise (NoRacePair ρ)

theorem lanesOf_pairwise (ρ : Valuation) : ∀ (n : Nat) {body : List Touch},
    laneRule body = true → (lanesOf n body).Pairwise (NoRacePair ρ)

theorem accepted_no_leaked_ticket {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (t : Ticket) :
    cfg ≠ Cfg.err (.leak t)

theorem accepted_no_aliased_args {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) : cfg ≠ Cfg.err .aliasedArgs

theorem accepted_frees_each_allocation_once {p : Program} (hp : accepts p = true)
    {ρ : Valuation} {st : State} (hr : Reach ρ p.scope (Cfg.start p) (Cfg.done st)) :
    (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)

def Progress : Prop :=
  ∀ (p : Program) (ρ : Valuation), accepts p = true →
    ∀ cfg, Reach ρ p.scope (Cfg.start p) cfg →
      (∃ st, cfg = Cfg.done st) ∨ cfg = Cfg.trap ∨ succ ρ p.scope cfg ≠ []

theorem accepted_progress : Progress
```

The race theorem is proved for the whole calculus, not for a fragment. It rests on `Ok_succ`, the preservation lemma: every successor of a configuration satisfying the invariant satisfies it too, the invariant is `False` on error configurations, and it is trivially true of a trap.

The release theorem is about normal termination only. A run that traps has aborted, and nothing is claimed about the cells it leaves behind.

`accepted_progress` is cheap on its own, because the machine is total by construction and every statement has at least one successor. Its content is that the stuck configurations are exactly the errors, which `accepted_no_fault` excludes. It replaces the `OpenGoal.Progress` that earlier versions of this file left unproved.

#### The regression, and that the faults are reachable

`ownership_regression` is the executable sanity check: the Lean encodings of the CAIRN programs pinned in `tests/soundness/test_soundness.py` and `tests/soundness/test_concurrency.py` are classified the way the Python checker classifies them.

Rejected: a move beside a view a task still holds, a leased read, a double move, an unawaited ticket, two arguments of one call overlapping with a write, a place moved on one path only, branches that disagree about live tickets, two tasks writing one place, two tasks writing parts that really overlap (`d[0..6]` and `d[3..9]`), two parts with nothing lent between them to order their bounds, one call handed two overlapping parts, a `len` read of an owner a task may replace, one field lent to two tasks, a new value landing in a lent field's cell, a field read while the record is lent whole, a move of a record one field of which is lent, a copy of an owner, and a use after the implicit release.

Accepted: the two-part and K-way splits (`d[0..a]`, `d[a..b]`, `d[b..n]`), the backwards part that orders two others, a `len` read under a lease of the elements, two fields of one record lent to two tasks, a `len` read of a field under a lease of that field's elements, shared read-only lending, a move on both paths, and a scalar copy.

Each of those classifications was re-checked against the Python checker on the corresponding CAIRN source while this model was written. The build prints `ownership-regression: pass`, and the Python gate asserts on that line.

The safety theorems would be vacuous if the machine could never fault, so a fault is also shown reachable for programs the checker rejects, each by exhibiting the step sequence rather than by a tactic. `leasedRead_races`, `overlappingTasks_races`, `overlappingParts_races`, `sameFieldToTwoTasks_races`, `laneWritesFixedIndex_races`, `laneWritesShared_races` and `laneReadsOther_races` drive the machine to `Race`; `fieldPartsOverlapInOneCall_aliases` to `AliasedArgs`; `copyAnOwner_doubleFrees` to `DoubleFree`; `useAfterDrop_usesDeadPlace` to `UseAfterMove`; and `unawaitedTicket_leaks` to `Leak`. `witnesses_are_rejected` confirms that the checker rejects all eleven. `UseAfterFree` is the one error configuration with no witness: no program here is shown to reach it.

`backwardsPart_traps` is the other side of the same coin. That program is accepted, and under the valuation it is written for (`a = 6`, `b = 3`, `n = 9`) the machine reaches `Trap` at the guard of `d[6..3]`, not a race. Together with `ownership_regression`, which rules out a checker that says no to everything, that pins the result from both sides.

#### What this does NOT cover

* Any connection to `checking.py`. The Lean checker is a hand-written abstraction of the Python rules. It is not extracted from them, not compared against them by a test, and the Python checker does many things this model does not.
* The guard is an assumption about the emitter, not a theorem. The chaining rule is sound in this calculus because the machine performs the `lo <= hi` guard where the slice is formed, before the borrow is taken. That the emitted C++ does the same, `cr::part` at the call site, on the spawning thread, before the task starts, is asserted by `src/cairn/compiler/codegen.py`, `src/cairn/runtime/cairn_owners.hpp` and a test that pins exactly this (`tests/soundness/test_concurrency.py::test_a_part_is_guarded_by_the_spawner_before_its_task_exists`: the guard sits in the capture list of the task's lambda, and a backwards part aborts after `spawning` is printed and before the task or the next statement runs). It is not asserted by any proof. If a part guard ever moved into the task, the rule would be unsound and this model would no longer describe the language.
* Single elements, parts of parts and invisible bounds. `a[i]`, `a[lo..hi][j..k]` and any bound `path` writes as `?` are outside the model. `checking.py` treats them conservatively, so everything of that base overlaps; nothing here proves that it does.
* Where a field read is charged. `checking.py:e_field` suppresses the whole-local read its base would otherwise perform and charges `leased(box.a, "ro", elements=False)` at the outermost field of the path, so two tasks may hold `box.a` and `box.b` at once. The model writes that read out as an explicit `call [(hdr box.a, ro)]` in the regression programs rather than building it into the field rule, and nothing proves that `checking.py` charges it exactly where the model does. Assignment, `take` and `swap` name `whole box.a` in the model and `leased(box.a, "rw")` in `checking.py`; that those two agree is also unproved.
* Everything a region is besides its accesses. `lane:f` callbacks and the `E-PARALLEL-CALL` effect rule, device placement and `E-PLACEMENT`, `reduce`, `compact`, queued device work and `after`, and the value a lane computes are all outside the model. A lane body is its footprint; the model does not say what a lane-private local holds, only that it is not a place of the enclosing scope. A lane's index expression other than the binder is modelled as the whole element footprint, so the model calls a race what `parallel i in n { x[i + 1] = 0; }` would not actually have. The checker rejects that program either way, and nothing here claims the converse.
* That a region really completes before the next statement. The machine blocks the spawner while lanes are live. That the emitted `cr::par::run`, or a CUDA launch and its synchronize, joins every lane before returning is asserted by `src/cairn/compiler/codegen.py` and the runtime, and tested, not proved. It is the same kind of assumption as the part guard.
* Closure captures. `checking.py:disjoint` also compares a closure's captured places against the call's arguments, and a captured part contributes its own bounds to the chain before its slice has been formed. Closures are not modelled at all, so that case is neither proved nor refuted here.
* Device streams. `e_spawn` hides the leases of tickets named in `after`, because queued device work runs after them on one stream. There are no streams in this model, so that exemption is absent: the calculus would reject those programs.
* Linear values other than tickets, `defer`, `take` and `swap` as statements, loops, `return`, `break` and `continue`, closures, traits, generics, placement, atomics, mutexes, effects and the foreign boundary. None of them appear.
* Callee bodies. A call is its footprint. `AliasedArgs` is detected, not caused: the model does not say what a callee would do with two overlapping views, only that the checker never hands it any.
* Value-level behaviour. A task's write does not change the abstract value at a place; only a replacement of the cell does. The race result is the absence of conflicting concurrent access, not determinism of results.
* Machine reality. No native memory model, no allocator, no C++ and no thread semantics. `Race` is a property of this abstract machine, not of hardware, and `Trap` is a configuration of it, not a signal.
* What a trap leaves behind. A trapped run makes no claim about released cells; it is an abort.

### Value-level source equivalence

`src/cairn/verify/scalar_semantics.py` is a symbolic evaluator over a source fragment, and Z3 answers whether two versions of one function can differ. It models exact-width integer and Boolean values, `f32`/`f64`, records, tag-only enums and payload sums with `match` and `try`, array views (`ro<T>[n]`, `rw<T>[n]`) and the parts `x[lo..hi]` taken from them, single borrows of values, fixed local storage (`stack x:T[N] = zeroed;` and `Array[T, N]` locals), function-local heap owners (`buffer x:T[n] = zeroed;`, `Buf[T](n)`), `compact`, host `reduce`, checked and wrapping operations, explicit conversions, assignment to a name, a field or an element, branching, short-circuiting, bounded `for` and `while` with `break` and `continue`, early return, `void` results and acyclic calls.

An observation is the returned value together with the final contents of every `rw` parameter, or one undifferentiated abort. An owner that moves (`take`, `swap`, a returned, stored or by-value owner), recursion, tasks, lanes, device placement, closures, `dyn` dispatch, atomics and the foreign boundary are unsupported: each returns unknown with the reason that refused it.

#### Views

A view parameter is one SMT array per scalar component of its element, read and written at `offset + i` while `i` is below the extent its signature names. The extent is a literal or an earlier `usize` parameter, so `xs` and `out` of `fn f(n:usize, xs:ro<T>[n], out:rw<T>[n])` share one extent term. `len(xs)` is that extent, an index at or above it is the trap `cr::at` takes, and a part `x[lo..hi]` shifts the window under the one guard `cr::part` applies (`lo <= hi <= len`, and `hi - lo` equal to the extent the callee declares).

The admitted inputs are the ones the emitted entry guards admit. `cr::view` says each view is a valid, aligned region of its extent, and `cr::disjoint` says the storage behind an `rw` view is distinct from the storage behind every other view of the same call, which is why two `rw` views are modeled as independent arrays. Read-only views may alias, and independent arrays of equal contents cover that case, so the quantification is over more inputs than a caller can build, never fewer. A single `rw` borrow of a value has no runtime guard: its independence is the `E-ALIAS` rule every call site obeys.

An `rw` view is observed element by element over its whole extent, so the solver is asked for an index inside the extent where the two runs differ.

A view whose elements carry a tag is unknown, because no entry guard checks a tag inside storage and the model cannot admit only well-formed elements without a quantifier. Two views of one array in one call, the visibly disjoint `b[0..mid]`, `b[mid..n]` split the checker allows, are unknown, since the model updates each argument's storage independently. A local `buffer` or `Buf` is zeroed storage of its own; allocation failure is outside the model, and an owner that leaves its place is not modeled at all.

#### Collectors and reductions

`compact` is unrolled as the host emits it: the predicate once per input, the projection only where it selects, the store at the running count (unguarded, as the certificates license), the tail untouched, and the count returned.

`reduce` is the in-order host fold from the identity the emitter uses, offered for `add_wrap`, `mul_wrap`, `&`, `|`, `^`, `min`, `max` and the checked unsigned `+`, all of which give the same result and the same trap in any order. A float reduction, whose order is unspecified, is unknown.

#### Values

A value is flattened into its scalar components: a record is its fields in declaration order, a sum is the emitted `std::uint32_t` tag beside every variant payload, an inline array is its elements. Two values look alike when their components do, except that a sum compares its tag and the payload of the active variant only, so storage the emitter never reads is not an observation.

Quantification is over well-formed values: every tag names a declared variant, which is exactly what the emitted entry guard `if (tag >= n) cr::trap();` admits. A record or sum named in the signature must have the same definition on both sides, or the answer is `invalid-contract` rather than a proof about two different types with one name.

#### Traps

Checked `+ - *`, division and remainder by zero or signed-minimum over minus one, narrowing and sign-changing conversions, shift counts at or above the width, the array bounds guard and the float-to-integer guard all abort. Every abort is one undifferentiated observation, so "both trap" counts as equal behaviour. By default the reference must additionally return on every admitted input, and `allow_reference_traps` is the explicit opt-out that compares return against abort.

#### Floating point

`f32` and `f64` use Z3's FloatingPoint theory. Every operation rounds to nearest even exactly once, which is the contract the compiler builds with (`-ffp-contract=off -fno-fast-math`, `--fmad=false` on the device): no contraction and no reassociation are modeled, because none is authorized.

Comparisons are the IEEE predicates, so a `NaN` operand compares false and `+0.0 == -0.0`. Equality of results is instead equality of the IEEE datum, so returning `-0.0` is not the same function as returning `+0.0`. `cr::truncate` is modeled as the header writes it, including the `low - F(1)` boundary it uses where that value is not representable, so NaN and out-of-range values trap. Integer-to-float conversion rounds once, which for `f32` is not always what rounding through `f64` would give.

Two modeling assumptions are stated rather than proved: that the C++ compiler rounds a decimal literal to nearest even, and that the host evaluates `float` arithmetic in `float` (`FLT_EVAL_METHOD == 0`, which the 64-bit target profile gives).

#### NaN

The theory has exactly one NaN and so cannot speak about payload bits, while the emitted C++ returns whatever bit pattern the machine produced and a foreign caller may read it. Rather than assume an equality the model cannot justify, the checker asks one more question before accepting: if an observed float, at any depth of the returned value or at any index of an `rw` view, may be NaN on an admitted input, the answer is `unknown`, never `smt-equivalent`. A caller who does not care excludes them with a precondition (`assume="x==x"`), which is a caller obligation like every other one here. NaN inputs are ordinary admitted values.

#### Loops

`for`, `while`, `compact` and `reduce` are unrolled up to 16 iterations, and what would still be running after that becomes an obligation of its own: Z3 is asked whether any admitted input reaches a seventeenth iteration, and anything but "no" is `unknown`. `break` leaves the loop, `continue` rejoins the increment, and mutually exclusive paths are merged at the end of each iteration so a branching body costs iterations rather than powers of two. A caller's path enters the functions it calls, so a callee's trip count is bounded by what reaches it.

An extent is symbolic, so a pass over `0..n` is unknown until a precondition bounds `n` within that budget; a literal extent needs none. Sixteen unrolled iterations over storage is also where the solver budget bites: the query grows with the bound and with the width of the elements, and a timeout is `unknown`, never a pass.

#### Evaluation order

Traps are order-insensitive here, because one abort is the same observation wherever it happens. A `try` is not: it returns. So `try` is modeled only as the whole right-hand side of a binding, an assignment, a return or an expression statement, and a `try` written as an operand of a larger expression is unknown, because C++ leaves the order of those operands unspecified and the answer would depend on it.

#### The query

The fixed host-owned reference R, candidate C and domain D produce definedness/value pairs (d_R,v_R), (d_C,v_C), where v is the observation above. The checker requires a well-defined nonempty domain, a complete unrolling, and by default a reference that returns on all admitted inputs. It asks Z3 whether this is satisfiable:

    D && ((d_R != d_C) || (d_R && d_C && v_R != v_C))

Unsatisfiable, with no reachable NaN in what is observed, means `smt-equivalent` in the named source model.

A satisfiable counterexample is independently replayed through an operational evaluator written against Python values before rejection. Storage a counterexample lends is read back by naming the first 16 elements of every view in the query, so the witness carries the contents that separate the two; a mismatch that needs a longer view than that is reported unknown rather than shown unreplayed.

Unavailable tools, translation errors, unsupported syntax, budget exhaustion, mismatched replay and solver timeouts are unknown, never acceptance. Domain restrictions are caller obligations, not guards automatically inserted by the native builder. The shipped module demo uses all declared inputs.

Source identity, translator identity, query hashes, solver version and outcomes are recorded. There is no checked proof reconstruction into Lean, and no theorem relates this translator to native output. A wrong reference can still express the wrong human requirement.

### Whole-module coverage, not selected-function promotion

```sh
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

The coverage checker parses and checks both complete sources, compares public records, enums and sums, and enumerates every function on both sides. Missing or extra entries, unsupported functions, mismatched types, partial references or any undecided obligation prevent `smt-module-equivalent`. Each entry retains its own result, and the receipt lists covered and uncovered functions. A tagged result is covered when its payloads are modeled values, and one uncovered function keeps the whole module incomplete.

`--all` passes no precondition, so a function whose trip count depends on a symbolic extent stays uncovered there even though `equivalent(..., assume=...)` decides it. There is a 64000-byte limit per input, at most 128 functions, and a soft 30-second solver budget. That budget is not a security sandbox or a strict wall-clock bound on all compilation.

`examples/proof_scope/mixed.cairn` is a negative coverage fixture: comparison with itself must remain incomplete, because it contains a function that moves an owner out of its place, which the model does not follow. Empty coverage is not success. Public-type changes also block aggregate acceptance even if numeric function results agree.

### Tests, native code and failures

Unit tests, independent Python behaviour oracles, both native compilers, instrumented allocation and release observations, ASan, UBSan and LSan, and object comparisons provide finite executed evidence. [testing](#testing) has the commands.

Instrumented lifetime counters run at O0 and do not establish optimized allocation counts. Production allocation-limit and invalid-access fixtures must terminate by SIGABRT. `testing.evaluate` requires both a passing behavioural report and a successful child exit, so a process cannot print a pass and then crash into a successful receipt.

Runtime address-space and CPU limits are protections against some runaway executions, not isolation. Native section equality is code-identity evidence under one compiler and flag profile, not a universal correctness or latency theorem. A new implementation may typecheck, pass examples and still be incorrect or slower on untested inputs.

### Tested, not proved: ownership, lanes, tasks and placement as implemented

The 1.0 rules as `checking.py` actually implements them are outside the calculus above: affine and linear values, second-class borrows, leases over parts whose bounds are not visible, the effect rules a lane's callees obey, placement and the effect fixed point. Those are exercised by acceptance and rejection tests, and nothing connects them mechanically to that calculus.

Accepted programs run natively under both compilers and under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer. Device guards are exercised by death tests that must abort the host. These are finite executions, not theorems: a sanitizer-clean run shows the absence of those faults on those inputs only.

Generic code is checked per instance, so an uninstantiated template is unchecked and is listed in the receipt rather than trusted.

### Formal completion gate

Three formal steps remain.

Prove that the emitted collector loop refines the Lean model, or generate it from the model.

Extend the ownership calculus past the places it now has, to single elements, parts of parts, closures, `lane:f` callbacks and placement. Then prove of the emitter what that calculus assumes of it: that a part's `lo <= hi` guard runs on the spawning thread before the task that borrows it starts.

Relate `checking.py` to the calculus by something stronger than review. Today the Lean checker is an abstraction written by hand beside the Python one, not extracted from it.

Only a pinned Lean build with audited axioms may be called Lean verification. The receipt field above is the single place the compiler says so, and it is scoped to the certificate bundle.

### The Lean development

`proofs/` is the Lean 4 side of everything above: how to build it, what each file holds, the statements themselves, the pinned toolchain and the axiom audit. It proves two things, and nothing about the compiler that emits either. The first is the bounded collector, whose store `out[k]` carries no dynamic bounds check, licensed by the cursor invariant `0 <= k <= i <= n <= M`. The second is the ownership-and-lease calculus of `Cairn/Places.lean` and `Cairn/Ownership.lean`: a statement language over named locals and the places borrowed out of them, an executable checker mirroring the rules `src/cairn/compiler/checking.py` enforces (`overlaps`, `leased`, `disjoint`, the branch join, the linear ticket), an interleaving small-step machine with explicit error states and a trap, and the theorems that an accepted program reaches no error state and never gets stuck, for every valuation of the part bounds and of the lane count.

#### Build and audit

```sh
# One-time toolchain install (binaries land in ~/.elan/bin)
curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --default-toolchain none

# Build everything (elan reads proofs/lean-toolchain and fetches v4.34.0)
cd proofs && lake build

# The axiom audit on its own
cd proofs && lake env lean Cairn/Audit.lean

# Regenerate the certificate module from the Python rules
.venv/bin/python tools/checks/export_lean_certificates.py

# Fail if the Lean file has drifted from the Python rules
.venv/bin/python tools/checks/export_lean_certificates.py --check

# Both, plus the build and the axiom audit, under pytest
.venv/bin/python -m pytest -q tests/verification/test_lean_proofs.py
```

`lake build` prints the axiom audit and the line `ownership-regression: pass`, which the Python gate asserts on.

#### Layout

| File | Contents |
| --- | --- |
| `Cairn/Affine.lean` | `Form`, `Form.eval`, `Rule`, `Certificate`, the computable `check`, and `check_sound`. |
| `Cairn/CollectorCertificates.lean` | Generated. The 17 obligations and their certificates as Lean data, `all_checked`, and one corollary per obligation. |
| `Cairn/Collector.lean` | Executable model of the loop; the invariant derived from the certificates; store-in-bounds, stable selection, and increment bounds. |
| `Cairn/Places.lean` | What a borrow names: roots (a local and a field path), bounds, valuations, the chain of guarded `lo <= hi` facts (`reaches`), the checker's syntactic overlap (`ovl`, mirroring `checking.py:overlaps`), the real footprints under a valuation (`meets`), and the bridge `ovl_sound`. |
| `Cairn/Ownership.lean` | The ownership and lease calculus: syntax (`Touch` is one lane access), the executable checker `accepts`, the interleaving machine over tasks and lanes, the preservation lemma `Ok_succ`, the soundness and progress theorems, and the regression over the programs `tests/soundness/test_soundness.py` pins. |
| `Cairn/Audit.lean` | `#print axioms` for every headline theorem, and the ownership regression line. |
| `Cairn.lean` | Root module importing everything. |

No dependencies. Lean 4 core only (`omega`, `decide`, `simp`, `Int`/`Nat`/`List` lemmas). Mathlib is deliberately not used, so the trusted base is the pinned Lean toolchain and nothing else.

#### What is proved

##### 1. The certificate checker is sound

`check r c` accepts a rule/certificate pair exactly when, coefficient-wise, `conclusion = (c0, 0, 0, 0, 0) + sum_j w_j * assumption_j` with every `w_j >= 0` and `c0 >= 0`, which is a transcription of `check()` in `src/cairn/verify/linear_certificates.py`. Soundness is universal over integer assignments, not an enumeration of states:

```lean
theorem Cairn.check_sound {r : Rule} {c : Certificate} (h : check r c = true) :
    ∀ K I N M : Int, (∀ a ∈ r.assumptions, 0 ≤ a.eval K I N M) →
      0 ≤ r.conclusion.eval K I N M
```

##### 2. All seventeen shipped certificates are accepted, by kernel computation

```lean
theorem Cairn.Collector.certificates_length : certificates.length = 17 := by decide
theorem Cairn.Collector.all_checked :
    certificates.all (fun rc => check rc.1 rc.2) = true := by decide
```

`decide`, not `native_decide`: the Lean kernel replays the exact integer arithmetic. Applying `check_sound` to each pair turns each named obligation into a Lean theorem about arbitrary integers, for example:

```lean
theorem Cairn.Collector.obligation_store_strictly_below_capacity (K I N M : Int)
    (h0 : 0 ≤ Form.eval ⟨0, 1, 0, 0, 0⟩ K I N M)
    (h1 : 0 ≤ Form.eval ⟨0, -1, 1, 0, 0⟩ K I N M)
    (h2 : 0 ≤ Form.eval ⟨0, 0, -1, 1, 0⟩ K I N M)
    (h3 : 0 ≤ Form.eval ⟨0, 0, 0, -1, 1⟩ K I N M)
    (h4 : 0 ≤ Form.eval ⟨-1, 0, -1, 1, 0⟩ K I N M)
    : 0 ≤ Form.eval ⟨-1, -1, 0, 1, 0⟩ K I N M
```

##### 3. The invariant, derived from those obligations

`Inv K I N M` bundles `0 ≤ K`, `K ≤ I`, `I ≤ N`, `N ≤ M`. Each transition theorem is proved by applying the corresponding certified obligation, not by re-deriving the arithmetic with `omega`. `omega` appears only to translate between the affine encoding `c + ck*K + ci*I + cn*N + cm*M >= 0` and ordinary inequalities, and between `Nat` indices and their `Int` images.

The eight transition theorems live in `namespace Inv` with `variable {K I N M : Int}` in scope, and each names the obligation it applies:

```lean
theorem Inv.init {N M : Int} (hn : 0 ≤ N) (hm : N ≤ M) : Inv 0 0 N M   -- initial.*
theorem Inv.emit  (h : Inv K I N M) (hlt : I < N) : Inv (K + 1) (I + 1) N M -- emit.invariant.0-3
theorem Inv.skip  (h : Inv K I N M) (hlt : I < N) : Inv K (I + 1) N M      -- skip.invariant.0-3
theorem Inv.store_nonneg         (h : Inv K I N M) (hlt : I < N) : 0 ≤ K   -- store.nonnegative
theorem Inv.store_lt_capacity    (h : Inv K I N M) (hlt : I < N) : K < N   -- store.strictly_below_capacity
theorem Inv.emit_increment_fits  (h : Inv K I N M) (hlt : I < N) : K + 1 ≤ M -- emit.cursor_increment_fits
theorem Inv.step_increment_fits  (h : Inv K I N M) (hlt : I < N) : I + 1 ≤ M -- step.input_increment_fits
theorem Inv.exit_le_capacity     (h : Inv K I N M) : K ≤ N                 -- exit.output_count_bounded
```

##### 4. The executable loop model and its proofs

`State β` carries `(out : List β, k : Nat, i : Nat)`. `step` performs `out[k] = proj x; ++k` on a selected input and always `++i`, `run` folds `step` over the inputs, and `collect` starts both cursors at zero. The model really runs: `Cairn/Collector.lean` contains a worked `decide`-checked example.

The invariant is preserved by every iteration, each step using only `InvN.emit` or `InvN.skip`, that is the `emit.invariant.*` and `skip.invariant.*` certificates:

```lean
theorem run_preserves_inv (pred : α → Bool) (proj : α → β) (n : Nat) (M : Int) :
    ∀ (xs : List α) (out : List β) (k i : Nat),
      InvN k i n M → i + xs.length ≤ n →
      InvN (run pred proj xs ⟨out, k, i⟩).k (run pred proj xs ⟨out, k, i⟩).i n M
```

Every store is in bounds with no dynamic guard, for every position in the input where the loop body runs:

```lean
theorem store_index_lt_capacity (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    (run pred proj pre ⟨out, 0, 0⟩).k < out.length

theorem store_index_lt_buffer_length (…same hypotheses…) :
    (run pred proj pre ⟨out, 0, 0⟩).k < (run pred proj pre ⟨out, 0, 0⟩).out.length
```

The second form is stated against the live buffer at the moment of the store, so `List.set`, which is total and would silently discard an out-of-range write, never discards anything.

Functional correctness is stable selection, with capacity exactly `n`:

```lean
theorem collect_spec (pred : α → Bool) (proj : α → β) (xs : List α) (out : List β)
    (hcap : out.length = xs.length) :
    (collect pred proj xs out).k = (xs.filter pred).length
    ∧ (collect pred proj xs out).k ≤ out.length
    ∧ (collect pred proj xs out).i = xs.length
    ∧ (collect pred proj xs out).out.length = out.length
    ∧ (collect pred proj xs out).out.take (collect pred proj xs out).k
        = (xs.filter pred).map proj
    ∧ (collect pred proj xs out).out.drop (collect pred proj xs out).k
        = out.drop (collect pred proj xs out).k
```

The prefix `out[0..k)` is exactly `(inputs.filter pred).map proj` in input order, `k` is the number of selected inputs and is within capacity, the buffer does not change length, and the tail `out[k..n)` is untouched. The general form, `run_spec`, gives the same statement from an arbitrary `(k, i)` with `k ≤ i` and enough room left.

Neither increment overflows, with the capacity representable as `n ≤ M`:

```lean
theorem increments_fit (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (M : Int) (hM : (out.length : Int) ≤ M)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    ((run pred proj pre ⟨out, 0, 0⟩).k : Int) + 1 ≤ M
    ∧ ((run pred proj pre ⟨out, 0, 0⟩).i : Int) + 1 ≤ M
```

##### 5. The ownership and lease calculus

`reaches_sound`, `ovl_sound`, `accepted_no_fault` and its six named faults, `accepted_threads_disjoint`, `lanesOf_pairwise`, `accepted_frees_each_allocation_once` and `accepted_progress`, with `Ok_succ` as the preservation lemma, `ownership_regression` as the executable regression against the Python checker's classifications, and twelve witnesses that drive the machine into a fault or a trap. [verification](#verification) quotes each statement and says what the model contains.

#### What is NOT proved

This development proves things about models. Each of the following remains trusted, exactly as before.

* The ownership calculus is an abstraction written by hand. Nothing extracts it from `checking.py` or compares the two, and it assumes of the emitter what `cr::part` and `cr::par::run` do and no proof states. [verification](#verification) has the full list of what it covers and what it leaves out.
* The compiler-to-model correspondence. Nothing here relates `Cairn.Collector.step`/`run` to what `src/cairn` actually emits. That the Python emitter produces this loop, with this capacity and these cursor updates, is trusted code review, not a theorem.
* The generated C++ and the native code. No refinement theorem connects the model to the emitted C++, to the machine instructions a C++ compiler produces from it, to the runtime, or to any target memory model.
* Machine arithmetic. The model uses mathematical `Int`/`Nat`. `M` is a parameter standing for "largest representable cursor"; the theorems say the cursors stay `≤ M`, and they do not model wrapping, `size_t`, or pointer arithmetic.
* Alias, lifetime and initialization safety. That `out` is live, uniquely owned, correctly sized and initialized, and that `pred`/`proj` do not read or alias `out`, are assumptions of the model, not conclusions.
* Everything else in the compiler. Parser, typechecker, allocator, foreign callers, operating system. Proving one relation confers nothing on them.
* The Python checker itself. `check_sound` is proved about the Lean transcription of `check()`. That the transcription matches the Python byte for byte is human-checked. The Python-only guards it omits, rejecting non-`int` inputs and integers wider than 4096 bits, are representation hygiene for a dynamically typed host and carry no mathematical content. The rule data itself is generated from the Python, so it cannot drift silently.

#### Pinned versions

| Component | Version |
| --- | --- |
| Lean | 4.34.0 (`leanprover/lean4:v4.34.0`, commit `293d5d0c0c3f3dded4688b3ccd6a33939ac5102b`) |
| Lake | 5.0.0-src+293d5d0 |
| elan | 4.2.4 |
| Dependencies | none (`lake-manifest.json` lists no packages; no Mathlib) |
| Host of record | Linux aarch64 (GH200) |

A clean `lake build` (after `rm -rf .lake/build`) takes about four seconds wall clock on the host of record.

#### Axiom audit result

`Cairn/Audit.lean` runs `#print axioms` on every headline declaration: `check_sound`, `check_sound'`, `eval_combine_nonneg`, `forall_mem_of_satisfies`, `all_checked`, `certificates_length`, all 17 `obligation_*` corollaries, the 8 `Inv.*` transition theorems, `run_preserves_inv`, `run_spec`, `collect_spec`, `store_index_lt_capacity`, `store_index_lt_buffer_length`, `increments_fit`, the ownership declarations (`reaches_sound`, `ovl_sound`, `accepted_no_fault` and its six named faults, `accepted_threads_disjoint`, `lanesOf_pairwise`, `lane_borrows_dont_race`, `races_borrow_of_lease`, `accepted_frees_each_allocation_once`, `accepted_progress`, `Ok_succ`, `Ok_start`, `checkBlock_mono`, `releaseAll_final`, `ownership_regression`), the twelve non-vacuity witnesses and `witnesses_are_rejected`.

The result:

* `Cairn.Collector.certificates_length` does not depend on any axioms.
* `Cairn.forall_mem_of_satisfies`, `Cairn.Collector.all_checked`, `Cairn.Ownership.Ok_start`, `Cairn.Ownership.releaseAll_final`, `Cairn.Ownership.races_borrow_of_lease`, `Cairn.Ownership.ownership_regression` and every `Cairn.Ownership.Regress.*` witness depend on `[propext]`.
* Every other declaration depends on `[propext, Quot.sound]`.

So the only axioms anywhere in this development are `propext` and `Quot.sound`, both of which arrive through core-library lemmas and the `omega`/`simp`/`decide` tactics. `Classical.choice` does not appear. There is no `sorryAx` (no `sorry`), no `Lean.ofReduceBool` (no `native_decide`), no `axiom` declaration, no `unsafe` and no `implemented_by`. `tests/verification/test_lean_proofs.py::test_lean_sources_contain_no_escape_hatches` enforces that against the sources, and the build test enforces it against the audit output.

`evidence/v1_0/lean/print-axioms.txt` is the 1.0 capture and predates the ownership module; it is a dated record of that run, not of this tree.

#### Drift

`Cairn/CollectorCertificates.lean` is generated by `tools/checks/export_lean_certificates.py` from `collector_rules()`. Its header records the SHA-256 of the exported obligation table, the same digest `cairn certificates` reports (`5648cb8f…c1f2` at the time of writing). Editing the Python rules without regenerating makes `tools/checks/export_lean_certificates.py --check`, and therefore `tests/verification/test_lean_proofs.py`, fail.

#### Evidence

`evidence/v1_0/lean/` holds the captured `lake build` log with timing, the `#print axioms` output, `lean --version`/`lake --version`/`elan --version`, and `summary.json` (theorem-by-theorem axiom lists, toolchain, date, host, source hashes).

## Compiler architecture

A project becomes a native artifact in nine stages. Each owns one question, and nothing but the emitter produces text. Paths below are under `src/cairn/`.

| Stage | Module | Entry point | What it produces |
|---|---|---|---|
| Load | `projects/project.py` | `load_project` | one combined source from the manifest's listed inputs, a sha256 per file, a line-to-file map |
| Parse | `compiler/syntax.py` | `Parser.parse` | tokens, spans, a tree that resolves nothing |
| Link modules | `compiler/modules.py` | `link` | the imported `std.*` modules, merged in; nothing else is importable and nothing is fetched |
| Derive recipes | `compiler/expansion.py` | `derive` | each `derive`'s declarations, as ordinary code of the deriving module |
| Specialize families | `compiler/expansion.py` | `specialize` | one function per variant of a family's natural range |
| Check | `compiler/checking.py` | `Checker.bodies` | one typed tree, annotated in place (`Expr.ty`, `Expr.ref`) |
| Judge | `compiler/checking.py` | `Checker.judge` | every effect row, and the rules that need all of them |
| Emit | `compiler/codegen.py` | `Emitter.units` | readable C++20: one shared header, one unit per module |
| Build | `projects/build.py` | `build` | a fresh directory, a hashed native artifact, a `cairn.build/1` receipt |

`compiler/cairnc.py` is the facade. `compile_program` runs parse through judge; `generate` calls `audit_collector` (`verify/linear_certificates.py`) before it emits a line, so the collector's one unchecked store never reaches C++ without its seventeen certificates.

### Where a rule lives

Checking is one pass per function over one typed tree, and a generic instance is checked as ordinary monomorphic code. `effects.py`, `traits.py` and `constants.py` are functions over the checker, not separate passes, so `checking.py` stays the statement and expression rules.

| Rule | File | Function |
|---|---|---|
| Names, types, generic instances | `compiler/checking.py` | `Checker.resolve`, `Checker.expr` |
| Places, second-class borrows | `compiler/checking.py` | `Checker.place` |
| Affine moves, releases and linear values | `compiler/checking.py` | `Checker.consume`, `Checker.leaks`, `Checker.releases`, `Checker.released`, `Checker.branches` |
| Task leases | `compiler/checking.py` | `Checker.leased` |
| Lane race freedom | `compiler/checking.py` | `Checker.region`, `Checker.judge_lane_callbacks` |
| Placement | `compiler/checking.py` | `Checker.host_only`, `Checker.judge` |
| Effect vocabulary, fixed point, operand order | `compiler/effects.py` | `fixed_point`, `audit` |
| Traits, bounds, overlap, dynamic tables | `compiler/traits.py` | `implemented`, `dispatch`, `vtable`, `certify` |
| Constant folding | `compiler/constants.py` | `constant`, `fold` |
| Each primitive's type and cost, beside its lowering | `compiler/builtins.py` | `check_*` and `lower_*` |
| Manifests, vendored dependencies | `projects/project.py` | `read_manifest`, `contained_file`, `claim`, `dependencies` |
| Native flags, the freestanding effect ban | `projects/toolchain.py` | `command`, `flags`, `audit_effects` |

Per-function state lives in one `Scope` that the checker swaps when it checks an instance in the middle of its caller, so instantiation is re-entrant. Signatures are resolved for every concrete function before any body is checked, so a call site never sees an unresolved type.

Three questions have one answer each. *Who may touch this place now?* is `leased`, which every access passes: it enforces task leases and records what a closure captures, and those captures are the closure's borrows at the call it is written in. *Who implements this trait for this type?* is `implemented`: it holds an impl to its trait's declaration, rejects overlap, instantiates generic impls, and serves static calls, `dyn` borrows and `Dyn` values alike. *What may run in a lane?* is one rule applied three times: to the lane's own row, to the rows of the functions it calls, and, through the `lane:f` footprint that the fixed point renames up the call graph, to the closure or function finally passed for a parameter that lanes call. Dynamic call edges are drawn after every body is checked, since a later coercion can add an implementation.

A lane body is one lambda whose entry point, `cr::par::run` or `cr::gpu::launch`, is chosen by placement, so host and device share the emitter. `toolchain.py` chooses the one native command line: clang++/g++, or nvcc under the same strict floating-point and warning contract. A device program's host pass makes one authorized departure from that contract, `-fexceptions` in place of `-fno-exceptions`, because CCCL 3 (CUDA 13) writes unguarded throw and catch inside the headers CUB's dispatch requires. Nothing in the runtime throws, guards still abort, and `tests/tooling/test_tools.py` pins the swap to that one flag.

### Runtime headers

| Header in `runtime/` | Owns |
|---|---|
| `cairn_runtime.hpp` | the guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard is host and device callable, and the device trap was chosen by measurement |
| `cairn_owners.hpp` | the movable zeroed `Buf`, `Defer`, borrowed callables, checked parts |
| `cairn_parallel.hpp` | the host lane pool, linear tasks, `Mutex` and `Atomic` with explicit orders |
| `cairn_gpu.hpp` | scoped device, pinned and unified memory, lanes, linear stream tickets, reduction, stable compaction |

Generated code includes only the headers it needs. A freestanding image includes neither concurrent header: `toolchain.audit_effects` rejects every effect that reaches them, and `cairn_parallel.hpp` refuses to compile under `CAIRN_FREESTANDING` so that stays true.

The one piece of global state is that lane pool, and it is visible in the source: the first host `parallel` statement of a process creates it, every later one reuses it. Its size is `std::thread::hardware_concurrency()`, or `CAIRN_LANES` when that names a count from 1 to 1024; anything else traps. A region below 16384 elements (`lanes::CUTOFF`) is the loop it replaces and never touches the pool. Above that it publishes a descriptor on its own stack, engages one lane per 8192 elements (`lanes::GRAIN`) up to the pool's size, and returns when every claimed chunk has run. The thread that starts a region is always one of its lanes and can finish it alone, so a blocked, busy or absent worker delays a region but cannot deadlock one, and regions started at once from several tasks each make progress. The pool is never destroyed: an exit handler stops and joins its workers, after which a region still runs correctly on the thread that starts it, so no static destroyed later finds it gone. Both numbers, and the wake and spin policy behind them, were measured on one machine; `evidence/v1_2/host_regions` records what was measured and what was not.

### Agent and proof paths

`agent/agent_tools.py` supplies typed source sites, the canonical read-only projection of the whole language, and sealed edit sessions whose effect ceiling spans the full effect vocabulary. `agent/sketches.py` binds named choices to host-owned ranges and contracts. `agent/teaching.py` selects short rule cards from lexical tokens. Splicing preserves everything outside the authorized range, and the complete linked module is rechecked, not the displayed packet alone.

`verify/linear_certificates.py` checks exact affine implications; `proofs/` proves that checker sound in Lean, checks the same seventeen certificates there, and proves the collector loop model in bounds and stable. `verify/scalar_semantics.py` and `verify/smt_bridge.py` compare values against a fixed reference source (scalars, floats, records, sums, fixed local arrays) over bounded loops, with concrete replay. `verify/verification.py` owns aggregate coverage and cannot mark a module checked because one function passed. [verification](#verification) has the boundaries.

No agent, test generator or solver may rewrite the authority it is checked against. Effects do not specify functional behavior. Native libraries never import the agent tooling or Z3.

### Packaging

The wheel holds the compiler package, the runtime headers, the target support files, the `std` sources, the typed marker and the CLI metadata. Tests, benchmarks, proofs, evidence and historical specifications stay out of it. Ordinary compilation has no third-party Python dependency; Z3, Lean, CUDA and QEMU are optional local tools, and [testing](#testing) says what their absence does to a gate.

## Testing

Every gate runs locally, publishes nothing and needs no network. A gate whose tool is absent skips with a reason or reports `unknown`, which is never a pass.

### Everyday gates

```sh
make lint          # ruff format --check, ruff check, cairn fmt --check
make test          # the whole suite in parallel; hardware- and tool-dependent parts skip with a reason
make proof         # certificates, the Lean export drift check, lake build, scalar module equivalence
make gpu embedded  # CUDA runtime and lanes, and the QEMU board, where the hardware is present
```

`make proof` needs `lake` on `PATH`; elan installs it in `~/.elan/bin`. Its Lean half prints the axioms behind every theorem, and only `propext` and `Quot.sound` are allowed:

```text
info: Cairn/Audit.lean:21:0: 'Cairn.check_sound' depends on axioms: [propext, Quot.sound]
```

`make proof` runs only the positive coverage case. Run the negative one by hand: `cairn verify examples/proof_scope/mixed.cairn examples/proof_scope/mixed.cairn --all` must come back incomplete and nonzero, because a function that moves an owner cannot inherit a scalar pass. Record both outcomes. Neither implies a successful Lean build; [verification](#verification) has the boundaries.

### What each folder establishes

Run one folder with `python -m pytest -q tests/soundness -n auto`.

| `tests/` folder | What it establishes |
|---|---|
| `language/` | the accepted breadth of the language, built and run natively under both compilers; the twelve tour programs; every `cairn` block in README.md and in this manual |
| `soundness/` | every hole an audit found stays closed; tasks, leases, atomics, mutexes, host and CUDA lanes, closures |
| `verification/` | the certificates, `proofs/` in step with `collector_rules()` and building, the SMT translator against concrete replay, coverage that no single function can confer |
| `projects/` | manifests, vendored dependencies, incremental builds, the five applications, the freestanding image under QEMU with an exact UART transcript |
| `runtime/` | the self-checking binaries in `tests/native/`, at several `CAIRN_LANES` counts |
| `tooling/` | `cairn fmt` over every `.cairn` in the checkout plus whitespace and comment fuzz, a real `cairn lsp` subprocess, publication against fakes, every script under `tools/` and `bench/` |
| `agent/` | projections, packets, rule cards, sketches, guarded edits, and the canonical projection round-tripping every sample and `std` module to identical native code |
| `checks/`, `native/` | not pytest modules: the Python oracles `tools/checks/verify.py` drives, and the C++ and CUDA fixtures `runtime/` compiles |

Sanitizers run where they bite: the ownership program under Address, Leak and UndefinedBehavior, tasks and lanes under Thread. Death tests must abort the host, one per invocation: a guard that fires inside a CUDA lane, an unawaited ticket. A task whose child exits abnormally has failed, whatever it printed. Examples are gates too, and `cairn run examples/systems --memory-mib 1024` with `cairn test examples/systems --cxx g++` drives a typed decimal parser and a stack and heap sorting and filter pipeline whose contracts inspect meaningful outputs rather than a successful compilation.

### Rejection and behaviour tables

A rejection table maps a sentence naming the rule to a diagnostic code and a program. One parametrized test compiles each entry and requires exactly that code, so a rule that stops biting fails by name. `tests/soundness/test_soundness.py` holds 82 entries over 25 codes; smaller tables sit beside the feature they guard. Every safety rule has one.

A native behaviour table maps a sentence to an expected process exit status and a program. The test emits C++ for that entry point alone, builds under clang++ with `-fsanitize=address,undefined`, runs it, and requires exactly that status: `0` where the program judges itself, `-6` where a guard must abort. Fifteen entries follow the second audit.

### The tools

```sh
python tools/checks/verify.py --gcc --sanitize
python tools/checks/validate_systems.py
python tools/checks/validate_semantics.py --gcc
```

| Script under `tools/checks/` | What it checks |
|---|---|
| `verify.py` | rebuilds the native artifacts and drives the oracles in `tests/checks/`: equal boundary checks, strict floating flags, both compilers, independent codec and template cases, exhaustive small collectors |
| `validate_systems.py` | decimal values and first error offsets, sorts, filters, unchanged inputs, output tails and identity edits against Python oracles under both compilers, plus an O0 observer counting allocation and release across returns, loops and match exits |
| `validate_semantics.py` | the translator and trap-aware interpreter against Python arithmetic, the concrete interpreter, instrumented clang and gcc, and input-pinned SMT |
| `semantic_check.py`, `semantic_corpus.py` | two scalar implementations against one immutable reference; same-contract pairs, every label decided and replayed |
| `curriculum_verify.py`, `mutation_checks.py` | teaching programs against independent finite oracles; one hand-authored defect per algorithm family, all of which the finite tests must catch |
| `check_compact_forms.py`, `native_scalar.py` | complete definitions, not generated expansions; a test-only trap observer that is not the production runtime |
| `density.py`, `export_lean_certificates.py` | lexical density accounting; `--check` fails when `collector_rules()` and `proofs/` have drifted |

Production sanitizer and SIGABRT fixtures are separate from that O0 observer, `validate_systems.py` proves nothing about allocation or lifetime safety for arbitrary programs, and a trusted translator or oracle can still hold a bug. `bench/cpu/codegen_only.py` compares code sections by instruction bytes and relocations and implies no fresh timing run: five of its nine selected function sections were byte-identical to the C++ references on AArch64 at 1.0, where the 0.6 figure of eight of nine was x86-64 under another compiler. These harnesses write under `results/`, which is ignored and may be replaced on rerun.

`tools/ai/measure_context.py` keeps its counterfactual honest: the same current full JSON packet for each legacy-profile function, with only the rule-card text swapped for the preserved 0.5 text, and new-feature packets reported apart because they have no executable 0.5 baseline. Its counts are exact plain ByT5 bytes with no special tokens; `--tiktoken o200k_base` needs a separately installed package and vocabulary and is reported only if it ran. Packet sizes are not logged conversations, training gains or comprehension scores, and wider feature coverage can enlarge the full card while common packets shrink.

### Evidence

`python tools/release/collect_evidence.py --release v1_1` runs the release gates (format, lint, mypy, `cairn fmt --check`, the suite, certificates, the Lean drift check, module equivalence) and writes `evidence/<release>/summary.json`: each gate's command, status, exit code, seconds and last three output lines, plus `cairn doctor`, the commit, whether the worktree was dirty, and source-line counts. A missing tool is `unavailable`, a timeout `timed-out`, and nothing is retried. The `lean/`, `gpu/` and `embedded/` records come from the harnesses that produce them.

`evidence/` holds one directory per release. `summary.json` is the entry point of each, `v1_2/RUN_NOTES.md` says what ran, on which machine, and what was not done, and every directory keeps the names and source identity it was recorded under. `v1_0/` also holds `lean/`, `gpu/` and `embedded/` records, `v1_1/` the preregistered `ai_pilot/`, and `v1_2/host_regions/` a later measurement of host parallel regions that supersedes the host-parallel column of `v1_0/gpu/benchmark.json`. Earlier releases are history, not fresh measurements, and no old result confers verification on new source: `collect_evidence.py` writes a new directory rather than reusing one. `evidence/verification-run.json` is a retained child-command log from an earlier layout, run on an x86-64 host.

`docs/project/BASELINE.json` and `UPSTREAM.json` name the archives this source was built from, each by sha256, with the commit and tag the import started from and the standing policy that no prior result is a new measurement.

Distribution is its own gate. Build the wheel offline, install it outside the source checkout, run the native examples, the independent contracts and both verification modes, and compare every packaged source and runtime byte with the repository. Verify the Git bundle, extract the final ZIP, repeat the unit and example gates. A worktree success is not an installed-package success.

### Failure policy

A failed, timed-out or interrupted command is recorded as such. An unchanged rerun is a new result, not erasure of the failure. A task runner needs a good child exit as well as its JSON result. Process limits are not a sandbox. Whole-compiler correctness, native refinement, model proficiency, GPU performance and C++-breadth completeness each need evidence that these finite gates do not give.

### Inherited teaching fixtures

`training/` holds the inherited lessons. `source/` holds the 0.3 program lessons that 0.4 retained. `semantic/` holds the 0.4 same-contract scalar preferences, obligations and protocol repairs. These are inputs kept for audit, not new results. Historical compiler hashes, solver results, packet IDs and version labels may still refer to their original release, so re-run the generator and checker for a new admission record rather than relabelling an old receipt.

`tools/ai/curriculum.py` regenerates `source/`; `tools/checks/semantic_corpus.py` and `tools/ai/protocol_curriculum.py` regenerate `semantic/`. `tools/checks/curriculum_verify.py` and `tools/checks/mutation_checks.py` check `source/` against independent finite oracles.

No model was trained. Answers and oracles are included here and are not secret held-out data. Some older static contrast lessons change their API or meaning, so do not reward them as contract-preserving repairs.

## Safety and trust

CAIRN is a young compiler, not a security sandbox and not an entirely verified toolchain. Use OS isolation for hostile programs, adapters or compilers.

CPU, address-space and core-dump limits do not protect files, credentials, syscalls or network access. `cairn run` caps virtual address space at 1024 MiB by default (64..65536 MiB). The cap is lifted for device programs, because CUDA reserves far more address space than it uses, and it never constrains a shared library loaded by a foreign host. Host OOM, stack exhaustion, allocation failure and process termination remain possible.

### What safe code promises, and on what evidence

Outside `unsafe` and `extern`, an accepted program cannot use a moved owner, leak or double-consume a linear value, alias a mutable borrow, keep a borrow past its call, race in a parallel region, touch a place a live task was lent, or index memory of the wrong placement. Arithmetic, bounds, tags, extents and array parts are guarded, and a failed guard aborts the process, on the device as on the host.

These rules are implemented in `checking.py` and exercised by rejection tests and by native runs under Address, Leak, UndefinedBehavior and Thread sanitizers and device death tests. They are not mechanized: a checker bug is a soundness bug, and generic code is checked per instance. Aborts do not run cleanup.

### The foreign boundary

`extern` declarations, `mmio_read`, `mmio_write` and `asm` are usable only inside `unsafe { }`. Their effects (`ffi:symbol`, `io`, `mmio`, `asm`) propagate to every transitive caller, and `unsafe` blocks are counted per function in the receipt, so an audit starts from the receipt rather than from a text search:

```json
"process": { "effects": ["ffi:getpid", "io"], "syntactic_check_sites": { "unsafe_blocks": 1 } }
```

An extern's signature and effects are trusted as written. Callers of exported functions must supply live, initialized, correctly typed storage for every borrow, and a valid tag and active payload for every sum; numerical entry guards cannot prove provenance or exclude concurrent foreign access.

### Builds

Manifests are data and accept local listed paths only: no hooks, commands, downloads, arbitrary flags, traversal or symlinks. One checker reads every manifest of a build, so a dependency's `cairn.toml` is refused for the same unknown table, unknown option, bad name or unknown kind, architecture or target as the root's. Every path in any of them, whether a source, a test contract or a dependency directory, has one canonical spelling inside its own root: no `.`, `..` or backslash segment.

Each rule below is enforced in `src/cairn/projects/` and pinned in `tests/projects/test_projects.py`.

| Rule | Enforced by | Test that pins it |
|---|---|---|
| Manifests are data, and a dependency's is read as strictly as the root's | `project.read_manifest` | `test_bad_manifests_fail_closed` |
| No path segment is a symbolic link | `project.contained_file` | `test_symlink_source_rejected` |
| A dependency is vendored inside the root, modules only, pinned by hash, nothing fetched | `project.dependencies` | `test_vendored_dependencies_load_first_stay_private_and_are_pinned` |
| A symbolic link is not a dependency directory | `project.dependencies` | `test_a_symbolic_link_is_not_a_dependency` |
| One directory is one project under one name, and one name is one project | `project.dependencies` | `test_one_name_is_one_project_of_the_build` |
| No project declares a `std.*` module, and no project reopens a module another declared | `project.claim` | `test_a_project_of_one_file_declares_no_module_of_the_packaged_library` |
| An executable's `main` comes only from the sources the root project lists | `build.build` | `test_the_entry_point_is_the_root_project_s_own` |
| A cached object is reused only while its bytes still match the digest beside it | `build.intact`, `build.store` | `test_a_cached_object_is_reused_only_while_its_bytes_still_match_its_key` |
| The object cache is `build/objects`; neither it, an object nor a digest may be a link | `build.objects` | `test_the_object_cache_stays_inside_the_project_build_directory` |
| A unit whose compile times out or is killed leaves no object and still writes `cairn.build/1` | `build.objects` | `test_a_unit_that_times_out_or_is_killed_records_the_same_build_as_one_unit_does` |

Only project modules, the modules of dependencies vendored inside the project root, and the packaged `std.*` can be imported. A file with no `module` header would declare into the previous file's module, which is why reopening is refused where a dependency's module is still open. A dependency is source you chose to vendor: it is checked like your own code, its `unsafe` blocks and `extern` declarations show in the effect rows of whatever calls them, and nothing else vouches for it. Since `main` is searched only in the root project's own sources, a dependency neither supplies the entry point nor denies the project its own.

Native builds use fresh directories and never reuse a stale binary. `--incremental` reuses an object only under a key that hashes everything that went into it, and only while the object still matches the sha256 written beside it, so a truncated, half-written or substituted file is compiled again instead of linked. That digest is an integrity check, not an authorization one. It catches an interrupted build, a corrupted or shared cache, and a stale file left under a valid name. It stops nobody who can write into the project's own `build/`: whoever can do that can rewrite the digest as easily as the object, and usually the sources too.

Compiler paths and output locations are trusted host choices, and `toolchain.py` is the only place a native flag is chosen. A freestanding image has no MMU, caches or vector table set up: a hardware fault hangs rather than reports, and the language's own guards are what stop bad indices there.

### Agents

A model cannot change a pinned reference, domain, signature, effect ceiling, visible dependency set or source outside its authorized range through an edit response, and the whole linked module is rechecked after every edit. Proof and feedback channels fail closed for missing tools or unsupported fragments. The formatter fails closed: same tokens and comments, or no change. The language server analyses the open buffer in-process and executes nothing.

### Publication policy

Development is local. Publication is opt-in, private-only and non-force. The preflight scans every reachable committed blob for finite credential patterns and excluded binary and secret paths, and is not an exhaustive detector. The GitHub workflow is manual-only, private-repository guarded and read-only. No license or public host has been selected. [Publication](#publication) below has the sequence and its failure modes. Report sensitive issues only through the owner's private channel.

## The AI edit protocol

How an agent edits CAIRN: what it is given, what it may propose, and what decides whether the result is kept. [Safety and trust](#safety-and-trust) states what an edit response can never move. An edit request itself is the `cairn.edit/1` object of [docs/cards/EDIT_SCHEMA.json](cards/EDIT_SCHEMA.json): a session digest, a replacement of at most 64000 UTF-8 bytes, and a kind, which is `body` for a whole function or `expr` for one site, named by its own digest.

### The rule cards

`src/cairn/agent/teaching.py` holds the rule cards, seventeen of them: base, integers, views, compact, calls, floats, records, generators, memory, sums, generics, owners, effects, parallel, tasks, closures and modules. Each is a dozen lines of what that part of the language admits and refuses, and `select_cards` picks them from the lexical tokens of the source at hand, so a packet carries only the cards its program touches. `cairn inspect --symbol f` prints the whole packet: source, scope, effects and those cards.

The complete set is what `tools/ai/ai_pilot.py prepare` writes as a subject's only documentation and what `tools/ai/measure_context.py` counts. Shorter wording never grants edit authority, and supplying a card implies no model training or proficiency result.

### Using the agent's existing skills without trusting its guesses

The design target is successful, independently checked changes per total budget, not the shortest possible string. The model chooses an algorithm or expression; the compiler reconstructs routine structure, rejects unauthorized changes, and checks what it can. Familiar syntax and Python tooling are hypotheses about transfer from pretraining, not evidence that a particular model is proficient. No model weights have been changed here, and the only fresh-model trial that has run is the preregistered pilot under `evidence/v1_1/ai_pilot/`.

#### The host chooses the unit of reasoning

Use an ordinary full-function edit when the algorithm's structure must change. Use one or more named expression slots when the decision really is local. Slots must be nonoverlapping and resolve against the exact original source. They are not a mechanism for concealing missing dependencies. The packet still includes the same bidirectional call-graph source component and selected language cards as a whole-function edit, plus expected types and lexical bindings at each slot. An attached scalar contract discloses its full reference source, input domain and trap policy. These are read-only task facts. The model must not guess a hidden meaning or substitute an easier reference.

A trusted host can prepare the following sketch in Python:

```python
from cairn.agent.sketches import Sketch, ScalarContract

source = "fn average(x:u64,y:u64)->u64 { return (x+y)/2; }"
reference = """fn average(x:u64,y:u64)->u64 {
  return (x/2)+(y/2)+((x%2+y%2)/2);
}"""
sketch = Sketch(
    source,
    "average",
    task={"task": "Return floor((x+y)/2) for all u64 inputs, without traps."},
    semantic=ScalarContract(reference, "average"),
).hole("value", "(x+y)/2")
packet = sketch.packet()
# Send packet to a model using the caller's chosen adapter.
# Here this explicit string is an authored example, not a model response.
reply = '{"value":"(x & y) + shr(x ^ y, 1)"}'
candidate = sketch.fill_json(reply)
result = sketch.check_semantics(candidate)
assert result["status"] == "smt-equivalent"
```

Host scripts import the installed package, so run them from the repository root after `pip install -e '.[dev]'`. The model normally returns data, not Python to execute. Python is a familiar orchestration API for a trusted author; it is not a secure sandbox for arbitrary model code. `fill_json` rejects duplicates, nonfinite JSON, extra fields, non-string choices, syntax injection, stale bindings and choices outside the compiler's rules.

A data-only response need not echo long hashes. Identity, source, slot map and reference remain bound in the host. That is a local-session transport rule, not a signed authorization protocol. A saved JSON map is not a standalone approved patch. Distributed or multi-tenant integrations require authenticated session routing and OS-level isolation, neither of which is supplied here.

#### Behavior feedback is stronger than compiler feedback

`x+y` and `add_wrap(x,y)` may have identical types while differing at overflow. For the implemented scalar fragment, the checker asks whether any valid input makes the candidate return something different from the fixed reference, or abort when the reference returns. A distinguishing input becomes feedback only after replay in the independent Python arithmetic interpreter.

This avoids treating compiler acceptance as correctness. It also avoids asking the model to invent a plausible test result. The 40 negative curriculum cases were additionally replayed through instrumented Clang and GCC builds. General interactive checks do not automatically perform native replay.

The reference is an executable definition supplied by the host. It may itself misstate the human's intent. The checker cannot solve that authority problem. The default rejects references that can trap on admitted inputs, preconditions that trap, and empty input domains. A deliberate partial contract can opt into return-versus-abort equivalence. The model cannot change that policy.

An owner that moves, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary remain on the finite-test path. Unsupported symbolic work returns unknown. Do not treat that result as a proof or silently replace the task by the supported subset.

#### Counterexamples become persistent regression facts

`solve_finite` searches a caller-supplied finite list or product of expression choices. It typechecks every candidate. Previously found counterexamples can reject later candidates without another solver call. They can never accept a candidate: acceptance always uses a fresh all-width semantic check.

The included average demo examines four authored candidates. With cached witnesses it makes fewer semantic-checker calls in the recorded run, but the search is deterministic test plumbing, not an AI success rate. A solver's choice of witness can change the savings. No optimal candidate-ranking rule is claimed.

```sh
python3 tools/ai/sketch_demo.py
python3 tools/checks/semantic_check.py examples/sketch/reference.cairn \
  examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/release/build.py examples/sketch/after.cairn
```

The semantic status is `smt-equivalent`, not `Lean-verified`, `native-verified`, or `fastest`. Reference and candidate hashes, model profile, domain, query hashes and Z3 version accompany the receipt. Saved SMT-LIB obligations allow independent re-execution; no proof certificate is reconstructed in Lean. A nontrivial `--assume` is a caller precondition, not a guard emitted by the native builder. The builder does not use this receipt to remove checks, and the equivalence result makes no claim outside that domain. The average example uses `true`.

#### Training material must match the real tool protocol

The semantic curriculum has 40 same-signature, same-domain tasks in 14 algorithm families. Each has an equivalent implementation and a type-correct inequivalent one. Twenty-eight preference pairs are training material; twelve prompts belong to four proposed evaluation families. Width variants do not count as distinct algorithms. All audit answers ship, so these are not secret tests.

Twenty-nine applicable tasks also have executed named-choice repair transcripts. Twenty are training targets and nine are evaluation prompts. The SFT export puts the failed proposal and checker feedback in user context and includes only the correct JSON reply as an assistant target. It does not accidentally teach the wrong assistant turn. These are authored, executed fixtures, not transcripts of an actual LLM. Raw preference and protocol exports are provider-neutral records, not a promise that any provider's training endpoint accepts the metadata as-is.

Keep API-changing language lessons from 0.3 separate from these repair targets. Rewarding an agent for making a signature weaker or choosing an empty domain would train it to defeat the task rather than solve it. Solver timeouts, failed translations and tool errors must never become positive labels.

#### What to evaluate next

Freeze models, task semantics, inference budget, tool budget and native workload. Compare full-source edits, 0.3 expression packets, named choices, and named choices plus scalar feedback. Give C++ and Rust comparable scoped-edit and solver tools; otherwise a workflow gain will be misreported as a language gain. Use independently authored algorithm/application families, not these shipped answers.

Report first-attempt and bounded-repair functional success, unauthorized changes, regressions in callers, total model-token usage including failed attempts and history, solver/compile time, and measured native performance. Separately test models before and after any real fine-tuning. Byte-token transport measurements and synthetic repair logs are not replacements for that study.

### Closed generator contracts

The small dependency packet for editing existing family and wire examples. It is not the full language card or a proof certificate. `compiler/expansion.py` expands both, `std/wire.cairn` is the wire recipe and `verify/linear_certificates.py` checks the collector; changing any of them needs a larger audit context.

family/1. A static function with one natural parameter K and family prefix=base[a..b] produces b-a independent monomorphic functions prefix_a through prefix_(b-1). Each uses its literal K. Empty, negative, reversed, oversize, colliding, or unbound expansions are rejected. All emitted functions are checked. There is no dynamic dispatch, allocation, or evaluation of source strings. Increasing the range increases object size and compilation work. Expansion is capped at 1024 per family, 2048 functions total and 200000 AST/check visits.

wire/1. A record of fixed unsigned fields derives encode, decode, and byte-length functions. Field order is declaration order, each field little endian, no wire padding. Native record layout is not the wire layout. Encoded size is the sum of field widths divided by eight. Every field bit is retained. The buffer caller must supply the exact live extent; guards cannot recover a forged pointer's provenance. No tags, checksums, semantic ranges, alignment padding, implicit versions, or allocation are added.

bounded-collector/1. On immutable extent n with output capacity n, visit indices 0..n in order. Evaluate a read-only predicate once per input. Evaluate a read-only projection only for selected inputs and append it once. Never read output through the predicate or projection. The private cursor k starts at zero and satisfies k<=i at the start of iteration i. Thus every output store has k<n and final k<=n. Nonselected tail bytes/elements are unchanged. Inputs must not alias output; entry checks enforce interval disjointness under the FFI precondition. No worker threads, buffering, reordering, vectorization guarantee, or hidden heap allocation are part of this contract.

### The named-choice card

What the model is told when the host asks for named expressions rather than a whole function. Read the host packet: task, source, named slots, expected types, local bindings, allowed effects and selected language cards. Return only one JSON object mapping EVERY slot name to a CAIRN expression string. No Markdown, extra keys or duplicate keys. Example reply: {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses, then rechecks the complete module. Do not rewrite function signatures, task, reference, permissions, preconditions, compiler settings or tests. Missing context is not permission to invent a dependency. Slot names are descriptive identifiers, not code or persistent IDs. The host binds your reply; do not invent hashes.

Use familiar expressions, but preserve CAIRN semantics: fixed-width integers; ordinary +,-,* trap on overflow; explicit unsigned add_wrap/sub_wrap/mul_wrap; checked casts and shift counts; sequential loops; no implicit allocation. A type error requests a local type repair, not changing the public API. A semantic counterexample gives an input where your proposal returns a wrong value or traps. Fix the algorithm on the original domain. Do not patch only that example.

Typed means only the static checks passed. The optional scalar checker compares with a fixed host reference for all declared-width inputs under a total, nonempty host precondition. smt-equivalent trusts the translator and Z3; it is not a Lean proof or native-code proof. It models integers, bools, floats, records, sums, branches, acyclic calls, array views and their parts, fixed and function-local storage, `compact`, host `reduce` and loops it can unroll, but not an owner that moves, recursion, concurrency or GPU execution. Unknown/timeout is never accepted. Tests and benchmarks are separate stages. No model has been trained or evaluated by this release merely because this card is supplied.

## Publication

`tools/release/publish_private.py` creates one new private repository under a personal account and pushes `main` to it. It is the only publication path, and no test or build performs a real publication.

```sh
python tools/release/publish_private.py SamMausberg/cairn
python tools/release/publish_private.py SamMausberg/cairn --execute
```

The first command is a local-only dry run. The second needs an already authenticated official `gh` CLI with permission to create a private repository under that same personal account. The script never asks for a pasted token, installs an authentication helper, or changes global credentials. Authenticate through your normal trusted GitHub CLI flow before running it.

### Before it will run

The source tree must be clean, on `main`, with no configured remote. A clone of the supplied Git bundle may carry an origin pointing at that bundle; remove that local-only remote first, and never remove a real GitHub remote to bypass its protection. The scan reads all reachable committed history, not the latest files alone. A source ZIP has no Git history, so publish from the repository ZIP or the bundle.

### The sequence

Local audit, identity check, private creation, identity and privacy read-back, exact-commit non-force push, final privacy and commit read-back, then local `origin` registration. Git hooks are disabled for the push, and credential-helper selection is command-scoped. Creation is the collision check: if the name already exists the script stops without modifying that repository. The repository is never intentionally public, even temporarily. No force, delete, reuse-existing, visibility-change or public-fallback mode is implemented.

### What can still go wrong

A failure after creation can leave an empty private repository. The script does not delete it and does not weaken a check to recover. A failure after the push may leave private code uploaded; inspect the reported account and repository through GitHub before trying anything else. Privacy checks cannot prevent an administrator changing visibility concurrently or later, and there is no absolute guarantee against a compromised client, service or local Git configuration.

The automated tests use fakes for every remote interaction. A passing fake test shows that the orchestration takes its expected branches. It does not show that your future account permissions or a GitHub request will succeed. No live publication test ran here.

### What has been published

Once, after the 1.0 evidence was collected, `main` and the release tags were pushed at the owner's request to a new private GitHub repository whose privacy was verified before and after the upload. No remote workflow has run: the GitHub workflow is manual-only, private-repository guarded and read-only. No license has been selected and nothing is published for reuse.

## Remaining gates

CAIRN 1.0 implements the breadth that 0.2 proposed, each feature with an application and with rejection and behaviour tests. What is still missing is stated here as gates rather than plans, and [capabilities.json](project/capabilities.json) carries the same list as data.

### Language

- Recipes are library code over record schemas, naturals and names of functions (`std.wire` replaced the closed wire generator byte for byte). They do not take arbitrary expression fragments and no checked theorem is replayed per instance, so a recipe's guarantee is that its output is checked like any other code.
- The collector remains a closed, certified form: a user cannot write a loop that carries its own certificates.
- Asynchronous I/O is a task over blocking I/O. A completion queue, cancellation, device-loss recovery and multi-device collectives are absent. Device regions and transfers do queue as linear stream tickets ordered with `after`.
- Lending one field of a record to a task leases the whole record, so two tasks cannot take two fields of one record. The rule is safe and stricter than it needs to be. Extent identity does not travel through a field either: the checker cannot learn that `len(c.price)` equals `c.rows`, so such calls pass a part and pay its guard.
- Separate compilation is opt-in: `--incremental` keeps one object per module, reused by content hash. Device programs and freestanding images are still one translation unit, and no cross-module inlining (LTO) is attempted in that mode.
- Bounds cover traits, kinds (`copy`, `affine`) and closed scalar classes, every template of `std` is certified once against its bounds, and `cairn check --generics` holds a project to the same on request, certifying a natural parameter through the instances its families name. Nothing requires it: a program's unbounded templates are still accepted per instance.

### Proof

- The collector certificates and loop model are Lean-checked, and so is a core ownership and lease calculus over locals, record field paths, whole owners, headers, elements, array parts with visible bounds and `parallel` regions. An accepted program there has no use-after-move, use-after-free, double free, leaked ticket, aliased call argument or data race, never gets stuck, and frees every cell exactly once, under any interleaving and every valuation of those bounds and of the lane count.
- That calculus is written by hand beside `checking.py`, not extracted from it. Relating the two by something stronger than review would close this.
- It assumes of the emitter that a part's `lo <= hi` guard runs before the task that borrows it starts, and that a region completes before the next statement. Both are tested, not proved.
- Single elements, parts of parts, closures, `lane:f` callbacks, device placement, `reduce`/`compact` and queued device work are outside the calculus.
- The emitter's correspondence to the loop model and native refinement are unproved. Proving that the emitted loop refines the model, or generating it from the model, would close the first.
- The SMT model covers records, tag-only enums and payload sums with `match` and `try`, IEEE `f32`/`f64`, fixed local storage, array views with their parts, `rw` borrows, function-local heap scratch, `compact`, host `reduce`, and loops it can unroll within a sixteen-iteration budget. It still rejects an owner that moves, recursion, tasks, lanes, device placement, closures, `dyn` and the foreign boundary, and reports as unknown rather than equal a trip count it cannot bound (which a pass over a symbolic extent is, until a precondition bounds it), a tag inside a view, two views of one array in one call, and an observed NaN.

[verification](#verification) states what each model contains, what it assumes and what it leaves out.

### Performance

- `evidence/v1_0/gpu/benchmark.json` is one machine and three kernels; device wins depend on transfer cost.
- Host regions no longer create threads per statement: the first region of a process builds a lane pool and the rest reuse it. That moved the size at which a region beats the sequential loop from about ten million cheap elements to about a hundred thousand, and from three million to thirty thousand for a body costing about one and a half nanoseconds an element (`evidence/v1_2/host_regions`, one machine, both compilers). A region below sixteen thousand elements is still the loop it replaces, by design and by measurement.
- What remains unmeasured is the shape of the win: no tuned C++, OpenMP or TBB baseline has been built with equal flags and equal safety boundaries, nothing has been run on x86-64 or across more than one memory domain, and the two lane bodies measured both touch one element and nothing else. No claim is made against tuned C++ or CUDA baselines. A preregistered suite with equal safety boundaries would close this.

### AI evidence

- The edit protocol, packets and rule cards cover the whole language. One preregistered pilot has run (`evidence/v1_1/ai_pilot`): nine fresh subjects of one model family, given only the rule cards and compiler diagnostics, solved nine of nine small tasks against hidden tests, eight on the first compile, including a recipe for a feature designed that day. It shows the cards suffice for that; it shows no advantage over anything.
- No experiment with equal budgets against C++ and Rust tooling, other model families or larger programs has run. Token counts are still byte counts. No corpus or deterministic search substitutes for that experiment.

### Packaging

- A project can vendor other projects inside its root (`[dependencies]`), pinned by hash in every receipt. There is no registry, no version resolution and no fetching, by design.

# Language reference

Every example here is compiled by the test suite, and every refused one must fail with the code shown. [verification.md](verification.md) says which parts are proved.

Three rules explain most of the language. Costs are visible: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function has an inferred effect row. Borrows are second class: a borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references. Short forms are contracts: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary code with their obligations attached.

This file covers values, control flow, records, sums, constants, tests and printing. [memory.md](memory.md) covers views, owners and effects, [abstractions.md](abstractions.md) generics, traits, closures and modules, [concurrency.md](concurrency.md) tasks, lanes and devices, and [numerics.md](numerics.md) storage floats and gradients.

## Values and arithmetic

Identifiers are ASCII, and comments (`//` to the end of the line) are UTF-8. Parameters and `let` locals are immutable, `let mut` is mutable, and no name may shadow another (`E-SHADOW`). `reg x = 0;` and `each i in n { }` are older spellings of `let mut x = 0;` and `for i in 0..n { }`.

The scalars are `bool`, the unsigned `u8 u16 u32 u64 usize` (`usize` is 64-bit), the signed `i8 i16 i32 i64`, and `f32 f64`. Literals are decimal and `0x` integers, floats with a point or an exponent, `true` and `false`, `'c'` (one byte, compatible with `u8`) and `"text"`, a static `ro<u8>[n]` view with the escapes `\n \t \r \0 \\ \" \' \xNN`. A literal takes the type expected of it, `u64` or `f64` when nothing expects one.

`+ - *` abort on overflow in every build. `/` and `%` reject a zero divisor and the signed minimum over `-1`, and a signed remainder truncates toward zero. `add_wrap sub_wrap mul_wrap` are modular, `shl_wrap` and `shr` take a `usize` count below the width, `& | ^ ~` are unsigned, and `min` and `max` are integer-only.

`x += e` is `x = x + e`, checked the same way, and so are `-= *= /= %= &= |= ^=`. The place is evaluated once, so `xs[i] += 1` finds its element and pays its bounds guard once. There is no wrapping compound form: write `x = add_wrap(x, e)`.

```cairn
fn payload(total:u32, header:u32) -> u32 = total - header;   // aborts if header > total
fn share(part:u32, whole:u32) -> f64 = f64(part) / f64(whole);

fn main() -> i32 {
  let total:u32 = 1500;
  if payload(total, 20) != 1480 { return 1; }
  let mut seq:u32 = 4294967290;
  seq = add_wrap(seq, 10);                                   // modular, and it says so
  seq += 1;                                                  // checked, as seq = seq + 1 is
  if seq != 5 { return 2; }
  let drift:i32 = -7;
  if drift / 2 != -3 || drift % 2 != -1 { return 3; }        // toward zero
  if shr(0xff00, 8) != 0xff || (0xf0 & 0x3c) != 0x30 { return 4; }
  let ratio = share(payload(total, 20), total);
  if u32(ratio * 100.0) != 98 || u8(255) != 255 { return 5; }  // truncates toward zero
  return 0;
}
```

Conversions are explicit type calls. An integer target is range checked: narrowing aborts outside the target, and float to integer aborts on NaN or on a value the target cannot hold.

```cairn rejects E-TYPE-MISMATCH
fn payload(total:u32, header:u32) -> u32 = total - header;
fn main() -> i32 { let header:u64 = 20; return i32(payload(1500, header)); }
```

```text
Expected u32, got u64.
```

Floats compile with `-ffp-contract=off -fno-fast-math` (and `--fmad=false` on the device): no contraction and no reassociation. A failed guard aborts the process. It does not unwind, and it rolls nothing back.

Six builtins cover what IEEE 754 defines exactly, so every compiler, the host and a device lane give the same bits: `sqrt` (correctly rounded), `floor`, `ceil` and `trunc` for `f32` and `f64`; `abs` for a float or a signed integer, trapping on the signed minimum; and `to_bits`, a float's IEEE pattern. Any other argument is `E-MATH-TYPE`. `exp`, `log`, `sin` and the rest depend on the math library's last bit, so they are not builtins: [std.math](library.md#stdmath) calls the C library, and its row says so. A program's own function with one of these names is the one a call reaches.

```cairn
fn hypot(x:f64, y:f64) -> f64 = sqrt(x * x + y * y);   // no trap: its row is empty

fn main() -> i32 {
  if hypot(3.0, 4.0) != 5.0 || floor(-2.5) != -3.0 || trunc(-2.5) != -2.0 { return 1; }
  let root:f32 = sqrt(2.0);
  if to_bits(root) != 0x3fb504f3 { return 2; }          // the nearest f32 to the square root of 2
  let drift:i32 = -7;
  if abs(drift) != 7 { return 3; }
  return 0;
}
```

```cairn rejects E-MATH-TYPE
fn f(x:u64) -> u64 = sqrt(x);
```

```text
sqrt takes f32 or f64, not u64.
```

The compiler leaves a guard out where the checker has shown it cannot fail. Inside `for i in 0..n`, `parallel i in n` or a `reduce` over `n`, `x[i]` into a view of extent `n` needs no bounds check, and neither does `i + 1`. The same holds for `x[k]` after `if k >= n { return 0; }`, for `x[i - 1]` under `if i > 0`, for a bin `usize(v & 255)` into 256 counters, and for `x[k]` on the right of `k < n && x[k] > 3`. The facts come from loop and lane binders, immutable `let` bindings, conditions, early exits and a collector's predicate, over `usize` values that cannot change, never from `let mut` locals. A part `x[lo..hi]` loses its guard once `lo <= hi <= len(x)` is established.

Removing a guard never changes what a program does: the row still says `trap`, and the receipt counts the site under `discharged_check_sites`. Every removed guard carries the facts that justify it, and an independent audit checks each one before a line is emitted. `--keep-guards` on `emit`, `build` and `run` writes every guard. [verification.md](verification.md#the-guard-elision-rule) says what of this is proved.

## Functions and control flow

A block body needs explicit `return` statements, and every path of a non-void function must return one (`E-RETURN`). There is no block-tail return. An expression body, `fn payload(total:u32, header:u32) -> u32 = total - header;`, is that one return.

The control forms are `if / else if / else`, `while`, `for i in lo..hi`, `for x in xs`, `break`, `continue` and nested `{ }` blocks. A `for` evaluates `lo` and then `hi` once, and an empty or reversed range does nothing. `&&` and `||` short-circuit. No loop implies parallelism.

`for x in xs { }` walks the elements of a view, a `Buf`, an `Array` or a fixed array, and `for i, x in xs { }` names the position too. It means `for i in 0..len(xs) { let x = xs[i]; }` and pays the guards that loop pays. Each element is copied, so the elements must be copyable (`E-ELEMENT-LOOP`); an owner in an array is taken, swapped or lent through `xs[i]`.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for b in bytes {                                  // b is bytes[i] for i in 0..len(bytes)
    if b == 0 { continue; }                         // padding carries no checksum
    sum = add_wrap(sum, u32(b));
  }
  return sum;
}

fn main() -> i32 {
  let frame = "GET /\0\0";
  let mut v = Buf[u8](4);
  for i, x in v { v[i] = u8(i) + 1; }               // v is a mutable owner: its reads keep their guard
  if checksum(len(frame), frame) != 71 + 69 + 84 + 32 + 47 || checksum(len(v), v) != 10 { return 1; }
  return 0;
}
```

```cairn rejects E-RETURN
fn kind(first:u8) -> u8 { if first == 71 { return 1; } }
```

```text
Not all paths of kind return.
```

```cairn rejects E-ELEMENT-LOOP
fn main() -> i32 { let rows = Buf[Buf[u8]](2); for row in rows { } return 0; }
```

```text
for row in rows copies each element, and Buf[u8] is not copyable: write for i in 0..len(rows) and take, swap or lend rows[i].
```

A call can be a statement: `count(log);` drops what `count` returns, and `try check(v);` drops the success payload. A dropped owner is released where the statement ends, and a dropped linear value is `E-LINEAR-LEAK`. An outcome is never dropped silently: a sum `try` accepts, such as `Result` or `Option`, is handled with `try` or `match`, or dropped on purpose with `let _ = check(v);`, and otherwise the call is `E-DISCARD`. A call that only computes, such as `min(a, b);`, is `E-DISCARD` too.

```cairn
import std.core (Result);

fn count(log:rw<u64>) -> u64 { log += 1; return log; }
fn check(v:u64) -> Result[u64, u8] { if v > 9 { return Err(1); } return Ok(v); }

fn step(v:u64, log:rw<u64>) -> Result[u64, u8] {
  count(log);                                      // the count it returns is dropped
  try check(v);                                    // or return the failure from here
  let _ = check(v + 100);                          // a failure let go on purpose
  return Ok(v);
}
```

```cairn rejects E-DISCARD
import std.core (Result);
fn check(v:u64) -> Result[u64, u8] { if v > 9 { return Err(1); } return Ok(v); }
fn step(v:u64) { check(v); }
```

```text
This call returns std.core.Result[u64, u8], an outcome to handle: use try or match, or drop it on purpose with let _ = ...
```

## Records and sums

`struct` is a record, and `enum` a tagged sum with zero or one payload per variant. Fields and payloads may be any value type, owners included, but never a borrow, `void`, or their own type by value (`E-RECORD-TYPE`). A record is copyable when all its fields are, and a tag-only enum may be compared with `==`.

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

```cairn rejects E-RECORD-TYPE
struct Frame { head:u8; next:Frame; }
```

```text
Record fields cannot contain their own type by value; reach it through a Buf.
```

A `Buf` field may name an earlier `usize` field of the same record as its extent (`E-EXTENT` otherwise). Then `len(c.price)` and `c.rows` are one identity, and the column goes to a call whole, with no part and no guard.

```cairn
struct Chart { rows:usize; price:Buf[f64][rows]; qty:Buf[f64][rows]; }

fn both(n:usize, xs:ro<f64>[n], ys:ro<f64>[n]) -> f64 {
  let mut sum:f64 = 0.0;
  for i in 0..n { sum += xs[i] * ys[i]; }
  return sum;
}

fn main() -> i32 {
  let n:usize = 4;
  let mut c = Chart(n, Buf[f64](n), Buf[f64](n));
  c.price[0] = 2.0;
  c.qty[0] = 3.0;
  if both(c.rows, c.price, c.qty) != 6.0 { return 1; }     // no part, no guard
  if both(len(c.price), c.price, c.qty) != 6.0 { return 2; }
  return 0;
}
```

Nothing checks the relation at run time, so it is established once and never broken. A constructor builds the column inline as `Buf[T](n)` from the same `n` it gives the extent field, and neither half is ever assigned, taken, swapped or lent `rw` on its own (`E-EXTENT-FIELD`). Moving the whole record keeps both halves together.

```cairn rejects E-EXTENT-FIELD
struct Chart { rows:usize; price:Buf[f64][rows]; }
fn main() -> i32 { let b = Buf[f64](4); let c = Chart(4, b); return 0; }
```

```text
price holds rows elements: build it here as Buf[T](n) on the same n that rows is given.
```

```cairn rejects E-EXTENT-FIELD
struct Chart { rows:usize; price:Buf[f64][rows]; }
fn main() -> i32 { let mut c = Chart(4, Buf[f64](4)); c.rows = 0; return 0; }
```

```text
rows takes part in the declared extent of Chart.price; assign, take or swap the whole record.
```

A nested record carries the identity too (`box.chart.price` has the extent `box.chart.rows`), but an element of an array of records does not: `cs[0].price` is passed as a part and pays its guard.

## match and try

`match` evaluates its subject once and needs exactly one arm per variant, with no wildcard (`E-MATCH-COVERAGE`). A payload arm binds one fresh immutable value, and matching an owner consumes it. An arm that is one `return`, `break`, `continue`, assignment or call may leave out its braces, and anything longer is a block (`E-PARSE`).

```cairn
struct Header { kind:u8; size:u32; }
enum Parsed { Ok(Header); Short(usize); }

fn parse(n:usize, bytes:ro<u8>[n]) -> Parsed {
  if n < 5 { return Short(n); }
  return Ok(Header(bytes[0], u32(n) - 5));
}

fn main() -> i32 {
  match parse(len("\x07abcdefg"), "\x07abcdefg") {
    Ok(head) => { if head.kind != 7 || head.size != 3 { return 1; } }
    Short(_) => return 2;
  }
  match parse(2, "hi") {
    Ok(_) => return 3;
    Short(got) => { if got != 2 { return 4; } }
  }
  return 0;
}
```

```cairn rejects E-MATCH-COVERAGE
enum Op { Read; Write; Flush; }
fn cost(op:Op) -> u64 { match op { Read => return 1; Write => return 2; } }
```

```text
Every variant must have exactly one arm; missing Op.Flush.
```

```cairn rejects E-PARSE
enum Op { Read; Write; }
fn cost(op:Op) -> u64 { match op { Read => let c = 1; Write => return 2; } return 0; }
```

```text
An arm without braces is one return, break, continue, assignment or call; write a block for anything else.
```

`_` binds nothing. `Err(_) => return 1;` drops the payload: an owner is released where the arm ends, and a linear one is refused (`E-LINEAR-LEAK`), because only a consumer may end it. Nothing can read `_` (`E-UNBOUND`), so it may repeat, as in `for _ in 0..3`.

```cairn
import std.core (Option);

fn depth(a:Option[u64], b:Option[Buf[u8]]) -> u64 {
  let mut n:u64 = 0;
  match a {
    Some(_) => { match b { Some(_) => n = 2; None => n = 1; } }   // the Buf is released here
    None => return 0;
  }
  for _ in 0..3 { n += 1; }
  return n;
}

fn main() -> i32 {
  if depth(Some(7), Some(Buf[u8](4))) != 5 || depth(None, None) != 0 { return 1; }
  return 0;
}
```

```cairn rejects E-LINEAR-LEAK
import std.core (Option);
linear struct Lease { id:u64; }
fn gone(l:Option[Lease]) -> u64 { match l { Some(_) => return 1; None => return 0; } }
```

```text
Some(_) would drop a linear Lease: bind it and consume it.
```

A variant may leave out its type wherever the context names the sum: the return type, an annotated `let`, an assignment, a parameter, a field, another variant's payload, or the other side of `==`. The bare form compiles exactly as the qualified one, which stays legal everywhere.

```cairn
import std.core (Option);
enum Op { Read; Write; }

fn half(x:u64) -> Option[u64] {
  if x % 2 != 0 { return None; }
  return Some(x / 2);
}

fn main() -> i32 {
  let mut op = Op.Read;
  op = Write;                                     // the target is an Op
  match half(6) {
    Some(v) => { if v != 3 || op != Write { return 1; } }
    None => return 2;
  }
  return 0;
}
```

A bare name is a variant only when nothing else of that name is visible; otherwise it is `E-VARIANT-AMBIGUOUS`. Where no sum is expected, `let x = None;` is `E-UNBOUND`, and a private sum keeps its variants private (`E-PRIVATE`).

```cairn rejects E-VARIANT-AMBIGUOUS
struct Line { width:u64; }
enum Shape { Dot; Line(Line); }
fn thin() -> Shape = Line(Line(1));
```

```text
Line is both Shape.Line and a type; write Shape.Line.
```

`try e` takes a two-variant sum, success first and failure second. It yields the success payload, or returns the failure from the enclosing function, whose return type must be a two-variant sum with the same failure payload (`E-TRY`); the two sums may be different types. It is the only propagation form.

```cairn
struct Header { kind:u8; size:u32; }
enum Read { Ok(Header); Err(u8); }
enum Sized { Ok(u32); Err(u8); }

fn head(n:usize, bytes:ro<u8>[n]) -> Read {
  if n < 5 { return Err(1); }
  return Ok(Header(bytes[0], u32(n) - 5));
}
fn body_size(n:usize, bytes:ro<u8>[n]) -> Sized {
  let h = try head(n, bytes);                     // or return Sized.Err(1) from here
  return Ok(h.size);
}

fn main() -> i32 {
  match body_size(7, "\x07abcdef") { Ok(size) => { if size != 2 { return 1; } } Err(_) => return 2; }
  match body_size(2, "hi") { Ok(_) => return 3; Err(e) => { if e != 1 { return 4; } } }
  return 0;
}
```

A `try` may not sit beside an operand that already owns something, because leaving from there would abandon it. Bind the `try` first.

```cairn rejects E-EFFECT-ORDER
struct Frame { body:Buf[u8]; size:usize; }
enum Sized { Ok(usize); Err(u8); }
fn size(v:u8) -> Sized { if v == 0 { return Err(1); } return Ok(2); }
fn build(v:u8, body:rw<Buf[u8]>) -> Sized { let f = Frame(take(body), try size(v)); return Ok(f.size); }
```

```text
Bind this try first: leaving from here would abandon an owner that another operand already holds.
```

## Constants

A `const` folds at compile time from literals, other constants in any order, arithmetic, comparisons and conversions, exactly as the machine would compute it. The result must fit its type and be finite, and division by zero or a constant defined through itself is `E-CONST`. A constant natural may stand wherever a natural is written: an extent, `Array[u64, N]`, `scale[N](x)`.

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

## Tests and assert

`assert(cond)` traps when `cond` is false, and `assert(cond, "why")` also says why (`E-ARITY` for anything else). A failed assert prints where it was written, `assertion failed at src/main.cairn:12: why`, and aborts as every failed guard does. `assert_eq(a, b)` also prints both values, `left 1, right 2`, and takes only integers, bools and floats (`E-ASSERT-EQ`; write `assert(a == b)` for anything else).

`test name { ... }` is a body with no parameters or result that only [`cairn test`](tools.md#cairn-test) runs, each in its own process. A module declares each test name once (`E-TEST`). Nothing can call a test, so it may share its name with the function it tests, and `test` is an ordinary name everywhere else.

```cairn
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

test average {
  assert(average(10, 20) == 15);
  assert_eq(average(1, 2), 1, "rounds down");
}

fn main() -> i32 {
  let test = average(2, 4);                  // an ordinary name here
  if test != 3 { return 1; }
  return 0;
}
```

```cairn rejects E-TEST
test average(x:u64) { assert(x > 0); }
```

```text
A test is `test average { ... }`: one name per module, no parameters, no result.
```

```cairn rejects E-ASSERT-EQ
enum Op { Read; Write; }
test ops { assert_eq(Op.Read, Op.Read); }
```

```text
assert_eq prints what it compares, and Op is not an integer, bool or float: write assert(a == b).
```

## print and format

`println("total ", n, ' ', ok)` writes its arguments in turn, then a newline. Integers print in decimal, bools as `true` or `false`, a character literal as its byte, and byte strings, views and `Vec[u8]` as their bytes. A float prints the shortest digits that read back to the same value, laid out as JavaScript lays out a number (`100000`, `1.5`, `0.000001`, `1e-7`). `print` leaves out the newline, `eprint` and `eprintln` write to standard error, and `format(out, ...)` appends the same text to a `Vec[u8]`.

```cairn
import std.vec (Vec);

fn report(n:usize, name:ro<u8>[n], hits:u64, rate:f64) { println(name, ": ", hits, " hits, ", rate, " per second"); }

fn main() -> i32 {
  let mut line = vec.new[u8]();
  format(line, "worker ", 3);
  report(line, 1500, 12.5);
  eprintln("done ", true, ' ', -1);
  return 0;
}
```

Every argument is computed, left to right, before a byte is written, so a failed guard leaves nothing of the line written. A line of up to 4096 bytes goes out in one `write` from a stack buffer, so a pipe keeps it whole. Printing allocates nothing and its row is `io` and `ffi:write`; `format` charges `alloc` and `free` for the growth. A write the kernel refuses ends that print and traps nothing.

A record, a sum or a storage float is `E-PRINT-ARG`, and a target `format` cannot grow is `E-FORMAT-TARGET`. A device lane cannot print (`E-PLACEMENT`), and neither can a host lane (`E-PARALLEL-CALL`).

```cairn rejects E-PRINT-ARG
struct Point { x:u64; y:u64; }
fn show(p:Point) { println(p); }
```

```text
print writes integers, bools, character literals, floats and bytes; format a Point with std.fmt first, or print its fields.
```

## What the language does not have

No inheritance, implicit boxing, lifetime annotations, implicit conversion, operator overloading, shadowing or block-tail return. No wildcard arm, and no propagation form but `try`. No exceptions, unwinding or rollback: a failed guard aborts. No orphan rule, because coherence is judged over the whole program. No cancellation of a task or of queued device work. No loop that implies parallelism. No downloads: dependencies are vendored sources.

# Language reference

This reference describes what the compiler implements. The test suite runs every construct natively, compiles every accepted example and requires every refused one to fail with the code shown. [verification.md](verification.md) says which of it is proved. The 0.2, 0.3 and 0.4 specifications under `docs/history/` are earlier proposals, and a feature they describe exists only if this reference describes it too.

Three rules explain most of the language. Costs are visible: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row. Borrows are second class: a borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references. Short forms are contracts: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

This file covers values, control flow, records, sums, constants and tests. [memory.md](memory.md) covers views, owners, linear values, layout, effects and the foreign boundary, [abstractions.md](abstractions.md) generics, traits, closures, modules and recipes, [concurrency.md](concurrency.md) tasks, lanes and devices, and [numerics.md](numerics.md) storage floats, quantization and derived gradients.

## Values and arithmetic

Identifiers are ASCII, comments (`//` to the end of the line) are UTF-8. Parameters and `let` locals are immutable, `let mut` is mutable, and no name may shadow another (`E-SHADOW`). `reg x = 0;` and `each i in n { }` are older spellings of `let mut x = 0;` and `for i in 0..n { }`.

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

Six builtins cover what IEEE 754 defines exactly. `sqrt(x)` is correctly rounded, and `floor`, `ceil` and `trunc` are exact, for `f32` and `f64`, so every compiler, the host and a device lane give the same bits. `abs(x)` takes a float, exactly, or a signed integer, and traps on the minimum, whose magnitude its type cannot hold. `to_bits(x)` is a float's IEEE pattern as a `u32` or `u64`. Any other argument is `E-MATH-TYPE`. The functions whose last bit depends on the math library, `exp`, `log`, `sin` and the rest, are not builtins: `std.math` calls the C library for them, and its row says `ffi:exp` ([library.md](library.md#stdmath)). A function of the program's own with one of these names is the one a call reaches.

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

The compiler leaves a guard out of the emitted C++ where the checker has shown it cannot fail. Inside `for i in 0..n`, `parallel i in n` or a `reduce` over `n`, `x[i]` into a view of extent `n` needs no bounds check, and neither does `i + 1`. The same holds after `if k >= n { return 0; }` for `x[k]`, for `x[i - 1]` under `if i > 0`, for a bin `usize(v & 255)` into 256 counters, for `data[i]` below `len(data)` when `data` is an immutable owner, and for a row `p[b * 256 + v]` of a buffer of `k * 256` when `b < k` and `v < 256`. The facts come from loop and lane binders, immutable `let` bindings, conditions and early exits, the left side of `&&` or `||` for its right side (so `k < n && x[k] > 3` needs no check) and a collector's predicate for its projection, over `usize` values that cannot change, and nothing about `let mut` locals. A part `x[lo..hi]` loses its guard on the same terms once `lo <= hi <= len(x)` is established and its extent is `hi - lo`, as for `s[n - m..n]` after `if m > n { return 0; }`. An extent a call leaves out, or writes as `hi - lo` over the bounds of a part among its own arguments, costs no subtraction guard: the part's guard traps first when `lo > hi`, before the callee runs. Removing a guard never changes what a program does: the row still says `trap`, and the receipt counts each such site under `discharged_check_sites` beside `syntactic_check_sites`. Every removed guard carries the facts that justify it, and `src/cairn/verify/elision.py` checks each one on its own before a line is emitted. `cairn emit --keep-guards`, and the same flag on `build` and `run`, writes every guard. [verification.md](verification.md#the-guard-elision-rule) says what of this is proved.

Storage floats (`f16`, `bf16`, `f8e4m3`, `f8e5m2`) hold a value in fewer bits and convert by one stated rounding; [numerics.md](numerics.md) has their rules, `quantize` and `derive grad`.

## Functions and control flow

A block body needs explicit `return` statements, and every path of a non-void function must return one (`E-RETURN`). There is no block-tail return. An expression body, `fn payload(total:u32, header:u32) -> u32 = total - header;`, is that one return.

The control forms are `if / else if / else`, `while`, `for i in lo..hi`, `for x in xs`, `break`, `continue` (to the nearest loop, also from a match arm) and nested `{ }` blocks. A `for` evaluates `lo` and then `hi` once, and an empty or reversed range does nothing. `&&` and `||` short-circuit. No loop implies parallelism.

`for x in xs { }` walks the elements of a view, a `Buf`, an `Array` or a `stack` or `buffer` array, and `for i, x in xs { }` names the position too. It is `for i in 0..len(xs) { let x = xs[i]; }`: the length is read once, each element is copied into an immutable `x`, and the guards are the ones that loop has, so a read of an immutable view pays none and a mutable owner's keeps its bounds check. The array is written as a name or a field path, and its elements must be copyable (`E-ELEMENT-LOOP`); an owner in an array is taken, swapped or lent through `xs[i]`.

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

A call is a statement of its own: `count(log);` drops what `count` returns, and `try check(v);` drops the success payload. A dropped owner is released where the statement ends, so a call that makes a `Buf` and drops it charges `alloc` and `free` there, and a dropped linear value is `E-LINEAR-LEAK`. An outcome is never dropped in silence: a two-variant sum that `try` accepts, such as `Result` or `Option`, is handled with `try` or `match`, or let go by name with `let _ = check(v);` (`E-DISCARD`). A call that only computes, such as `min(a, b);` or `u64(x);`, does nothing as a statement and is `E-DISCARD` too. `let _ = e;` binds nothing, so it may repeat, and `_` cannot be read.

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

`struct` is a record, `enum` a tagged sum with zero or one payload per variant. Fields and payloads may be any value type (scalars, records, sums, owners), never a borrow, never `void`, and never their own type by value, directly or through an inline `Array` (`E-RECORD-TYPE`). A record is copyable when all of its fields are. A tag-only enum may be compared with `==`.

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

A `Buf` field may name an earlier `usize` field of the same record as its extent. Then `len(c.price)` and `c.rows` are one identity, and the column goes to a call whole. The name must be an earlier `usize` field (`E-EXTENT`), and the field that declares it must be a `Buf`.

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

Nothing checks the relation at run time, so it is established once and never broken. A constructor writes the carrier inline as `Buf[T](n)` on the same expression the extent field is given; anything else is `E-EXTENT-FIELD`. Neither the extent field nor a carrier is assigned on its own, moved out by `take` or `swap`, or lent as a whole `rw` place. Moving the record, `take`, `swap` and zeroed storage carry both halves together and cost nothing.

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

The identity is a field path on a local, so a nested record carries it too (`box.chart.price` has the extent `box.chart.rows`). An element of an array of records does not: `cs[0].price` is passed as a part, like any other `Buf`.

## match and try

`match` evaluates its subject once and needs exactly one arm per variant (`E-MATCH-COVERAGE`). There is no wildcard. An arm names a variant of the subject, with or without its type. A payload arm binds one fresh immutable value, and matching an owner consumes it. An arm that is one `return`, `break`, `continue`, assignment or call may leave out its braces: `None => return 0;` is `None => { return 0; }`, and anything longer is a block (`E-PARSE`).

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

`_` binds nothing. `Err(_) => return 1;` drops the payload it matches: a copyable one costs nothing, an owner is released where the arm ends and the row says `free`, as `let _ = e;` releases one, and a linear one is refused (`E-LINEAR-LEAK`), because only a consumer may end it. Nothing can read `_` (`E-UNBOUND`), so it repeats freely, in nested arms and as the binder of a loop that only counts, `for _ in 0..3`.

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

A variant may leave out its type wherever the context names the sum. In an expression, `None`, `Some(x)` and `Ok(v)` belong to the sum the context expects: the return type, an annotated `let`, an assignment, a parameter, a field, the payload of another variant, or the other side of `==`. The bare form checks and emits exactly as the qualified one, which stays legal everywhere.

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

A bare name is a variant only when nothing else of that name is visible. A local, constant, function or type of the same name is `E-VARIANT-AMBIGUOUS`, and where no sum is expected, `let x = None;` is `E-UNBOUND` with a message that names the sum declaring it. A sum another module keeps private keeps its variants private too (`E-PRIVATE`).

```cairn rejects E-VARIANT-AMBIGUOUS
struct Line { width:u64; }
enum Shape { Dot; Line(Line); }
fn thin() -> Shape = Line(Line(1));
```

```text
Line is both Shape.Line and a type; write Shape.Line.
```

`try e` takes a two-variant sum, success first and failure second. It yields the success payload, or returns the failure from the enclosing function or closure, whose return type must be a two-variant sum with the same failure payload (`E-TRY`). The families may differ, so a `Done[E]` failure propagates out of a function returning `Result[T, E]`. It is the only propagation form, and it is always written out.

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

Inside a larger expression, a `try` may not sit beside an operand that already owns something, because leaving from there would abandon it. Bind the `try` first.

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

A `const` folds at compile time, from literals, other constants in any order of declaration, arithmetic, comparison, `&&`, `||`, `!` and the scalar conversions. The folding is exact: integer division and remainder go toward zero as at run time, and in an `f32` constant every literal, conversion and operation rounds once, as the machine will. The result must fit its type and be finite. Division by zero and a constant defined through itself are `E-CONST`.

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

## Tests and assert

`assert(cond)` is a guard the program writes: it traps when `cond` is false, and `assert(cond, "why")` also says why. The condition is a `bool` and the text one string literal (`E-ARITY`), and the row gains `trap`. A failed assert prints where it was written, `assertion failed at src/main.cairn:12: why`, and aborts as every failed guard does. A build that knows the project's files names the file and line; a plain compile names the function, so the canonical projection still lowers to the same C++.

`test name { ... }` is a test: a body checked like a void function with any effects, which `cairn test` runs in a process of its own ([tools.md](tools.md#cairn-test)). It takes nothing and returns nothing, and a module declares each test name once (`E-TEST`). No other build holds a test and nothing can call one, so a test may share its name with the function it tests, and `test` is an ordinary name everywhere else.

```cairn
fn average(x:u64, y:u64) -> u64 = (x & y) + shr(x ^ y, 1);

test average {
  assert(average(10, 20) == 15);
  assert(average(1, 2) == 1, "rounds down");
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

## What the language does not have

No inheritance and no implicit boxing. No lifetime annotations: a borrow cannot outlive the call it is written in. No implicit conversion, no operator overloading, no shadowing, no block-tail return. No wildcard arm, and no propagation form other than `try`. No exception, no unwinding and no rollback: a failed guard aborts. No orphan rule, because coherence is judged over the whole program. No cancellation of a task or of queued device work. No loop that implies parallelism. No downloads: dependencies are vendored sources.

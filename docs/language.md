# The core language

This page is the first of the six pages of the language reference. It covers values and arithmetic, functions and control flow, records and sums, `match` and `try`, constants, tests and printing. After reading it you can write, run and test a CAIRN program of one file that computes with scalars, records and sums. The [index](README.md) lists the other five pages. The test suite compiles every example here, and [verification.md](verification.md) says which parts are proved.

## Three rules

Three rules explain most of the language.

- Costs are visible: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so.
- Borrows are second class: a borrow, a reference to a value that someone else holds, exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references.
- Short forms are contracts: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary code, and every check and obligation of that code comes with them.

Every function has an inferred effect row, which records the costs the first rule makes visible. The row is the list of what the function may do when it runs, such as `alloc` (take heap memory), `io` (input or output) or `trap` (abort on a failed check). [memory.md](memory.md#effects) lists every effect.

An owner is a value that holds heap storage, such as a `Buf`, and releases it when it goes out of scope. Using an owner as a value moves it ([memory.md](memory.md#owners-and-moves)).

A guard is a check the compiler writes into the program where an operation could go wrong at run time: an index past the end of an array, an integer overflow, a division by zero. A failed guard aborts the process at once. Nothing unwinds and nothing is rolled back.

## Values and arithmetic

### Names and bindings

Identifiers are ASCII. A comment runs from `//` to the end of the line and may hold any UTF-8 text. Parameters and `let` locals are immutable, and `let mut` makes a local mutable. No name may shadow another name in scope (`E-SHADOW`). `reg x = 0;` and `each i in n { }` are older spellings of `let mut x = 0;` and `for i in 0..n { }`.

### Scalars and literals

The scalar types are `bool`; the unsigned integers `u8`, `u16`, `u32`, `u64` and `usize`; the signed integers `i8`, `i16`, `i32` and `i64`; and the floats `f32` and `f64`. A `usize` is 64 bits wide.

Integer literals are decimal or hexadecimal (`0x`). A float literal has a point or an exponent (`1.5`, `1e5`). `true` and `false` are the `bool` literals. `'c'` is one byte and works as a `u8`. `"text"` is a static `ro<u8>[n]` view of its bytes (a view is a borrowed array, [memory.md](memory.md#arrays-views-and-parts)), with the escapes `\n \t \r \0 \\ \" \' \xNN`.

A literal takes the type the context expects of it. When nothing expects a type, an integer literal is a `u64` and a float literal an `f64`.

A minus sign written on an integer literal makes one constant, so a signed type's minimum is a literal. `-128` is an `i8`, and `const I64_MIN:i64 = -9223372036854775808;` is the least `i64`. With nothing expecting a type, `-1` is an `i64`. A negated literal holds no overflow guard. Negating a value does.

### Checked arithmetic

`+`, `-` and `*` abort on overflow in every build. `/` and `%` abort on a zero divisor, and on the signed minimum divided by `-1`. Signed division truncates toward zero, as in C, so `-7 / 2` is `-3` and `-7 % 2` is `-1`.

Arithmetic that wraps says so by name. `add_wrap`, `sub_wrap` and `mul_wrap` are modular: they wrap around at the width of their type. `shl_wrap` and `shr` shift by a `usize` count, which a guard holds below the width. The wrapping forms and the shifts take unsigned integers only (`E-WRAP-TYPE`). The bitwise operators `&`, `|`, `^` and `~` take unsigned integers only (`E-OPERATOR`). `min` and `max` take integers only (`E-MINMAX`).

`x += e` means `x = x + e` and is checked the same way, and so are `-=`, `*=`, `/=`, `%=`, `&=`, `|=` and `^=`. The place on the left is evaluated once, so `xs[i] += 1` finds its element and pays its bounds guard once. There is no compound form that wraps. Write `x = add_wrap(x, e)`.

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

### Conversions

A conversion is a call of the target type, such as `u32(x)` or `f64(n)`, and nothing converts on its own: passing a `u64` where a `u32` is expected is `E-TYPE-MISMATCH`. A conversion to an integer type is range checked. Narrowing aborts when the value is outside the target, and a float converted to an integer aborts on NaN or on a value the target cannot hold.

```cairn rejects E-TYPE-MISMATCH
fn payload(total:u32, header:u32) -> u32 = total - header;
fn main() -> i32 { let header:u64 = 20; return i32(payload(1500, header)); }
```

```text
Expected u32, got u64.
```

### Floats and the math builtins

Floats compile with `-ffp-contract=off -fno-fast-math`, and device code with `--fmad=false` as well. The C++ compiler may neither fuse a multiply and an add into one operation nor reassociate float arithmetic.

Six builtins cover what IEEE 754 defines exactly, so every compiler, the host and a device lane give the same bits. `sqrt` (correctly rounded), `floor`, `ceil` and `trunc` take an `f32` or an `f64`. `abs` takes a float or a signed integer, and traps on the signed minimum. `to_bits` gives a float's IEEE bit pattern. Any other argument is `E-MATH-TYPE`.

`exp`, `log`, `sin` and the rest depend on the last bit of the C math library, so they are not builtins. [std.math](library.md#stdmath) calls the C library for them, and its effect row says so. When a program declares its own function with one of these names, a call reaches the program's function.

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

### Guards the compiler leaves out

The compiler leaves a guard out where the checker has shown that it cannot fail. Inside `for i in 0..n`, `parallel i in n` or a `reduce` over `n`, the index `x[i]` needs no bounds check when `x` is a view whose extent, the length written in its type, is `n`, and `i + 1` needs no overflow check. The same holds for `x[k]` after `if k >= n { return 0; }`, for `x[i - 1]` under `if i > 0`, for a bin `usize(v & 255)` into 256 counters, and for `x[k]` on the right of `k < n && x[k] > 3`.

The checker takes these facts from the index a loop binds, the index a lane binds (a lane runs one index of a `parallel` region, [concurrency.md](concurrency.md#parallel-regions)), immutable `let` bindings, conditions, early exits and the `where` test of a `compact` ([concurrency.md](concurrency.md#reduce-and-compact)). Each fact is about `usize` values that cannot change, and a `let mut` local never supplies one. A part `x[lo..hi]` loses its guard once `lo <= hi <= len(x)` is established.

Leaving a guard out never changes what a program does. The function's row still says `trap`, and the build receipt, the JSON record `cairn build` writes, counts the site under `discharged_check_sites`. Every guard left out carries the facts that justify it, and an independent audit checks each one before a line of C++ is emitted. `--keep-guards` on `emit`, `build` and `run` writes every guard. [verification.md](verification.md#the-guard-elision-rule) says what of this is proved.

## Functions and control flow

A function with a block body returns through explicit `return` statements, and every path of a function that returns a value must reach one (`E-RETURN`). The last expression of a block is never returned for you. An expression body, as in `fn payload(total:u32, header:u32) -> u32 = total - header;`, is that one `return`.

The control forms are `if`, `else if` and `else`, `while`, `for i in lo..hi`, `for x in xs`, `break`, `continue` and nested `{ }` blocks. A `for` evaluates `lo` and then `hi`, each once, and an empty or reversed range runs no iteration. `&&` and `||` evaluate their right side only when the left side does not decide the result.

`for x in xs { }` walks the elements of a view, a `Buf`, an `Array` or a fixed array, and `for i, x in xs { }` names the position too. It means `for i in 0..len(xs) { let x = xs[i]; }` and pays the guards that loop pays. Each element is copied, so the elements must be copyable (`E-ELEMENT-LOOP`). An owner in an array is reached through `xs[i]`, which you take, swap or lend.

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

### Calls as statements

A call can stand as a statement. `count(log);` drops what `count` returns, and `try check(v);` drops the success payload. A dropped owner is released where the statement ends. A dropped linear value is `E-LINEAR-LEAK`, because a linear value must be consumed ([memory.md](memory.md#linear-values-and-defer)).

An outcome is never dropped silently. A call that returns a sum `try` accepts, such as `Result` or `Option`, is handled with `try` or `match`, or dropped on purpose with `let _ = check(v);`. Otherwise the call is `E-DISCARD`. A builtin that only computes a value, such as `min(a, b);`, is `E-DISCARD` as a statement too, and so is a statement that computes a value with no call at all, such as `x + 1;`.

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

`struct` declares a record, like a C `struct`. `enum` declares a tagged sum, like a Rust `enum`, in which each variant carries zero or one payload. Fields and payloads may be any value type, owners included. A field or payload is never a borrow, `void`, or its own type by value (`E-RECORD-TYPE`); reach a value of the same type through a `Buf`. A record is copyable when all its fields are. An enum whose variants carry no payload may be compared with `==`, and comparing any other record or sum is `E-OPERATOR`.

```cairn
struct Header { kind:u8; size:u32; }
struct Frame { head:Header; body:Buf[u8]; }      // a Buf makes Frame an owner
enum Op { Read; Write; }                         // no payloads: equality is allowed

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

### Fields that carry an extent

An extent is the length written into an array or view type, such as `n` in `ro<f64>[n]`. Two views with the same extent are known to have the same length ([memory.md](memory.md#arrays-views-and-parts)).

A `Buf` field may name an earlier `usize` field of the same record as its extent, as `price:Buf[f64][rows]` does. Naming anything else is `E-EXTENT`. Then `len(c.price)` and `c.rows` are the same extent, and the column goes to a call whole, with no part and no guard.

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

Nothing checks the relation at run time, so the checker makes sure it is established once and never broken. A constructor builds the column in place as `Buf[T](n)` from the same `n` it gives the extent field. Neither field is ever assigned, taken, swapped or lent `rw` as a whole place on its own (`E-EXTENT-FIELD`), though the column's elements may be written. Moving the whole record keeps both together.

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

A nested record carries the extent too: `box.chart.price` has the extent `box.chart.rows`. An element of an array of records does not. `cs[0].price` is passed as a part and pays its guard.

## match and try

`match` takes a sum apart. It evaluates its subject once and needs exactly one arm per variant, with no wildcard arm (`E-MATCH-COVERAGE`). A payload arm binds one fresh immutable value, and matching an owner consumes it. An arm that is one `return`, `break`, `continue`, assignment or call may leave out its braces. Anything longer is a block (`E-PARSE`).

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

### The name `_`

`_` binds nothing. `Err(_) => return 1;` drops the payload: an owner is released where the arm ends, and a linear payload is refused (`E-LINEAR-LEAK`), because only a consumer may end it. Nothing can read `_` (`E-UNBOUND`), so it may repeat, as in `for _ in 0..3`.

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

### Variants without their sum's name

A variant may leave out its sum's name wherever the context names the sum: the return type, an annotated `let`, an assignment, a parameter, a field, another variant's payload, or the other side of `==`. The bare form compiles exactly as the qualified one, which stays legal everywhere.

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

A bare name is a variant only when nothing else of that name is visible. Otherwise it is `E-VARIANT-AMBIGUOUS`. Where no sum is expected, `let x = None;` is `E-UNBOUND`. A private sum keeps its variants private (`E-PRIVATE`).

```cairn rejects E-VARIANT-AMBIGUOUS
struct Line { width:u64; }
enum Shape { Dot; Line(Line); }
fn thin() -> Shape = Line(Line(1));
```

```text
Line is both Shape.Line and a type; write Shape.Line.
```

### try

`try e` takes a sum of exactly two variants, success first and failure second, as Rust's `?` takes a `Result`. It yields the success payload, or returns the failure from the enclosing function. That function's return type must be a sum of two variants with the same failure payload (`E-TRY`), and the two sums may be different types.

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

A `try` may not sit beside an operand that already owns something, because returning from there would abandon that owner. Bind the `try` to a name first. [memory.md](memory.md#operand-order) has the other rules of operand order.

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

The compiler folds a `const` before the program runs, exactly as the machine would compute it. It is made of literals, other constants in any order, arithmetic, comparisons and conversions, and anything else, such as a call, is `E-CONST`. An integer result must fit its type (`E-LITERAL-RANGE` for arithmetic, `E-CONST` for a conversion). A float result must be finite, and a division by zero or a constant defined through itself is `E-CONST`.

A constant natural, a whole number known when the program compiles, may stand wherever a natural is written: an extent, `Array[u64, N]`, `scale[N](x)`.

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

`assert(cond)` traps when `cond` is false, and `assert(cond, "why")` also says why. The message is one string literal, and any other form is `E-ARITY`. A failed assert prints to standard error where it was written, as in `assertion failed at src/main.cairn:12: why`, and aborts as every failed guard does. `assert_eq(a, b)` also prints both values, as in `left 1, right 2`. It takes only integers, bools and floats (`E-ASSERT-EQ`), so for anything else write `assert(a == b)`.

`test name { ... }` declares a test: a body with no parameters and no result that only [`cairn test`](tools.md#cairn-test) runs, each test in a process of its own. A module declares each test name once (`E-TEST`). Nothing can call a test, so it may share its name with the function it tests, and `test` is an ordinary name everywhere else.

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

`println("total ", n, ' ', ok)` writes its arguments in turn, then a newline. Integers print in decimal and bools as `true` or `false`. A character literal prints as its byte, while a `u8` held in a variable prints as its number. Byte strings, `u8` views and parts, a `Buf` or `Array` of `u8`, and a record that lends a `u8` view, such as `Vec[u8]`, print as their bytes. A float prints the shortest digits that read back to the same value, laid out as JavaScript lays out a number (`100000`, `1.5`, `0.000001`, `1e-7`).

`print` leaves out the newline, and `eprint` and `eprintln` write to standard error. `format(out, ...)` appends the same text to a `Vec[u8]`.

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

Every argument is computed, left to right, before a byte is written, so a failed guard in an argument leaves nothing of the line written. A line of up to 4096 bytes goes out in one `write` from a buffer on the stack, so a pipe keeps it whole. Printing allocates nothing, and its row is `io` and `ffi:write`. `format` adds `alloc` and `free` to the row for the growth. A write the kernel refuses, such as one to a full disk, ends that print and traps nothing. A write to a closed pipe raises `SIGPIPE`, which the runtime leaves at its default, so the process ends there as a C program would.

A record, a sum or a storage float ([numerics.md](numerics.md#storage-floats)) as an argument is `E-PRINT-ARG`; widen a storage float with `f32(x)` first. A `format` target that cannot grow is `E-FORMAT-TARGET`: it must be a `Vec[u8]`, or a record that lends `buf[0..len]` of a `Buf[u8]`. A device lane cannot print (`E-PLACEMENT`), and neither can a host lane (`E-PARALLEL-CALL`).

```cairn rejects E-PRINT-ARG
struct Point { x:u64; y:u64; }
fn show(p:Point) { println(p); }
```

```text
print writes integers, bools, character literals, floats and bytes; format a Point with std.fmt first, or print its fields.
```

## What the language does not have

CAIRN has no inheritance, implicit boxing, lifetime annotations, implicit conversion, operator overloading or shadowing, and a block never returns its last expression. There is no wildcard arm, and no way to pass an error up but `try`. There are no exceptions, no unwinding and no rollback: a failed guard aborts. There is no orphan rule, because the compiler checks over the whole program that each type has one implementation of each trait ([abstractions.md](abstractions.md#traits)). Neither a task nor queued device work can be cancelled. No loop implies parallelism. Nothing is downloaded: dependencies are vendored sources.

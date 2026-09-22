# Values, functions, records and sums

## Values and arithmetic

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

## Functions and control flow

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

## Records and sums

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

A `Buf` field may name an earlier `usize` field of the same record as its extent, which makes `len(c.price)` and `c.rows` one identity and lets a column go to a call whole. The name must be an earlier field and it must be `usize` (`E-EXTENT`); the field that declares it must be a `Buf`.

```cairn
struct Chart { rows:usize; price:Buf[f64][rows]; qty:Buf[f64][rows]; }

fn both(n:usize, xs:ro<f64>[n], ys:ro<f64>[n]) -> f64 {
  let mut sum:f64 = 0.0;
  for i in 0..n { sum = sum + xs[i] * ys[i]; }
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

Nothing checks the relation at run time, so it is established once and never broken. A constructor writes the carrier inline as `Buf[T](n)` on the same expression the extent field is given; anything else is `E-EXTENT-FIELD`. Neither the extent field nor a carrier is assigned on its own, moved out by `take` or `swap`, or lent as a whole `rw` place, for the same reason. Moving the record, `take`, `swap` and zeroed storage all carry both halves together and cost nothing.

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


## Constants

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

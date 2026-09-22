# Language reference

This reference describes what the compiler implements. The test suite runs every construct natively, compiles every accepted example and requires every refused one to fail with the code shown. [verification.md](verification.md) says which of it is proved. The 0.2, 0.3 and 0.4 specifications under `docs/history/` are earlier proposals, and a feature they describe exists only if this reference describes it too.

Three rules explain most of the language. Costs are visible: nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row. Borrows are second class: a borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references. Short forms are contracts: `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

This file covers values, control flow, records, sums, memory, ownership and effects. [abstractions.md](abstractions.md) covers generics, traits, closures, modules and recipes, and [concurrency.md](concurrency.md) covers tasks, lanes and devices.

## Values and arithmetic

Identifiers are ASCII, comments (`//` to the end of the line) are UTF-8. Parameters and `let` locals are immutable, `let mut` is mutable, and no name may shadow another (`E-SHADOW`). `reg x = 0;` and `each i in n { }` are older spellings of `let mut x = 0;` and `for i in 0..n { }`.

The scalars are `bool`, the unsigned `u8 u16 u32 u64 usize` (`usize` is 64-bit), the signed `i8 i16 i32 i64`, and `f32 f64`. Literals are decimal and `0x` integers, floats with a point or an exponent, `true` and `false`, `'c'` (one byte, compatible with `u8`) and `"text"`, a static `ro<u8>[n]` view with the escapes `\n \t \r \0 \\ \" \' \xNN`. A literal takes the type expected of it, `u64` or `f64` when nothing expects one.

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

Conversions are explicit type calls. An integer target is range checked: narrowing aborts outside the target, and float to integer aborts on NaN or on a value the target cannot hold.

```cairn rejects E-TYPE-MISMATCH
fn payload(total:u32, header:u32) -> u32 = total - header;
fn main() -> i32 { let header:u64 = 20; return i32(payload(1500, header)); }
```

```text
Expected u32, got u64.
```

Floats compile with `-ffp-contract=off -fno-fast-math` (and `--fmad=false` on the device): no contraction and no reassociation. A failed guard aborts the process. It does not unwind, and it rolls nothing back.

The compiler leaves a guard out of the emitted C++ where the checker has shown it cannot fail. Inside `for i in 0..n`, `parallel i in n` or a `reduce` over `n`, `x[i]` into a view of extent `n` needs no bounds check, and neither does `i + 1`. The same holds after `if k >= n { return 0; }` for `x[k]`, for `x[i - 1]` under `if i > 0`, for a bin `usize(v & 255)` into 256 counters, for `data[i]` below `len(data)` when `data` is an immutable owner, and for a row `p[b * 256 + v]` of a buffer of `k * 256` when `b < k` and `v < 256`. The facts come from loop and lane binders, immutable `let` bindings, conditions and early exits, the left side of `&&` or `||` for its right side (so `k < n && x[k] > 3` needs no check) and a collector's predicate for its projection, over `usize` values that cannot change, and nothing about `let mut` locals. Removing a guard never changes what a program does: the row still says `trap`, and the receipt counts each such site under `discharged_check_sites` beside `syntactic_check_sites`. [verification.md](verification.md#the-guard-elision-rule) says what of this is proved.

## Functions and control flow

A block body needs explicit `return` statements, and every path of a non-void function must return one (`E-RETURN`). There is no block-tail return. An expression body, `fn payload(total:u32, header:u32) -> u32 = total - header;`, is that one return.

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

`match` evaluates its subject once and needs exactly one arm per variant (`E-MATCH-COVERAGE`). There is no wildcard. A payload arm binds one fresh immutable value, and matching an owner consumes it.

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
    Short(got) => { return 2; }
  }
  match parse(2, "hi") {
    Ok(head) => { return 3; }
    Short(got) => { if got != 2 { return 4; } }
  }
  return 0;
}
```

```cairn rejects E-MATCH-COVERAGE
enum Op { Read; Write; Flush; }
fn cost(op:Op) -> u64 { match op { Read => { return 1; } Write => { return 2; } } }
```

```text
Every variant must have exactly one arm; missing Op.Flush.
```

A variant may leave out its type wherever the context names the sum. An arm names a variant of the subject, so `Some(v) =>` needs nothing more. In an expression, `None`, `Some(x)` and `Ok(v)` belong to the sum the context expects: the return type, an annotated `let`, an assignment, a parameter, a field, the payload of another variant, or the other side of `==`. The bare form checks and emits exactly as the qualified one, which stays legal everywhere.

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
    None => { return 2; }
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
  match body_size(7, "\x07abcdef") { Ok(size) => { if size != 2 { return 1; } } Err(e) => { return 2; } }
  match body_size(2, "hi") { Ok(size) => { return 3; } Err(e) => { if e != 1 { return 4; } } }
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

## Arrays, views and parts

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

Extents agree by name and literal identity, not by value, and `len(v)` supplies the identity of `v`. `len("ready")` is the literal's byte count. A record may give one of its `Buf` fields the identity of an earlier `usize` field ([records and sums](#records-and-sums)), and then `c.price` has the extent `c.rows` and goes to a call whole.

```cairn rejects E-TYPE-MISMATCH
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 = u32(bytes[0]);
fn main() -> i32 { let a = Buf[u8](8); let b = Buf[u8](8); return i32(checksum(len(a), b)); }
```

```text
Expected ro<u8>[len(a)]@host, got ro<u8>[len(b)]@host.
```

A call may leave out its extent parameters. A `usize` parameter that a later view parameter names as its extent can only be that view's length, so when a call omits every such parameter the checker writes each one in as `len` of the first view argument that names it, or `hi - lo` for a part. Everything after the checker sees the call written out: the same C++, the same effect row. Every other view with that extent must match it, as it would a written length. A call passes all of its extents or none of them (`E-ARITY`), and an `extern` takes every argument, in the order C gives them.

```cairn
fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 {
  let mut t:u64 = 0;
  for i in 0..n { t = t + xs[i] * ys[i]; }
  return t;
}

fn main() -> i32 {
  let mut v = Buf[u64](8);
  for i in 0..8 { v[i] = u64(i); }
  if dot(v, v) != dot(len(v), v, v) { return 1; }            // the same call
  if dot(v[0..4], v[4..8]) != 0 * 4 + 1 * 5 + 2 * 6 + 3 * 7 { return 2; }
  return 0;
}
```

```cairn rejects E-TYPE-MISMATCH
fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 = xs[0] * ys[0];
fn main() -> i32 { let a = Buf[u64](8); let b = Buf[u64](8); return i32(dot(a, b)); }
```

```text
Expected ro<u64>[len(a)]@host, got ro<u64>[len(b)]@host.
```

A part `bytes[lo..hi]` goes wherever an array borrow is expected and carries one dynamic guard: `lo <= hi <= len`, and `hi - lo` equal to the callee's extent, which for a part may be any `usize` arithmetic. A part of a part guards once per level. Bounds and extents are written from names, literals, fields, elements, operators, `len` and the arithmetic builtins; a call is bound to a name first (`E-CALL-SHAPE`).

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

## Owners and moves

Four forms of storage hold elements, and all four are zero-initialized, because every type has an all-zero value.

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

Owners are affine. Using one as a value (binding it, passing it by value, returning it, putting it in a field) moves it, and its name is dead afterwards (`E-MOVED`). Release at scope exit is implicit, and the `free` effect is charged where that release runs: the end of a block or match arm that still holds the owner, a `return` that leaves while it is held, a function handed one by value that passes it on to nobody, and the place a new value is assigned over. A function that only drops an owner carries `free` alone; one that hands the same owner on carries neither `free` nor `alloc`. An outer owner cannot be moved inside a loop (`E-MOVE-IN-LOOP`), a closure or a lane.

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

An owner cannot be moved out of a place (`E-PARTIAL-MOVE`). `take(place)` moves the value out and leaves the zero value behind, and `swap(a, b)` exchanges two places. Growth is library code: `std.vec` reallocates with `Buf`, `swap` and an assignment, so its allocation shows in every caller's row.

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

A whole record is taken apart the way it was built. `let Ring(slots, used) = r;` consumes `r` and binds every field in declaration order (`let mut Ring(...)` binds them mutably), which is the way out for an owner or a linear value kept inside a record. Anything that is not that record by value with one name per field is `E-UNPACK`. A record of another module must be `pub`, and a `linear` record is taken apart only by the module that declares it (`E-PRIVATE`), so a protocol cannot be ended from outside.

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

## Linear values and defer

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

## Layout and the machine

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

`mmio_read[u32](address)`, `mmio_write[u32](address, value)` and `asm("wfi")` reach the machine from inside `unsafe { }`, and add `mmio` and `asm` to the row. A freestanding build produces one ELF image with no operating system, C library or C++ runtime under it; [tools.md](tools.md#the-freestanding-target) has the target table and what the profile guarantees.

```cairn
fn wake(base:usize) {
  unsafe {
    let status = mmio_read[u32](base);
    mmio_write[u32](base + 4, status | 1);
    asm("wfi");
  }
}
```

## Effects

Every function carries a row: the least fixed point of its own local effects and its callees' rows, with borrowed footprints renamed to the caller's arguments. A row says what may happen, never what is computed. The build receipt has it under `functions.<name>.effects`.

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

`pure` and `effects(read:x, trap)` declare a ceiling, which is checked (`E-EFFECT-CEILING`). `pure` still allows `trap`, `diverge`, `local_read`, `local_write`, `stack_storage`, `zero_init`, `ffi_precondition` and the reads of what the function was lent, so `checksum` keeps `read:bytes` in its row.

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

## Operand order

A call that writes through a borrow or allocates cannot be a nested operand (`E-EFFECT-ORDER`). Bind it to a name first, so the cost is a statement of its own. A call that only releases stays an ordinary operand: a drop runs where C++ ends the scope, and it writes no place another operand can name.

```cairn rejects E-EFFECT-ORDER
fn fill(n:usize, out:rw<u8>[n], value:u8) -> usize { for i in 0..n { out[i] = value; } return n; }
fn main() -> i32 { let mut frame = Buf[u8](4); let done = fill(len(frame), frame, 1) + len(frame); return 0; }
```

```text
Bind a writing call to its own statement before using its result.
```

C++ leaves the order of operands open, so a call the outside world can observe (I/O, the machine, atomics and locks, a function value) may not sit beside another call in one expression, nor beside an operand whose own guard may abort: an element, a part, checked arithmetic. `&&`, `||` and a call's own arguments are sequenced and are not affected.

```cairn rejects E-EFFECT-ORDER
extern fn putchar(c:i32) -> i32 effects(io);
fn say(c:i32) -> i32 { unsafe { return putchar(c); } }
fn main() -> i32 { return say(65) + say(66) - 131; }
```

```text
Bind this call first: it can be observed from outside, and the operand beside it could run, or abort, before or after it.
```

## extern and unsafe

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

## What the language does not have

No inheritance and no implicit boxing. No lifetime annotations: a borrow cannot outlive the call it is written in. No implicit conversion, no operator overloading, no shadowing, no block-tail return. No wildcard arm, and no propagation form other than `try`. No exception, no unwinding and no rollback: a failed guard aborts. No orphan rule, because coherence is judged over the whole program. No cancellation of a task or of queued device work. No loop that implies parallelism. No downloads: dependencies are vendored sources.

# CAIRN 1.1 language profile

This document states implemented behavior. `docs/history/` records earlier proposals and must not be used to infer accepted features. Grammar lives in `syntax.py`, types, ownership and effects in `checking.py`, lowering in `codegen.py`, guards in `runtime/*.hpp`. Every construct below is executed natively by the test suite; none of it is a whole-compiler proof ([verification.md](../internals/verification.md)).

Three rules explain most of the language.

Costs are visible. Nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row.

Borrows are second class. A borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references.

Short forms are contracts. `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

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
Every variant must have exactly one arm.
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

## Owners and moves

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

Owners are affine. Using one as a value (binding it, passing it by value, returning it, putting it in a field) moves it, and its name is dead afterwards (`E-MOVED`). Release at scope exit is implicit and shows in the row as `free`. An outer owner cannot be moved inside a loop (`E-MOVE-IN-LOOP`), a closure or a lane.

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

## Generics

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

## Bounds

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

## Certifying a template

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

## Traits

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

## dyn and Dyn

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

## Function values and closures

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

## Effects

Every function carries a row, the least fixed point of its own local effects and its callees' rows, with borrowed footprints renamed to the caller's arguments. A row says what may happen, never what is computed. The build receipt has it under `functions.<name>.effects`.

| effect | appears when |
| --- | --- |
| `read:x`, `write:x` | the borrow `x` is read, written |
| `local_read`, `local_write` | the function's own storage is read, written |
| `alloc`, `free`, `zero_init` | heap storage is taken, released, zeroed |
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

## Operand order

A call that writes through a borrow or allocates cannot be a nested operand (`E-EFFECT-ORDER`). Bind it to a name first, so the cost is a statement of its own.

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

## Tasks and leases

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

## Atomics and mutexes

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

## Parallel regions

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

## reduce and compact

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

Its one unchecked store is justified by seventeen affine certificates, checked before every emission and proved sound in Lean together with in-bounds stores and stable selection for the loop model ([verification.md](../internals/verification.md)).

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

## Placement and device memory

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

## Queued device work

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

## Modules

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

## Projects

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

A freestanding `target` refuses any program whose effect rows need a hosted runtime ([freestanding.md](freestanding.md)). The host chooses trusted compilers (`clang++`, `g++`, and `nvcc` when a program uses the device), and builds use fresh directories. Generated C++ is readable and keeps the C ABI for every function whose signature is C compatible. A library exports every function; an executable contains only what its `main` reaches, and `main` may live in a module, while the receipt still covers everything that was checked.

`--debug` adds symbols and `#line` maps to the authored files. `--incremental` compiles one object per module against a shared interface header (types, tables, prototypes), and reuses an object only when its unit, that header, the command line, the runtime headers and the compiler version hash to the same key, and the stored object still matches the digest written beside it in `build/objects`.

A body-only edit then recompiles one module and a signature change recompiles all; a truncated or substituted file is compiled again, not linked. Incremental builds give up inlining across modules, and device programs and freestanding images stay one unit.

## Dependencies

`[dependencies] geometry = "deps/geometry"` names a project vendored inside this one's root, with its own `cairn.toml` and its own dependencies loaded first, at most 16 per manifest and 4 deep, a diamond loaded once. A dependency contributes modules only, and only what it marks `pub` is reachable. Nothing is fetched, no path leaves the root, no path has a `.` or `..` segment, no symbolic link is followed, and the receipt pins each dependency's manifest and sources by hash.

A dependency's manifest is read by the same checker as yours, so an unknown table or option is refused there too, and its `[build]`, which the build ignores, must still name a known kind, architecture and target. One directory is one project under one name: a second name for it is an error, not a diamond, and one name is one project of the build, the root's own included.

A module belongs to exactly one project. No project declares a `std.*` module, reopening a module another project declared names both projects and fails, and so does a file with no `module` header that would silently continue a dependency's. An executable's entry point is searched only in the sources this manifest lists, so a dependency neither supplies `main` nor denies you yours.

## Recipes

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

## Layout, the machine and freestanding targets

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

A freestanding build produces one ELF image with no operating system, C library or C++ runtime under it; [freestanding.md](freestanding.md) has the target table and what the profile guarantees.

## What the language does not have

* No inheritance and no implicit boxing.
* No lifetime annotations: a borrow cannot outlive the call it is written in.
* No implicit conversion, no operator overloading, no shadowing, no block-tail return.
* No wildcard arm, and no propagation form other than `try`.
* No exception, no unwinding and no rollback: a failed guard aborts.
* No orphan rule, because coherence is judged over the whole program.
* No cancellation of a task or of queued device work.
* No loop that implies parallelism.
* No downloads: dependencies are vendored sources.

## Scope of proof

Typed, native-built, finite-tested, SMT-equivalent and Lean-checked are distinct claims; see [verification.md](../internals/verification.md). Generic instances, owners, lanes and the foreign boundary are native-implemented and tested, not mechanized.

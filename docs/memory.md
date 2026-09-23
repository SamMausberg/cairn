# Memory, ownership and effects

Where values live and what code may do with them: views and parts, owners and moves, linear values, layout, effect rows, operand order and the foreign boundary. As in [language.md](language.md), every example is compiled by the suite and every refused one fails with the code shown.

## Arrays, views and parts

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements, and `ro<T>` and `rw<T>` borrow one value, which the callee uses like the value itself. A borrow argument names a place (`frame`, `f.body`), and an `ro<T>` parameter also accepts a temporary. An extent is a literal or an earlier `usize` parameter, and `len(view)` reads it.

```cairn
fn fill(n:usize, out:rw<u8>[n], value:u8) { for i in 0..n { out[i] = value; } }
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
  return sum;
}
fn bump(seen:rw<u64>) { seen += 1; }              // a single borrow, assigned by name

fn main() -> i32 {
  stack frame:u8[16] = zeroed;
  let mut seen:u64 = 0;
  fill(len(frame), frame, 3);
  bump(seen);
  if checksum(len(frame), frame) != 48 || seen != 1 { return 1; }
  return 0;
}
```

Extents agree by name and literal identity, not by value, and `len(v)` is the identity of `v`. A record's `Buf` field can carry the identity of an earlier `usize` field ([records and sums](language.md#records-and-sums)).

```cairn rejects E-TYPE-MISMATCH
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 = u32(bytes[0]);
fn main() -> i32 { let a = Buf[u8](8); let b = Buf[u8](8); return i32(checksum(len(a), b)); }
```

```text
Expected ro<u8>[len(a)]@host, got ro<u8>[len(b)]@host.
```

A call may leave out its extent parameters. A `usize` parameter that names a later view's extent can only be that view's length, so the checker writes it in as `len` of the first view argument, or `hi - lo` for a part, and everything after the checker sees the call written out. Every other view with that extent must still match. A call passes all its extents or none (`E-ARITY`), and an `extern` takes every argument.

```cairn
fn dot(n:usize, xs:ro<u64>[n], ys:ro<u64>[n]) -> u64 {
  let mut t:u64 = 0;
  for i in 0..n { t += xs[i] * ys[i]; }
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

A record may name the view it lends. With `lends data[0..len];` in its body, the record named where a view is expected means the part `data[0..len]` of itself, with that part's guard, row, lease and alias rules, and `for b in line` walks that part. The bounds are read again at every use, so the length may change freely and a length past the storage traps at the part's guard. `std.vec` declares it, so a `Vec` goes to a call whole.

```cairn
struct Line { data:Buf[u8]; len:usize; lends data[0..len]; }

fn sum(n:usize, bytes:ro<u8>[n]) -> u64 {
  let mut t:u64 = 0;
  for b in bytes { t += u64(b); }
  return t;
}

fn main() -> i32 {
  let mut line = Line(Buf[u8](8), 2);
  line.data[0] = 3;
  line.data[1] = 4;
  if sum(line) != 7 || sum(line.data[0..line.len]) != 7 { return 1; }        // the same call
  let mut seen:u64 = 0;
  for b in line { seen += u64(b); }
  if seen != 7 { return 2; }
  return 0;
}
```

The bounds are literals or `usize` fields of the record (`E-LENDS`). A task leases the elements it was lent, not the length.

```cairn rejects E-LENDS
struct Line { data:Buf[u8]; len:u32; lends data[0..len]; }
fn main() -> i32 { let line = Line(Buf[u8](8), 2); return 0; }
```

```text
A lent view's bounds are literals or usize fields of Line; len is u32.
```

A part `bytes[lo..hi]` goes wherever an array borrow is expected and carries one guard: `lo <= hi <= len`, and `hi - lo` equal to the callee's extent. A part of a part guards once per level. Bounds are names, literals, fields, elements and arithmetic; bind a call to a name first (`E-CALL-SHAPE`).

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
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

Read-only borrows may alias. A mutable borrow must not overlap any other argument of the same call (`E-ALIAS`): distinct fields of one record are disjoint, and two parts of one array are disjoint only when they visibly share a boundary, as `bytes[0..mid]` and `bytes[mid..n]` do.

A function that takes views is emitted twice. Its C symbol `cf_f` is the checked entry, which checks null, alignment, length and overlap before running the body `ci_f`. A foreign caller, a test driver and `cf_main` reach the entry. A call from CAIRN goes straight to `ci_f`, because every view it can pass was already checked and `E-ALIAS` has shown the mutable ones overlap nothing. `--keep-guards` sends every call through the entry.

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

Four forms of storage hold elements, all zero-initialized, because every type has an all-zero value.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
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

Owners are affine. Using one as a value (binding, passing by value, returning, storing in a field) moves it, and its name is dead afterwards (`E-MOVED`). An owner is released at scope exit, and `free` is charged where that happens: the end of the block that still holds it, a `return`, or a place a new value is assigned over. A function that hands an owner on carries neither `free` nor `alloc`. An outer owner cannot be moved inside a loop (`E-MOVE-IN-LOOP`), a closure or a lane.

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

An owner cannot be moved out of a place (`E-PARTIAL-MOVE`). `take(place)` moves it out and leaves the zero value behind, and `swap(a, b)` exchanges two places. Growth is library code built from these, so `std.vec`'s allocation shows in every caller's row.

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

`let Ring(slots, used) = r;` consumes `r` and binds every field in order, which is how an owner or a linear value leaves a record whole. Anything but one name per field is `E-UNPACK`. A `linear` record is taken apart only by its own module (`E-PRIVATE`), so a protocol cannot be ended from outside.

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

A `linear struct` must be consumed exactly once on every path: leaving one unconsumed is `E-LINEAR-LEAK`, and consuming it on some paths only is `E-LINEAR-BRANCH`. `defer call(...);` runs one visible call at every normal exit of its block and counts as the consumption. An abort runs no cleanup.

```cairn
linear struct Lease { id:u64; }

fn acquire(id:u64) -> Lease = Lease(id);
fn release(l:Lease, freed:rw<u64>) { let Lease(id) = l; freed += id; }

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

`mmio_read[u32](address)`, `mmio_write[u32](address, value)` and `asm("wfi")` reach the machine from inside `unsafe { }`, and add `mmio` and `asm` to the row. [The freestanding target](tools.md#the-freestanding-target) runs with no operating system under it.

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

Every function has a row: its own effects joined with its callees' rows, with borrowed footprints renamed to the caller's arguments. A row says what may happen, not what is computed. The build receipt lists it under `functions.<name>.effects`.

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

`pure` and `effects(read:x, trap)` declare a checked ceiling (`E-EFFECT-CEILING`). `pure` still allows `trap`, `diverge`, `local_read`, `local_write`, `stack_storage`, `zero_init`, `ffi_precondition` and reads of what the function was lent.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 pure {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
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

C++ leaves the order of operands open, so a call the outside world can observe (I/O, the machine, atomics, locks, a function value) may not sit beside another call, or beside an operand that may abort. `&&`, `||` and a call's own arguments are sequenced and are not affected.

```cairn rejects E-EFFECT-ORDER
extern fn putchar(c:i32) -> i32 effects(io);
fn say(c:i32) -> i32 { unsafe { return putchar(c); } }
fn main() -> i32 { return say(65) + say(66) - 131; }
```

```text
Bind this call first: it can be observed from outside, and the operand beside it could run, or abort, before or after it.
```

## extern and unsafe

An `extern` declaration names a C symbol, a signature and the effects the body may have, and `extern "close" fn close_fd(...)` binds a symbol under another name. The checker cannot see the body, so the effects are mandatory and are trusted as written. An extern's extent may name a later parameter, as C orders a pointer and its length.

Foreign calls, `mmio_read`, `mmio_write` and `asm` are legal only inside `unsafe { }`, which the receipt counts per function. The guards cannot establish where storage came from, so a caller must supply live, initialized storage for each borrow; that obligation is the `ffi_precondition` in the row.

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

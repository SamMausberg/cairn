# Memory, ownership and effects

This part of the reference covers where values live and what code may do with them: views and parts, owners and moves, linear values, layout, effect rows, operand order and the foreign boundary. [language.md](language.md) covers values, control flow, records, sums and tests, [abstractions.md](abstractions.md) generics, traits, closures, modules and recipes, and [concurrency.md](concurrency.md) tasks, lanes and devices. As there, every construct runs natively in the suite, every accepted example compiles and every refused one fails with the code shown.

## Arrays, views and parts

`ro<T>[n]` and `rw<T>[n]` borrow `n` elements; `ro<T>` and `rw<T>` borrow one value, which the callee reads and assigns like the value itself. A borrow argument names a place (`frame`, `f.body`, `grid`), and an `ro<T>` parameter also accepts a temporary. An extent is a literal or an earlier immutable `usize` parameter, and `len(view)` reads that metadata.

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

Extents agree by name and literal identity, not by value, and `len(v)` supplies the identity of `v`. `len("ready")` is the literal's byte count. A record may give one of its `Buf` fields the identity of an earlier `usize` field ([records and sums](language.md#records-and-sums)), and then `c.price` has the extent `c.rows` and goes to a call whole.

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

A record may name the view it lends. `lends data[0..len];` in its body says that the record, named where an array view is expected, means the part `data[0..len]` of itself: `io.print(out)` is `io.print(out.data[0..out.len])`, the same C++, guard, row, lease and alias rules. A `for` over it is the index loop over that part, so `for b in line` is `for i in 0..line.len { let b = line.data[i]; }`. The bounds are read again at every call and nothing about them is assumed, so the length may change freely and a length past the storage traps at the part's guard. `std.vec` declares it, so a `Vec` goes to a call whole.

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

A record lends one `Buf` field between two bounds, each a literal or a `usize` field of the record (`E-LENDS`). A task leases what it was lent, the elements, and not the length.

```cairn rejects E-LENDS
struct Line { data:Buf[u8]; len:u32; lends data[0..len]; }
fn main() -> i32 { let line = Line(Buf[u8](8), 2); return 0; }
```

```text
A lent view's bounds are literals or usize fields of Line; len is u32.
```

A part `bytes[lo..hi]` goes wherever an array borrow is expected and carries one dynamic guard: `lo <= hi <= len`, and `hi - lo` equal to the callee's extent, which for a part may be any `usize` arithmetic. A part of a part guards once per level. Bounds and extents are written from names, literals, fields, elements, operators, `len` and the arithmetic builtins; a call is bound to a name first (`E-CALL-SHAPE`).

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

Read-only borrows may alias. A mutable borrow must not overlap any other argument of the same call (`E-ALIAS`): distinct fields of one record are disjoint, and two parts of one array are disjoint only when they visibly share a boundary, as `bytes[0..mid]` and `bytes[mid..n]` do. A function that takes views is emitted twice. Its C symbol `cf_f` is the checked entry: it checks null, alignment, length and overlap numerically, and then runs the body `ci_f`. A call from CAIRN code goes to `ci_f` directly, because each view such a call can pass is one its caller was given and checked, storage the caller holds, a string, or a part its guard keeps inside one of those, and `E-ALIAS` has already shown that a mutable one overlaps no other argument. A function type and a `dyn` member carry no array view, so the checked entry is what a foreign caller, a test driver or a `cf_main` reaches. `--keep-guards` sends every call through it.

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

# Memory, ownership and effects

This page says where values live and what code may do with them: views and parts of arrays, owners and moves, linear values, effect rows, operand order, the foreign boundary, record layout and the machine, tile layouts, and foreign implementations. After reading it you can pass arrays to functions without copying them, hand heap storage from one function to another, read and cap a function's effect row, and call C, assembly and vendored C++ or CUDA. It builds on [language.md](language.md).

## Arrays, views and parts

In C a function that takes an array takes a pointer and a length, and nothing stops the two from disagreeing, or the pointer from outliving the array. CAIRN passes arrays as views. A view carries its length in its type and exists only while a call runs.

### Views and extents

A view borrows the elements of an array. `ro<T>[n]` borrows `n` elements to read, and `rw<T>[n]` borrows them to read and write. `ro<T>` and `rw<T>` borrow one value, which the callee uses like the value itself.

A borrow argument names a place: something that holds a value and can be assigned or lent, such as a local (`frame`), a field (`f.body`) or an element (`xs[i]`). An `ro<T>` parameter also accepts a temporary, such as `1 + 2`. An `rw` parameter and a view of an array need a place, or a string literal for an `ro<u8>[n]` (`E-CALL-VIEW`). A view is not one value: passing `xs` where one is expected, as an `ro<T>`, `rw<T>` or `ro<dyn Trait>` parameter, a function value's parameter or `Dyn[Trait](...)`, is `E-TYPE-MISMATCH`, and an element, `xs[i]`, fits.

The extent of a view is the length in its type, `n` in `ro<u8>[n]`. An extent is a literal, a constant or an earlier immutable `usize` parameter (`E-EXTENT`), and `len(view)` reads it.

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

Two extents match only when they are the same name or the same literal. The checker never compares their values, so `len(a)` and `len(b)` are different extents even when both arrays hold 8 elements at run time. `len(v)` is the extent of `v`. A record's `Buf` field can carry the extent of an earlier `usize` field ([records and sums](language.md#records-and-sums)).

```cairn rejects E-TYPE-MISMATCH
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 = u32(bytes[0]);
fn main() -> i32 { let a = Buf[u8](8); let b = Buf[u8](8); return i32(checksum(len(a), b)); }
```

```text
Expected ro<u8>[len(a)]@host, got ro<u8>[len(b)]@host.
```

### Parts

A part `bytes[lo..hi]` is a view of the elements from index `lo` up to, but not including, `hi`, like a subslice in Rust. It goes wherever an array borrow is expected and carries one guard: `lo <= hi <= len`, and `hi - lo` equal to the callee's extent. A part of a part guards once per level. Bounds are names, literals, fields, elements and arithmetic, so bind a call to a name first (`E-CALL-SHAPE`).

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
  return sum;
}

fn main() -> i32 {
  let frame = "\x07\x00\x00\x00hello";
  let n = len(frame);
  if checksum(n - 4, frame[4..n]) != 532 { return 1; }     // the payload, after a header of four bytes
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

### Leaving out extents

A call may leave out its extent parameters. A `usize` parameter that names a later view's extent can only be that view's length, so the checker writes in `len` of the first view argument with that extent, or `hi - lo` for a part. Every later stage sees the call written out. Every other view with that extent must still match. A call passes all its extents or none (`E-ARITY`), and a call of an `extern` passes every argument.

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

### Records that lend a view

A record may name the view it lends. With `lends data[0..len];` in its body, the record written where a view is expected means its part `data[0..len]`, with that part's guard, effect row, lease and alias rules, and `for b in line` walks that part. The bounds are read at every use, so the length may change, and a length past the storage traps at the part's guard. `std.vec` declares one, so a `Vec` goes to a call whole.

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

The bounds are literals or `usize` fields of the record (`E-LENDS`). A running task holds a lease on what it was lent: until the caller waits for the task, nobody may write what the task reads or touch what it writes ([concurrency.md](concurrency.md#tasks-and-leases)). A task lent such a record leases the elements of the part, and the length field stays free.

```cairn rejects E-LENDS
struct Line { data:Buf[u8]; len:u32; lends data[0..len]; }
fn main() -> i32 { let line = Line(Buf[u8](8), 2); return 0; }
```

```text
A lent view's bounds are literals or usize fields of Line; len is u32.
```

### Aliasing

Two mutable borrows of one array let a write through one change what the other reads, which is how `memcpy` with overlapping arguments goes wrong in C. Borrows to read may overlap. A mutable borrow must overlap no other argument of the same call (`E-ALIAS`).

Distinct fields of one record are disjoint. Two parts of one array are disjoint only when one visibly ends where or before the other begins: at the same name, as `bytes[0..mid]` and `bytes[mid..n]` do; at literals in order, as `p[0..2]` and `p[4..6]` do; or through a chain of parts of the same call, as `d[0..a]`, `d[a..b]` and `d[b..n]` are.

```cairn rejects E-ALIAS
fn swap_ends(n:usize, a:rw<u8>[n], b:rw<u8>[n]) { swap(a[0], b[0]); }
fn main() -> i32 { let mut frame = Buf[u8](8); swap_ends(4, frame[0..4], frame[2..6]); return 0; }
```

```text
A mutable view cannot be passed to overlapping call arguments.
```

The C++ the compiler writes holds two versions of a function that takes views. The checked entry `cf_f` checks null, alignment, length and overlap, and a foreign caller, a test driver and `cf_main` reach it. A call from CAIRN reaches the body `ci_f` directly, because every view such a call can pass was already checked, and `E-ALIAS` has shown that the mutable ones overlap nothing. `--keep-guards` sends every call through the entry.

### Borrows stay in calls

A borrow cannot be stored, returned or bound to a local. Binding one to a local, or writing a part anywhere but as a call argument, is `E-VIEW-ALIAS`. Returning one is `E-ESCAPE`, and a record field that is a borrow is `E-RECORD-TYPE`. The one exception is `let frame = "text";`, whose storage is static.

```cairn rejects E-VIEW-ALIAS
fn main() -> i32 { let mut frame = Buf[u8](8); let payload = frame[4..8]; return 0; }
```

```text
A part xs[lo..hi] is a borrow: it exists only as a view argument of a call. Keep lo and hi in locals and write the part in each call that reads it.
```

## Owners and moves

An owner is a value that holds heap storage and releases it when it goes out of scope, like a `Vec` in Rust or a `unique_ptr` in C++. `Buf[T]` is the basic owner, and a record with an owner field is an owner too. Four forms of storage hold elements, all filled with zeros, because every type has a value whose bytes are all zero.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u32 {
  let mut sum:u32 = 0;
  for byte in bytes { sum = add_wrap(sum, u32(byte)); }
  return sum;
}

fn main() -> i32 {
  let n:usize = 4;
  buffer scratch:u8[n] = zeroed;      // heap storage for this block; its extent is n
  stack header:u8[4] = zeroed;        // fixed local storage, at most 65536 bytes per function
  let mut body = Buf[u8](n);          // an owner, which can move; its extent is len(body)
  let mut tag = Array[u8, 4]();       // a fixed array stored inline as a value
  body[0] = 9;
  tag[3] = 1;
  if checksum(n, scratch) + checksum(4, header) != 0 || checksum(len(body), body) != 9 { return 1; }
  if checksum(4, tag) != 1 { return 2; }
  return 0;
}
```

A `buffer` lives on the heap until its block ends, and its extent is the `n` it names. A `stack` array lives in the function's frame, and one function's `stack` declarations hold at most 65536 bytes together (`E-STACK-LIMIT`). `Buf[T](n)` makes an owner that can be moved, returned and stored. `Array[T, N]()` is a fixed array held inline, as a value.

### Moves

Owners are affine: each is used as a value at most once. Using one as a value (binding it, passing it by value, returning it, storing it in a field) moves it, and its name is dead afterwards (`E-MOVED`). An owner is released at scope exit, and `free` enters the effect row where that happens: at the end of the block that still holds it, at a `return`, or at a place a new value is assigned over. A function that hands an owner on carries neither `free` nor `alloc`.

An owner from outside a loop, a closure or a lane cannot be moved inside it (`E-MOVE-IN-LOOP`), because the body may run more than once.

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

### take and swap

An owner cannot be moved out of a place (`E-PARTIAL-MOVE`), since that would leave a hole in a record or an array. `take(place)` moves it out and leaves the zero value behind, and `swap(a, b)` exchanges two places. Growth is library code built from these, so the allocation of `std.vec` shows in every caller's row.

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

### Taking a record apart

`let Ring(slots, used) = r;` consumes `r` and binds every field in order, which is how an owner or a linear value leaves a record whole. Anything but one name, or `_`, per field is `E-UNPACK`. Only the module that declares a `linear` record may take it apart (`E-PRIVATE`), so code outside cannot end its protocol.

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

Some values stand for an obligation that must be met exactly once, such as giving back a resource that was acquired. A `linear struct` must be consumed exactly once on every path, where consuming it means moving it: passing it by value, returning it, storing it, or taking it apart with `let`. Leaving one unconsumed is `E-LINEAR-LEAK`, and consuming it on some paths only is `E-LINEAR-BRANCH`.

`defer call(...);` runs one visible call at every normal exit of its block, and counts as the consumption. An abort runs no cleanup.

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

## Effects

Every function has an effect row: the set of things it may do when it runs, such as allocate, write through a borrow, start a thread or abort on a failed guard. The row is the function's own effects joined with its callees' rows, where each callee's reads and writes of its parameters are renamed to the caller's arguments: if `fill` writes its parameter `out`, the call `fill(frame)` writes `frame`. A row says what may happen. It says nothing about what the function computes. `cairn doc` prints the row beside each function it documents, and the build receipt lists every function's row under `functions.<name>.effects`.

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
| `asm:ptx`, `asm:x86_64`, `asm:aarch64` | typed assembly for that target runs |
| `fence`, `barrier` | typed assembly declares that it orders memory, or waits for its block |
| `trap`, `diverge` | a guard may abort, the call graph has a cycle |
| `ffi_precondition` | the caller must supply live, initialized storage for a borrow |

A signature may declare a ceiling: the most its row may hold. `pure` and `effects(read:x, trap)` are ceilings, and a function whose row goes past its ceiling is `E-EFFECT-CEILING`. `pure` still allows `trap`, `diverge`, `local_read`, `local_write`, `stack_storage`, `zero_init`, `ffi_precondition` and reads of what the function was lent.

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

C++ leaves the order of an expression's operands open: in `f() + g()`, either call may run first. CAIRN lowers to C++, so the checker refuses an operand whose effect would make the result depend on that order.

A call that writes through a borrow or allocates cannot sit beside another operand (`E-EFFECT-ORDER`). Bind it to a name first, so its cost is a statement of its own. The refusal names the call and what it writes.

```cairn rejects E-EFFECT-ORDER
fn fill(n:usize, out:rw<u8>[n], value:u8) -> usize { for i in 0..n { out[i] = value; } return n; }
fn main() -> i32 { let mut frame = Buf[u8](4); let done = fill(len(frame), frame, 1) + len(frame); return 0; }
```

```text
fill writes frame, so an operand beside it could run before or after it: bind it to its own statement first (let v = fill(len(frame), frame, 1);) and use v here.
```

A conversion or a unary operator has one operand and nothing beside it, so that operand may be such a call. The call runs first, and then the conversion checks its result.

```cairn
struct Input { at:u64; }
fn next(inp:rw<Input>) -> u64 { inp.at += 1; return inp.at; }
fn main() -> i32 {
  let mut inp = Input(0);
  let n = usize(next(inp));                       // a call that writes, as a conversion's operand
  let m = -i64(next(inp));
  if n != 1 || m != -2 { return 1; }
  return 0;
}
```

A call the outside world can observe (I/O, a foreign call, the machine, atomics, locks, tasks, parallel regions, device memory and transfers, a function value) may not sit beside another call, or beside an operand that may abort. A nested call also may not move, take or, as a closure, write a place that another operand names. `&&` and `||` are sequenced, and a call runs after its own arguments, so these rules leave both alone.

```cairn rejects E-EFFECT-ORDER
extern fn putchar(c:i32) -> i32 effects(io);
fn say(c:i32) -> i32 { unsafe { return putchar(c); } }
fn main() -> i32 { return say(65) + say(66) - 131; }
```

```text
Bind this call first: it can be observed from outside, and the operand beside it could run, or abort, before or after it.
```

## extern and unsafe

An `extern` declaration names a C symbol, gives it a CAIRN signature, and states the effects its body may have. `extern "close" fn close_fd(...)` binds a symbol under another name. The checker cannot see the body, so the effects are mandatory (`E-EXTERN-EFFECTS`) and are trusted as written. An extern's extent may name a later parameter, as C puts a pointer before its length.

Foreign calls, `mmio_read`, `mmio_write` and `asm` are legal only inside `unsafe { }` (`E-UNSAFE`), and the build receipt counts each function's `unsafe` blocks. The guards cannot establish where foreign storage came from, so a caller must supply live, initialized storage for each borrow. That obligation is the `ffi_precondition` in the row.

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

### Typed assembly

Typed assembly states what the string form cannot: where the instructions run, the values they take and give back, and what they do to memory. The checker checks everything around the instructions and trusts the effects they declare.

```cairn
fn high(a:u64, b:u64) -> u64 {
  unsafe {
    asm x86_64 "movq %1, %%rax; mulq %2; movq %%rdx, %0" (out hi:u64, a, b) clobbers(rax, rdx);
    return hi;
  }
}

fn reversed(n:usize, out:rw<u32>[n]@device, xs:ro<u32>[n]@device) {
  parallel i in n {
    unsafe {
      asm ptx sm_75 "brev.b32 %0, %1;" (out r:u32, xs[i]);
      out[i] = r;
    }
  }
}
```

The target is `ptx`, `x86_64` or `aarch64`. PTX runs only in device code, a device lane or a `kernel fn`, and names the GPU architecture it needs: `sm_75` runs on sm_75 and later, `sm_90a` only on sm_90a, and `sm_100f` on the sm_100 family from sm_100 on. A build for a [device target](tools.md#the-device-target) that does not meet the need is refused, and an nvcc run for another architecture stops at the statement. Host assembly runs only in host code. The checker accepts `x86_64` and `aarch64` assembly on a machine of either family, and a build on a host of the other family refuses it. PTX outside device code, host assembly in device code, and a build for a host family or a device target that the assembly does not match are each `E-ASM-TARGET`.

Operands are numbered as written, outputs first. The template names every one of them, `%0`, `%1`, with `%%` for a literal percent sign and, on a host, one modifier letter, as in `%k0` (`E-ASM-OPERANDS`). The type chooses the register class. On x86-64 and AArch64 an integer takes a general register and a float a vector register. In PTX, `u16` and `i16` take `h`, integers of 32 bits `r`, integers of 64 bits and `usize` `l`, `f32` `f` and `f64` `d`. A `bool`, a storage float, a record, or a `u8` in PTX has no class (`E-ASM-CONSTRAINT`). `out name:T` binds a fresh immutable local after the statement, and `out name:T = e` starts it at `e`. `clobbers(rax, rdx)` names the host registers the instructions write besides their outputs (`E-ASM-CLOBBER`).

A view or local array given as an input passes its address. The statement then declares `read:x` or `write:x` for it (`E-ASM-EFFECT`), which lends it for the statement as a call would. So a task's lease refuses it (`E-LEASED`), and an `ro` view refuses `write:x` (`E-WRITE-LEASE`). `effects(...)` may also name `fence`, `barrier`, `atomic`, `io` and `mmio`. Any declared effect makes the lowering `volatile` with a `"memory"` clobber, and so does a statement with no outputs. `asm volatile` keeps a statement whose outputs depend on more than its inputs, such as a clock read, from being merged or moved.

The row gets `asm:TARGET` and the declared effects, and the build receipt lists each statement under `assembly` as `declared-not-checked`. In device code, typed PTX may declare reads, writes and `fence`, and nothing else (`E-ASM-LANE`). An address reaches the whole array, so the lane rule ([concurrency.md](concurrency.md#parallel-regions)) treats it as an access to the whole array: through one, a lane may read what no lane writes, and write nothing outside itself (`E-PARALLEL-RACE`).

```cairn rejects E-ASM-LANE
fn settle(n:usize, out:rw<u32>[n]@device) {
  parallel i in n {
    unsafe { asm ptx sm_75 "bar.sync 0;" effects(barrier); }
    out[i] = 0;
  }
}
```

```text
Typed PTX in device code may declare reads, writes and fence; barrier is not lane-safe.
```

## Layouts

A tile is a small block of an array in two dimensions, and the participants of a tile are whoever handles its elements, such as the threads of a block. A layout says where each element of a tile is stored, and a spread says which participant holds it. `layout NAME = ...;` declares one at the top of a module, and the checker evaluates it there. The compiler works out a layout while it compiles the program, and no layout exists as a value at run time.

```cairn
layout TILE = pad(rows(32, 32), 1);                 // 32 x 32, rows 33 elements apart
layout LOAD = spread(TILE, 8, 32, 1, 1);            // 256 participants, 4 elements each
layout STORE = spread(transpose(TILE), 8, 32, 1, 1);
const CELLS:usize = TILE.cosize();                  // 1055

fn through(out:rw<f32>[1024], x:ro<f32>[1024]) {
  buffer s:f32[CELLS] = zeroed;
  for t in 0..LOAD.participants() {
    for v in 0..LOAD.values() { s[LOAD.at(t, v)] = x[LOAD.row(t, v) * 32 + LOAD.col(t, v)]; }
  }
  for t in 0..STORE.participants() {                // out is x transposed, whatever TILE's storage
    for v in 0..STORE.values() { out[STORE.row(t, v) * 32 + STORE.col(t, v)] = s[STORE.at(t, v)]; }
  }
}
```

The storage layouts are `rows(R, C)`, `cols(R, C)` and `strided(R, C, SR, SC)`. `pad(L, P)` adds `P` to the larger stride. `swizzle(L, B, M, S)` flips bits `M` to `M + B` of each offset with the `B` bits `S` places above them, as CuTe's `Swizzle<B, M, S>` does. `transpose(L)` swaps the two dimensions, and `tile(L, TR, TC)` sees `L` as a grid of `TR x TC` tiles, with coordinates `(i, j, r, c)`.

`spread(L, TR, TC, VR, VC)` gives `TR x TC` participants, numbered row by row, a `VR x VC` block of `L` each, and repeats that pattern down and across `L` as many whole times as fit. A participant's values run across its block, then down it, then over the repeats. `inverse(D)` answers which participant holds an element: over the grid of `D`'s pairs, `INV.row(r, c)` is the participant holding element `(r, c)` and `INV.col(r, c)` its value. It exists where every mode of `D`, one pair of extent and stride within a dimension as in CuTe, moves one coordinate and counts it in mixed radix, and `inverse(L)` of a compact storage layout takes an offset back to its element.

In code, `L.at(r, c)` is an element's offset, `D.row(t, v)` and `D.col(t, v)` are the coordinates of participant `t`'s value `v`, and `D.at(t, v)` is that element's offset in `D`'s tile. Each argument is checked against its extent and traps outside it. `size()`, `cosize()`, `extent(k)`, `participants()` and `values()` are constants.

Every declaration is held to two rules. A storage layout gives each element its own offset, and a spread gives each element of its tile exactly one holder, so writes through either never collide and never miss an element. A spread that leaves an element to nobody is `E-LAYOUT-GAP`. Two elements at one offset, or one element with two holders, is `E-LAYOUT-OVERLAP`.

```cairn rejects E-LAYOUT-GAP
layout TILE = rows(32, 32);
layout LOAD = spread(TILE, 7, 32, 1, 1);            // 224 participants leave rows 28 to 31 to nobody
```

```text
No participant holds element (28, 0) of the 32 x 32 tile; a spread must cover it: 224 participants with 4 values each hold 896 of its 1024 elements.
```

```cairn rejects E-LAYOUT-OVERLAP
layout FRAGMENTS = rows(4, 4);
layout WARPS = spread(FRAGMENTS, 2, 4, 2, 2);       // 8 participants of 4 fragments each, for 16 fragments
```

```text
Element (0, 0) of the 4 x 4 tile is held by participant 0 (value 0) and participant 2 (value 0); a spread must give each element one holder.
```

`L.at(D.row(t, v), D.col(t, v))` reads `D`'s tile through another layout of the same shape. A spread over another shape is `E-LAYOUT-CONSUMER`, and anything else malformed is `E-LAYOUT`.

```cairn rejects E-LAYOUT-CONSUMER
layout TILE = rows(32, 32);
layout WIDE = rows(64, 32);
layout LOAD = spread(TILE, 8, 32, 1, 1);
fn at(t:usize, v:usize) -> usize = WIDE.at(LOAD.row(t, v), LOAD.col(t, v));
```

```text
LOAD spreads a 32 x 32 tile, and WIDE lays out 64 x 32: its coordinates name no element of WIDE.
```

### What the compiler reports about a layout

The build receipt lists each layout under `layouts`. For a spread over at most 4096 elements, `cairn explain` also names the participant that holds each element. For a spread, both say whether it covers its tile exactly once, and the widest run of adjacent values that every participant's values fall into (`runs`, by element size: what one access of at most 16 bytes moves). They also say how many ways a warp's accesses split over the 32 banks of shared memory (`bank_ways`). Reading the tile above a column at a time costs 32 ways when it is stored by rows, and 1 way when it is padded or swizzled. Between two spreads over one tile, or over a tile and its transpose, `cairn explain` says what moving values from one to the other needs (`conversions`): `none`, `registers`, a `shuffle` within each warp, or `shared`, which is a store, a barrier and a load.

Every answer comes from enumerating the layout, so one layout holds at most 262,144 elements, or pairs of participant and value, and the SMT model answers `unknown` for a function that uses one. Every offset a storage layout places is below 2^63 - 1, the most elements an array holds, and a swizzle reads and flips bits 0 to 62 (`B + M + S` at most 63). The lowered arithmetic on 64 bits therefore never wraps, and gives what the enumeration gives (`E-LAYOUT`).

A [plan](concurrency.md#plans)'s `vector` and `stage` ask the same questions of a device region's lanes: how many adjacent elements one access moves, and whether a block's tile holds every element its lanes read. In a [cooperative region](devices.md#cooperative-regions) the phase rule, which keeps two threads of a block from touching one shared element between two barriers where either writes, runs each thread's `L.at(...)` with its own numbers, so a tile written through one layout and read through its transpose is checked element by element. `examples/tensor/transpose.cairn` does that through a tile stored by rows, a padded tile and a swizzled tile.

## Foreign implementations

Existing C++ and CUDA can be an [implementation](abstractions.md#implementations) of a CAIRN reference without being rewritten. The project vendors the source and names in its manifest the symbols it defines. An `extern` gives each symbol its CAIRN signature and effects, and an implementation's body is the foreign call.

```toml
[foreign]
"vendor/histogram.cpp" = ["histogram_u32_interleaved"]
"vendor/stencil.cu" = ["stencil_1d_tiled"]
```

```cairn
fn blend3(l:f32, c:f32, r:f32) -> f32 = 0.25 * l + 0.5 * c + 0.25 * r;

fn stencil_1d(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device)
  effects(read:x, write:out, trap, ffi_precondition, par:device, ffi:stencil_1d_tiled) {
  parallel i in n {
    if i > 0 && i + 1 < n { out[i] = blend3(x[i - 1], x[i], x[i + 1]); } else { out[i] = x[i]; }
  }
}

extern "stencil_1d_tiled" fn stencil_launch(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) launch(n, 256)
  effects();

fn stencil_tiled(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) implements stencil_1d {
  unsafe { stencil_launch(n, out, x); }
}

plan stencil_1d use stencil_tiled;
```

`launch(threads, block)` makes an extern a CUDA `__global__` kernel. A host call launches it over `threads` indices, `block` threads to a block, on the calling thread's stream, and returns once that stream has run it, as a device `parallel` region does. The thread count is a `usize` parameter or a literal. A launched kernel returns nothing, takes scalars and `@device` or `@unified` views, and runs whole warps, 32 to 1024 threads to a block (`E-LAUNCH`, `E-PLACEMENT`). Its row adds `par:device` and `trap`, so no lane can call it (`E-PARALLEL-CALL`). The kernel is trusted to guard its own indices and to touch only the views it is given, as its declared effects say.

An implementation stays inside its reference's ceiling, so the reference names the foreign symbol its implementations may reach (`E-IMPL-EFFECT` otherwise). The build compiles each vendored source as it is, with the program's own flags and device target and the runtime headers on the include path. It asserts that each symbol has the C++ types its extern passes, so a definition of other types does not build. A C++ symbol has C linkage, and a kernel keeps its C++ name. A source that defines nothing the program declares, such as a copy of `bench/gpu/parallel_gpu.cu`, is compiled and inspected and never linked. The build receipt pins every source by its sha256.

`cairn foreign` says what one foreign implementation has, each claim apart: its declared contract, which is trusted and never checked; whether it is native-built; what ptxas reports of a CUDA source's kernels; and validation against its reference with the vendored objects linked, under clang++ and g++. An implementation that runs device code has its device tests built and never run, since device code runs only under `make gpu`. [tools.md](tools.md#cairn-foreign) shows the record.

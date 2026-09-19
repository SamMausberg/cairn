# The CAIRN standard library

Twelve small modules, written in CAIRN, shipped inside the package and linked on demand:
`import std.map (Map);` brings in `map.insert(...)` and the bare name `Map`. Nothing is
downloaded and nothing is implicit — a module you do not import is not in your program, and
every module you do import is compiled whole (there is no dead-code elimination yet).

Three habits explain most of the API shape:

* **A lookup answers with an index, never a borrow.** Borrows are second class, so `map.find`
  and `arena.find` return `Option[usize]` and the caller reads `m.vals[slot]` itself. That read
  is a place, so it can be passed on, borrowed, `take`n or assigned, which is more than a
  returned reference could do.
* **Every array parameter carries its length.** `f(n, xs)` where the callee says `xs:ro<u8>[n]`;
  a whole view or buffer matches by name identity, and a part `v.data[lo..hi]` matches whatever
  `usize` expression you pass as the length, at the cost of one bounds guard:
  `io.write(f, n - done, data[done..n])`.
* **Costs are in the signature.** A function that allocates says `alloc` in its effect row and
  so does everyone who calls it; a function that touches a file says `io` and `ffi:write`. The
  row is in the build receipt (`cairn check`/`build` output, `functions.<name>.effects`).

| module | what it is for | allocates |
| --- | --- | --- |
| `std.core` | `Option`, `Result`, the `Ord`/`Eq`/`Hash` traits | no |
| `std.mem` | `fill`, `copy`, `equal` over views | no |
| `std.vec` | the growable owner `Vec[T]` | yes |
| `std.text` | integers to and from bytes, comparison, search, hashing | only `push_u64` |
| `std.sort` | in-place heapsort and binary search | no |
| `std.io` | files, standard streams, a monotonic clock | `read_file` only |
| `std.net` | blocking TCP | no |
| `std.map` | open-addressed `Map[K, V]` | yes |
| `std.arena` | generational `Arena[T]` with stale-handle detection | yes |
| `std.sys` | the C library, declared once | no |

---

## std.core

`Option[T]`, `Result[T, E]` and the three traits everything else dispatches on.

| declaration | meaning |
| --- | --- |
| `enum Option[T] { Some(T); None; }` | absence, not a null |
| `enum Result[T, E] { Ok(T); Err(E); }` | the only failure carrier; `try` propagates it |
| `trait Ord { fn less(a:ro<Self>, b:ro<Self>) -> bool; }` | strict weak order |
| `trait Eq { fn same(a:ro<Self>, b:ro<Self>) -> bool; }` | equivalence |
| `trait Hash { fn hash(value:ro<Self>) -> u64; }` | Fibonacci hashing for the integer types |

`Ord`, `Eq` and `Hash` are implemented for the unsigned integers plus `i32`/`i64` (`Ord` only).
Implement them for your own types where you need them:

```cairn
impl Ord for Boxed { fn less(a:ro<Boxed>, b:ro<Boxed>) -> bool = a.rank < b.rank; }
```

**Sharp edge.** A nullary variant of a sum that carries an owner must be written `Option.None()`
with parentheses. `Option.None` on its own is read as a place, and a place holding an owner
cannot be moved (`E-PARTIAL-MOVE`).

## std.mem

| function | cost |
| --- | --- |
| `fill[T](n, dst:rw<T>[n], value:T)` | n assignments |
| `copy[T](n, dst:rw<T>[n], src:ro<T>[n])` | n assignments |
| `equal[T:Eq](n, a:ro<T>[n], m, b:ro<T>[m]) -> bool` | stops at the first difference |

All three instantiate only for copyable elements: an owner would have to be moved out of a
place. `copy` takes two views with the same extent name; overlapping parts of one array are
rejected at the call site (`E-ALIAS`) and the entry guard checks it numerically as well.

```cairn
buffer a:u8[8] = zeroed;
buffer b:u8[8] = zeroed;
mem.fill(len(a), a, 7);
mem.copy(len(b), b, a);
```

## std.vec

`struct Vec[T] { data:Buf[T]; len:usize; }` — a growable owner. `data` and `len` are public,
because passing `v.data[0..n]` to a view parameter is the normal way to hand a Vec to anything.

| function | notes |
| --- | --- |
| `new[T]() -> Vec[T]`, `with_capacity[T](capacity) -> Vec[T]` | `new` allocates nothing until the first push |
| `capacity[T](v:ro<Vec[T]>) -> usize` | `len(v.data)` |
| `reserve[T](v:rw<Vec[T]>, wanted)` | doubles; elements move by `swap`, never by copy |
| `push[T](v:rw<Vec[T]>, item:T)` | amortized O(1), `alloc` in the row |
| `pop[T](v:rw<Vec[T]>) -> Option[T]` | moves the element out |
| `get[T](v:ro<Vec[T]>, i) -> T` / `set[T](v:rw<Vec[T]>, i, item)` | copyable elements only; an out-of-range index traps |
| `clear[T]`, `truncate[T](v, count)` | release elements now rather than at scope exit |
| `extend_from[T](v:rw<Vec[T]>, n, src:ro<T>[n])` | append a view of copyable elements |

```cairn
let mut line = vec.new[u8]();
text.push_u64(line, 42);
line.extend_from(5, "world");
let n = line.len;
io.println(n, line.data[0..n]);
```

## std.text

Numbers to bytes and back, plus the searching a line protocol needs. No substring can be
returned — a borrow is second class — so every search answers with an index and the caller
passes `s[lo..hi]` onwards.

| function | notes |
| --- | --- |
| `parse_u64(n, s:ro<u8>[n]) -> Result[u64, ParseError]` | `ParseError` is `Empty`, `Invalid(at)` or `Overflow(at)` |
| `write_u64(n, out:rw<u8>[n], value) -> usize` | bytes written, 0 when it does not fit |
| `write_hex(n, out:rw<u8>[n], value, width) -> usize` | exactly `width` lowercase digits |
| `push_u64(v:rw<Vec[u8]>, value)` | the same, appended to a Vec |
| `compare(n, a, m, b) -> i32` / `equal(n, a, m, b) -> bool` | lexicographic, shorter first |
| `find_byte(n, s, byte, from) -> Option[usize]` | scan from an index |
| `find(n, s, m, needle) -> Option[usize]` | naive search; needles are short |
| `hash_bytes(n, s) -> u64` | FNV-1a, no table |

```cairn
match text.find_byte(n, line, ' ', 0) {
  Option.Some(at) => { let word = at; let rest = n - at - 1; }  // line[0..word], line[at+1..n]
  Option.None => {}
}
```

## std.sort

Heapsort: the one O(n log n) order that needs no recursion (no `diverge` from a call cycle), no
scratch buffer (no `alloc`) and moves elements only with `swap`, so it sorts owners too.
Equal elements are not kept in order.

| function | notes |
| --- | --- |
| `sort_by[T](n, xs:rw<T>[n], less:ro<fn(ro<T>, ro<T>) -> bool>)` | closure or declared function |
| `sort[T:Ord](n, xs:rw<T>[n])` | `sort_by` with the trait's `less` |
| `search[T:Ord](n, xs:ro<T>[n], key:ro<T>) -> Option[usize]` | lower bound on a sorted view |

```cairn
sort.sort(len(xs), xs);
sort.sort_by(len(xs), xs, |a:ro<u64>, b:ro<u64>| -> bool { return a > b; });
```

The closure is a borrowed callable: it captures by reference, exists only as that argument and
cannot allocate or escape. The caller's row gains `indirect_call`.

## std.io

A `File` is `linear`: the type system, not a convention, is what closes a descriptor. Errors are
errno inside an `IoError`.

| function | notes |
| --- | --- |
| `open(n, path:ro<u8>[n], flags) -> Result[File, IoError]` | `path` must end in a NUL byte |
| `close(f:File)` | consumes the File; it cannot report a status (see below) |
| `read(f, n, into:rw<u8>[n]) -> Result[usize, IoError]` | one syscall; 0 means end of file |
| `read_full(f, n, into) -> Result[usize, IoError]` | loops; a short count means the file ended |
| `write(f, n, data) -> Result[usize, IoError]` | loops; a short write is an error here |
| `seek(f, offset:i64, whence) -> Result[u64, IoError]`, `size(f)` | `SET`, `CUR`, `END` |
| `sync(f)`, `truncate(f, length)` | `Ok(0)` on success |
| `remove(n, path)`, `rename(n, from, m, to)` | both paths NUL terminated |
| `read_file(n, path) -> Result[Vec[u8], IoError]` | one open, one size, one pass |
| `print`, `println`, `eprintln`, `newline`, `print_u64` | best effort, no Result |
| `monotonic_ns() -> u64` | CLOCK_MONOTONIC |

Flags: `READ`, `WRITE`, `APPEND`, `TRUNCATE`. Every call is one syscall; nothing buffers.

```cairn
fn save(n:usize, path:ro<u8>[n]) -> Result[usize, io.IoError] {
  let f = try io.open(n, path, io.TRUNCATE);
  defer io.close(f);                       // runs on every exit, including the ones `try` takes
  let wrote = try io.write(f, 6, "alpha\n");
  let flushed = try io.sync(f);
  return Result.Ok(wrote);
}
```

`defer io.close(f)` is the idiom, not a nicety: `try` refuses to leave a function while a linear
value is unconsumed, so a File that is not deferred cannot be used with `try` at all.

**Why `close` returns nothing.** Consuming a linear value requires a function that never reaches
a `return` statement, because `return` demands that every linear value already be consumed. A
closing function that reported its status would need one. Report through a borrow if you need
the status.

## std.net

Blocking TCP. A `Socket` is linear for the same reason a File is. There is no thread, poll or
timeout in the language yet, so one connection is served at a time.

| function | notes |
| --- | --- |
| `listen_on(ip:ro<u8>[4], port:u16, backlog) -> Result[Socket, IoError]` | sets `SO_REUSEADDR` |
| `accept(s:ro<Socket>) -> Result[Socket, IoError]` | blocks |
| `connect_to(ip:ro<u8>[4], port:u16) -> Result[Socket, IoError]` | blocks |
| `send(s, n, data) -> Result[usize, IoError]` | loops until the whole view is gone |
| `recv(s, n, into) -> Result[usize, IoError]` | one syscall; 0 means the peer closed |
| `close(s:Socket)` | consumes the Socket |

The address is four bytes, so a string literal is a perfectly good IPv4 address:

```cairn
let server = try net.listen_on("\x7f\x00\x00\x01", 39800, 16);
defer net.close(server);
let client = try net.accept(server);
defer net.close(client);
```

Every failure path inside the module closes the raw descriptor before a `Socket` exists, and
reads errno first, because `close` would overwrite it.

## std.map

`Map[K, V]`, open addressing with linear probing and tombstones. The map owns its keys and
values, so `Map[u64, Vec[u8]]` is ordinary.

| function | notes |
| --- | --- |
| `new[K, V]() -> Map[K, V]` | no allocation until the first insert |
| `insert[K:Hash + Eq, V](m:rw<Map[K,V]>, key:K, value:V)` | moves both in; replacing releases the old value |
| `find[K:Hash + Eq, V](m:ro<Map[K,V]>, key:ro<K>) -> Option[usize]` | the slot, not the value |
| `remove[K:Hash + Eq, V](m:rw<Map[K,V]>, key:ro<K>) -> Option[V]` | moves the value out |
| `count`, `slots`, `live(m, slot)` | iteration is `for slot in 0..m.slots()` |

Expected O(1); insert rehashes past three quarters full, so `alloc`, `free` and `zero_init` are
in every caller's row. `K` must implement both `Hash` and `Eq`, and both bounds are checked when
the instance is made.

```cairn
let mut m = map.new[u64, Vec[u8]]();
m.insert(7, payload);
match m.find(key) {
  Option.Some(slot) => { let n = m.vals[slot].len; io.println(n, m.vals[slot].data[0..n]); }
  Option.None => {}
}
for slot in 0..m.slots() { if m.live(slot) { /* m.keys[slot], m.vals[slot] */ } }
```

## std.arena

The language's answer to graphs and cycles. Values live in one owned array and are named by a
copyable `Handle { slot; generation }`. Removing a value bumps its slot's generation, so every
handle to it stops resolving: a use after free becomes a `None`, not a dangling pointer.

| function | notes |
| --- | --- |
| `new[T]() -> Arena[T]` | |
| `insert[T](a:rw<Arena[T]>, value:T) -> Handle` | amortized O(1), reuses removed slots |
| `find[T](a:ro<Arena[T]>, h:Handle) -> Option[usize]` | None when the handle is stale |
| `remove[T](a:rw<Arena[T]>, h:Handle) -> Option[T]` | moves the value out, invalidates the handle |
| `count`, `slots`, `alive(a, slot)`, `handle(a, slot)` | iteration by index |

```cairn
struct Node { name:u64; edges:Vec[Handle]; }   // a cycle of handles, not of owners
let mut g = arena.new[Node]();
let no_edges = vec.new[Handle]();              // a call that allocates cannot be nested
let a = g.insert(Node(1, no_edges));
match g.find(a) { Option.Some(slot) => { g.items[slot].edges.push(a); } Option.None => {} }
```

## std.sys

Every libc binding the library uses, declared exactly once: `read`, `write`, `open`, `close`,
`lseek`, `fsync`, `ftruncate`, `unlink`, `rename`, `clock_gettime`, `getpid`, the BSD socket
calls, `__errno_location`, and `errno()` on top of it. Prefer `std.io` and `std.net`; this
module exists because an extern's C symbol *is* its CAIRN name, so two modules cannot both
declare `close`.

Two conventions are worth copying into your own bindings:

```cairn
// An extent may name a later parameter, which is how C orders (pointer, length).
pub extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);
// A C string is a pointer, not a view. Declare one element and pass a part of your storage:
pub extern fn open(path:ro<u8>[1], flags:i32, mode:u32) -> i32 effects(io);
//   sys.open(path[0..1], flags, 420)  -- with the NUL byte inside `path`
```

`errno` is a macro over a thread-local `int*`, which no CAIRN signature can return, so
`std.sys.errno` declares `__errno_location` as a `usize` and does one `mmio_read[u32]` of that
address inside `unsafe`. It is the only place in the library that needs the `mmio` effect.

---

## std.wire

One recipe, and the first piece of the compiler to become library code. `derive wire for Packet;`
(no import needed: a bare recipe name falls back to `std.<name>`) generates `encode_Packet(out,
value)`, `decode_Packet(input)` and `wire_size_Packet()` for a record of fixed-width unsigned
fields: little-endian, declaration order, no padding, extents known statically. It infers no
framing, authentication or validation. Read `src/cairn/std/wire.cairn` as the worked example of
`each`, `where`, `fold` and `$` splices; its output is pinned byte for byte against the closed
generator it replaced.

## std.derived

Three recipes that generate trait implementations: `derive eq for P;` (field-wise `same`), `derive ord for P;` (lexicographic `less`, declaration order) and `derive hash for P;` (FNV-1a over the fields' hashes). Each is an ordinary `impl` of the `std.core` trait for the record, so the record then satisfies `[T:Ord]` for `std.sort` or `[K:Hash + Eq + affine]` for `std.map`. A field whose type lacks the trait is reported as such (`bool does not implement std.core.Ord`); `std.core` itself implements `Ord` and `Eq` for every integer type, `Eq` for `bool`, and `Hash` for the unsigned integers, with one bounded impl per class.

## Known sharp edges

* **A linear value inside a record leaves by taking the record apart.** `take` cannot forge the zero
  a `File` would leave behind, so a wrapper is consumed whole: `let Conn(sock, sent) = c;` binds
  every field and `c` is gone. A `File` or a `Socket` may therefore live inside your own state.
* **One failure family per function.** `try` requires the enclosing function to return the same
  sum family with the same failure payload, so everything fallible here is
  `Result[_, IoError]`; the void-ish ones answer `Ok(0)`.
* **A call that allocates or writes through a borrow cannot be a nested operand.** Bind it
  first: `let empty = vec.new[Handle](); a.insert(Node(1, empty));`.
* **`max(8, n)` does not compile when `n` is a `usize`** — the literal does not adapt to its
  peer for `min`/`max`. Write `max(n, 8)`.

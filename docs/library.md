# The standard library

The standard library is twenty modules, written in CAIRN and shipped inside the package. This page shows what each module is for, with a complete program for each, so that you can pick the module you need and see how its API is meant to be used. [std_api.md](std_api.md) lists every signature and effect row, generated from the module sources.

## Using a module

`import std.map (Map);` brings in the module's functions under its short name, as `map.insert(...)`, and the bare name `Map`. `import std.sort as sort;` names the module without bringing in any type. A module you do not import is not in your program. Every module ships with the compiler, so nothing is fetched.

Three habits run through the whole API. A lookup returns an index, never a borrow: `map.find` returns `Option[usize]`, and you read `m.vals[slot]` yourself. A position you keep while the collection changes is a handle that is checked when you use it (`arena.Handle`, `map.Slot`). A function that allocates has `alloc` in its effect row, the list of what it may do that a caller can observe ([memory.md](memory.md#effects)), and so does every function that calls it.

A guard is a check the compiled code makes before an operation, such as an index bound. When a guard fails, the program traps: it aborts at once, without running cleanup. Where this page says a call traps, or names a guard failure, the program ends this way.

| Module | What it is for | Allocates |
| --- | --- | --- |
| `std.core` | `Option`, `Result`, and the traits `Ord`, `Eq` and `Hash` | no |
| `std.vec` | `Vec[T]`, a growable array that owns its elements | yes |
| `std.text` | integers to and from bytes, comparison, search, hashing | only `push_u64`, `push_i64` |
| `std.fmt` | integers, hex, padding and floats in exact fixed point, appended to a `Vec[u8]` | yes |
| `std.io` | open files, standard streams, a monotonic clock | only `read_file`, `read_to_end` |
| `std.fs` | files by path, with no NUL to write | only `read` |
| `std.env` | the program's arguments and environment | yes |
| `std.time` | a monotonic clock, the date, sleeping | no |
| `std.map` | `Map[K, V]`, a hash table with open addressing | yes |
| `std.derived` | `derive eq`, `derive ord`, `derive hash` | no |
| `std.sort` | a heapsort in place, a stable radix sort of unsigned keys, and binary search | no |
| `std.arena` | `Arena[T]`: values named by handles that notice when their value was removed | yes |
| `std.mem` | `fill`, `copy`, `equal` over views | no |
| `std.wire` | `derive wire`: records of unsigned fields to bytes and back | no |
| `std.math` | the C math library on `f64`, whose last bit varies between machines | no |
| `std.zlib` | the system zlib: compressing a whole view, CRC-32, Adler-32 | only `compress` |
| `std.image` | RGBA images, PNG and PPM files | yes |
| `std.draw` | shapes, blending, text, a record of what was drawn, and frame capture, on the CPU | only `layout`, `mark`, `json`, `capture` |
| `std.net` | blocking TCP | only `listen_on`, `accept`, `connect_to` |
| `std.sys` | the C library, declared once | no |

## std.core

`std.core` holds the types and traits every other module shares. `Option[T]` is a value or nothing, and CAIRN has no null. `Result[T, E]` is a value or a failure, and it is the only way a function reports one. The three traits are `Ord`, whose `less` is a strict weak order, `Eq`, whose `same` is an equivalence, and `Hash`, whose `hash` returns a `u64`. The compiler does not check those laws; it checks that every implementation of a trait member is `pure`, a ceiling on its effect row that allows reads and traps but no allocation, no I/O and no write through a borrow.

```cairn
import std.core (Option, Result, Ord);

struct Frame { stream:u32; bytes:u32; }
impl Ord for Frame { fn less(a:ro<Frame>, b:ro<Frame>) -> bool pure = a.bytes < b.bytes; }

fn widest(n:usize, frames:ro<Frame>[n]) -> Option[usize] {
  if n == 0 { return None; }
  let mut best:usize = 0;
  for i in 1..n { if less(frames[best], frames[i]) { best = i; } }
  return Some(best);
}

fn under(n:usize, frames:ro<Frame>[n], limit:u32) -> Result[u32, u32] {
  match widest(n, frames) {
    Some(at) => {
      if frames[at].bytes > limit { return Err(frames[at].stream); }
      return Ok(frames[at].bytes);
    }
    None => return Ok(0);
  }
}

fn headroom(n:usize, frames:ro<Frame>[n]) -> Result[u32, u32] {
  let used = try under(n, frames, 1500);   // on failure this returns the stream number at once
  return Ok(1500 - used);
}

fn main() -> i32 {
  stack frames:Frame[2] = zeroed;
  frames[0] = Frame(1, 512);
  frames[1] = Frame(2, 1400);
  match headroom(2, frames) { Ok(spare) => { if spare != 100 { return 1; } } Err(_) => return 2; }
  frames[1] = Frame(2, 9000);
  match headroom(2, frames) { Ok(_) => return 3; Err(s) => { if s != 2 { return 4; } } }
  return 0;
}
```

`std.core` implements `Ord` and `Eq` for every integer type, `Eq` for `bool`, and `Hash` for the unsigned integers. For a type of your own, write an `impl` or generate one with [std.derived](#stdderived). Every trait member is `pure`, so an implementation of `less` that prints is refused with `E-EFFECT-CEILING`.

## std.vec

`Vec[T]` is a growable array that owns its elements. Its declaration is `struct Vec[T:affine] { data:Buf[T]; len:usize; lends data[0..len]; }`. Where a function expects a view, a `Vec` passes its first `len` elements, as [memory.md](memory.md#arrays-views-and-parts) explains, and a `for` loop walks them.

```cairn
import std.vec as vec;
import std.text as text;

fn main() -> i32 {
  let mut line = vec.new[u8]();              // nothing is allocated until the first push
  for i in 1..4 {
    text.push_u64(line, u64(i) * 11);
    vec.push(line, 32);                      // a space
  }
  vec.truncate(line, line.len - 1);          // release the trailing space now, before the scope ends
  if line.len != 8 { return 1; }             // "11 22 33"
  if line.data[0] != 49 || line.data[7] != 51 { return 2; }
  if vec.capacity(line) < line.len { return 3; }
  return 0;
}
```

When a `Vec` runs out of room, its capacity doubles and its elements move with `swap`, so an owner is never copied and `push` costs amortized O(1). The move is one pass over two views of the same extent, so no index in it is guarded, and `extend_from` copies a view into one part. `pop` and `remove` move an element out as an `Option[T]`. `get` takes only copyable elements, and `set` takes any. Both trap on an index at or past `len`.

## std.text

`std.text` turns numbers into bytes and back, and searches bytes the way a line protocol needs. A function cannot return a borrow, so a search returns an index, and you pass the part `s[lo..hi]` on yourself.

```cairn
import std.core (Option, Result);
import std.text as text;

// The decimal field that starts at `from` and ends at the next comma or at the end of the line.
fn field(n:usize, line:ro<u8>[n], from:usize) -> Result[u64, text.ParseError] {
  match text.find_byte(n, line, 44, from) {
    Some(at) => return text.parse_u64(at - from, line[from..at]);
    None => return text.parse_u64(n - from, line[from..n]);
  }
}

fn main() -> i32 {
  let line = "23,19,x9";
  match field(line, 0) {
    Ok(value) => { if value != 23 { return 1; } }
    Err(_) => return 2;
  }
  match field(line, 6) {
    Ok(_) => return 3;
    Err(why) => {
      match why {
        Invalid(at) => { if at != 0 { return 4; } }   // offset of the byte at fault
        Overflow(_) => return 5;
        Empty => return 6;
      }
    }
  }
  stack out:u8[4] = zeroed;
  let used = text.write_hex(out, 48879, 4);             // a call that writes gets its own statement
  if used != 4 || out[0] != 98 { return 7; }            // "beef"
  return 0;
}
```

The `write_` functions write into storage you pass and return the number of bytes they used, or 0 when the value does not fit. The `push_` functions append to a `Vec[u8]`, so they allocate. A parser that fails reports the offset of the byte at fault. `hash_bytes` is FNV-1a.

## std.fmt

`std.fmt` builds text in a `Vec[u8]`. Every call appends, so a line is a run of calls followed by one write. `hex`, `padded`, `left` and `right` pad and align. `fixed(out, x, places)` writes a float with `places` digits after the point, rounded as printf's `%.*f` rounds, and the test suite checks every case against Python's formatting.

```cairn
import std.fmt;
import std.vec (Vec);

fn main() -> i32 {
  let mut line = vec.new[u8]();
  let count:usize = 7;
  fmt.left(line, "mean", 6, ' ');
  fmt.fixed(line, 2.0 / 3.0, 3);
  fmt.bytes(line, " of ");
  fmt.padded(line, count, 3, '0');
  fmt.bytes(line, " at 0x");
  fmt.hex(line, 48879, 8);
  println(line);                 // mean  0.667 of 007 at 0x0000beef
  return 0;
}
```

Every call has `alloc` and `free` in its row, because appending may grow the `Vec`. `fixed` works in 720 bytes of stack, and more than 40 places is a guard failure.

## std.io

`std.io` works on open files and the standard streams. A `File` is `linear`, which means the compiler requires every path through the program to close it exactly once. A failure is an `IoError`, which holds the errno the C library set. `io.outcome(result)` turns what an [I/O ring](concurrency.md#io-rings) reports into a `Result`.

```cairn
import std.core (Option, Result);
import std.io as io;

const PATH:usize = 13;   // "readings.log" and its NUL

fn record(path:ro<u8>[PATH], n:usize, data:ro<u8>[n]) -> Result[usize, io.IoError] {
  let f = try io.open(PATH, path, io.TRUNCATE);
  defer io.close(f);                       // runs on every exit, including the ones `try` takes
  let wrote = try io.write(f, n, data);
  try io.sync(f);
  return Ok(wrote);
}

fn lines(path:ro<u8>[PATH]) -> Result[u64, io.IoError] {
  let bytes = try io.read_file(PATH, path);
  let mut count:u64 = 0;
  for b in bytes { if b == 10 { count += 1; } }
  return Ok(count);
}

fn main() -> i32 {
  let path = "readings.log\x00";
  match record(path, 14, "23,19\n31,7\n42\n") {
    Ok(wrote) => { if wrote != 14 { return 1; } }
    Err(_) => return 2;
  }
  match lines(path) {
    Ok(count) => { if count != 3 { return 3; } }
    Err(_) => return 4;
  }
  match io.remove(PATH, path) { Ok(_) => {} Err(_) => return 5; }
  return 0;
}
```

`defer io.close(f)` is the idiom. `try` refuses to leave a function while a linear value is still open, so without the `defer` a `File` cannot be used with `try` at all:

```cairn rejects E-LINEAR-LEAK
import std.core (Result);
import std.io as io;

fn first_byte(path:ro<u8>[6]) -> Result[u8, io.IoError] {
  let f = try io.open(6, path, io.READ);
  stack one:u8[1] = zeroed;
  let got = try io.read(f, 1, one);   // this `try` may leave while f is still open
  io.close(f);
  return Ok(one[0]);
}

fn main() -> i32 {
  match first_byte("a.txt\x00") { Ok(_) => return 0; Err(_) => return 1; }
}
```

`close` returns nothing, because a function that consumes a linear value cannot return a status. If you need one, report it through a borrow.

A path in `std.io` ends in a NUL byte, because C reads a pointer and no length; [std.fs](#stdfs) takes paths without one. `read` is one system call and returns 0 at the end of the file. `read_full` and `write` loop until the kernel has done all of it. `read_to_end` asks a regular file how much is left first, so the file arrives in one allocation, and a pipe's `Vec` doubles as it fills. Nothing here buffers. For output, the [print builtins](language.md#print-and-format) usually serve.

## std.fs

`std.fs` works on files by path. A path is its bytes alone, with no NUL at the end, so a path from the command line, from a `Vec` or from a literal goes straight in. A path of 4096 bytes or more is `IoError(36)` (ENAMETOOLONG), and a path that holds a NUL is `IoError(22)` (EINVAL). Both are error values, never traps.

```cairn
import std.core (Result);
import std.fs;
import std.io (IoError);

fn log_twice(n:usize, path:ro<u8>[n]) -> Result[usize, IoError] {
  try fs.write(path, "started\n");                // create the file, or cut it to nothing
  try fs.append(path, "done\n");
  let back = try fs.read(path);                   // reads until the kernel says the file ended
  try fs.remove(path);
  if fs.exists(path) { return Ok(0); }
  return Ok(back.len);
}

fn main() -> i32 {
  match log_twice("/tmp/cairn-fs-example.log") {
    Ok(n) => { if n != 13 { return 1; } }
    Err(_) => { return 2; }
  }
  return 0;
}
```

`open` returns the `std.io` `File`. `read` reads until the kernel reports the end, so a pipe or a `/proc` file whose size is 0 reads whole. `exists` answers for the moment it runs, and the answer can be stale by the next call.

## std.env

`std.env` reads the program's arguments and environment from `/proc/self`, where Linux keeps them as the kernel passed them. Any module can call it, and the program needs no startup code for it. `args()` returns an `Args`, in which argument `i` is `a.text.data[a.begin(i)..a.end(i)]` and argument 0 is the program's path. An `i` past the last argument is a guard failure. `var(name)` returns the value a variable had when the program started, or `None`.

```cairn
import std.core (Option, Result);
import std.env (Args);
import std.io (IoError);

fn greet() -> Result[usize, IoError] {
  let a = try env.args();
  for i in 1..a.count() {
    let lo = a.begin(i);
    let hi = a.end(i);
    println("argument: ", a.text.data[lo..hi]);
  }
  match try env.var("HOME") {
    Some(home) => println(home);
    None => println("no HOME");
  }
  return Ok(a.count());
}

fn main() -> i32 {
  match greet() {
    Ok(_) => { return 0; }
    Err(_) => { return 1; }
  }
}
```

Each call to `args` or `var` reads one small file into a `Vec` it returns.

## std.time

`now()` returns an `Instant` on the monotonic clock, which never jumps, and `since(start)` returns the nanoseconds from that instant to now. Measure with these two. `wall_ns()` reads the date, in nanoseconds since 1970-01-01 UTC, and it moves when someone sets the system clock. `sleep(ns)` waits at least `ns` nanoseconds, and a signal that wakes the thread early does not cut the wait short.

```cairn
import std.time (Instant);

fn main() -> i32 {
  let start = time.now();
  time.sleep(1000000);                            // a millisecond
  let took = time.since(start);
  if took < 1000000 { return 1; }
  return 0;
}
```

Each function is one system call, except that `sleep` calls again for the rest of the wait after a signal wakes it. `sleep` blocks the calling thread.

## std.map

`Map[K, V]` is a hash table with open addressing, linear probing and tombstones. The map owns its keys and values, so `Map[u64, Vec[u8]]` is ordinary.

```cairn
import std.core (Option);
import std.map as map;
import std.text as text;

fn bump(counts:rw<map.Map[u64, u64]>, key:u64) {
  match map.find(counts, key) {
    Some(slot) => counts.vals[slot] += 1;
    None => map.insert(counts, key, 1);
  }
}

// One count per distinct word, keyed by the word's FNV digest: a Vec has no Hash of its own.
fn tally(n:usize, line:ro<u8>[n], counts:rw<map.Map[u64, u64]>) {
  let mut start:usize = 0;
  while start < n {
    let mut stop = n;
    match text.find_byte(n, line, 32, start) { Some(at) => stop = at; None => {} }
    if stop > start { bump(counts, text.hash_bytes(stop - start, line[start..stop])); }
    start = stop + 1;
  }
}

fn main() -> i32 {
  let mut counts = map.new[u64, u64]();
  let line = "put get put del get put";
  tally(line, counts);
  if map.count(counts) != 3 { return 1; }
  match map.find(counts, text.hash_bytes(3, "put")) {
    Some(slot) => { if counts.vals[slot] != 3 { return 2; } }
    None => return 3;
  }
  let mut seen:u64 = 0;
  for slot in 0..map.slots(counts) { if map.live(counts, slot) { seen += counts.vals[slot]; } }
  if seen != 6 { return 4; }
  return 0;
}
```

`insert` releases the old value when it replaces one, and `remove` moves the value out.

A slot index from `find` stays good only until the next `insert` or `remove`, because growth rehashes and a slot can be reused. For a position you keep longer, `slot(m, key)` returns a `Slot` stamped when its key was placed. `resolve(m, s)` returns `None` once that key is removed or the map has rehashed, and you then look the key up again. It never returns another entry's index. `update(m, key, f)` lends the value to a closure, and the closure may not reach the map (`E-ALIAS`).

```cairn
import std.core (Option);
import std.map (Map, Slot);

fn main() -> i32 {
  let mut m = map.new[u64, u64]();
  m.insert(1, 10);
  let mut kept = Slot(0, 0);
  match m.slot(1) { Option.Some(s) => { kept = s; } Option.None => { return 1; } }
  let gone = m.remove(1);
  m.insert(9, 90);                                   // may reuse the slot key 1 had
  match m.resolve(kept) { Option.Some(_) => { return 2; } Option.None => {} }
  let found = m.update(9, |v:rw<u64>| { v += 1; });
  match m.get(9) { Option.Some(v) => { if !found || v != 91 { return 3; } } Option.None => { return 4; } }
  return 0;
}
```

Each operation costs expected O(1), and `insert` rehashes once the map would be more than three quarters full. `K` must implement `Hash` and `Eq`, so a key type with `derive eq` and no `derive hash` is refused:

```cairn rejects E-TRAIT-IMPL
import std.core (Eq);
import std.map as map;

struct Route { source:u32; port:u16; }
derive eq for Route;

fn main() -> i32 {
  let mut hits = map.new[Route, u64]();
  map.insert(hits, Route(10, 80), 4);                // Route does not implement Hash
  return 0;
}
```

## std.derived

`derive eq` compares two records field by field, `derive ord` orders them lexicographically in declaration order, and `derive hash` hashes their fields. Each writes an ordinary `impl` of the `std.core` trait, so a record can be sorted or used as a map key.

```cairn
import std.core (Option, Eq, Hash);
import std.map as map;

struct Route { source:u32; port:u16; }
derive eq for Route;
derive hash for Route;

fn main() -> i32 {
  let mut hits = map.new[Route, u64]();
  map.insert(hits, Route(10, 80), 4);
  map.insert(hits, Route(10, 443), 9);
  map.insert(hits, Route(10, 80), 5);      // replaces, and releases the old value
  if map.count(hits) != 2 { return 1; }
  let wanted = Route(10, 80);
  match map.find(hits, wanted) {
    Some(slot) => { if hits.vals[slot] != 5 { return 2; } }
    None => return 3;
  }
  if !same(Route(10, 80), wanted) || same(Route(10, 81), wanted) { return 4; }
  return 0;
}
```

A field whose type lacks the trait is refused with `E-TRAIT-IMPL`, and the message names the field.

## std.sort

`sort` is a heapsort. It costs O(n log n), uses no recursion and allocates nothing, and it moves elements only with `swap`, so it sorts owners too. Equal elements do not keep their order.

```cairn
import std.core (Option, Ord, Eq);
import std.sort as sort;

struct Trade { venue:u32; cents:u64; }
derive ord for Trade;   // lexicographic in declaration order: venue, then cents
derive eq for Trade;

fn main() -> i32 {
  let mut book = Buf[Trade](4);
  book[0] = Trade(2, 900);
  book[1] = Trade(1, 700);
  book[2] = Trade(2, 100);
  book[3] = Trade(1, 50);
  sort.sort(book);
  if book[0].venue != 1 || book[0].cents != 50 || book[3].cents != 900 { return 1; }
  let wanted = Trade(2, 100);
  match sort.search(book, wanted) {
    Some(at) => { if at != 2 { return 2; } }
    None => return 3;
  }
  sort.sort_by(book, |a:ro<Trade>, b:ro<Trade>| -> bool { return a.cents > b.cents; });
  if book[0].cents != 900 { return 4; }
  return 0;
}
```

`sort_by` takes a closure or a function, and `search` does a binary search of a sorted view.

`radix_sort` sorts unsigned keys stably, eight bits a pass, and places each digit with a [`scan`](concurrency.md#scan). It stops once the largest key has no digit left. It allocates nothing, because you lend it a scratch view of the same extent.

```cairn
import std.sort as sort;

fn main() -> i32 {
  let n:usize = 5;
  buffer keys:u32[n] = zeroed;
  buffer spare:u32[n] = zeroed;
  keys[0] = 70000;
  keys[1] = 3;
  keys[2] = 512;
  keys[3] = 3;
  keys[4] = 0;
  sort.radix_sort(keys, spare);
  if keys[0] != 0 || keys[1] != 3 || keys[3] != 512 || keys[4] != 70000 { return 1; }
  return 0;
}
```

## std.arena

`std.arena` is how you build graphs and cycles. Values live in one owned array, and you name each one by a copyable `Handle { slot; generation }` instead of a borrow. Removing a value bumps its slot's generation, so a handle to a removed value finds `None`, where a pointer in C would dangle.

```cairn
import std.core (Option);
import std.arena as arena;
import std.vec as vec;

struct Step { cost:u64; needs:vec.Vec[arena.Handle]; }   // a cycle of handles, not of owners

fn main() -> i32 {
  let mut plan = arena.new[Step]();
  let no_needs = vec.new[arena.Handle]();     // a call that allocates is not an operand beside another
  let fetch = arena.insert(plan, Step(3, no_needs));
  let empty = vec.new[arena.Handle]();
  let parse = arena.insert(plan, Step(5, empty));
  match arena.find(plan, parse) {
    Some(slot) => vec.push(plan.items[slot].needs, fetch);
    None => return 1;
  }
  match arena.remove(plan, fetch) {
    Some(dropped) => { if dropped.cost != 3 { return 2; } }
    None => return 3;
  }
  match arena.find(plan, fetch) { Some(_) => return 4; None => {} }  // stale
  return 0;
}
```

`insert` reuses removed slots and costs amortized O(1). `find` and `remove` cost O(1).

## std.mem

`fill`, `copy` and `equal` work over whole views. `fill` and `copy` assign, so they take only copyable elements. `equal` takes any element type that implements `Eq`, and it stops at the first difference.

```cairn
import std.mem as mem;

fn main() -> i32 {
  stack pattern:u8[8] = zeroed;
  buffer frame:u8[16] = zeroed;
  mem.fill(pattern, 255);
  mem.copy(8, frame[0..8], pattern);
  if !mem.equal(8, frame[0..8], len(pattern), pattern) { return 1; }
  if mem.equal(len(frame), frame, len(pattern), pattern) { return 2; }   // lengths differ
  return 0;
}
```

`copy` is no memmove. It refuses two overlapping parts of one array (`E-ALIAS`), so shift a buffer with an ordinary loop. `equal` takes two extents, because the two lengths may differ.

## std.wire

`derive wire for Header;` generates `encode_Header`, `decode_Header` and `wire_size_Header` for a record whose fields are all unsigned integers of fixed width. The encoding is little-endian, in declaration order, with no padding.

```cairn
import std.core (Eq);

struct Header { check:u32; kind:u32; key_len:u32; val_len:u32; }
derive wire for Header;   // no import: a bare recipe name falls back to std.<name>
derive eq for Header;

fn main() -> i32 {
  stack bytes:u8[16] = zeroed;
  let head = Header(3735928559, 1, 4, 9);
  encode_Header(bytes, head);
  if bytes[0] != 239 || bytes[3] != 222 { return 1; }   // little-endian, low byte first
  if !same(decode_Header(bytes), head) { return 2; }
  if wire_size_Header() != 16 { return 3; }             // the fields, with no padding
  return 0;
}
```

The encoding adds no framing, authentication or validation. A field of any other type is refused with `E-DERIVE-FIELD`. `src/cairn/std/wire.cairn` is a worked example of a recipe.

## std.math

`std.math` is the C math library on `f64`: `exp`, `log`, `log2`, `pow`, `sin`, `cos`, `tan` and `atan2`, with the constants `PI` and `E`. The last bit of a result depends on which libm the program links (glibc, musl and CUDA's differ), so a result can differ from one machine to another. The [builtins](language.md#values-and-arithmetic) `sqrt`, `floor`, `ceil`, `trunc` and `abs` give the same bits everywhere. The module is for host code only.

```cairn
import std.math;

fn main() -> i32 {
  let half = math.sin(math.PI / 6.0);
  let grown = math.exp(1.0);
  if abs(half - 0.5) > 1e-15 || abs(grown - math.E) > 1e-15 { return 1; }
  return 0;
}
```

Each call is a foreign call, so its row says `ffi:exp` and the like. A foreign call cannot share an expression with another operand that could run or abort before or after it ([operand order](memory.md#operand-order)), so bind it to a name first:

```cairn rejects E-EFFECT-ORDER
import std.math;

fn main() -> i32 {
  let both = math.sin(1.0) + math.cos(1.0);   // bind each call first
  if both > 3.0 { return 1; }
  return 0;
}
```

## std.zlib

`std.zlib` binds the system zlib. `compress(data, level)` returns the zlib stream of a whole view, and `crc32` and `adler32` continue a checksum. Importing the module links `-lz`.

```cairn
import std.zlib;

fn main() -> i32 {
  match zlib.compress("hello hello hello hello", 9) {
    Ok(z) => { if z.len == 0 { return 1; } }
    Err(_) => return 2;
  }
  if zlib.crc32(0, "abc") != 891568578 { return 3; }
  return 0;
}
```

Only the calls that do their whole work in one call are bound. zlib's streaming interface keeps pointers into the caller's buffers between calls, and a CAIRN borrow never outlives its call, so a library that streams needs a wrapper that owns the buffers. Level 0 stores, 1 is fastest, 9 is smallest and -1 is zlib's default. A level outside -1 to 9 returns `ZError(-2)`, an error value and never a trap. A project's own `extern` declarations name their system libraries under [`[build] libraries`](abstractions.md#projects).

## std.image

`Image` is an RGBA picture held in one owned array, rows top first, so pixel `(x, y)` is `px[y * w + x]`. A pixel is a `u32` written `0xRRGGBBAA`, so `0xff8800ff` is opaque orange. `get` and `set` trap on a column past the width, where a plain index would read the next row.

```cairn
import std.image (Image);

fn main() -> i32 {
  let mut img = image.new(64, 32);
  image.shade(img, |x:usize, y:usize| -> u32 { return image.rgba(u8(x * 4), u8(y * 8), 96, 255); });
  match image.save_png(img, "gradient.png") {
    Ok(_) => return 0;
    Err(_) => return 1;
  }
}
```

`png` writes 8-bit RGBA in stored zlib blocks, so it needs no library. When size matters, `packed` writes the PNG around a stream you compress yourself with `std.zlib` from `image.scanlines(img)`. `ppm` writes a binary PPM (`P6`), which drops the alpha channel. `fill` and `shade` run as parallel regions, so the closure `shade` takes may read what it captured and write nothing (`E-PARALLEL-CALL`).

## std.draw

`std.draw` draws shapes and text into an `Image` on the CPU and blends colours, with a built-in 8 by 13 bitmap font. Every shape is clipped, so a shape partly outside the image draws the part inside, and no shape traps.

```cairn
import std.draw;
import std.image (Image);

fn main() -> i32 {
  let mut img = image.new(120, 40);
  image.fill(img, 0x202830ff);
  draw.rect(img, 8, 8, 104, 24, 0x3060c0ff);
  draw.text(img, 16, 14, "Save", 0xffffffff, 1);
  draw.circle(img, 100, 20, 6, 0xffcc00c0);   // translucent: it blends over what is there
  let mut l = draw.layout();
  draw.mark(l, "button", 8, 8, 104, 24);
  match draw.capture(img, l, 0) {
    Ok(_) => return 0;
    Err(_) => return 1;
  }
}
```

The drawing rules use integer arithmetic and are exact, so a test holds them to an independent rasterizer pixel for pixel. A colour blends source over destination with integer rounding: an opaque colour replaces a pixel, and a fully transparent one leaves it as it was.

A `Layout` records named rectangles as the program draws them, and a test can check them in CAIRN with `place`, `inside` and `apart`. `capture(img, l, k)` writes `frame-k.png` and `frame-k.json` into the directory `CAIRN_SHOT` names. When `CAIRN_SHOT` names none, `capture` does nothing, so the program runs the same without it. [`cairn shot`](agents.md#requests-beyond-an-edit) sets `CAIRN_SHOT` and returns the frames. Nothing here opens a window, and no windowing library is bound.

## std.net

`std.net` is blocking TCP. A `Socket` is linear, as a `File` is, and an address is four bytes. To serve many connections from one thread, keep their accepts and receives in an [I/O ring](concurrency.md#io-rings), as `examples/apps/service` does.

```cairn
import std.core (Result);
import std.io (IoError);
import std.net as net;

// One process plays both ends: connect_to completes into the listener's backlog, so the
// accept that follows returns without a second thread.
fn echo_once(port:u16) -> Result[usize, IoError] {
  let server = try net.listen_on("\x7f\x00\x00\x01", port, 16);
  defer net.close(server);
  let client = try net.connect_to("\x7f\x00\x00\x01", port);
  defer net.close(client);
  let session = try net.accept(server);
  defer net.close(session);
  try net.send(client, 5, "ping\n");
  stack heard:u8[5] = zeroed;
  let got = try net.recv(session, len(heard), heard);
  try net.send(session, got, heard[0..got]);
  stack echoed:u8[5] = zeroed;
  let last = try net.recv(client, len(echoed), echoed);
  if echoed[0] != 112 { return Err(IoError(71)); }   // 'p'
  return Ok(last);
}

fn main() -> i32 {
  match echo_once(39812) {
    Ok(n) => { if n != 5 { return 1; } }
    Err(_) => return 2;
  }
  return 0;
}
```

`send` loops until the whole view is sent. `recv` is one system call, and a result of 0 means the peer closed its end. `listen_on`, `accept` and `connect_to` each allocate the 16-byte address they hand the kernel.

## std.sys

`std.sys` declares every C library function the rest of the library uses, once. An extern's C symbol is its CAIRN name, so two modules cannot both declare `close`. Prefer `std.io` and `std.net`, which wrap these calls.

```cairn
import std.sys as sys;

// Your own binding, with the two conventions std.sys uses. An extent may name a later
// parameter, which is how C orders (pointer, length).
extern fn getrandom(into:rw<u8>[n], n:usize, flags:u32) -> i64 effects(io);

fn main() -> i32 {
  stack seed:u8[8] = zeroed;
  unsafe {
    let got = getrandom(seed, len(seed), 0);   // a call that writes gets its own statement
    if got != 8 || sys.getpid() <= 0 { return 1; }
  }
  return 0;
}
```

A C string is passed as a view of one element whose storage holds the NUL. `errno` is a thread-local variable that no signature can return, so `std.sys.errno` reads it with one `mmio_read[u32]`, the library's only use of `mmio`.

## Two rules the library follows

A linear value inside a record leaves by taking the record apart, as in `let Conn(f, sent) = c;`. `take` cannot do it, because `take` leaves a zero behind, and a zero `File` would be a forged one (`E-LINEAR-STORAGE`).

`try` requires the failure type of the call to match the failure type of the function it is in (`E-TRY` otherwise). So every function that reads or writes a file, a socket or the program's environment fails with the same `IoError`: those of `std.io`, `std.fs`, `std.env` and `std.net`, `image.save_png` and `draw.capture`.

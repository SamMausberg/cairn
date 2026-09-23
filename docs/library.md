# The standard library

Twenty modules, written in CAIRN and shipped inside the package. `import std.map (Map);` brings in `map.insert(...)` and the bare name `Map`, and a module you do not import is not in your program. [std_api.md](std_api.md) has every signature and effect row, generated from these sources.

Three habits explain the API. A lookup answers with an index, never a borrow: `map.find` returns `Option[usize]`, and the caller reads `m.vals[slot]` itself. A position kept across changes is a handle checked on use (`arena.Handle`, `map.Slot`). A function that allocates says `alloc` in its row, and so does everyone who calls it.

| module | what it is for | allocates |
| --- | --- | --- |
| `std.core` | `Option`, `Result`, the `Ord`/`Eq`/`Hash` traits | no |
| `std.vec` | the growable owner `Vec[T]` | yes |
| `std.text` | integers to and from bytes, comparison, search, hashing | only `push_u64`, `push_i64` |
| `std.io` | files, standard streams, a monotonic clock | only `read_file`, `read_to_end` |
| `std.math` | the C math library on `f64`, whose last bit varies | no |
| `std.zlib` | the system zlib: whole-view streams, CRC-32, Adler-32 | only `compress` |
| `std.image` | RGBA images, PNG and PPM files | yes |
| `std.draw` | shapes, blending, text, layout records and frame capture on the CPU | only `Layout`, `capture` |
| `std.time` | a monotonic clock, the date, sleeping | no |
| `std.env` | the program's arguments and environment | yes |
| `std.fs` | files by path, without a NUL to write | only `read` |
| `std.fmt` | integers, hex, padding and exact fixed-point floats into a `Vec[u8]` | yes |
| `std.map` | open-addressed `Map[K, V]` | yes |
| `std.derived` | `derive eq`, `derive ord`, `derive hash` | no |
| `std.sort` | in-place heapsort, a stable radix sort of unsigned keys, and search | no |
| `std.wire` | `derive wire`: fixed-width records to bytes | no |
| `std.arena` | generational `Arena[T]` with stale-handle detection | yes |
| `std.mem` | `fill`, `copy`, `equal` over views | no |
| `std.net` | blocking TCP | no |
| `std.sys` | the C library, declared once | no |

## std.core

`Option[T]`, `Result[T, E]` and the three traits everything else dispatches on: `Ord` (`less`, a strict weak order), `Eq` (`same`, an equivalence) and `Hash` (`hash`). `Option` stands for absence, and there is no null. `Result` is the only failure carrier, and `try` is the only thing that propagates it.

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
  let used = try under(n, frames, 1500);   // on failure this leaves with the stream number
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

`std.core` implements `Ord` and `Eq` for every integer type, `Eq` for `bool` and `Hash` for the unsigned integers. Write your own impl, or generate one with [std.derived](#stdderived). Every trait member is `pure`, so a `less` that prints is `E-EFFECT-CEILING`.

## std.vec

`struct Vec[T:affine] { data:Buf[T]; len:usize; lends data[0..len]; }` is a growable owner. Named where a view is expected, it [lends its elements](memory.md#arrays-views-and-parts), and a `for` walks them.

```cairn
import std.vec as vec;
import std.text as text;

fn main() -> i32 {
  let mut line = vec.new[u8]();              // nothing is allocated until the first push
  for i in 1..4 {
    text.push_u64(line, u64(i) * 11);
    vec.push(line, 32);                      // a space
  }
  vec.truncate(line, line.len - 1);          // release the trailing space now, not at scope exit
  if line.len != 8 { return 1; }             // "11 22 33"
  if line.data[0] != 49 || line.data[7] != 51 { return 2; }
  if vec.capacity(line) < line.len { return 3; }
  return 0;
}
```

Growth doubles and moves elements with `swap`, so an owner is never copied, and `push` is amortized O(1). `pop` and `remove` move an element out as an `Option[T]`. `get` and `set` take only copyable elements and trap past `len`.

## std.text

Numbers to bytes and back, and the searching a line protocol needs. A search answers with an index, and the caller passes `s[lo..hi]` on.

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
  let used = text.write_hex(out, 48879, 4);             // a writing call gets its own statement
  if used != 4 || out[0] != 98 { return 7; }            // "beef"
  return 0;
}
```

The `write_` functions answer with the number of bytes used, or 0 when the value does not fit. The parsers report the offset of the byte at fault. `hash_bytes` is FNV-1a.

## std.io

A `File` is `linear`, so every path must close it, and a failure is an errno inside an `IoError`. `io.outcome(result)` reads what an [I/O ring](concurrency.md#io-rings) reports as a `Result`.

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

`defer io.close(f)` is the idiom: `try` refuses to leave a function while a linear value is unconsumed, so without the `defer` a File cannot be used with `try` at all (`E-LINEAR-LEAK`). `close` reports nothing, because a consumer of a linear value cannot return a status; report through a borrow if you need it.

A path here ends in a NUL byte, since C reads a pointer and not a length; [std.fs](#stdfs) takes paths without one. `read` is one syscall and answers 0 at end of file, and `read_full` and `write` loop. Nothing buffers; for output, the [print builtins](language.md#print-and-format) usually serve.

## std.fmt

Text built into a `Vec[u8]`: every call appends, so a line is a run of calls and one write. `hex`, `padded`, `left` and `right` pad and align. `fixed(out, x, places)` writes a float with that many digits after the point, rounded as printf's `%.*f` rounds, and the suite checks every case against Python's formatting.

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

Every call's row has `alloc` and `free`, because appending may grow the `Vec`.

## std.fs

Files by path, where a path is its bytes alone, so one from the command line, a `Vec` or a literal goes straight in. A path of 4096 bytes or more, or one holding a NUL, is an error value, never a trap.

```cairn
import std.core (Result);
import std.fs;
import std.io (IoError);

fn log_twice(n:usize, path:ro<u8>[n]) -> Result[usize, IoError] {
  try fs.write(path, "started\n");                // create or cut to nothing
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

`open` hands back the `std.io` `File`. `exists` answers for this instant only.

## std.env

The program's arguments and environment, read from `/proc/self`, so any module can read them. `args()` reads them into an `Args`, where argument `i` is `a.text.data[a.begin(i)..a.end(i)]`, the program's path first. `var(name)` is one variable's value, or `None`.

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

## std.time

`now()` is an `Instant` on the monotonic clock and `since(start)` the nanoseconds from it. `wall_ns()` reads the date, which moves when the system clock is set, and `sleep(ns)` waits at least that long.

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

## std.math

The C math library on `f64`: `exp`, `log`, `log2`, `pow`, `sin`, `cos`, `tan` and `atan2`, with `PI` and `E`. Their last bit depends on which libm links the program, so a result is not reproducible across machines, unlike the [builtins](language.md#values-and-arithmetic) `sqrt`, `floor`, `ceil`, `trunc` and `abs`. A call here is a foreign call, so its row says `ffi:exp` and the like, and it stands in its own statement (`E-EFFECT-ORDER`).

```cairn
import std.math;

fn main() -> i32 {
  let half = math.sin(math.PI / 6.0);
  let grown = math.exp(1.0);
  if abs(half - 0.5) > 1e-15 || abs(grown - math.E) > 1e-15 { return 1; }
  return 0;
}
```

## std.zlib

The system zlib: `compress(data, level)` gives the zlib stream of a whole view, and `crc32` and `adler32` continue a checksum. Importing the module links `-lz`.

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

Only the one-shot calls are bound. zlib's streaming interface keeps pointers into the caller's buffers between calls, and a CAIRN borrow never outlives its call, so a library built that way needs a wrapper that owns the buffers. A level outside -1 to 9 is `ZError(-2)`, a value and not a trap. A project's own `extern` declarations name their system libraries under [`[build] libraries`](abstractions.md#projects).

## std.image

`Image` is an RGBA picture in one owned array, rows top first, so pixel `(x, y)` is `px[y * w + x]`, written `0xRRGGBBAA`. `get` and `set` trap on a column past the width instead of reading the next row.

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

`png` writes 8-bit RGBA in stored zlib blocks, so it needs no library; `packed` compresses through `std.zlib` when size matters. `fill` and `shade` are parallel regions, so `shade`'s closure may read what it captured and write nothing (`E-PARALLEL-CALL`).

## std.draw

Shapes, blending and text drawn into an `Image` on the CPU, in a built-in 8 by 13 bitmap font. Every shape is clipped, so a shape partly outside the image draws its inside part and none traps.

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

The rules are integer and exact, so a test holds them to an independent rasterizer pixel for pixel. A colour blends source over destination with integer rounding, so an opaque colour replaces a pixel and a clear one leaves it.

A `Layout` records named rectangles as the program draws them, and a test can check them in CAIRN (`place`, `inside`, `apart`). `capture(img, l, k)` writes `frame-k.png` and `frame-k.json` into the directory `CAIRN_SHOT` names, and does nothing when it names none, so a program runs unchanged without it. [`cairn shot`](agents.md#requests-beyond-an-edit) sets it and returns the frames. Nothing here opens a window, and no windowing library is bound.

## std.map

`Map[K, V]`: open addressing, linear probing, tombstones. The map owns its keys and values, so `Map[u64, Vec[u8]]` is ordinary.

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

A slot index is good only until the next `insert` or `remove`, since growth rehashes and a slot can be reused. `slot(m, key)` gives a `Slot` stamped when its key was placed, and `resolve(m, s)` answers `None` once that key is gone, never another entry's index. `update(m, key, f)` lends the value to a closure, which may not reach the map (`E-ALIAS`).

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

Operations are expected O(1), and `insert` rehashes past three quarters full. `K` must implement `Hash` and `Eq`: a key with `derive eq` and no `derive hash` is `E-TRAIT-IMPL`.

## std.derived

`derive eq` (field-wise), `derive ord` (lexicographic, in declaration order) and `derive hash` write ordinary `impl`s of the `std.core` traits, so a record can be sorted or used as a map key.

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

A field whose type lacks the trait is named (`E-TRAIT-IMPL`).

## std.sort

`sort` is a heapsort: O(n log n) with no recursion, no allocation and moves only by `swap`, so it sorts owners too. Equal elements are not kept in order.

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

`sort_by` takes a closure or a function, and `search` binary-searches a sorted view.

`radix_sort` sorts unsigned keys stably, eight bits a pass, placing each digit with a [`scan`](concurrency.md#scan). It stops once the largest key has no digit left and allocates nothing: the caller lends the scratch view.

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

## std.wire

`derive wire for Header;` generates `encode_Header`, `decode_Header` and `wire_size_Header` for a record of fixed-width unsigned fields: little-endian, in declaration order, no padding.

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

It adds no framing, authentication or validation. Any other kind of field is `E-DERIVE-FIELD`. `src/cairn/std/wire.cairn` is a worked example of a recipe.

## std.arena

How to build graphs and cycles. Values live in one owned array and are named by a copyable `Handle { slot; generation }`. Removing a value bumps its slot's generation, so a use after free becomes a `None`, not a dangling pointer.

```cairn
import std.core (Option);
import std.arena as arena;
import std.vec as vec;

struct Step { cost:u64; needs:vec.Vec[arena.Handle]; }   // a cycle of handles, not of owners

fn main() -> i32 {
  let mut plan = arena.new[Step]();
  let no_needs = vec.new[arena.Handle]();     // a call that allocates cannot be a nested operand
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

`insert` reuses removed slots, and `find` and `remove` are O(1).

## std.mem

`fill`, `copy` and `equal` over views of copyable elements.

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

`copy` is not a memmove: overlapping parts of one array are refused (`E-ALIAS`), so shift a buffer with an ordinary loop. `equal` takes two extents, since the lengths may differ.

## std.net

Blocking TCP. A `Socket` is linear, as a `File` is. To serve many connections from one thread, keep their accepts and receives in an [I/O ring](concurrency.md#io-rings), as `examples/apps/service` does. An address is four bytes.

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

`send` loops until the whole view is gone, and `recv` is one syscall, where 0 means the peer closed its end.

## std.sys

Every libc binding the library uses, declared once, because an extern's C symbol is its CAIRN name and two modules cannot both declare `close`. Prefer `std.io` and `std.net`.

```cairn
import std.sys as sys;

// Your own binding, with the two conventions std.sys uses. An extent may name a later
// parameter, which is how C orders (pointer, length).
extern fn getrandom(into:rw<u8>[n], n:usize, flags:u32) -> i64 effects(io);

fn main() -> i32 {
  stack seed:u8[8] = zeroed;
  unsafe {
    let got = getrandom(seed, len(seed), 0);   // a writing call gets its own statement
    if got != 8 || sys.getpid() <= 0 { return 1; }
  }
  return 0;
}
```

A C string is passed as a one-element view whose storage holds the NUL. `errno` is a thread-local that no signature can return, so `std.sys.errno` reads it with one `mmio_read[u32]`, the library's only use of `mmio`.

## Sharp edges

A linear value inside a record leaves by taking the record apart, `let Conn(f, sent) = c;`, because `take` cannot forge the zero a `File` would leave behind (`E-LINEAR-STORAGE`).

One failure type per function: `try` requires the same failure payload, so everything fallible here is `Result[_, IoError]` (`E-TRY` otherwise).

A call that allocates or writes through a borrow cannot be a nested operand. Bind it first (`E-EFFECT-ORDER`).

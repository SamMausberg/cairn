# The standard library

Eighteen modules, written in CAIRN, shipped inside the package and linked on demand. `import std.map (Map);` brings in `map.insert(...)` and the bare name `Map`. A module you do not import is not in your program. An executable keeps only what `main` reaches, a library build keeps every function of the modules it imports, and a generic function exists only at the types it is used with.

[std_api.md](std_api.md) holds every signature and every effect row, generated from these sources by `cairn doc --std`. This file is the working guide: what each module is for, a program that uses it, and where it bites.

Three habits explain the API shape. A lookup answers with an index, never a borrow: `map.find` and `arena.find` return `Option[usize]`, and the caller reads `m.vals[slot]` itself, which is a place and can be passed on, borrowed, taken or assigned. A position kept across changes is a handle checked on use: `arena.Handle` and `map.Slot`. Every array parameter carries its length, `f(n, xs)` against a callee's `xs:ro<u8>[n]`; a whole view or buffer matches by name identity, and a part `v.data[lo..hi]` matches whatever `usize` expression you pass, at the cost of one bounds guard. Costs are in the signature: a function that allocates says `alloc` in its effect row and so does everyone who calls it.

| module | what it is for | allocates |
| --- | --- | --- |
| `std.core` | `Option`, `Result`, the `Ord`/`Eq`/`Hash` traits | no |
| `std.vec` | the growable owner `Vec[T]` | yes |
| `std.text` | integers to and from bytes, comparison, search, hashing | only `push_u64`, `push_i64` |
| `std.io` | files, standard streams, a monotonic clock | only `read_file`, `read_to_end` |
| `std.math` | the C math library on `f64`, whose last bit varies | no |
| `std.zlib` | the system zlib: whole-view streams, CRC-32, Adler-32 | only `compress` |
| `std.time` | a monotonic clock, the date, sleeping | no |
| `std.env` | the program's arguments and environment | yes |
| `std.fs` | files by path, without a NUL to write | only `read` |
| `std.fmt` | integers, hex, padding and exact fixed-point floats into a `Vec[u8]` | yes |
| `std.map` | open-addressed `Map[K, V]` | yes |
| `std.derived` | `derive eq`, `derive ord`, `derive hash` | no |
| `std.sort` | in-place heapsort and search | no |
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
  match headroom(2, frames) { Ok(spare) => { if spare != 100 { return 1; } } Err(s) => return 2; }
  frames[1] = Frame(2, 9000);
  match headroom(2, frames) { Ok(spare) => return 3; Err(s) => { if s != 2 { return 4; } } }
  return 0;
}
```

`std.core` implements `Ord` and `Eq` for every integer type, `Eq` for `bool` and `Hash` for the unsigned integers, one bounded impl per class; `hash` is Fibonacci hashing, one wrapping multiply whose high bits carry the mix. Write your own impl, or generate one with [std.derived](#stdderived). Every trait member is declared `pure` and every implementation is held to it, so `[T:Ord]` promises a comparison that only reads: a `less` that prints is `E-EFFECT-CEILING`, "Ord.Frame.less exceeds its declared effects."

## std.vec

`struct Vec[T:affine] { data:Buf[T]; len:usize; }`, a growable owner. Both fields are public, because passing `v.data[0..n]` to a view parameter is the normal way to hand a Vec to anything.

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

`reserve` doubles and moves elements with `swap`, so an owner is never copied; `push` is amortized O(1) and puts `alloc` in every caller's row. `pop`, `remove` and `swap_remove` move an element out as an `Option[T]`, `insert` moves one in and shifts the tail by swaps, and `find` answers the index of the first element equal to a key by the element's `Eq`. `get`, `set` and `extend_from` take only copyable elements, because an owner would have to be moved out of a place, and `get` and `set` trap on an index at or past `len`. `clear` and `truncate` release elements now and keep the capacity.

## std.text

Numbers to bytes and back, plus the searching a line protocol needs. A substring cannot be returned, so every search answers with an index into the input and the caller passes `s[lo..hi]` onwards.

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
    Err(why) => return 2;
  }
  match field(line, 6) {
    Ok(value) => return 3;
    Err(why) => {
      match why {
        Invalid(at) => { if at != 0 { return 4; } }   // offset of the byte at fault
        Overflow(at) => return 5;
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

`write_u64`, `write_i64` and `write_hex` answer with the number of bytes used, and 0 when the value does not fit; `write_hex` writes exactly `width` lowercase digits and loses whatever is above them. `parse_i64` takes an optional leading `-` and reports the same three errors as `parse_u64`, with the offset of the byte at fault. `compare` and `equal` are lexicographic, shorter first, and `starts_with` and `ends_with` are the two comparisons a path or a protocol line needs. `find` is naive, because a line protocol's needles are short, and `find_last_byte` searches backwards. `hash_bytes` is FNV-1a with no table. `push_u64` and `push_i64` are the two functions here that allocate.

## std.io

A `File` is `linear`, so the checker requires every path to close it. A failure is an errno inside an `IoError`, because CAIRN cannot express the `int*` the C library keeps its error behind. `io.outcome(result)` reads the result an I/O ring reports ([concurrency.md](concurrency.md#io-rings)), a count or a negative errno, as a `Result[usize, IoError]`.

```cairn
import std.core (Option, Result);
import std.io as io;

const PATH:usize = 13;   // "readings.log" and its NUL

fn record(path:ro<u8>[PATH], n:usize, data:ro<u8>[n]) -> Result[usize, io.IoError] {
  let f = try io.open(PATH, path, io.TRUNCATE);
  defer io.close(f);                       // runs on every exit, including the ones `try` takes
  let wrote = try io.write(f, n, data);
  let flushed = try io.sync(f);
  return Ok(wrote);
}

fn lines(path:ro<u8>[PATH]) -> Result[u64, io.IoError] {
  let bytes = try io.read_file(PATH, path);
  let n = bytes.len;
  let mut count:u64 = 0;
  for i in 0..n { if bytes.data[i] == 10 { count += 1; } }
  return Ok(count);
}

fn main() -> i32 {
  let path = "readings.log\x00";
  match record(path, 14, "23,19\n31,7\n42\n") {
    Ok(wrote) => { if wrote != 14 { return 1; } }
    Err(why) => return 2;
  }
  match lines(path) {
    Ok(count) => { if count != 3 { return 3; } }
    Err(why) => return 4;
  }
  match io.remove(PATH, path) { Ok(done) => {} Err(why) => return 5; }
  return 0;
}
```

`defer io.close(f)` is the idiom: `try` refuses to leave a function while a linear value is unconsumed, so a File that is not deferred cannot be used with `try` at all (`E-LINEAR-LEAK`, "f is linear: consume it, or defer its consumer, on every path"). `close` reports nothing, because consuming a linear value requires a function that never reaches a `return`, and `return` demands that every linear value already be consumed. Report through a borrow if you need the status.

Open flags are `READ`, `WRITE`, `APPEND` and `TRUNCATE`; `seek` takes `SET`, `CUR` or `END`; `sync` and `truncate` answer `Ok(0)`. A path ends in a NUL byte, since C reads a pointer and not a length. `read` is one syscall and answers 0 at end of file; `read_full` and `write` loop, and a short write is an error here even though it is not one to the kernel. Nothing buffers: n bytes written is n bytes of syscall. `print`, `println`, `eprintln`, `newline`, `print_u64` and `print_i64` are best effort and return nothing. `read_stdin` is one read from standard input, answering 0 at end of input. `read_to_end(f, into)` appends everything left to read in 4096-byte steps, for a pipe, a socket or a `/proc` file whose size says 0. `monotonic_ns` reads CLOCK_MONOTONIC; `std.time` has the same clock as a value.

## std.fmt

Text built into a `Vec[u8]`: every call appends, so a line is a run of calls and one write. `uint` and `int` take any unsigned or signed integer type, `hex(out, v, width)` writes at least `width` lowercase digits, `padded(out, v, width, fill)` right-aligns a number, and `left` and `right` pad text. `fixed(out, x, places)` writes a float with that many digits after the point, rounding its exact binary value half to even, which is what printf's `%.*f` prints, for every finite `f64` and up to forty places; `nan`, `inf` and `-inf` are spelled out and a negative zero keeps its sign. `tests/language/test_std_fmt.py` checks every line against Python's own formatting.

```cairn
import std.fmt;
import std.io;
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
  io.println(line.data[0..line.len]);              // mean  0.667 of 007 at 0x0000beef
  return 0;
}
```

The row of every call carries `alloc` and `free`, because appending may grow the `Vec`. `fixed` works in 720 bytes of its own stack and needs no allocation beyond the digits it appends.

## std.fs

The same files by path, where a path is its bytes alone. Each call copies the path into NUL-terminated storage of its own, so a path from the command line, from a `Vec` or from a literal goes straight in. A path of 4096 bytes or more is the error 36 (`ENAMETOOLONG`), and a NUL inside one is 22 (`EINVAL`): both are values, never guards.

```cairn
import std.core (Result);
import std.fs;
import std.io (IoError);

fn log_twice(n:usize, path:ro<u8>[n]) -> Result[usize, IoError] {
  let first = try fs.write(path, "started\n");    // create or cut to nothing
  let then = try fs.append(path, "done\n");
  let back = try fs.read(path);                   // reads until the kernel says the file ended
  let gone = try fs.remove(path);
  if fs.exists(path) { return Ok(0); }
  return Ok(back.len);
}

fn main() -> i32 {
  match log_twice("/tmp/cairn-fs-example.log") {
    Ok(n) => { if n != 13 { return 1; } }
    Err(e) => { return 2; }
  }
  return 0;
}
```

`open` hands back the `std.io` `File`, and `read`, `write`, `append`, `remove`, `rename` and `exists` do one job each. `exists` answers for this instant; the next call may find something else.

## std.env

The arguments and the environment the program was started with. Linux keeps both under `/proc/self` as the kernel passed them, so reading them needs no start-up code and works from any module. `args()` reads them once into an `Args`: argument `i` is `a.text.data[a.begin(i)..a.end(i)]`, the program's own path first, and `a.text.data[a.end(i)]` is the NUL a C call wants. `var(name)` is the value of one variable, copied out, or `None`. `cairn run app -- in.txt -v` starts the program with `in.txt` and `-v`.

```cairn
import std.core (Option, Result);
import std.env (Args);
import std.io (IoError);

fn greet() -> Result[usize, IoError] {
  let a = try env.args();
  for i in 1..a.count() {
    let lo = a.begin(i);
    let hi = a.end(i);
    io.print("argument: ");
    io.println(a.text.data[lo..hi]);
  }
  match try env.var("HOME") {
    Some(home) => { io.println(home.data[0..home.len]); }
    None => { io.println("no HOME"); }
  }
  return Ok(a.count());
}

fn main() -> i32 {
  match greet() {
    Ok(n) => { return 0; }
    Err(e) => { return 1; }
  }
}
```

## std.time

`now()` is an `Instant` on CLOCK_MONOTONIC, which never jumps, and `since(start)` is the nanoseconds from it. `wall_ns()` reads CLOCK_REALTIME, the date, which moves when the system clock is set. `sleep(ns)` waits at least that long, sleeping through a signal that wakes it early. Each is one system call, so its row says `io` and `ffi:clock_gettime` or `ffi:nanosleep`.

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

The C math library on `f64`: `exp`, `log`, `log2`, `pow`, `sin`, `cos`, `tan` and `atan2`, with `PI` and `E`. Their last bit depends on which libm links the program, glibc, musl or CUDA's, so a result is not reproducible across machines. The builtins `sqrt`, `floor`, `ceil`, `trunc` and `abs` are the other kind: IEEE 754 makes them the same everywhere ([language.md](language.md#values-and-arithmetic)). A call here is a foreign call, so its row says `ffi:exp` and the like, and, because the library may write `errno`, it stands in its own statement or initializer (`E-EFFECT-ORDER`).

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

`std.zlib` binds the system zlib. `compress(data, level)` gives the zlib stream of a whole view as a `Vec[u8]`, and `crc32` and `adler32` continue a checksum over a view. Importing the module links `-lz`, found where the C compiler finds it, and nothing is downloaded.

```cairn
import std.zlib;

fn main() -> i32 {
  match zlib.compress("hello hello hello hello", 9) {
    Ok(z) => { if z.len == 0 { return 1; } }
    Err(e) => return 2;
  }
  if zlib.crc32(0, "abc") != 891568578 { return 3; }
  return 0;
}
```

Only the calls that take a pointer and a length for the length of one call are bound. zlib's streaming interface keeps pointers into the caller's buffers inside a `z_stream` between calls, and a CAIRN borrow never outlives its call, so a C library built that way needs a wrapper that owns the buffers, or its one-shot entry points. The rows say `ffi:compress2`, `ffi:crc32_z` or `ffi:adler32_z` and `ffi_precondition`, with no `io`. A level outside -1 to 9 is `ZError(-2)`, a value and not a trap. A project whose own `extern` declarations call a system library names it under `[build]` as `libraries = ["z"]` ([abstractions.md](abstractions.md#projects)).

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

`find` answers with the slot, not the value, and `contains` with a bool. `insert` moves key and value in and releases the old value when it replaces one; `remove` moves the value out.

A slot index from `find` is good until the next `insert` or `remove`: growth rehashes every entry, and a removed key's slot can be reused by another key, so an old index may still be in bounds and name the wrong entry. Three forms keep a position honest instead. `slot(m, key)` answers with a `Slot`, an index and the stamp its entry got when its key was placed, and `resolve(m, s)` answers `None` once that key is removed or the map rehashes, never another entry's index. `update(m, key, f)` lends the value to a closure and answers whether the key was there; the call holds the map, so a closure that reaches the map is refused (`E-ALIAS`). `get(m, key)` copies a copyable value out.

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
  match m.resolve(kept) { Option.Some(at) => { return 2; } Option.None => {} }
  let found = m.update(9, |v:rw<u64>| { v += 1; });
  match m.get(9) { Option.Some(v) => { if !found || v != 91 { return 3; } } Option.None => { return 4; } }
  return 0;
}
``` Operations are expected O(1), and `insert` rehashes past three quarters full, so `alloc`, `free` and `zero_init` are in every caller's row. `K` must implement `Hash` and `Eq`, checked where the instance is made: a key with `derive eq` and no `derive hash` is `E-TRAIT-IMPL`, "Route does not implement Hash; std.map.insert needs [K:Hash+Eq+affine]."

## std.derived

Three recipes that generate trait implementations: `derive eq` (field-wise `same`), `derive ord` (lexicographic `less`, declaration order) and `derive hash` (FNV-1a over the fields' own hashes). Each writes an ordinary `impl` of the `std.core` trait, so the record then satisfies `[T:Ord]` for `std.sort` and `[K:Hash + Eq + affine]` for `std.map`.

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

A field whose type lacks the trait is named: `derive ord` over a record with a `bool` field is `E-TRAIT-IMPL`, "bool does not implement std.core.Ord." `cairn expand` prints what a `derive` generated, as source.

## std.sort

Heapsort: the one O(n log n) order that needs no recursion (no `diverge` from a call cycle), no scratch buffer (no `alloc`) and moves elements only with `swap`, so it sorts owners too. Equal elements are not kept in order.

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

`sort_by` takes a closure or a declared function; `sort` is `sort_by` with the trait's `less`. `search` binary-searches an already sorted view and answers the index of an element equal to the key, or `None`. The closure is a borrowed callable: it captures by reference, exists only as that argument, and cannot allocate or escape. The caller's row gains `indirect_call`.

## std.wire

One recipe, and the first piece of the compiler to become library code. `derive wire for Header;` generates `encode_Header`, `decode_Header` and `wire_size_Header` for a record of fixed-width unsigned fields: little-endian, declaration order, no padding, extents known statically.

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

It infers no framing, no authentication and no validation. Any other kind of field is `E-DERIVE-FIELD`, "wire/1 supports only fixed-width unsigned scalar fields." `src/cairn/std/wire.cairn` is the worked example of `each`, `where`, `fold` and `$` splices; its output is pinned byte for byte against the closed generator it replaced.

## std.arena

The language's answer to graphs and cycles. Values live in one owned array and are named by a copyable `Handle { slot; generation }`. Removing a value bumps its slot's generation, so every handle to it stops resolving: a use after free becomes a `None`, not a dangling pointer.

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
  match arena.find(plan, fetch) { Some(slot) => return 4; None => {} }  // stale
  return 0;
}
```

`insert` is amortized O(1) and reuses removed slots; `find` and `remove` are O(1), and `remove` moves the value out. Iterate with `for slot in 0..a.slots()` and `a.alive(slot)`; `a.handle(slot)` gives the handle a live slot currently answers to, and `a.count()` how many are live.

## std.mem

`fill`, `copy` and `equal` over views, one pass each. All three instantiate only for copyable elements, because an owner would have to be moved out of a place.

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

`copy` takes two views of one extent, so it is not a memmove: overlapping parts of one array are refused at the call site with `E-ALIAS`, and the entry guard checks it numerically as well. Shift a buffer down with an ordinary loop. `equal` takes two extents, since a comparison is the one place where the lengths may differ, and it stops at the first difference.

## std.net

Blocking TCP. A `Socket` is linear for the same reason a `File` is. To serve many connections from one thread, keep their accepts and receives in an I/O ring ([concurrency.md](concurrency.md#io-rings)) and answer with `net.send_all(fd, data)`, which takes the raw descriptor a ring's accept returns. `examples/apps/service` does exactly that. An address is four bytes, so a string literal is an IPv4 address.

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
  let sent = try net.send(client, 5, "ping\n");
  stack heard:u8[5] = zeroed;
  let got = try net.recv(session, len(heard), heard);
  let back = try net.send(session, got, heard[0..got]);
  stack echoed:u8[5] = zeroed;
  let last = try net.recv(client, len(echoed), echoed);
  if echoed[0] != 112 { return Err(IoError(71)); }   // 'p'
  return Ok(last);
}

fn main() -> i32 {
  match echo_once(39812) {
    Ok(n) => { if n != 5 { return 1; } }
    Err(why) => return 2;
  }
  return 0;
}
```

`listen_on` sets `SO_REUSEADDR`, so a restart does not lose to the previous listener's TIME_WAIT. `accept` and `connect_to` block. `send` loops until the whole view is gone; `recv` is one syscall, and a count of 0 means the peer closed its end, never an error. Every failure path inside the module closes the raw descriptor before a `Socket` exists, and reads errno first, because `close` would overwrite it.

## std.sys

Every libc binding the library uses, declared exactly once: `read`, `write`, `open`, `close`, `lseek`, `fsync`, `ftruncate`, `unlink`, `rename`, `clock_gettime`, `getpid`, the BSD socket calls, `__errno_location`, and `errno()` on top of it. Prefer `std.io` and `std.net`. This module exists because an extern's C symbol is its CAIRN name, so two modules cannot both declare `close`.

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

The second convention is for C strings: a pointer is not a view, so `open` is declared `path:ro<u8>[1]` and called as `sys.open(path[0..1], flags, 420)` with the NUL byte inside `path`. `errno` is a macro over a thread-local `int*`, which no CAIRN signature can return, so `std.sys.errno` declares `__errno_location` as a `usize` and does one `mmio_read[u32]` of that address inside `unsafe`. It is the only place in the library that needs the `mmio` effect.

## Sharp edges

A linear value inside a record leaves by taking the record apart. `take` cannot forge the zero a `File` would leave behind (`E-LINEAR-STORAGE`, "take would leave a forged linear value behind; swap two places instead"), so a wrapper is consumed whole: `let Conn(f, sent) = c;` binds every field and `c` is gone. A `File` or a `Socket` may therefore live inside your own state.

One failure family per function. `try` requires the enclosing function to return the same sum family with the same failure payload, so everything fallible here is `Result[_, IoError]` and the void-ish ones answer `Ok(0)`. Mixing families is `E-TRY`, "try returns the failure of std.core.Result[u64, std.io.IoError], which Broken cannot carry."

A call that allocates or writes through a borrow cannot be a nested operand. Bind it first: `let empty = vec.new[Handle](); arena.insert(plan, Step(1, empty));`. Nesting it is `E-EFFECT-ORDER`, "Bind a writing call to its own statement before using its result."

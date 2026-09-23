# Tasks, lanes and devices

## Tasks and leases

`let t = spawn f(args);` evaluates the arguments and runs a declared function on its own thread, a parked one when there is one. `t` is a linear ticket: `wait(t)` consumes it on every path of the same function and returns `f`'s result, and the ticket cannot be stored, passed or returned.

Until the `wait`, every place lent to the task is leased: nobody may write what the task reads or touch what it writes, the owner included (`E-LEASED`). Read-only lending is shared freely. Parts of one array may go mutably to different tasks when each visibly ends where the next begins, so a K-way split works.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for x in xs { sum = add_wrap(sum, x); }
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
  let sum = spawn total(samples);                          // a view lends the elements: len stays readable
  if wait(sum) != 404550 { return 1; }
  return 0;
}
```

A closure cannot follow a task to another thread (`E-SPAWN`).

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

A lease names the place that was lent, not the local it is rooted in, so two tasks may take two fields of one record, and `len(box.a)` still reads while a task holds `box.a`'s elements. Lending the record itself leases every field.

```cairn
struct Pair { left:Buf[u64]; right:Buf[u64]; }
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn main() -> i32 {
  let n:usize = 64;
  let mut pair = Pair(Buf[u64](n), Buf[u64](n));
  let left = spawn fill(pair.left, 0);                      // two fields, two threads
  let right = spawn fill(pair.right, 100);
  let k = len(pair.left);                                   // the field's header, which its elements do not cover
  wait(left);
  wait(right);
  if k != n || pair.left[1] != 1 || pair.right[1] != 101 { return 1; }
  return 0;
}
```

Lending one field twice is refused, as is replacing a lent field (`pair.left = Buf[u64](2)`).

```cairn rejects E-LEASED
struct Pair { left:Buf[u64]; right:Buf[u64]; }
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }

fn main() -> i32 {
  let n:usize = 64;
  let mut pair = Pair(Buf[u64](n), Buf[u64](n));
  let left = spawn fill(len(pair.left), pair.left, 0);
  let right = spawn fill(len(pair.left), pair.left, 100);
  wait(left);
  wait(right);
  return 0;
}
```

```text
pair.left is lent to left until wait(left).
```

## Task groups

A group collects tasks in the order they finish, where tickets are awaited in the order written. `let g = Group[T](n);` declares in place, with all its storage, a group of at most `n` tasks in flight with results of type `T`. It is never stored, passed, lent to a callee or returned (`E-PINNED`), and it is linear: `wait(g)` consumes it on every path (`E-LINEAR-LEAK`, `E-LINEAR-BRANCH`).

`spawn f(args) into g;` hands a task to the group, where `f` returns `T` (`E-TYPE-MISMATCH`), and leases what it borrows to the group until `wait(g)`. A submission beyond `n` tasks is a guard failure, never growth.

`let r = collect(g);` yields the result of whichever task finishes next, and with nothing outstanding is a guard failure. It returns no lease, since the checker cannot know which task finished. `wait(g)` joins every task, drops uncollected results, releases the leases and consumes the group.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for x in xs { sum = add_wrap(sum, x); }
  return sum;
}
fn work(n:usize, xs:ro<u64>[n], first:usize, count:usize) -> u64 = total(count, xs[first..first + count]);

fn main() -> i32 {
  let n:usize = 900;
  let a:usize = 300;
  let b:usize = 600;
  let mut samples = Buf[u64](n);
  let writers = Group[void](3);
  spawn fill(a, samples[0..a], 0) into writers;             // three disjoint parts, three threads
  spawn fill(b - a, samples[a..b], 300) into writers;
  spawn fill(n - b, samples[b..n], 600) into writers;
  wait(writers);                                            // joins all three and returns the leases
  let readers = Group[u64](4);
  for k in 0..4 { spawn work(samples, k * 200, 200) into readers; }  // read-only: shared
  let mut sum:u64 = 0;
  for _ in 0..4 { sum = add_wrap(sum, collect(readers)); }  // in the order they finish
  wait(readers);
  if sum != total(800, samples[0..800]) { return 1; }
  return 0;
}
```

Inside a loop a group may be lent only what its tasks read, since the next iteration would lend an `rw` place again while the group still holds it. An owner passed by value moves into the task, so a loop may hand the group a fresh `Buf` each time round. After an `if` or a `match`, the group holds what any path lent it.

```cairn rejects E-LEASED
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn main() -> i32 {
  let mut samples = Buf[u64](8);
  let writers = Group[void](4);
  for k in 0..4 { spawn fill(2, samples[k * 2..k * 2 + 2], 0) into writers; }
  wait(writers);
  return 0;
}
```

```text
samples[?..?] is lent to writers until wait(writers), and the next iteration would lend it again.
```

Queued device work cannot join a group (`E-SPAWN`), a `linear` result type is refused because `wait` might drop it (`E-LINEAR-STORAGE`), and a group is a host object (`E-PLACEMENT`).

## I/O rings

A ring keeps many kernel operations in flight from one thread and hands them back in the order they finish. `let mut q = IoRing(n);` declares in place a Linux io_uring of 1 to 4096 operations.

`q.read`, `q.write`, `q.recv` and `q.send` move a `Buf[u8]` into the ring, so the program cannot touch storage the kernel is using (`E-MOVED`), and `q.accept` takes none. `let data = q.next(tag, result);` waits for the next operation to finish and hands its `Buf` back with its tag and the kernel's result, a count or a negative errno, which `io.outcome(result)` turns into a `Result`.

A ring records no lease, so it may be lent `rw` to a callee or a task. It is never stored, passed by value or returned (`E-PINNED`), and it is linear: `wait(q)` consumes it in its function (`E-LINEAR-LEAK`) once every operation has finished, releasing every uncollected `Buf`. `defer wait(q);` covers every exit.

```cairn
import std.core (Result);
import std.io as io;
extern fn pipe(fds:rw<i32>[2]) -> i32 effects(io);

fn opened(fds:rw<i32>[2]) -> i32 { unsafe { return pipe(fds); } }

fn main() -> i32 {
  let mut fds = Array[i32, 2]();
  let made = opened(fds);
  if made != 0 { return 1; }
  let mut q = IoRing(4);
  defer wait(q);                                  // on every exit, after the kernel is done
  let into = Buf[u8](16);
  q.read(fds[0], into, 16, 0, 1);                 // waits in the kernel, not in a thread
  let mut hello = Buf[u8](5);
  hello[0] = 104;
  q.write(fds[1], hello, 5, 0, 2);
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  q.next(tag, result);                            // the write finishes first
  let read = q.next(tag, result);                 // then the read, holding the five bytes
  match io.outcome(result) {
    Ok(n) => { if tag != 1 || n != 5 || read[0] != 104 { return 2; } }
    Err(_) => return 3;
  }
  return 0;
}
```

```cairn rejects E-MOVED
fn main() -> i32 {
  let mut q = IoRing(2);
  let data = Buf[u8](8);
  q.read(0, data, 8, 0, 1);
  let first = data[0];                            // the kernel may be writing it
  wait(q);
  return 0;
}
```

```text
data was moved.
```

`q.timeout(ns, tag)` finishes after `ns` nanoseconds with `-ETIME`, which bounds a wait. `q.cancel(tag)` stops every operation under that tag; each still comes back through `next`, with `-ECANCELED` or its own result, and with its `Buf`.

Every submission comes back through `next` exactly once, and what the environment decides is a value there: a kernel short of memory returns it at once with `-EAGAIN` or `-ENOMEM`, and one that will not set up a ring, as under some container filters, leaves `q.status()` negative and returns every submission with that errno.

What the program decides stays a guard: a submission to a full ring, or a `next` with nothing in flight, traps. `q.room()` and `q.pending()` say how many submissions the ring still takes and how many `next` still owes, without entering the kernel.

```cairn
import std.core (Result);
import std.io as io;

// Take work while there is room, and count what was turned away.
fn submit_all(q:rw<IoRing>, jobs:usize) -> u64 {
  let mut refused:u64 = 0;
  for k in 0..jobs {
    if q.room() == 0 { refused += 1; } else { q.timeout(1000000, u64(k)); }            // a millisecond each
  }
  return refused;
}

fn main() -> i32 {
  let mut q = IoRing(4);
  defer wait(q);
  match io.outcome(q.status()) {
    Ok(_) => {}
    Err(_) => return 1;                // no io_uring here: fall back or report it
  }
  let refused = submit_all(q, 6);
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  while q.pending() > 0 { q.next(tag, result); }
  if refused != 2 { return 2; }
  return 0;
}
```

A ring is a host object (`E-PLACEMENT`). A host lane may read its three queries but not submit, collect, cancel or wait (`E-PARALLEL-CALL`).

## Atomics and mutexes

`Atomic[T]` (integers and `bool`) and `Mutex[T]` are declared in place and shared by `ro` borrow. They are the only interior mutability, and they are never stored, passed by value or returned (`E-PINNED`). Every atomic access names its memory order.

```cairn
fn count_live(n:usize, xs:ro<u64>[n], live:ro<Atomic[u64]>) {
  for x in xs { if x > 0 { live.fetch_add(1, Order.relaxed); } }
}

fn main() -> i32 {
  let mut samples = Buf[u64](4);
  samples[1] = 7;
  samples[3] = 9;
  let live = Atomic[u64](0);
  let t = spawn count_live(samples, live);
  wait(t);
  if live.load(Order.seq_cst) != 2 { return 1; }
  return 0;
}
```

A mutex has one operation, `with`, which may return a value. No guard object exists to escape, the closure cannot name the mutex it holds (`E-ALIAS`), and a thread that reaches the same mutex again traps instead of deadlocking.

```cairn
fn main() -> i32 {
  let bytes_sent = Mutex[u64](0);
  bytes_sent.with(|total:rw<u64>| { total += 1480; });
  let seen = bytes_sent.with(|total:rw<u64>| -> u64 { return total; });
  if seen != 1480 { return 1; }
  return 0;
}
```

## Parallel regions

`parallel i in n { body }` runs one lane per index and completes before the next statement. Whatever any lane writes may be touched only at element `[i]` or inside the lane's own block (`E-PARALLEL-RACE`), a shared scalar cannot be assigned (`E-PARALLEL-WRITE`: use `reduce`), and lanes cannot return, nest or move an outer owner. What a lane calls must be pure-like (`E-PARALLEL-CALL`), though a host lane may also allocate, use atomics and lock.

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
out is written by lanes, so every lane may touch only out[i], or only its own block out[i * S + j] with j below one constant S.
```

A lane may own a block instead: with a constant stride `S`, lane `b` may touch `out[b * S + j]` for any `j` the checker shows is below `S`, and lend a part of its block to a helper. An access the checker cannot place in the lane's block is `E-PARALLEL-RACE`.

```cairn
const BLOCK:usize = 4096;

fn histogram(n:usize, bins:rw<u64>[256], x:ro<u32>[n], k:usize, rows:usize, partial:rw<u64>[rows]) {
  parallel b in k {                                  // lane b owns partial[b * 256 .. b * 256 + 256]
    let lo = b * BLOCK;
    let hi = min(lo + BLOCK, n);
    let row = b * 256;
    for i in lo..hi {
      let v = usize(x[i] & 255);
      partial[row + v] = add_wrap(partial[row + v], 1);
    }
  }
  for v in 0..256 {
    let mut t:u64 = 0;
    for b in 0..k { t = add_wrap(t, partial[b * 256 + v]); }
    bins[v] = t;
  }
}
```

```cairn rejects E-PARALLEL-RACE
fn spill(k:usize, n:usize, out:rw<u64>[n]) {
  parallel b in k { for j in 0..9 { out[b * 8 + j] = 1; } }   // j = 8 is the next lane's first element
}
```

A lane may call a `fn` parameter of its function, which leaves `lane:f` in the row. What is finally passed is judged where it is written: a closure that reads its captures is accepted, and one that writes them is not.

```cairn rejects E-PARALLEL-CALL
fn shade(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }
fn main() -> i32 {
  let n:usize = 64;
  buffer squares:u64[n] = zeroed;
  let mut calls:u64 = 0;
  shade(n, squares, |x:u64| -> u64 { calls += 1; return x; });
  return 0;
}
```

```text
shade calls f from parallel lanes, where it cannot write:calls.
```

Host lanes are one pool of a thread per core, or `CAIRN_LANES`, made by the first region; a region below 16384 elements runs as the ordinary loop on the calling thread. The number of lanes shows only in the time, never in a result.

## Plans

A plan says how a function's regions run, apart from the code that says what they compute. Every split of a region's indices is one its race-free lanes already allow, so a plan changes how long a region takes and never its result, row or guards. `plan f { grain G; lanes L; }` has every host region in `f` claim at least `G` indices at a time on at most `L` lanes.

```cairn
fn mix(v:u64) -> u64 {
  let mut w = v;
  for _ in 0..20000 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }

plan spread { grain 1; lanes 8; }     // a few dozen slow lanes: one index per claim, eight threads
```

Without a plan the pool claims at least 8192 elements at a time, which suits cheap bodies. A body that costs microseconds per index wants a grain of 1.

A device region takes `block B` (threads per block, whole warps from 32 to 1024), `per_lane K` (about `K` indices per thread) and `unroll U` (from 1 to 32). Every index still runs exactly once. Without a plan a device region launches blocks of 256 threads and one index per thread.

```cairn
fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }

plan scale { block 128; per_lane 4; unroll 4; }   // 128 threads a block, about four indices each
```

`vector W` has each device lane run `W` adjacent indices with one load and one store of at most 16 bytes per array it touches only at `x[i]`, so an `f32` array takes `vector 4` at most. A pointer not aligned to the chunk width runs the scalar lanes instead. `E-PLAN` refuses a width that is not a power of two, a chunk wider than 16 bytes, and `vector` beside `fuse`.

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}

plan saxpy { vector 4; }   // two 128-bit loads and one 128-bit store for every four indices
```

```cairn rejects E-PLAN
fn wide(n:usize, out:rw<f64>[n]@device) { parallel i in n { out[i] = 1.0; } }

plan wide { vector 4; }    // four f64 are 32 bytes, and a lane moves 16 at once
```

`stage R` has each block of a device region load once into shared memory the elements its lanes read at `x[i + d]` with `|d|` at most `R`, from 1 to 32, for every array the region only reads. The lanes then read the tile between two barriers. `E-PLAN` refuses a region with nothing to stage, and `stage` beside `vector` or `fuse`.

```cairn
fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}

plan blur { stage 1; block 128; }   // each element of x crosses from memory once per block
```

`cairn predict` prices a staged region as the unplanned one: what a tile saves is what the device's caches would have missed, which only a device run measures. [`cairn tune`](tools.md#cairn-tune) reads a staged candidate's registers and tile from its compile, so a tile that costs resident warps is priced as costing them.

`fuse K` runs up to `K` adjacent regions, from 2 to 16, as one traversal: each lane runs the first body at its index, then the next. The regions must share a placement and an extent, touch what they write only at their own index, and have bodies that cannot trap or be observed from outside. A local array only the chain touches then lives in each lane as one value and is never allocated. A host `reduce` over the same extent may end the chain, keeping its fold order.

```cairn
fn blend(n:usize, out:rw<f64>[n], x:ro<f64>[n], a:f64, b:f64) {
  buffer scaled:f64[n] = zeroed;
  parallel i in n { scaled[i] = a * x[i]; }
  parallel j in n { out[j] = scaled[j] + b; }
}

plan blend { fuse 2; }     // one pass over x and out; scaled lives in each lane, never in memory

fn energy(n:usize, x:ro<f64>[n]) -> f64 {
  buffer squared:f64[n] = zeroed;
  parallel i in n { squared[i] = x[i] * x[i]; }
  let e = reduce + for i in n yield squared[i];       // the in-order sum, as it was
  return e;
}

plan energy { fuse 2; }    // one fold that squares as it adds
```

No body may trap, because fusing two trapping bodies could let the later body's guard fail first. Fusion is a plan item rather than automatic because one fused body is not always faster than two short loops that each vectorize. `--keep-guards` never fuses, and the receipt lists every chain under `fused`. On one shared host, fused element-wise regions ran 1.1x to 2.6x faster than as written, and chains that no longer allocate their scratch 1.5x to 62x faster, most of that at ten million elements and more ([evidence/v1_0/fusion](../evidence/v1_0/fusion/README.md)).

`E-PLAN` refuses a plan for a function without the kind of region an item needs, a second plan for one function, an unknown or repeated item, a value out of range (a grain of 0, lanes outside 1 to 1024, `per_lane` outside 1 to 65536), and a `fuse` with no two regions it may join. `plan` is a keyword only at the top of a module.

```cairn rejects E-PLAN
fn walk(n:usize, out:rw<u64>[n]) { for i in 0..n { out[i] = 1; } }
plan walk { grain 64; }
```

```cairn rejects E-PLAN
fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }
plan scale { block 100; }
```

```text
block runs from 32 to 1024, a multiple of 32; 100 is outside.
```

```cairn rejects E-PLAN
fn count(n:usize, out:rw<u64>[n], x:ro<u64>[n]) {
  parallel i in n { out[i] = x[i] + 1; }       // checked: it traps if x[i] is the largest u64
  parallel j in n { out[j] = out[j] * 2; }
}
plan count { fuse 2; }
```

With the schedule apart from the algorithm, as in Halide, every plan [`cairn tune`](tools.md#cairn-tune) tries is correct and it only ranks them; whether that makes tuning cheaper than rewriting the loop has not been measured.

## reduce and compact

`reduce` combines with one of `add_wrap mul_wrap & | ^ min max` on integers, or `+ *` on floats. On the host it is an in-order fold. Over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats.

Checked `+` is allowed on unsigned integers, where no partial sum can overflow unless the total does, so whether it traps cannot depend on the order. Signed `+` and integer `*` are not, because a partial result can overflow alone.

`parallel` in place of `for` runs a host reduction on the lane pool, in blocks fixed by the count alone. Every operator allowed is associative, so the answer is the in-order fold's on any number of lanes, and a checked `+` traps exactly when the in-order fold would. Floats are refused (`E-REDUCE-ORDER`), because a sum in blocks is a different function of the same inputs. Each `yield` runs under the rules of a lane.

```cairn
fn checksum(n:usize, bytes:ro<u8>[n]) -> u64 {
  let total = reduce + parallel i in n yield u64(bytes[i]);    // on the lane pool, and still checked
  let mixed = reduce ^ parallel i in n yield mul_wrap(u64(bytes[i]), 0x9e3779b97f4a7c15);
  return total ^ mixed;
}
```

```cairn rejects E-REDUCE-ORDER
fn dot(n:usize, x:ro<f64>[n], y:ro<f64>[n]) -> f64 {
  let s = reduce + parallel i in n yield x[i] * y[i];
  return s;
}
```

On the device a reduction is a tree either way, so `parallel` there changes nothing.

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

`compact` writes the stable selected prefix into storage of capacity exactly `n`, evaluating the predicate once per input and the projection only when selected. It leaves the tail unchanged and allocates nothing on the host. Over a `@device` output it is stream compaction with device scratch (`gpu_alloc`, `gpu_free`). Its one unchecked store rests on seventeen certificates proved in Lean ([verification.md](verification.md)).

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

## scan

`scan` writes every prefix into an array. `let total = scan + out for i in n yield e;` sets `out[i]` to `e(0) + ... + e(i)` and binds the whole, and `scan + exclusive out ...` sets `out[i]` to what came before `i`. The operators are `reduce`'s (`E-SCAN-OP`). The output is an `rw` view or buffer of exactly the scan's count (`E-SCAN-TARGET`, `E-SCAN-EXTENT`), and a yield may read it only at its own element (`E-PARALLEL-RACE`), so a scan may run in place.

```cairn
fn offsets(n:usize, starts:rw<usize>[n], sizes:ro<usize>[n]) -> usize {
  let used = scan + exclusive starts for i in n yield sizes[i];   // where each piece begins, and the whole
  return used;
}

fn main() -> i32 {
  let n:usize = 4;
  buffer sizes:usize[n] = zeroed;
  buffer starts:usize[n] = zeroed;
  for i in 0..n { sizes[i] = i + 1; }
  let used = offsets(n, starts, sizes);
  if used != 10 || starts[0] != 0 || starts[3] != 6 { return 1; }
  scan max sizes for i in n yield sizes[i] * 2;                    // in place: each yield reads its own element
  if sizes[0] != 2 || sizes[3] != 8 { return 2; }
  return 0;
}
```

`parallel` in place of `for` runs the scan on the lane pool in two passes, with the in-order result on any number of lanes. Floats scan only in the written order (`E-SCAN-ORDER`). Over `@device` views the scan is CUB's; it compiles for the device and runs only under `make gpu`.

```cairn rejects E-SCAN-ORDER
fn running(n:usize, out:rw<f64>[n], x:ro<f64>[n]) { scan + out parallel i in n yield x[i]; }
```

`std.sort.radix_sort` uses `scan + exclusive` to place each digit, so it is stable and allocates nothing. On one shared sixteen-lane machine the pooled scan ran 1.3 to 1.7 times faster than the loop from a hundred thousand to ten million `u64` elements, and the radix sort 5.6 to 10.5 times faster than the heapsort (`evidence/v1_0/scan/`).

## Placement and device memory

A view's placement is part of its type: `@host` (the default), `@pinned`, `@unified`, `@device`. A region whose body indexes a `@device` view runs as CUDA lanes, and otherwise on host threads, from the same lane body. Host code cannot index `@device` memory, and device lanes cannot index host memory (`E-PLACEMENT`). `@unified` is visible to both, and a `@pinned` view serves wherever a `@host` one is asked for. `transfer(dst, src)` is the only way elements cross, with extents that agree and sides that do not overlap.

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

Whatever a device lane reaches is device code. A helper with a host view parameter or a host-only construct is refused there (`E-PLACEMENT`), and pure helpers run on either side.

```cairn rejects E-PLACEMENT
fn peek(m:usize, host_side:ro<u64>[m]) -> u64 = host_side[0];
fn go(n:usize, d:rw<u64>[n]@device, m:usize, host_side:ro<u64>[m]) {
  parallel i in n { d[i] = peek(m, host_side); }
}
```

```text
A device lane reaches peek, where host_side is a host view.
```

`kernel fn` declares a device helper, callable only from device lanes and other kernels (`E-PLACEMENT` anywhere else).

```cairn
kernel fn at(w:usize, n:usize, grid:ro<f32>[n]@device, x:usize, y:usize) -> f32 = grid[y * w + x];

fn blur(w:usize, n:usize, out:rw<f32>[n]@device, grid:ro<f32>[n]@device) {
  parallel i in n { out[i] = 0.5 * at(w, n, grid, i % w, i / w); }
}
```

## Queued device work

`spawn transfer(...)` and `spawn parallel ... after t { }` queue a transfer or a device region on its own stream and return at once. The ticket is linear and leases every view the work touches until `wait`. `after a, b` orders the new work behind live tickets by device events, never by stopping the host, and work queued after a ticket may touch what that ticket holds. Only device work is queued this way (`E-SPAWN`). A failed enqueue or a lost device traps, and there is no cancellation.

```cairn
fn stage(n:usize, host_x:ro<f32>[n], x:rw<f32>[n]@device, out:rw<f32>[n]@device) {
  let up = spawn transfer(x, host_x);
  let work = spawn parallel i in n after up { out[i] = 2.0 * x[i]; };
  wait(up);
  wait(work);
}
```

## Device execution

Device work runs on the calling thread's execution context (`runtime/cairn_exec.hpp`): a stream and its event, one scratch arena and a budget, made by the thread's first device operation and kept. A region over device views, and a `transfer`, returns once that stream has run it, so the host sees the result and a guard that fired in a lane aborts the process; nothing waits for the rest of the device. A device `reduce`, `scan` or `compact` takes its temporaries from the arena, which grows to the largest request it has met. Queued work borrows a lane that comes back at its `wait`.

Run again, a pipeline makes no stream, allocates no temporary and waits only for its own stream. On a host machine that counts them (`tests/runtime/test_execution.py`), one pipeline of regions, a vector and a staged plan, a reduction, a scan, a compaction, transfers and two queued tickets made two streams and three arena allocations on its first pass and none after, with nine stream waits a pass ([evidence](../evidence/v1_0/execution/README.md)). Its CUDA build compiles for sm_120 without `cudaDeviceSynchronize` and has not run on a GPU. A device `mma_unordered` runs on the same stream and waits only for it.

A region does not wait for queued work it does not touch; each ticket is waited for at its own `wait`. On a device without concurrent managed access (Windows and WSL2), a live ticket's kernel may still run after a region returns, and the host must not touch `@unified` memory while any kernel runs.

A C program that owns a stream hands it to a device library with `NAME_device_stream(stream)`, which `cairn build --header` declares ([tools.md](tools.md#cairn-build---header)). The calling thread's synchronous device work then runs on that stream, after what the caller queued there, and queued work starts after it too; `NULL` gives the thread its own stream back. A device view the library takes is a pointer to memory the caller owns.

## Cooperative regions

`blocks b in G threads t in T { body }` runs `G` blocks of `T` threads. A block's threads share the arrays the body declares `shared` and wait for each other at `barrier`, so, unlike `parallel` lanes, they may read what another thread wrote once a barrier lies between. A region runs on the device when a view it indexes is `@device`, and on host threads otherwise.

```cairn
fn block_sums(n:usize, x:ro<u64>[n], g:usize, out:rw<u64>[g]) {
  blocks b in g threads t in 256 {
    shared partial:u64[256] = zeroed;              // one per block, zeroed where the block starts
    let i = b * 256 + t;
    if i < n { partial[t] = x[i]; }
    barrier;                                       // every thread's element is in place
    for k in 0..3 {
      let s:usize = shr(128, k);
      if t < s { partial[t] = partial[t] + partial[t + s]; }
      barrier;
    }
    if t < 32 {                                    // the first warp, whole
      let total = reduce + warp yield partial[t];
      if t == 0 { out[b] = total; }
    }
  }
}
```

Each side names up to three binders, fastest first: in `blocks bx, by in gx, gy threads tx, ty in 32, 8`, thread `(tx, ty)` is thread `tx + 32 * ty` of its block. The grid's extents are any `usize` values; the thread extents are literals or constants whose product is a whole number of warps, 32 to 1024 (`E-COOP-SHAPE`). A `shared` array is declared directly in the body with a constant length and starts on 128 bytes, where a tensor-core fragment may load from it ([numerics.md](numerics.md#tensor-core-fragments)), and a block's arrays hold at most 48 KiB together (`E-COOP-SHARED`).

Every thread of the block must reach a barrier, so a barrier may not sit under a condition that depends on a thread's name, nor in a loop a `break` or `continue` can leave early (`E-COOP-BARRIER`). The warp operations `shuffle(v, lane)`, `shuffle_xor(v, mask)`, `shuffle_down(v, delta)` and `reduce OP warp yield v` need every thread of the warp (`E-COOP-WARP`), which a condition such as `t < 32`, `t / 32 == w`, or `ty < 4` when `tx` counts 32 keeps whole. A condition decides who arrives only where it is the same in every thread it could split: a local assigned from a thread's name, lent `rw` to a call, written through one of its elements or assigned by a closure differs, as does what an atomic or typed `asm` returns, and the right side of `&&` and `||` runs only in the threads the left side lets through. `reduce OP warp` takes `reduce`'s operators and combines in a fixed butterfly, halves then quarters down to neighbours, so every thread gets the same answer on the host and the device.

```cairn rejects E-COOP-BARRIER
fn f(g:usize) {
  blocks b in g threads t in 64 {
    if t < 5 { barrier; }                          // threads 5 to 63 would never arrive
  }
}
```

Between two barriers, a phase, no two threads of a block may touch one element of a shared array where either writes. The checker runs the body for every thread of one block, with the block's names and everything outside the region as symbols, and refuses a phase where two threads write one element (`E-COOP-CONFLICT`), a thread reads what another writes earlier in the phase (`E-COOP-UNORDERED`), or a thread writes over what another may still be reading (`E-COOP-REUSE`). An index it cannot follow, such as one read from data or one that differs from another by a value known only at run time, is `E-COOP-UNDECIDED` beside a write.

```cairn rejects E-COOP-UNORDERED
fn reverse(g:usize, out:rw<u64>[g]) {
  blocks b in g threads t in 256 {
    shared s:u64[256] = zeroed;
    s[t] = u64(t);
    let v = s[255 - t];                            // written by thread 255 - t, with no barrier between
    if t == 0 { out[b] = v; }
  }
}
```

```text
thread t = 255 reads s[0] at line 5, which thread t = 0 writes at line 4 in the same phase: nothing makes the write happen first. Put a barrier after line 4 and before line 5 runs.
```

```cairn rejects E-COOP-REUSE
fn shift(g:usize, out:rw<u64>[g]) {
  blocks b in g threads t in 256 {
    shared s:u64[256] = zeroed;
    s[t] = u64(t);
    barrier;
    let v = s[(t + 1) % 256];
    s[t] = v;                                      // thread t - 1 may not have read s[t] yet
  }
}
```

An array from outside the region is shared by every block, and no barrier orders two blocks, so each of its elements may be written by at most one thread of one block, and read by another only if nobody writes it (`E-COOP-GLOBAL`). The checker shows this when the index is a sum of the block, thread and loop names, each times a weight larger than all the lighter terms add up to: `b * 256 + t`, or the transpose's `(bx * 32 + ty + 8 * k) * h + by * 32 + tx` when `h` is `32 * gy`. A condition on the index, `if col < h`, counts toward that bound, when the names it sums all count up or all count down. A loop whose range moves with another name, such as `for c in l..l + 2`, is bounded by nothing. A thread may read the elements it writes, as `c += ...` does, when the read's index is the write's, in the same loop or in another over the same range, under the write's conditions.

```cairn
// out is x transposed: x has 32 * gy rows of 32 * gx elements.
fn transpose(gx:usize, gy:usize, n:usize, out:rw<f32>[n], x:ro<f32>[n]) {
  let w = gx * 32;
  let h = gy * 32;
  blocks bx, by in gx, gy threads tx, ty in 32, 8 {
    shared tile:f32[1056] = zeroed;                // 32 rows of 33: a column's elements in 32 banks
    for k in 0..4 {
      let r = ty + k * 8;
      tile[r * 33 + tx] = x[(by * 32 + r) * w + bx * 32 + tx];
    }
    barrier;
    for k in 0..4 {
      let r = ty + k * 8;
      out[(bx * 32 + r) * h + by * 32 + tx] = tile[tx * 33 + r];
    }
  }
}
```

A closure in the body may run in any phase and any thread, so it names no shared array, pipeline or array from outside (`E-COOP-UNDECIDED`). A thread obeys everything a lane obeys: it cannot assign a scalar from outside (`E-PARALLEL-WRITE`), start another region (`E-PARALLEL-NEST`), do I/O (`E-PARALLEL-CALL`), or reach the other side's memory (`E-PLACEMENT`). The row gains `par:device` or `par:host`, `zero_init` for the shared arrays, and `trap` for the guards; the receipt lists each array's bytes and the block's total under `local_storage`.

On the device the region is one launch on the thread's execution context, as `parallel` is: blocks of `T` threads, the arrays in static shared memory, `barrier` as `__syncthreads()` and the warp operations as `__shfl_*_sync` over the whole warp. It compiles for `sm_120` and has not run on a GPU. On the host each block's threads are real threads meeting at a `std::barrier`, two blocks at a time, so the thread sanitizer checks the phase rule on real runs; this lowering creates `2 * T` threads per region and is not a fast path.

A `pipeline` is shared memory the block fills from an outside array while its threads read another part of it. `pipeline tiles:u64[256] depth 2;` declares two stages of 256 elements of a 4- or 8-byte scalar. `tiles.fill(x, start, count)` starts copying `x[start .. start + count]` into the next free stage and zeroes the rest of it. `tiles.wait()` waits, in every thread, for the oldest stage in flight, which then reads as `tiles[i]`, and `tiles.release()` marks it read, to be freed at the next barrier. The whole block reaches each of these together (`E-COOP-BARRIER`).

```cairn
// out[r * 256 + t] is the sum of x[r * cols + t], x[r * cols + t + 256], ...: row r, a tile at a time.
fn strided_sums[D:nat](rows:usize, cols:usize, n:usize, x:ro<u64>[n], m:usize, out:rw<u64>[m]) {
  let steps = (cols + 255) / 256;
  blocks r in rows threads t in 256 {
    pipeline tiles:u64[256] depth D;
    for k in 0..D - 1 {                            // D - 1 tiles on their way before the first is read
      let start = min(k * 256, cols);
      tiles.fill(x, r * cols + start, min(256, cols - start));
    }
    let mut sum:u64 = 0;
    for k in 0..steps {
      let ahead = min((k + D - 1) * 256, cols);
      tiles.fill(x, r * cols + ahead, min(256, cols - ahead));
      tiles.wait();                                // tile k has landed
      sum += tiles[t];
      tiles.release();
      barrier;                                     // every thread has read tile k: its stage is free
    }
    out[r * 256 + t] = sum;
  }
}

fn double(rows:usize, cols:usize, n:usize, x:ro<u64>[n], m:usize, out:rw<u64>[m]) { strided_sums[2](rows, cols, n, x, m, out); }
```

The checker follows each stage through the body: available, transfer in flight, readable, readers in flight, available again at the next barrier. It refuses a read, `release` or `wait` with no stage in the state it needs (`E-STAGE-UNREADY`), a `fill` with no free stage and a `wait` while a stage is still readable (`E-STAGE-BUSY`), and a loop or an `if` that leaves a pipeline in another state than it found it, or a `break` or `continue` in a loop that operates on one (`E-STAGE-LOOP`). Any use of `tiles` but as the receiver of its operations, `first(tiles)` or `tiles[0..4]`, reads the readable stage. A fill copies from an array from outside the region that lives where the region runs (`E-PLACEMENT`).

```cairn rejects E-STAGE-BUSY
fn sums(rows:usize, cols:usize, n:usize, x:ro<u64>[n], m:usize, out:rw<u64>[m]) {
  let steps = (cols + 255) / 256;
  blocks r in rows threads t in 256 {
    pipeline tiles:u64[256] depth 2;
    tiles.fill(x, r * cols, min(256, cols));
    let mut sum:u64 = 0;
    for k in 0..steps {
      let ahead = min((k + 1) * 256, cols);
      tiles.fill(x, r * cols + ahead, min(256, cols - ahead));
      tiles.wait();
      sum += tiles[255 - t];
      tiles.release();                             // no barrier: the next fill reuses this stage
    }
    out[r * 256 + t] = sum;
  }
}
```

```text
tiles.fill at line 9 would refill the stage of tiles released at line 12 while other threads may still be reading it (tiles[...] at line 11). Put a barrier after line 12 and before line 9 runs.
```

The depth is a constant of the declaration, and raising it changes only the block's shared memory (depth times the stage's bytes, in the receipt's `local_storage` and the kernel's static shared memory) and how many copies a `wait` leaves in flight, which the checker counts (`cp.async.wait_group 1` at depth 2, `2` at depth 3): `strided_sums[2]` and `strided_sums[3]` compute the same sums. [`cairn predict`](tools.md#cairn-predict) prices what the depth changes: the shared memory, the blocks an SM holds, and the copies each block keeps in flight. On the device a fill is one `cp.async` per element, committed as one group per thread. On the host it is each thread's own copy, so a read the checker let through too early would race with it under the thread sanitizer.

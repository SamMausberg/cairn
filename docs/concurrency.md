# Concurrency

This page shows how a CAIRN program does several things at once on the CPU and the GPU. A task runs a function on a thread of its own, a task group collects tasks as they finish, an I/O ring keeps kernel operations in flight from one thread, and a parallel region or a collector (`reduce`, `compact`, `scan`) runs one body per index, on host threads or on the GPU. Each run of that body is a lane. After reading it you can split work across threads, and you will know what each refusal means and what each form costs. Placement, queued device work, device execution and cooperative regions are in [devices.md](devices.md).

Two rules keep tasks and lanes from racing on memory they share, and the compiler checks both before the program runs. A running task holds what it was lent until the program waits for it. A lane of a parallel region touches only its own element of anything that lanes write.

## Tasks and leases

A task runs a declared function on a thread of its own. `let t = spawn f(args);` evaluates the arguments, then runs `f` on a parked thread if one is free and on a new thread otherwise. `t` is a ticket, and `wait(t)` waits for the task to end and returns `f`'s result.

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

A ticket is linear: `wait(t)` must consume it exactly once on every path of the function that spawned the task (`E-LINEAR-LEAK`, `E-LINEAR-BRANCH`). A ticket stays where it was declared, so it cannot be stored, passed or returned (`E-PINNED`). A closure cannot follow a task to another thread, so a spawned call takes no closure (`E-SPAWN`).

Until the `wait`, every place lent to the task is leased. A place is a local, a field or an element, or a part of an array. A lease is the checker's record that a running task holds a place. While the lease lasts, nobody may write what the task reads or touch what it writes, the owner included (`E-LEASED`). A place lent `ro`, only for reading, may be lent to any number of tasks at once.

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

Parts of one array may be lent `rw`, for reading and writing, to different tasks when each part visibly ends where the next begins, so an array can be split among any number of tasks. A bound is visible when it is a literal, a name that cannot change, or an expression of literals and constants, which counts as the number it folds to. With `const BINS:usize = 256;`, the parts `d[0..BINS]` and `d[BINS..2 * BINS]` are disjoint.

A lease covers the place that was lent and nothing wider than it, whichever local that place belongs to. Two tasks may therefore take two fields of one record, and `len(box.a)` still reads while a task holds the elements of `box.a`. Lending the record itself leases every field.

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

The checker refuses lending one field to two tasks, and replacing a field while it is lent (`pair.left = Buf[u64](2)`).

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

A task group collects tasks in the order they finish. Separate tickets are waited for in the order the program writes the waits. `let g = Group[T](n);` declares, in place and with all its storage, a group of at most `n` tasks in flight whose results have type `T`.

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

`spawn f(args) into g;` hands a task to the group, and `f` must return `T` (`E-TYPE-MISMATCH`). The group holds a lease on everything the task borrows until `wait(g)`. A submission beyond `n` tasks fails a guard, a check at run time whose failure aborts the process. The group never grows.

`let r = collect(g);` returns the result of whichever task finishes next. A `collect` with no task outstanding fails a guard. It gives back no lease, because the checker cannot know which task finished. `wait(g)` joins every task, drops the results nobody collected, releases the leases and consumes the group.

A group is linear and stays where it was declared. `wait(g)` must consume it on every path (`E-LINEAR-LEAK`, `E-LINEAR-BRANCH`), and it is never stored, passed, lent to a callee or returned (`E-PINNED`).

Inside a loop, a group may be lent only what its tasks read. Lending an `rw` place is refused because the next iteration would lend it again while the group still holds it. An owner, such as a `Buf`, passed by value moves into its task ([memory.md](memory.md#owners-and-moves)), so a loop may hand the group a fresh `Buf` each time round. After an `if` or a `match`, the group holds whatever any path lent it.

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

Queued device work cannot join a group (`E-SPAWN`). A group whose result type is `linear` is refused, because `wait` might drop a result (`E-LINEAR-STORAGE`). A group is a host object, and a device lane cannot use one (`E-PLACEMENT`).

## Parallel regions

A parallel region runs one body per index. `parallel i in n { body }` runs the body once for each `i` below `n`, and each of those runs is a lane. The region completes before the next statement starts. Its lanes run on a pool of host threads, or on CUDA threads when the body indexes `@device` views ([devices.md](devices.md)).

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

The lane rule: whatever any lane writes may be touched only at element `[i]`, or inside the lane's own block (`E-PARALLEL-RACE`). Apart from atomic updates, two lanes therefore never touch one element where either writes, so the lanes of a region cannot race.

```cairn rejects E-PARALLEL-RACE
fn shade(n:usize, out:rw<u64>[n]) { parallel i in n { out[0] = u64(i); } }
```

```text
out is written by lanes, so every lane may touch only out[i], or only its own block out[i * S + j] with j below one constant S.
```

A lane may own a block of elements in place of one. With a constant stride `S`, lane `b` may touch `out[b * S + j]` for any `j` the checker shows is below `S`, and it may lend a part of its block to a helper. An access the checker cannot place inside the lane's block is `E-PARALLEL-RACE`.

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

A lane cannot assign a scalar from outside the region (`E-PARALLEL-WRITE`); a total across lanes is what [`reduce`](#reduce-and-compact) computes. A lane cannot return from the function (`E-PARALLEL-CONTROL`), start another region (`E-PARALLEL-NEST`) or move an owner from outside the region (`E-MOVE-IN-LOOP`).

What a lane calls may do what a `pure` function may, and also write what it was lent: it may trap, loop, use its own locals, and read and write the places it was lent. It may not do I/O, call a foreign function, start a task or touch a machine register (`E-PARALLEL-CALL`). A host lane may also allocate, use atomics and take locks. A device lane may also run typed PTX and fences ([memory.md](memory.md#layout-and-the-machine)). Any lane may [update an element atomically](#atomics-and-mutexes).

A lane may call a `fn` parameter of its function, which leaves `lane:f` in the function's effect row, the list of effects the compiler infers for every function. The checker then judges the function value where the caller writes it: a closure that only reads its captures is accepted, and one that writes them is refused.

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

Host lanes run on one pool, made by the first region, of one thread per core or `CAIRN_LANES` threads. A region below 16384 elements runs as the ordinary loop on the calling thread. The number of lanes changes how long a region takes, and it never changes a result.

## reduce and compact

`reduce` combines one value per index into a total. `let s = reduce + for i in n yield e;` adds `e` over every `i` below `n`. The operators are `add_wrap`, `mul_wrap`, `&`, `|`, `^`, `min` and `max` on integers, and `+` and `*` on floats.

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

On the host a reduction folds in index order. Over device views it combines in a tree whose order is unspecified. The integer operators give the same total in any order. A float sum or product on the device may differ in the last places from the fold in index order.

Checked `+` is allowed on unsigned integers. No partial sum of unsigned values can overflow unless the total does, so whether the sum traps cannot depend on the order. Signed `+` and integer `*` are refused (`E-REDUCE-OP`), because a partial result can overflow while the total fits.

Writing `parallel` in place of `for` runs a host reduction on the lane pool, in blocks fixed by the count alone. Every operator allowed is associative, so the answer equals the fold in index order on any number of lanes, and a checked `+` traps exactly when that fold would. Floats are refused (`E-REDUCE-ORDER`), because a sum in blocks is a different function of the same inputs. Each `yield` runs under the rules of a lane.

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

A reduction may write its total into one element in place of binding it. `reduce op out[k] for i in n yield e;` is the fold, then `out[k] = total`, as one statement. `out` is an `rw` view or a buffer (`E-REDUCE-TARGET`) of the yields' type (`E-TYPE-MISMATCH`). `k` is evaluated once, before the lanes start, and no yield may read `out` (`E-PARALLEL-RACE`). The operators, the order rules and the effect row are those of `reduce`, with `write:out` added. A loop of them fills a row of totals:

```cairn
fn row_sums(r:usize, c:usize, rc:usize, x:ro<u64>[rc], sums:rw<u64>[r]) {
  for k in 0..r { reduce + sums[k] for j in c yield x[k * c + j]; }   // checked: traps if a row's total overflows
}
```

```cairn rejects E-REDUCE-TARGET
fn f(n:usize, x:ro<u64>[n]) { reduce + x[0] for i in n yield x[i]; }
```

Over `@device` views the total stays in device memory, where the next region reads it ([devices.md](devices.md#results-that-stay-on-the-device)).

`compact` keeps the elements that pass a test. `compact out for i in n where p yield e` writes `e` for each selected `i`, in order, to the front of `out`, whose capacity is exactly `n`, and returns how many it wrote. It evaluates the test once per input and the projection `e` only for a selected input. It leaves the rest of `out` unchanged and allocates nothing on the host.

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

Over a `@device` output, `compact` is stream compaction with scratch in device memory, and its row gains `gpu_alloc` and `gpu_free`. Its one store without a bounds check rests on the collector's seventeen certificates, arithmetic facts proved in Lean ([verification.md](verification.md#the-collector-certificates-and-the-loop-model)).

## scan

`scan` writes every running total into an array. `let total = scan + out for i in n yield e;` sets `out[i]` to `e(0) + ... + e(i)` and binds the whole sum. `scan + exclusive out ...` sets `out[i]` to the sum of what came before `i`.

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

The operators are those of `reduce` (`E-SCAN-OP`). The output is an `rw` view or a buffer (`E-SCAN-TARGET`) of exactly the scan's count (`E-SCAN-EXTENT`). A yield may read the output only at its own element (`E-PARALLEL-RACE`), so a scan may run in place.

Writing `parallel` in place of `for` runs the scan on the lane pool in two passes, and gives the result in index order on any number of lanes. Floats scan only in the written order, with `for` on the host (`E-SCAN-ORDER`).

```cairn rejects E-SCAN-ORDER
fn running(n:usize, out:rw<f64>[n], x:ro<f64>[n]) { scan + out parallel i in n yield x[i]; }
```

Over `@device` views the scan is CUB's. It compiles for the device, and the suite runs it on a device only under `make gpu`, which ran it on one RTX 5070 Ti in the 1.1.0 session ([evidence/v1_1/gpu](../evidence/v1_1/gpu/README.md)). A device scan whose total nobody binds leaves its prefixes in device memory and returns nothing to the host, so nothing waits for it until the host observes something ([devices.md](devices.md#results-that-stay-on-the-device)).

`std.sort.radix_sort` uses `scan + exclusive` to place each digit, so it is stable and allocates nothing. On one shared machine with sixteen lanes, the pooled scan ran 1.3 to 1.7 times as fast as the loop it replaces, from a hundred thousand to ten million `u64` elements, and the radix sort ran 5.6 to 10.5 times as fast as the heapsort (`evidence/v1_0/scan/`).

## Atomics and mutexes

An atomic or a mutex lets tasks share one value that they all change. `Atomic[T]`, for an integer or `bool` `T`, and `Mutex[T]` are declared in place and shared by `ro` borrow. They are the only way to change a value through an `ro` borrow. They are never stored, passed by value or returned (`E-PINNED`). Every atomic access names its memory order.

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

A mutex has one operation, `with`, which runs a closure on the value it guards and may return a value. No guard object exists that could escape. The closure cannot name the mutex it holds (`E-ALIAS`). A thread that reaches the same mutex again while it holds it traps, where it would otherwise deadlock.

```cairn
fn main() -> i32 {
  let bytes_sent = Mutex[u64](0);
  bytes_sent.with(|total:rw<u64>| { total += 1480; });
  let seen = bytes_sent.with(|total:rw<u64>| -> u64 { return total; });
  if seen != 1480 { return 1; }
  return 0;
}
```

One element of an array can be updated atomically too: from host code, from any lane, and from any thread of a cooperative region ([devices.md](devices.md#cooperative-regions)), on the host and the device alike. Each update returns the element's old value.

| Update | Element types | What it computes |
|---|---|---|
| `atomic_add_wrap(x[i], v)` | `u32`, `u64`, `usize` | adds modulo 2^N, as `add_wrap` does |
| `atomic_min`, `atomic_max` | `u32`, `i32`, `u64`, `i64`, `usize` | the smaller or the larger |
| `atomic_and`, `atomic_or`, `atomic_xor` | `u32`, `u64`, `usize` | the bitwise operation |
| `atomic_cas(x[i], expected, desired)` | `u32`, `i32`, `u64`, `i64`, `usize` | `desired` where the element holds `expected` |
| `atomic_add_unordered(x[i], v)` | `f32`, `f64` | adds in the order the threads arrive ([numerics.md](numerics.md#atomic-float-addition)) |

The only atomic add on integers wraps, because no thread sees the whole sum, so an atomic add cannot check it for overflow. Every update is relaxed: the updates of one element happen one at a time, and they order nothing else. The first argument is the element itself, written in place, and its type must be one the update takes (`E-ATOMIC`).

```cairn
fn histogram(n:usize, x:ro<u32>[n]@device, bins:rw<u32>[256]@device) {
  parallel i in n { atomic_add_wrap(bins[usize(x[i] & 255)], 1); }      // any lane, any bin, at once
}

fn claim(n:usize, owner:rw<usize>[8], won:rw<u32>[n]) {
  parallel i in n {
    let old = atomic_cas(owner[i % 8], 0, i + 1);                        // one lane takes each slot
    if old == 0 { won[i] = 1; } else { won[i] = 0; }
  }
}
```

Atomic updates are a class of their own beside plain reads and writes. Two atomic updates never race, so any lane may update any element, and the rules that give each element one writer do not count them. An array that a region updates atomically is not also read or written plainly in that region (`E-ATOMIC-MIXED`). That holds anywhere in a `parallel` region, anywhere in a cooperative region for an array from outside it, and between two barriers for a block's shared array. The program reads the result after the region, after a barrier, or in the region's finish ([devices.md](devices.md#cooperative-regions)).

```cairn rejects E-ATOMIC-MIXED
fn count(n:usize, x:ro<u32>[n], bins:rw<u32>[n]) {
  parallel i in n {
    atomic_add_wrap(bins[usize(x[i]) % n], 1);
    bins[i] = 0;                                   // another lane may be updating bins[i]
  }
}
```

An update adds `atomic`, `read:x` and `write:x` to the effect row, and `trap` for the element's index, which is guarded as any index is.

## Plans

A plan says how a function's regions run, kept apart from the code that says what they compute. `plan f { grain G; lanes L; }` has every host region in `f` claim at least `G` indices at a time, on at most `L` lanes.

```cairn
fn mix(v:u64) -> u64 {
  let mut w = v;
  for _ in 0..20000 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }

plan spread { grain 1; lanes 8; }     // a few dozen slow lanes: one index per claim, eight threads
```

A plan only picks one of the ways to split a region's indices that its lanes already allow, since the lanes cannot race. So a plan changes how long a region takes, and it never changes the region's result, effect row or guards. Without a plan the pool claims at least 8192 elements at a time, which suits cheap bodies. A body that costs microseconds per index wants a grain of 1.

A device region takes three items. `block B` sets the threads per block, whole warps from 32 to 1024. `per_lane K` gives each thread about `K` indices, from 1 to 65536. `unroll U`, from 1 to 32, unrolls each thread's index loop. Every index still runs exactly once. Without a plan a device region launches blocks of 256 threads with one index per thread.

```cairn
fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }

plan scale { block 128; per_lane 4; unroll 4; }   // 128 threads a block, about four indices each
```

`vector W` has each device lane run `W` adjacent indices, with one load and one store of at most 16 bytes for each array the lane touches only at `x[i]`. `W` is a power of two from 2 to 16, and `W` elements must fit in 16 bytes, so an `f32` array takes `vector 4` at most. Where a pointer is not aligned to the chunk's width, the region runs its scalar lanes. A lane that reaches its elements some other way says so itself with [`load_wide` and `store_wide`](devices.md#wide-loads-and-stores).

```cairn
fn saxpy(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, y:ro<f32>[n]@device, a:f32) {
  parallel i in n { out[i] = a * x[i] + y[i]; }
}

plan saxpy { vector 4; }   // two 128-bit loads and one 128-bit store for every four indices
```

`E-PLAN` refuses a width that is not a power of two, a chunk wider than 16 bytes, a region with no array to chunk, and `vector` beside `fuse`.

```cairn rejects E-PLAN
fn wide(n:usize, out:rw<f64>[n]@device) { parallel i in n { out[i] = 1.0; } }

plan wide { vector 4; }    // four f64 are 32 bytes, and a lane moves 16 at once
```

`stage R`, from 1 to 32, applies to every array a device region only reads. Each block loads into shared memory, once, the elements its lanes read at `x[i + d]` with `|d|` at most `R`, and the lanes then read that tile between two barriers. `E-PLAN` refuses a region with nothing to stage, and `stage` beside `vector` or `fuse`.

```cairn
fn blur(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device) {
  parallel i in n {
    if i >= 1 && i + 1 < n { out[i] = x[i - 1] + x[i] * 2.0 + x[i + 1]; }
    else { out[i] = x[i]; }
  }
}

plan blur { stage 1; block 128; }   // each element of x crosses from memory once per block
```

`cairn predict` prices a staged region as it prices the region without the plan. What a tile saves is what the device's caches would have missed, and only a device run measures that. [`cairn tune`](tools.md#cairn-tune) reads a staged candidate's registers and tile from its compile, so a tile that costs resident warps is priced as costing them.

`fuse K`, from 2 to 16, runs up to `K` adjacent regions as one pass over the indices: each lane runs the first body at its index, then the next body. The regions must share a placement, host or device, and an extent, the count of indices they run over, and each lane must touch what the regions write only at its own index. No body may trap, loop without end, or do anything observable from outside. A local array that only the chain touches then lives in each lane as one value and is never allocated. A host `reduce` over the same extent may end the chain, and it keeps its fold order.

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

No fused body may trap, because fusing two bodies that can trap could let the later body's guard fail first. Fusion happens only when a plan asks for it, because one fused body is not always faster than two short loops that each vectorize. A build with `--keep-guards` never fuses, and the build receipt, the record a build writes beside what it built, lists every fused chain under `fused`. On one shared host, fused regions that work element by element ran 1.1 to 2.6 times as fast as the regions as written. Chains that no longer allocate their scratch ran 1.5 to 62 times as fast, most of that at ten million elements and more ([evidence/v1_0/fusion](../evidence/v1_0/fusion/README.md)).

`E-PLAN` also refuses a plan for a function without the kind of region an item needs, a second plan for one function, and an unknown or repeated item. It refuses a value out of range: a grain of 0, lanes outside 1 to 1024, a `block` that is not a multiple of 32 from 32 to 1024, `per_lane` outside 1 to 65536, `unroll` or `stage` outside 1 to 32, `fuse` outside 2 to 16. It refuses a `fuse` with no two regions it may join. `plan` is a keyword only at the top of a module.

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

Keeping the schedule apart from the algorithm, as Halide does, means every plan [`cairn tune`](tools.md#cairn-tune) tries computes what the function computes without a plan, and the tuner only ranks the plans. Whether that makes tuning cheaper than rewriting the loop has not been measured.

## I/O rings

An I/O ring keeps many kernel operations in flight from one thread and hands them back in the order they finish. `let mut q = IoRing(n);` declares in place a Linux io_uring that holds 1 to 4096 operations.

`q.read`, `q.write`, `q.recv` and `q.send` each move a `Buf[u8]` into the ring, so the program cannot touch storage the kernel is using (`E-MOVED`). `q.accept` takes no buffer. `let data = q.next(tag, result);` waits for the next operation to finish, and hands its `Buf` back with its tag and the kernel's result: a count, or a negative errno, which `io.outcome(result)` turns into a `Result`.

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

A ring records no lease, so it may be lent `rw` to a callee or a task. It is never stored, passed by value or returned (`E-PINNED`). It is linear: `wait(q)` consumes it in the function that declared it (`E-LINEAR-LEAK`), once every operation has finished, and releases every `Buf` nobody collected. `defer wait(q);` covers every exit.

`q.timeout(ns, tag)` finishes after `ns` nanoseconds with `-ETIME`, which bounds a wait. `q.cancel(tag)` stops every operation under that tag. Each cancelled operation still comes back through `next`, with `-ECANCELED` or its own result, and with its `Buf`.

Every submission comes back through `next` exactly once, and what the environment decides arrives there as a value. A kernel short of memory returns a submission at once with `-EAGAIN` or `-ENOMEM`. A kernel that will not set up a ring, as under some container filters, leaves `q.status()` negative and returns every submission with that errno.

What the program decides stays a guard: a submission to a full ring, or a `next` with nothing in flight, traps. `q.room()` says how many more submissions the ring takes, and `q.pending()` how many results `next` still owes. Neither enters the kernel.

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

A ring is a host object, and a device lane cannot use one (`E-PLACEMENT`). A host lane may read the three queries `status`, `room` and `pending`, and may not submit, collect, cancel or wait (`E-PARALLEL-CALL`).

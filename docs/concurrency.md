# Tasks, lanes and devices

Tasks lease what they borrow, lanes are race free by construction, and placement is part of a view's type. [language.md](language.md) covers values and control flow, [memory.md](memory.md) memory, ownership and effects; [abstractions.md](abstractions.md) covers generics, traits, closures, modules and recipes.

## Tasks and leases

`let t = spawn f(args);` runs a declared function on its own thread. The thread is one an earlier task left parked, or a new one when none is, so a spawn never waits for another task to finish and a spawn in a loop pays for a thread only once ([internals](internals.md#compiler-architecture) has the policy). The arguments are evaluated at the spawn and carried by value, so a task never reads the spawner's locals. `t` is a linear ticket bound to its scope. `wait(t)` consumes it and returns `f`'s result, and it must do so on every path of the same function; the ticket cannot be stored, passed or returned.

Until the `wait`, every place lent to the task is leased: nobody may write what the task reads or touch what it writes (`E-LEASED`), including by moving the owner. Read-only lending is shared freely. Visibly disjoint parts of one array may be lent mutably to different tasks. A part ends where the next begins, the bounds are literals or names that cannot change, and because every lent part was guarded `lo <= hi`, the order of the bounds chains through the parts in between, so a K-way split works.

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

A lease names the place that was lent, not the local it is rooted in. Lending `box.a` leases `box.a`, so another task may take `box.b` at the same time, and `len(box.a)` still reads while a task holds that field's elements. Lending the record itself leases every field inside it, and a field of a record a task holds is not readable.

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

The same field twice is one piece of storage twice, and replacing a lent field's cell (`pair.left = Buf[u64](2)`) is refused for the same reason: the task's view lives in that cell.

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

Tickets are awaited in the order they are written. A group collects tasks in the order they finish. `let g = Group[T](n);` declares a group of at most `n` tasks in flight whose results have type `T`. It is declared in place and used only there: never stored in a record, passed by value, lent to a callee or returned (`E-PINNED`), since a lease recorded in a callee would end at its return while the task still runs. Like a ticket it is linear: `wait(g)` must consume it on every path of the same function (`E-LINEAR-LEAK`, `E-LINEAR-BRANCH`). The declaration takes the group's whole storage, so its row carries `alloc` and `free`, and nothing after it allocates.

`spawn f(args) into g;` runs a declared function on its own thread, exactly as `spawn` does, and hands the task to the group instead of naming a ticket. `f` must return `T` (`E-TYPE-MISMATCH`), and a closure cannot follow it (`E-SPAWN`). Every place the task borrows is leased to the group until `wait(g)`, and touching one in between is `E-LEASED` with the group as the holder. A submission when `n` tasks are already outstanding is a guard failure at run time, never silent growth.

`let r = collect(g);` blocks until some task of the group has finished and yields its result, whichever task that was. Collecting from a group with nothing outstanding is a guard failure. A collect returns no lease, because the checker cannot know which task finished; only `wait(g)` returns them. `wait(g)` joins every task still running, drops every result nobody collected, releases the leases and consumes the group. Submitting and collecting are `spawn` and `join`, and each carries the `trap` of its guard.

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
  for k in 0..4 { sum = add_wrap(sum, collect(readers)); }  // in the order they finish
  wait(readers);
  if sum != total(800, samples[0..800]) { return 1; }
  return 0;
}
```

A loop may lend a group what its tasks only read, since read-only lending is shared. Lending a place `rw` inside a loop is refused, because the next iteration would lend it to the group again while the group still holds it. For the same reason nothing a loop body touches may conflict with what an earlier iteration lent the group, so writing `d[0]` before submitting a reader of `d` is `E-LEASED` too.

After an `if` or a `match` the group holds what every path lent it. A part's bounds order other parts only when every path formed that part, because its `lo <= hi` guard ran only where it was formed, and the same holds for a part lent inside a loop, which may run no iteration at all.

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

A group holds tasks that run declared functions. Queued device work keeps its ticket and its `after` ordering (`E-SPAWN`), a result type that is `linear` cannot be dropped by `wait` and is refused (`E-LINEAR-STORAGE`), and a group is a host object (`E-PLACEMENT`).

## I/O rings

A task is a thread. A ring keeps many kernel operations in flight from one thread, and hands them back in the order they finish. `let mut q = IoRing(n);` declares, in place, a ring of at most `n` operations, for `n` from 1 to 4096; any other `n` traps. It is Linux io_uring, set up here once, so the declaration carries `alloc`, `free` and `io`.

An operation takes the bytes it works on by value. `q.read(fd, data, count, offset, tag)`, `q.write(fd, data, count, offset, tag)`, `q.recv(fd, data, count, tag)`, `q.send(fd, data, count, tag)` and `q.accept(fd, tag)` move the `Buf[u8]` called `data` into the ring, so the program cannot touch storage the kernel is using (`E-MOVED`). `let data = q.next(tag, result);` waits for the next operation to finish and hands its `Buf` back, with the tag it was given and the kernel's result: a byte count, a new descriptor, or a negative errno. `io.outcome(result)` turns that into a `Result[usize, IoError]`, so a failure is a value to match on. `count` may be less than `len(data)`, never more.

Nothing is borrowed across an operation, so a ring records no lease and may be lent `rw` to a callee or to a task, which then uses it alone until it returns. It is never stored, passed by value or returned (`E-PINNED`). It is linear: `wait(q)` consumes it in the function that declared it (`E-LINEAR-LEAK`), after every operation still in flight has finished, and releases every `Buf` nobody collected. `defer wait(q);` covers every exit.

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
    Err(e) => return 3;
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

`q.timeout(ns, tag)` is an operation that finishes after `ns` nanoseconds with `-ETIME`, which bounds how long a `next` can wait. `q.cancel(tag)` asks the kernel to stop every operation in flight under that tag. Each still comes back through `next`, with `-ECANCELED` or with its own result if it finished first, and with its `Buf`, so cancelling releases nothing early and a cancel that finds nothing does nothing.

Every submission comes back through `next` exactly once. What the environment decides is a value there: a submission the kernel refuses for want of memory returns at once with `-EAGAIN` or `-ENOMEM` and its `Buf`. A kernel that will not set a ring up at all (a container whose seccomp filter blocks io_uring, a sysctl that disables it, no descriptor left) leaves the ring down instead of stopping the program. `q.status()` is then that negative errno, and every submission comes straight back with it, so a program handles a missing backend on the path it already has for a failed operation. `q.status()` is 0 on a ring that is up.

What the program decides stays a guard. A submission to a full ring, or a `next` with nothing in flight, traps, and `q.room()` and `q.pending()` say how many submissions the ring still takes and how many `next` still owes, so a program under load sees both coming. The three queries read the ring and never enter the kernel, so their row is a read of the ring and nothing more, and a ring lent to a task cannot be asked until `wait` (`E-LEASED`).

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
    Ok(up) => {}
    Err(e) => return 1;                // no io_uring here: fall back or report it
  }
  let refused = submit_all(q, 6);
  let mut tag:u64 = 0;
  let mut result:i64 = 0;
  while q.pending() > 0 { q.next(tag, result); }
  if refused != 2 { return 2; }
  return 0;
}
```

A ring is a host object (`E-PLACEMENT`). A host lane may read `q.status()`, `q.room()` and `q.pending()`, which only the thread that owns the ring changes and which no lane can change, but it may not submit, collect, cancel or wait (`E-PARALLEL-CALL`). The value model reports a function that uses one as `unknown`.

## Atomics and mutexes

`Atomic[T]` (the integers and `bool`) and `Mutex[T]` are declared in place and shared by `ro` borrow. They are the only interior mutability in the language, and they are never stored in a record, passed by value or returned (`E-PINNED`). Every atomic access names its memory order: `load`, `store`, `swap`, `fetch_add`, `fetch_sub`, `fetch_and`, `fetch_or`, `fetch_xor` and `compare_exchange(expected, desired, Order.seq_cst, Order.seq_cst)`. The effects are `spawn`, `join`, `atomic` and `lock`.

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

A mutex has one operation, and it may return a value. No guard object exists to escape, the closure cannot name the mutex it holds (`E-ALIAS`), and a thread that reaches the same mutex again through another borrow traps instead of relocking. Lanes may use atomics and mutexes.

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

`parallel i in n { body }` runs one lane per index and completes before the next statement. Whatever any lane writes may be touched only at element `[i]` or inside the lane's own block (`E-PARALLEL-RACE`), a shared scalar cannot be assigned (`E-PARALLEL-WRITE`: use `reduce`), and lanes cannot return, nest or move an outer owner. A lane's own row, and the row of everything it calls, must be pure-like (`E-PARALLEL-CALL`); a host lane may also allocate, use atomics and lock, and call a function that writes through what the lane lends it.

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

A lane may own a block instead of an element. With a constant stride `S`, lane `b` may touch `out[b * S + j]` for any `j` the checker can show is below `S`, or any index it can place in `[b * S, b * S + S)` from a loop, a `let` or a condition, and it may lend a part inside that block to a helper that writes it. Every access a lane makes to an array lanes write must stay inside that lane's block, all with one stride, so two lanes never meet; an access the checker cannot place is still `E-PARALLEL-RACE`. The element rule is the block of stride 1. The pool sizes its claims by the block, so a region of a few hundred heavy lanes still spreads across the cores.

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

A lane may call, or hand on to a helper, a `fn` parameter of its function. That leaves `lane:f` in the row, and in a declared ceiling, renamed up the call graph like `read:x`. Whatever is finally passed is judged where it is written: a closure that reads its captures is accepted, a closure that writes what it captured is not, and a stored `fn` value counts as any function of its type whose address was taken. Dispatch from a host lane, from a function it calls, or from such a closure is judged against every implementation.

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

Host lanes are a pool. The first host region of a process creates them and every later one reuses them, so a region costs a hand-off rather than a thread, and a region of fewer than sixteen thousand elements is compiled as the ordinary loop it replaces and starts nothing. The pool holds one thread per core, or the number `CAIRN_LANES` names. How many lanes there are is never observable in a result, only in the time a region takes; `evidence/v1_2/host_regions` records where a region starts to pay off.

## Plans

A plan says how a function's host regions are split across the lane pool, apart from the code that says what they compute. `plan f { grain G; lanes L; }` makes every host `parallel` region in `f` hand out at least `G` indices per claim and run on at most `L` lanes; either item may be left out. A region's lanes are race free and finish before the next statement, so every split of its indices is one the region already allows. A plan changes how long a region takes, and not its result, its effect row or its guards. The receipt records it beside the function's row.

```cairn
fn mix(v:u64) -> u64 {
  let mut w = v;
  for k in 0..20000 { w = mul_wrap(w ^ shr(w, 29), 0xbf58476d1ce4e5b9); }
  return w;
}
fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = mix(u64(i)); } }

plan spread { grain 1; lanes 8; }     // a few dozen slow lanes: one index per claim, eight threads
```

Without a plan the pool claims at least 8192 elements' work at a time, and runs a region with less than 16384 elements' work on the thread that starts it; a lane that owns a block counts as its block. That suits cheap bodies. A body that costs microseconds per index wants a grain of 1.

A device region takes three items of its own. `block B` launches blocks of `B` threads, whole warps from 32 to 1024. `per_lane K` sizes the grid so that each thread runs about `K` indices before the grid wraps, and `unroll U` unrolls each thread's loop over its indices `U` times, from 1 to 32. Every index below the count still runs exactly once, on whatever thread the grid gives it, which is why none of the three can change a result. A block wider than the kernel's registers allow runs in the widest whole warps that fit.

```cairn
fn scale(n:usize, x:rw<f32>[n]@device, a:f32) { parallel i in n { x[i] = a * x[i]; } }

plan scale { block 128; per_lane 4; unroll 4; }   // 128 threads a block, about four indices each
```

Without a plan a device region launches blocks of 256 threads and one index per thread, up to 65535 blocks. `grain` and `lanes` apply to host regions and `block`, `per_lane` and `unroll` to device regions, so one plan may set both for a function that has both.

`fuse K` runs up to `K` adjacent regions of a function as one traversal, from 2 to 16: each lane runs the first body at its index, then the next, in order. The regions must sit side by side in one block, share a placement and an extent spelled the same way, and have lanes that own element `[i]` rather than a block. Whatever one of them writes and another touches, both touch only at their own index, so no lane of a later body reads what another lane of an earlier body writes. No body may trap, loop without end or be observed from outside: every guard in it was discharged, and every function it calls is quiet in the same sense. A local `buffer` or `stack` array that only the chain touches, each lane at its own index and never lent, lives in each lane as one value and is never allocated. A host `reduce` over the same extent may end the chain: each step of its fold runs the fused bodies for that index first, and the fold keeps its order, so a checked fold still traps where it did. A sequential fold then runs the bodies on its own thread, in order, and `reduce op parallel` keeps them on the pool. A device `reduce` stays apart, because CUB does not promise to evaluate one index's value once.

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

The rule is strict about traps on purpose. A failed guard aborts the process, and which lane of a region fails first is already open. Fusing two trapping bodies would let the later body's guard fail before the earlier body's, so a program could end in a way it could not end before. Fusion is a plan item rather than something the compiler does whenever it may, because joining two passes is not always faster: two short loops each vectorize on their own, and one fused body may not. `cairn predict` prices a fused chain as one region without its scratch, `cairn tune` tries `fuse` beside the other items, and the receipt lists under `fused` every chain as emitted and the arrays it kept in its lanes. The conservative emission, `--keep-guards`, never fuses. On one shared host, three fused element-wise regions ran 1.1x to 2.6x faster than as written, and chains that no longer allocate their scratch ran 1.5x to 62x faster, most of that at ten million elements and more, where the written version maps and faults a fresh buffer on every call ([evidence/v1_4/fusion](../evidence/v1_4/fusion/README.md)). The model still predicts that fusing a map into a sequential fold loses below that size, where it measured a win, so `cairn tune` can keep such a chain apart when it should not.

`E-PLAN` refuses a plan that names no function with a parallel region, an item whose kind of region the function lacks, a second plan for one function, an unknown or repeated item, and a value out of its range: a grain of 0, a lane count outside 1 to 1024, a block that is not whole warps from 32 to 1024, a `per_lane` outside 1 to 65536, an `unroll` outside 1 to 32 and a `fuse` outside 2 to 16. It refuses a `fuse` with no two regions it may join, such as bodies whose checked arithmetic can trap. `plan` is a keyword only at the top of a module, so it stays an ordinary name everywhere else, and so are its items.

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

Halide separated algorithms from schedules, and MLIR's transform dialect does the same inside a compiler. The open question for CAIRN is whether a schedule kept apart from the algorithm, where the checker holds it to the algorithm's ownership rules, makes tuning cheaper than rewriting the loop, for a person or an agent. [`cairn tune`](tools.md#cairn-predict) is the search this makes possible: every plan it tries is one the checker accepts, it ranks them all by prediction and times only the best few. Whether that is cheaper than rewriting the loop has not been measured.

## reduce and compact

`reduce` combines with one of `add_wrap mul_wrap & | ^ min max` on integers, or `+ *` on floats. On the host it is an in-order fold. Over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats.

Checked `+` is offered on unsigned integers, where no partial sum can overflow unless the total does, so the trap cannot depend on the order; on the device the sum carries an overflow flag through the reduction and the host traps. Signed `+` and integer `*` are not offered, because a partial result can overflow alone.

Writing `parallel` in place of `for` runs a host reduction on the lane pool. The count alone fixes how the work splits: one block below 16384 elements, otherwise `n / 8192` runs of consecutive indices, at most 256 of them. Each block folds in index order into a slot on the caller's stack, and the slots fold in block order. Every operator the form admits is associative, so the answer is the in-order fold's on any number of lanes, and a checked `+` traps exactly when the in-order fold would. Floats are refused (`E-REDUCE-ORDER`): a sum in blocks is a different function of the same inputs. The row gains `par:host`, and every `yield` runs, in no promised order, under the rules of a lane.

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

`compact` writes the stable selected prefix into existing storage of capacity exactly `n`. It evaluates the predicate once per input and the projection only when selected, never reads its output, leaves the tail unchanged and allocates nothing on the host. Over a `@device` output it is stable stream compaction whose scan needs device scratch, which shows as `gpu_alloc` and `gpu_free`; a device `reduce` likewise. Its one unchecked store is justified by seventeen affine certificates, checked before every emission and proved sound in Lean together with in-bounds stores and stable selection for the loop model ([verification.md](verification.md)).

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

`scan` writes every prefix of a sequence into an array. `let total = scan + out for i in n yield e;` sets `out[i]` to `e(0) + ... + e(i)` and binds the whole. `scan + exclusive out ...` sets `out[i]` to what came before `i`, so `out[0]` is the operator's identity, and the total is the same. A scan whose total nobody reads is a statement of its own. `scan` and `exclusive` are words only in this position, so both stay ordinary names everywhere else.

The operators are `reduce`'s, for the same reasons: `add_wrap mul_wrap & | ^ min max` on integers, checked `+` on unsigned integers only, and `+ *` on floats (`E-SCAN-OP`). A checked `+` traps exactly when the in-order total overflows, because every prefix of an unsigned sum is at most the whole. The output is an `rw` view or a buffer of exactly the scan's count (`E-SCAN-TARGET`, `E-SCAN-EXTENT`). Each yield runs once, before the element it feeds is written, and may read the output only at its own element (`E-PARALLEL-RACE` otherwise), so `scan + xs for i in n yield xs[i];` scans in place.

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

`parallel` in place of `for` runs the scan on the lane pool in two passes over `reduce`'s blocks. The first pass writes each block's own prefixes and its total, the totals scan in block order into each block's offset, and the second pass combines every element of a later block with its offset. Every operator the form admits is associative, so the result is the in-order scan's on any number of lanes, and a checked `+` still traps exactly when the whole overflows. The block totals live on the caller's stack, as a pooled reduction's do, the row gains `par:host`, and each yield runs under the rules of a lane. Over `@device` views the scan is CUB's, which takes device scratch (`gpu_alloc`, `gpu_free`); it compiles for the device, and runs only under `make gpu`.

Floats scan only in the written order, with `for` on the host (`E-SCAN-ORDER`), because a sum in blocks is a different function of the same inputs.

```cairn rejects E-SCAN-ORDER
fn running(n:usize, out:rw<f64>[n], x:ro<f64>[n]) { scan + out parallel i in n yield x[i]; }
```

`std.sort.radix_sort` is the library's use of the form: eight bits a pass, each pass counts its digit, turns the counts into each digit's first place with `scan + exclusive`, and moves the keys there in order, so the sort is stable and allocates nothing.

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

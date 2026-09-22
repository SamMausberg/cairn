# Tasks, lanes and devices

Tasks lease what they borrow, lanes are race free by construction, and placement is part of a view's type. [language.md](language.md) covers values, memory and effects; [abstractions.md](abstractions.md) covers generics, traits, closures, modules and recipes.

## Tasks and leases

`let t = spawn f(args);` runs a declared function on its own thread. The arguments are evaluated at the spawn and carried by value, so a task never reads the spawner's locals. `t` is a linear ticket bound to its scope. `wait(t)` consumes it and returns `f`'s result, and it must do so on every path of the same function; the ticket cannot be stored, passed or returned.

Until the `wait`, every place lent to the task is leased: nobody may write what the task reads or touch what it writes (`E-LEASED`), including by moving the owner. Read-only lending is shared freely. Visibly disjoint parts of one array may be lent mutably to different tasks. A part ends where the next begins, the bounds are literals or names that cannot change, and because every lent part was guarded `lo <= hi`, the order of the bounds chains through the parts in between, so a K-way split works.

```cairn
fn fill(n:usize, out:rw<u64>[n], start:u64) { for i in 0..n { out[i] = start + u64(i); } }
fn total(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut sum:u64 = 0;
  for i in 0..n { sum = add_wrap(sum, xs[i]); }
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
  for i in 0..n { sum = add_wrap(sum, xs[i]); }
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

## Atomics and mutexes

`Atomic[T]` (the integers and `bool`) and `Mutex[T]` are declared in place and shared by `ro` borrow. They are the only interior mutability in the language, and they are never stored in a record, passed by value or returned (`E-PINNED`). Every atomic access names its memory order: `load`, `store`, `swap`, `fetch_add`, `fetch_sub`, `fetch_and`, `fetch_or`, `fetch_xor` and `compare_exchange(expected, desired, Order.seq_cst, Order.seq_cst)`. The effects are `spawn`, `join`, `atomic` and `lock`.

```cairn
fn count_live(n:usize, xs:ro<u64>[n], live:ro<Atomic[u64]>) {
  for i in 0..n { if xs[i] > 0 { let before = live.fetch_add(1, Order.relaxed); } }
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
  bytes_sent.with(|total:rw<u64>| { total = total + 1480; });
  let seen = bytes_sent.with(|total:rw<u64>| -> u64 { return total; });
  if seen != 1480 { return 1; }
  return 0;
}
```

## Parallel regions

`parallel i in n { body }` runs one lane per index and completes before the next statement. Whatever any lane writes may be touched only at element `[i]` (`E-PARALLEL-RACE`), a shared scalar cannot be assigned (`E-PARALLEL-WRITE`: use `reduce`), and lanes cannot return, nest or move an outer owner. A lane's own row, and the row of everything it calls, must be pure-like (`E-PARALLEL-CALL`); a host lane may also allocate, use atomics and lock.

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
out is written by lanes, so every lane may touch only out[i].
```

A lane may call, or hand on to a helper, a `fn` parameter of its function. That leaves `lane:f` in the row, and in a declared ceiling, renamed up the call graph like `read:x`. Whatever is finally passed is judged where it is written: a closure that reads its captures is accepted, a closure that writes what it captured is not, and a stored `fn` value counts as any function of its type whose address was taken. Dispatch from a host lane, from a function it calls, or from such a closure is judged against every implementation.

```cairn rejects E-PARALLEL-CALL
fn shade(n:usize, out:rw<u64>[n], f:ro<fn(u64) -> u64>) { parallel i in n { out[i] = f(u64(i)); } }
fn main() -> i32 {
  let n:usize = 64;
  buffer squares:u64[n] = zeroed;
  let mut calls:u64 = 0;
  shade(n, squares, |x:u64| -> u64 { calls = calls + 1; return x; });
  return 0;
}
```

```text
shade calls f from parallel lanes, where it cannot write:calls.
```

Host lanes are a pool. The first host region of a process creates them and every later one reuses them, so a region costs a hand-off rather than a thread, and a region of fewer than sixteen thousand elements is compiled as the ordinary loop it replaces and starts nothing. The pool holds one thread per core, or the number `CAIRN_LANES` names. How many lanes there are is never observable in a result, only in the time a region takes; `evidence/v1_2/host_regions` records where a region starts to pay off.

## reduce and compact

`reduce` combines with one of `add_wrap mul_wrap & | ^ min max` on integers, or `+ *` on floats. On the host it is an in-order fold. Over device views it is a tree whose association order is unspecified, which is exact for the integer operators and explicitly not for floats.

Checked `+` is offered on unsigned integers, where no partial sum can overflow unless the total does, so the trap cannot depend on the order; on the device the sum carries an overflow flag through the reduction and the host traps. Signed `+` and integer `*` are not offered, because a partial result can overflow alone.

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

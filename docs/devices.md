# Devices

A `parallel` region, a collector or a cooperative region runs on the GPU when a view it indexes is `@device`, and on host threads otherwise. `parallel` regions, plans and the collectors themselves are in [concurrency.md](concurrency.md).

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

Device work runs on the calling thread's execution context (`runtime/cairn_exec.hpp`): a stream and its event, one scratch arena and a budget, made by the thread's first device operation and kept. A region over device views returns once that stream has run it, and so does a `transfer`, so the host sees the result and a guard that fired in a lane has aborted the process. Nothing waits for the rest of the device. A device `reduce`, `scan` or `compact` takes its temporaries from the arena, which grows to the largest request it has met. Queued work borrows one of the context's streams until its `wait`.

Run again, a pipeline makes no stream, allocates no temporary and waits only for its own stream. A host stand-in that counts CUDA calls (`tests/runtime/test_execution.py`) ran one pipeline of regions, a vector and a staged plan, a reduction, a scan, a compaction, transfers and two queued tickets. The first pass made two streams and three arena allocations, later passes made none, and each pass waited on a stream nine times ([evidence](../evidence/v1_0/execution/README.md)). Its CUDA build compiles for sm_120 without `cudaDeviceSynchronize` and has not run on a GPU. A device `mma_unordered` runs on the same stream and waits only for it.

A region does not wait for queued work it does not touch; each ticket is waited for at its own `wait`. On a device without concurrent managed access (Windows and WSL2), a live ticket's kernel may still run after a region returns, and the host must not touch `@unified` memory while any kernel runs.

A C program that owns a stream hands it to a device library with `NAME_device_stream(stream)`, which `cairn build --header` declares ([tools.md](tools.md#cairn-build---header)). The calling thread's synchronous device work then runs on that stream, after what the caller queued there, and queued work starts after it too; `NULL` gives the thread its own stream back. A device view the library takes is a pointer to memory the caller owns.

### One wait, or none

A function whose device work nothing on the host can see before it returns waits once, when it returns, instead of after each region. `smooth` below waits once for both regions; a function with a single region waits once either way and compiles as before.

```cairn
fn smooth(n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, tmp:rw<f32>[n]@device) {
  parallel i in n { tmp[i] = 2.0 * x[i]; }
  parallel i in n { out[i] = tmp[i] + 1.0; }     // one wait for both, when smooth returns
}
```

The rule reads the function's effect row, which covers everything it calls. The row may not hold a transfer to or from host memory, a device allocation (a `buffer`, or the scratch of a device `reduce`, `scan` or `compact`, whose result returns to the host), queued work or a wait, I/O, a foreign call, a machine register, host assembly, a call through a function value, a lock, or an atomic update outside a device region, and the function may take no `@unified` view. These are everything through which the host reads device memory, waits on the device or lets another host thread see progress, so work queued before the one wait reaches nothing the host reads earlier. An atomic update in a device lane, such as a histogram's, is device work like any other. When the function returns the host sees every result, and a guard that fired in a lane has aborted the process, just as when each region waited. A device operation whose result the host reads, such as a copy to host memory, still waits where it stands, and it waits for everything queued before it.

A device library's C header gives each such function a second entry, `cq_NAME(stream, ...)`. It checks its arguments as `cf_NAME` does, queues the same work on the caller's stream after what the caller queued there, and returns without waiting. It makes no stream, event or allocation, so PyTorch or any CUDA program can call it on its current stream, or capture it in a CUDA graph. The first call of each kernel in a process reads the kernel's attributes once. `cf_NAME` stays synchronous. A function the rule refuses has no `cq_` entry, and the header lists it under `E-ENQUEUE` with the reason:

```c
void cf_smooth(size_t n, float *out, const float *x, float *tmp);
void cq_smooth(void *stream, size_t n, float *out, const float *x, float *tmp);
/* No enqueued entry (E-ENQUEUE), because each must wait on the host before it returns:
 * total: it allocates device memory: a device buffer, or the scratch of a device reduce, scan or compact, whose
 * result returns to the host (no form of the language writes such a result to a device view yet).
 */
```

Under `cq_NAME` a failed guard is observed later than the call. The guard's `__trap()` ends its kernel and poisons the device context, so nothing queued after it runs and every later CUDA call in the process fails with `cudaErrorLaunchFailure`. The caller's next synchronization reports it (`cudaStreamSynchronize` returns it, `torch.cuda.synchronize()` raises it), and the next CAIRN entry the process calls aborts. What the kernel wrote before it trapped stays in device memory no copy can reach any more; that is why a `@unified` view, which the host reads without a CUDA call, keeps a function from having an enqueued entry.

The suite's host machine counts every CUDA call the runtime makes (`tests/runtime/test_enqueue.py`). An enqueued call of a cooperative region, two regions and a device copy made no wait, stream, event or allocation, even as the thread's first device work; its checked entry waited once, where it had waited four times; and a copy to host memory after enqueued work waited first. On an RTX 5070 Ti under WSL2, where a launch and its wait took about 100 us of host time, the owner-style two-pass sum of 2^26 floats took 656 to 679 us a call with a wait after each pass, 466 to 550 us with one, and 341 to 344 us through `cq_`, against 336 to 337 us for one-pass CUDA; a CUDA graph captured the entries as the process's first CUDA work ([evidence/v1_1/device_perf](../evidence/v1_1/device_perf/README.md)).

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

Each side names up to three binders, fastest first: in `blocks bx, by in gx, gy threads tx, ty in 32, 8`, thread `(tx, ty)` is thread `tx + 32 * ty` of its block. The grid's extents are any `usize` values; the thread extents are literals or constants whose product is a whole number of warps, 32 to 1024 (`E-COOP-SHAPE`). A `shared` array is declared directly in the body with a constant length and is aligned to 128 bytes, so a tensor-core fragment may load from it ([numerics.md](numerics.md#tensor-core-fragments)). A block's arrays hold at most 48 KiB together (`E-COOP-SHARED`).

Every thread of the block must reach a barrier. A barrier may not sit under a condition that depends on a thread's name, nor in a loop a `break` or `continue` can leave early (`E-COOP-BARRIER`). The warp operations `shuffle(v, lane)`, `shuffle_xor(v, mask)`, `shuffle_down(v, delta)`, `shuffle_up(v, delta)`, `reduce OP warp yield v` and the votes need every thread of the warp (`E-COOP-WARP`), which a condition such as `t < 32`, `t / 32 == w`, or `ty < 4` when `tx` counts 32 keeps whole. A condition decides who arrives only where it is the same in every thread it could split. A local differs between threads when it is assigned from a thread's name, lent `rw` to a call, written through one of its elements or assigned by a closure, and so does what an atomic or typed `asm` returns. The right side of `&&` and `||` runs only in the threads the left side lets through. `reduce OP warp` takes `reduce`'s operators and combines in a fixed butterfly, halves then quarters down to neighbours, so every thread gets the same answer on the host and the device.

A vote is one instruction for the warp. `warp_ballot(c)` is a `u32` whose bit `l` says whether lane `l`'s `c` holds, `warp_any(c)` and `warp_all(c)` whether any or every lane's does, and each gives every thread of the warp the same answer, so a warp operation may sit under a condition on it. `warp_match(v)` is the lanes whose `v`, a 32- or 64-bit integer or float, has this lane's bits, and differs from lane to lane. `shuffle_up(v, delta)` reads the lane `delta` below, and a lane with none keeps its own `v`, as `shuffle_down` does at the other end.

```cairn rejects E-COOP-BARRIER
fn f(g:usize) {
  blocks b in g threads t in 64 {
    if t < 5 { barrier; }                          // threads 5 to 63 would never arrive
  }
}
```

The statements between two barriers are a phase, and within a phase no two threads of a block may touch one element of a shared array where either writes. The checker runs the body for every thread of one block, with the block's names and everything outside the region as symbols. It refuses a phase where two threads write one element (`E-COOP-CONFLICT`), a thread reads what another writes earlier in the phase (`E-COOP-UNORDERED`), or a thread writes over what another may still be reading (`E-COOP-REUSE`). Beside a write, an index it cannot follow is `E-COOP-UNDECIDED`: one read from data, or one that differs from another by a value known only at run time.

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

A shared array declared with no initializer, `shared warps:u32[8];`, is not zeroed where each block starts. The checker accepts it when every element a thread reads was written first, by that thread earlier or by any thread before a barrier between them, whichever way the body runs. The same run of one block that checks the phases shows it: a write under a condition the block may not take, in a loop that may not run or by a callee counts for nothing, and a read at an index the checker cannot follow is accepted only once every element of the array was written, as a lookup table every thread fills before a barrier is.

```cairn
fn block_max(n:usize, x:ro<u32>[n], g:usize, out:rw<u32>[g]) {
  blocks b in g threads t in 256 {
    shared warps:u32[8];                           // no zero fill: every element read is written first
    let i = b * 256 + t;
    let mut v:u32 = 0;
    if i < n { v = x[i]; }
    let w = reduce max warp yield v;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t < 32 {
      let mut m:u32 = 0;
      if t < 8 { m = warps[t]; }
      let top = reduce max warp yield m;
      if t == 0 { out[b] = top; }
    }
  }
}
```

A read of an element nobody surely wrote first is `E-COOP-UNWRITTEN`, and an atomic update counts as a read. The row has no `zero_init` for such an array, the receipt says it starts `written before read`, and the device zeroes only the arrays declared `= zeroed`. On the host an unzeroed array starts each block filled with a pattern, so a read the rule let through would show.

```cairn rejects E-COOP-UNWRITTEN
fn flags(g:usize, out:rw<u32>[g]) {
  blocks b in g threads t in 256 {
    shared warps:u32[8];
    if t % 32 == 0 { warps[t / 32] = 1; }
    if t < 8 && warps[t] > 0 { if t == 0 { out[b] = 1; } }   // no barrier: warps[1] is thread 32's
  }
}
```

```text
warps is declared without `= zeroed`, and thread t = 1 reads warps[1] at line 5, and no thread surely wrote that element first. Write it first, in this thread or in another before a barrier, or declare the array `= zeroed`.
```

An array from outside the region is shared by every block, and no barrier orders two blocks. Each of its elements may be written by at most one thread of one block, and read by another only if nobody writes it (`E-COOP-GLOBAL`), [atomic updates](concurrency.md#atomics-and-mutexes) aside: any thread may update any element atomically, and an array so updated is touched no other way in the region (`E-ATOMIC-MIXED`). The checker shows this when the index is a sum of the block, thread and loop names, each times a weight larger than all the lighter terms add up to. `b * 256 + t` is such a sum, and so is the transpose's `(bx * 32 + ty + 8 * k) * h + by * 32 + tx` when `h` is `32 * gy`. A condition on the index, `if col < h`, counts toward that bound, when the names it sums all count up or all count down. A loop whose range moves with another name, such as `for c in l..l + 2`, is bounded by nothing. A block or thread name the index leaves out needs conditions that pin it to one value the same in every thread, such as `if t == 255`, `if t == n` or `if tx == 31 && ty == 7`; `if t >= 254` leaves two writers. A thread may read the elements it writes, as `c += ...` does, when the read's index is the write's, in the same loop or in another over the same range, under the write's conditions.

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

A region may end with a finish, `then threads t in T { }`, which runs once, in one block, after every block of the region has finished. Everything the blocks wrote is visible to it, so it may read any element of an array they wrote, plainly or atomically, which no thread of the region may. A one-pass reduction leaves each block's partial sum in `partial[b]` and adds the partials in the finish:

```cairn
fn total(n:usize, x:ro<u64>[n], g:usize, partial:rw<u64>[g], out:rw<u64>[1]) {
  blocks b in g threads t in 256 {
    shared warps:u64[8] = zeroed;
    let mut sum:u64 = 0;
    let mut i = b * 256 + t;
    while i < n {                                  // a grid-stride share of x
      sum = add_wrap(sum, x[i]);
      i += g * 256;
    }
    let w = reduce add_wrap warp yield sum;
    if t % 32 == 0 { warps[t / 32] = w; }
    barrier;
    if t == 0 {
      let mut s:u64 = 0;
      for k in 0..8 { s = add_wrap(s, warps[k]); }
      partial[b] = s;
    }
  } then threads t in 256 {                        // once, after every block
    if t == 0 {
      let mut s:u64 = 0;
      for k in 0..g { s = add_wrap(s, partial[k]); }
      out[0] = s;
    }
  }
}
```

The finish has the region's number of threads, under names and extents of its own (`E-COOP-SHAPE`), and no block name. Nothing of a block is in scope: the finish declares its own shared arrays, and every rule of a region holds inside it. It runs where the region runs (`E-PLACEMENT`), exactly once, also when the grid has no blocks, so a reduction over nothing writes its identity. With floats every sum above has an order the program fixes, so the answer is the same on every run with the same grid; an `atomic_add_unordered` into `out[0]` needs no partials and adds in the order the hardware picks.

On the host the finish is one more team of threads, started after every block's threads have been joined. On the device the region stays one launch: each block, once done, has one thread fence its writes device-wide and count the block in its launch's counter, and the block that brings the count to the grid's fences again, runs the finish and puts the counter back to zero. The counters are a table of 4096 words in the module's global memory, so a finish allocates nothing and adds nothing to the row, and a function with one has an enqueued entry a CUDA graph may capture. Launches on one stream run one after another and share that stream's word; two streams never share one, and a captured launch keeps a word of its own for good, since its graph may replay on any stream. Each launch claims its word under a tag no other launch has, and a block that finds another launch's tag there traps rather than counting with it. That can happen only when every word is taken and the one given up belonged to a stream whose last launch is still running; a process whose words all belong to captured graphs traps at its next launch. Two instances of one graph running at once share the word, as they share every array they write. It compiles for `sm_120` and has not run on a GPU.

A closure in the body may run in any phase and any thread, so it names no shared array, pipeline or array from outside (`E-COOP-UNDECIDED`). A thread obeys everything a lane obeys: it cannot assign a scalar from outside (`E-PARALLEL-WRITE`), start another region (`E-PARALLEL-NEST`), do I/O (`E-PARALLEL-CALL`), or reach the other side's memory (`E-PLACEMENT`). The row gains `par:device` or `par:host`, `zero_init` for the shared arrays declared `= zeroed`, and `trap` for the guards; the receipt lists each array's bytes and the block's total under `local_storage`.

On the device the region is one launch on the thread's execution context, as `parallel` is: blocks of `T` threads, the arrays in static shared memory, `barrier` as `__syncthreads()` and the warp operations as `__shfl_*_sync` over the whole warp. It compiles for `sm_120` and has not run on a GPU. On the host each block's threads are real threads meeting at a `std::barrier`, two blocks at a time, so the thread sanitizer checks the phase rule on real runs. This lowering creates `2 * T` threads per region and is not a fast path.

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

The checker follows each stage through the body: available, transfer in flight, readable, readers in flight, available again at the next barrier. It refuses a read, `release` or `wait` with no stage in the state it needs (`E-STAGE-UNREADY`), and a `fill` with no free stage or a `wait` while a stage is still readable (`E-STAGE-BUSY`). A loop or an `if` that leaves a pipeline in another state than it found it is `E-STAGE-LOOP`, as is a `break` or `continue` in a loop that operates on one. Any use of `tiles` but as the receiver of its operations, `first(tiles)` or `tiles[0..4]`, reads the readable stage. A fill copies from an array from outside the region that lives where the region runs (`E-PLACEMENT`).

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

The depth is a constant of the declaration, and `strided_sums[2]` and `strided_sums[3]` compute the same sums. Raising it changes only the block's shared memory (depth times the stage's bytes, in the receipt's `local_storage` and the kernel's static shared memory) and how many copies a `wait` leaves in flight, which the checker counts: `cp.async.wait_group 1` at depth 2, `2` at depth 3. [`cairn predict`](tools.md#cairn-predict) prices what the depth changes: the shared memory, the blocks an SM holds, and the copies each block keeps in flight. On the device a fill is one `cp.async` per element, committed as one group per thread. On the host it is each thread's own copy, so a read the checker let through too early would race with it under the thread sanitizer.

## Wide loads and stores

`load_wide[K](x, i)` reads `x[i]` to `x[i + K - 1]` as one `Array[T, K]`, and `store_wide(x, i, v)` writes one back. On the device each is one access of up to 16 bytes, the widest a thread moves at once, with the cache behaviour a hint names.

```cairn
// Each lane scales four adjacent elements, streaming x and out past caches that will not see them again.
fn scaled(m:usize, n:usize, out:rw<f32>[n]@device, x:ro<f32>[n]@device, a:f32) {
  parallel i in m {
    let v = load_wide[4](x, 4 * i, Cache.streaming);
    let mut w = Array[f32, 4]();
    for k in 0..4 { w[k] = a * v[k]; }
    store_wide(out, 4 * i, w, Cache.streaming);
  }
}
```

`K` is a constant power of two, and `K` elements of a scalar or storage float fill at most 16 bytes: 4 `f32`, 8 `f16`, 2 `f64`, 16 `u8` (`E-WIDE`). A store takes an `Array` of the array's own element type. Each access traps unless its `K` elements lie inside the array and `x[i]` sits on `K * sizeof(T)` bytes. PTX leaves a misaligned vector access undefined, and a view the caller passes may start anywhere its element's alignment allows, so the second guard stays; where `i` is a multiple of `K`, nvcc reduces it to one test of the base pointer. The host checks both guards too, so a host or emulated run traps where a device run would.

A hint is written by name: `Cache.all` (the default), `Cache.l2` (`.cg`), `Cache.streaming` (`.cs`), `Cache.last_use` (`.lu`, loads only) and `Cache.read_only` (`.nc`, the path `__ldg` takes). `Cache.read_only` reads only an `ro` view of device memory, which nothing writes while it is lent. A block's shared memory has no caches to hint, so a shared array takes no hint but `Cache.all`. On the host an access is `K` ordinary loads or stores, and the hint says nothing.

```cairn rejects E-WIDE
fn wide(n:usize, x:ro<f64>[n]@device, out:rw<f64>[n]@device) {
  parallel i in n / 4 { let v = load_wide[4](x, 4 * i); store_wide(out, 4 * i, v); }    // 32 bytes at once
}
```

```text
load_wide moves a power of two of elements in one access of at most 16 bytes: f64 takes 1 to 2, and 4 is not one of them.
```

Every other rule sees an access as the part `x[i..i + K]` it reaches. A lane that stores `out[4 * i..4 * i + 4]` owns that block of `out` (`E-PARALLEL-RACE` otherwise), and the phase rule and the rule that each outside element has one writer count all `K` elements. The row gains `read:x` or `write:x` and `trap`, and [`cairn explain`](tools.md#cairn-explain) lists each access with the bytes it moves and its cache operator. The example above compiles for sm_120 to `LDG.E.EF.128` and `STG.E.EF.128` with no local memory, and has not run on a GPU.

## Emulating device code on the host

`--emulate` builds, runs and tests a device program on a machine without a GPU. Every device region, collector, cooperative region, `kernel fn`, transfer and queued ticket then runs on host threads, so a kernel can be checked for correctness where no device is.

```sh
cairn run examples/cooperative/gpu.toml --emulate --device-target sm_120
cairn test my_kernels --emulate --device-target sm_90a
cairn validate my_kernels --symbol scale_tiles --emulate --device-target sm_120
```

The program is still judged against a device target, resolved as a device build resolves it ([tools.md](tools.md#the-device-target)), so a program that emulates is one that would build for that target. What the target refuses, emulation refuses with the same code: `E-TARGET-FEATURE` for a feature it lacks, `E-IMPL-TARGET` for a selected implementation that needs one, `E-ASM-TARGET` for PTX of a later architecture. nvcc does not run, so what only nvcc or ptxas would refuse is not checked, and the record says so.

The C++ is the program nvcc would compile, byte for byte. The host compiler builds it with `CAIRN_EMULATE` defined, and `runtime/cairn_emulate.hpp` becomes the machine under the execution context. Device memory is host memory of exactly the size asked for, a region's lanes run on the host lane pool, a staged block loads its whole tile before any lane reads it, and a reduction, scan and compaction fold in index order. A cooperative region's threads are host threads at a `std::barrier`, each thread holds its fragments whole, and `mma_unordered` is the reference loop. Each device example of this repository runs this way under clang++ and g++ and agrees with its host loops ([evidence](../evidence/v1_1/emulation/README.md)).

What the host cannot run as the device would is refused before any compiler runs, never approximated:

| Refused with `E-EMULATE` | Why |
|---|---|
| typed PTX, `asm ptx ...` | the host pass of a lane traps where the PTX stands |
| an `extern` kernel with `launch(...)` | the host has its declaration and not its body |
| vendored CUDA in the manifest's `[foreign]` table | only nvcc builds it and only a device runs it |
| a device feature the host does not model: `clusters`, `tma`, `wgmma`, `tcgen05`, `mma_f8f6f4` | nothing lowers it for the host |

Guards behave as they do on the host: a guard that fails in a lane aborts the process with `SIGABRT`. A device array is a host allocation of exactly its bytes, so a read one element past it is a heap overflow the address sanitizer reports. A cooperative region's threads are real threads, so the thread sanitizer reports a phase whose barrier is missing. `tests/projects/test_emulation.py` shows both by taking the guard and the barrier out of the emitted C++.

Queued work runs to completion at its `spawn`, in program order. That is one of the orders the language allows. Work queued `after` a ticket starts after it, and that ticket was spawned earlier. The host touches nothing a live ticket leases, so it cannot see the work finish early, and two tickets that are not ordered touch nothing in common. A guard that fails in queued work aborts at the `spawn` rather than at the `wait`.

[`cairn validate --emulate`](tools.md#cairn-validate) tests a device implementation against its reference this way, and its evidence is `finite-tested-emulated`: finite testing of the host emulation, never of the device. [`cairn tune`](tools.md#cairn-tune) chooses an implementation on that evidence only with `--accept-emulated`.

Every record says the device work was emulated. The build receipt and the records of `cairn run`, `cairn test` and `cairn validate` carry `emulation`, with the target the program was judged against, and `cairn run` at a terminal prints the same note on standard error. An emulated result is evidence about the host and never about a device. It is not a way to time device code either: the lanes run on a few host threads and each block's threads meet at operating-system barriers, so a time a program prints measures that. [numerics.md](numerics.md#emulated-device-runs) says where an emulated result can differ from a device run.

## What fast kernels use

Fast CUDA kernels lean on a known set of features. The table says how a CAIRN program writes each one. Safe means checked CAIRN. Typed PTX means only inside `unsafe { asm ptx ... }`, whose declared effects are trusted as written ([memory.md](memory.md#layout-and-the-machine)). Foreign means a [foreign CUDA implementation](memory.md#foreign-implementations) of a CAIRN reference: vendored CUDA that `cairn foreign` builds and inspects and `cairn validate` holds to the reference. Every row that is not safe has that route, so a kernel that needs the feature takes it rather than a narrower design.

| Feature | CUDA | CAIRN | Checked |
|---|---|---|---|
| 16-byte loads and stores with a cache hint | `float4`, `__ldg`, `__ldcg`, `__ldcs`, `__stcs` | safe: [`load_wide[K]` and `store_wide`](#wide-loads-and-stores) with a `Cache` hint, in any code, and `plan f { vector 4; }` over `x[i]` in a device `parallel` region | accepted, E-WIDE |
| Atomics on device memory | `atomicAdd`, `atomicMax`, `atomicCAS` | safe: [`atomic_add_wrap(x[i], v)`](concurrency.md#atomics-and-mutexes) and its kin, from any lane or thread, never beside a plain access of the array in one region | accepted, E-ATOMIC-MIXED |
| Atomics on shared memory | `atomicAdd` on `__shared__` | safe: the same updates on a shared array, apart from its plain accesses by a barrier | accepted, E-ATOMIC-MIXED |
| A last block that finishes, grid-wide sync | `__threadfence` and an atomic ticket, `grid.sync()` | safe for a last block: a region's [finish](#cooperative-regions), `then threads t in T { }`; a grid-wide barrier inside a kernel is foreign | accepted, E-COOP-SHAPE |
| Shared memory nobody zeroes | `__shared__ float s[256];` | safe: `shared s:f32[256];`, when every element a thread reads was [written first](#cooperative-regions) | accepted, E-COOP-UNWRITTEN |
| Warp vote and ballot | `__ballot_sync`, `__any_sync`, `__all_sync` | safe: `warp_ballot(c)`, `warp_any(c)` and `warp_all(c)`, one vote instruction each | accepted, E-COOP-WARP |
| Warp match | `__match_any_sync` | safe: `warp_match(v)` on 32- and 64-bit integers and floats | accepted, E-TYPE-MISMATCH |
| Shuffles | `__shfl_sync`, `__shfl_xor_sync`, `__shfl_down_sync`, `__shfl_up_sync` | safe: `shuffle`, `shuffle_xor`, `shuffle_down` and `shuffle_up` | accepted |
| Grid-stride loops | `i += gridDim.x * blockDim.x` | safe: a `while` loop in a cooperative region, and the runtime strides a grid past 65,535 blocks | accepted |
| Dynamic shared memory, more than 48 KiB | `extern __shared__`, `cudaFuncSetAttribute` | foreign: a shared array has a constant length, 48 KiB a block | E-COOP-SHARED |
| Launch bounds | `__launch_bounds__(T, B)` | safe in part: a cooperative kernel is `__launch_bounds__(T)`, and blocks per SM are not stated | accepted |
| Unrolling | `#pragma unroll` | safe: nvcc unrolls a loop of constant bounds, and `plan f { unroll U; }` a device region's index loop | accepted |
| Packed half and bf16 math | `__hfma2`, `__hmul2` | typed PTX on the `u32` bits, since storage floats never compute | E-OPERATOR, accepted |
| Fast approximate math | `__expf`, `__fdividef`, `rsqrtf` | typed PTX (`ex2.approx.f32`), since `std.math` is not callable in a lane | E-PARALLEL-CALL, accepted |
| No-alias knowledge | `__restrict__` | the checker knows an `ro` view is not written during the call (`E-ALIAS` at every call), and the lowering does not tell nvcc | accepted |
| Asynchronous copies | `cp.async` of 4, 8 or 16 bytes | safe as pipeline stages of 4- or 8-byte elements, one copy an element; 16-byte copies only inside `mma_unordered` | accepted, E-COOP-SHARED |
| `ldmatrix`, `mma.sync` | `ldmatrix.sync`, `mma.sync.m16n8k16` | safe on f16 and bf16 through `MmaA`, `MmaB` and `MmaAcc`; other shapes and types foreign | accepted |
| `wgmma`, TMA, clusters, distributed shared memory | `wgmma.mma_async`, `cp.async.bulk.tensor`, `__cluster_dims__` | foreign, on a target that has the feature; `TmemAcc` is refused where it is not lowered | E-TARGET-FEATURE |
| Block-wide cooperative groups | `this_thread_block().sync()`, `tiled_partition<32>` | safe: `barrier;` and the warp operations | accepted |
| Memory fences, `__nanosleep` | `__threadfence()`, `__nanosleep(ns)` | typed PTX, a fence declared as `effects(fence)` | accepted |
| Persistent kernels | one block an SM and a work loop | safe with a static schedule, and a work counter is an atomic update; writes at the index a counter hands out are foreign, since no rule shows them one writer | accepted, E-COOP-GLOBAL |
| Streams | `cudaStream_t`, events | safe: `spawn parallel ... after t` and `spawn transfer` queue work on a stream of their own; a cooperative region is not queued | accepted, E-PARSE |
| CUDA graphs | `cudaGraph_t` | safe for a library's caller: `cq_NAME` queues its work on the caller's stream with no wait, so the caller captures it ([one wait, or none](#one-wait-or-none)); a graph built inside CAIRN code is foreign | none |

`tests/soundness/test_expressiveness.py` holds the table. For each row it compiles the spellings the row names and requires what the last column says: accepted, or refused with that code. A row whose check is `none` has nothing to compile.

## Benchmark submissions

`cairn export --harness` packages one CAIRN function for a kernel benchmark: a SOL-ExecBench `solution.json`, a GPU MODE `submission.py` or a KernelBench `ModelNew`. Beside it are the export it embeds and `harness.json`, a `cairn.harness/1` record of the format and the upstream commit it follows, the function and its signature, the mapping, the entry the binding calls, the flags and why, and the export's identity.

```sh
cairn new rmsnorm --from-sol-execbench problems/rmsnorm/definition.json   # a signature, harness.toml, policy.json
cairn export rmsnorm --harness sol-execbench --symbol rmsnorm --out out/rmsnorm
cairn export out/rmsnorm                                                  # {"status": "harness-intact", ...}
```

The submission calls the library through a PyTorch binding. Before any pointer crosses, the binding checks each tensor's dtype, device, contiguity, dimensions and element count against the mapping and the signature, refuses a written tensor that overlaps another and a number that does not fit its parameter, and raises a Python exception that names the argument. It then queues the device work on the caller's current torch stream through the no-wait entry `cq_rmsnorm` and returns without waiting. A function the header lists under `E-ENQUEUE`, such as one that transfers from host memory, is called through `cf_rmsnorm` with the torch stream bound by `NAME_device_stream`, and returns once its work there has finished. The record names the entry and, for the second, the reason.

`harness.toml`, beside the manifest or named by `--mapping`, is data: which benchmark argument feeds which parameter, in the order the benchmark passes them, with its dtype and shape.

```toml
[axes]
hidden_size = 4096                   # fixed; any other axis is read from the first tensor whose shape names it

[extents]
n = "batch_size * hidden_size"       # each usize parameter no argument feeds: + - * / over axes, checked

[[argument]]
name = "hidden_states"
parameter = "x"
dtype = "bfloat16"
shape = ["batch_size", "hidden_size"]

[[argument]]
name = "output"
parameter = "out"
dtype = "bfloat16"
shape = ["batch_size", "hidden_size"]
output = true                        # a destination the benchmark allocated
```

An argument without `shape` is a Python number. A `[[result]]` is a tensor the adapter allocates and returns. `[benchmark]` names SOL-ExecBench's `definition`, GPU MODE's `leaderboard` and `gpu`, and the `problem` the printed commands evaluate. KernelBench's `forward` gets only inputs, so its outputs are results, and SOL-ExecBench passes every output preallocated after the inputs, so it takes none. A dtype is spelled as the benchmark or as CAIRN spells it, `bfloat16` or `bf16`, and `float4_e2m1fn_x2` and the other formats CAIRN has no type for are refused by name. A mapping the signature does not bear writes nothing:

| Refused | Code |
|---|---|
| a parameter fed twice or by nothing, an axis no shape names, an extent beyond `+ - * /`, an input CAIRN writes | `E-HARNESS-MAPPING` |
| a dtype other than the parameter's element type, a format CAIRN lacks, a float feeding an integer | `E-HARNESS-DTYPE` |
| a function with no C entry, such as one that takes an owner | `E-HARNESS-SYMBOL` |
| a result for SOL-ExecBench, a destination for KernelBench, a returned value, a solution naming no definition | `E-HARNESS-FORMAT` |
| a device target whose code does not load on the GPU the mapping names | `E-TARGET-MISMATCH` |

The flags keep CAIRN's numerics. SOL-ExecBench compiles with `nvcc -O3 --use_fast_math` unless a solution says otherwise, so `compile_options` gives `nvcc -std=c++20 -O3 --fmad=false -arch=sm_100a --extended-lambda --expt-relaxed-constexpr -Xcompiler -ffp-contract=off,-fno-fast-math` for the device target, and `c++ -std=c++20 -O3 -ffp-contract=off -fno-fast-math` for the binding. GPU MODE and KernelBench pass the same flags to `load_inline`. Their Python files embed the export's sources one line per line and check each one's sha256 before the build.

Nothing here runs an evaluator, submits or opens a connection. The record lists the commands that would, for you to run: `sol-execbench PROBLEM --solution solution.json`, KernelBench's `scripts/run_and_check.py`, and `popcorn submit --mode test`, `benchmark`, `profile` or `leaderboard`. `popcorn submit --mode leaderboard` is a public ranked submission under your name on gpumode.com.

A score keeps its benchmark's meaning, and the record states it. SOL-ExecBench's `S = 1 / (1 + (T_k - T_SOL) / (T_b - T_SOL))` is 0.5 at its PyTorch baseline and 1.0 at its modelled speed of light, so 0.74 is not 74 percent of anything. GPU MODE ranks by the benchmarks' mean times, by their geometric mean under `ranking_by: geom`. KernelBench's `fast_p` is the fraction of problems solved correctly and faster than PyTorch by more than `p`. None of these is the speed-of-light fraction `cairn predict` reports.

`cairn new DIR --from-sol-execbench definition.json` starts a project from a problem. It writes the reference's signature, with a `usize` per variable axis, a `const` per fixed one and an `@device` view per tensor, above an empty body and the PyTorch reference as a comment; `harness.toml`; the problem's files; and `policy.json`, with the tightest `max_atol` and `max_rtol` any workload states, for `cairn validate --policy`. The benchmark also requires a matched ratio where CAIRN requires every element, fails any NaN or infinity where CAIRN agrees a NaN with a NaN, compares in f32 where CAIRN compares in f64, and may cap the largest error; the answer and `harness.toml` say which of these apply. The device target defaults to `sm_100a`, the B200 the benchmark runs on.

`tests/projects/test_harness_torch.py` builds each format with CPU torch and runs a host-view kernel on CPU tensors against the benchmark's reference; it skips where torch is absent. [The evidence](../evidence/v1_1/harness/README.md) says where it ran, and records device builds compiled and linked for sm_100a against a CUDA torch and never run.

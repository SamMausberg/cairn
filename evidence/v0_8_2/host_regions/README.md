# Host parallel regions: threads per statement, then a lane pool

`benchmark.json` holds two runs of `bench/host_regions.cpp` on the same machine, with the same
flags, minutes apart. `per_statement_threads` builds it against the CAIRN 1.1 runtime headers
(commit `4ff485a`), where every `parallel` statement creates and joins its own threads.
`lane_pool` builds it against this branch, where the first region of a process creates the lanes
and every later region reuses them. Reproduce either with:

```
python3 bench/host_regions.py --label lane_pool
python3 bench/host_regions.py --runtime <dir of 1.1 headers> --label per_statement_threads
```

## What was measured

One machine: a rented GH200, 64 AArch64 cores, Linux 6.8, nothing else running, no CPU pinned
(a region wants every core, so pinning would measure one). Both compilers the project supports,
g++ 11.4 and clang++ 15.0.7, with `src/cairn/toolchain.py`'s own flags for an executable
(`-std=c++20 -O3 -ffp-contract=off -fno-fast-math -fno-exceptions -fno-rtti -Wall -Wextra -Werror
-march=armv8.2-a`). Medians of nine timed blocks after one warm up (five above 1e6 elements, three
above 1e7); a block repeats a small region until it covers sixteen million elements and the total
is divided back, so no row is a measurement of the clock's resolution. Every case is checked
against the sequential result before it is reported, and nothing is written if one disagrees.

Two lane bodies, chosen to bracket what an element can cost:

- **`saxpy_f32`**: `out[i] = a * x[i] + y[i]`. Two flops over twelve bytes, vectorized, about
  0.11 ns an element. A large region of it is bound by memory bandwidth, not by the cores.
- **`mixed_u64`**: eight rounds of xor-shift and multiply in registers, about 1.5 ns an element.
  A large region of it is bound by the cores.

Region sizes run from 1e3 to 1e8. A separate section runs a thousand regions of 64, 1024 and
16384 elements one after another, with a compiler barrier between them on both sides, so the
sequential side is a thousand separate loops rather than one nest the compiler may fold.

## What it says

Times in milliseconds for one region, medians, g++ (clang++ agrees within the noise; both are in
the file). "seq" is the sequential loop the statement replaces.

| n | seq | per-statement threads | lane pool | before ×| after × |
|---|---|---|---|---|---|
| saxpy 1e3 | 0.0001 | 1.379 | 0.0001 | 0.00 | 1.00 |
| saxpy 1e4 | 0.0010 | 1.322 | 0.0008 | 0.00 | 1.25 |
| saxpy 1e5 | 0.0129 | 1.317 | 0.0088 | 0.01 | 1.47 |
| saxpy 1e6 | 0.179 | 1.312 | 0.051 | 0.14 | 3.49 |
| saxpy 1e7 | 3.363 | 1.429 | 0.173 | 2.06 | 19.4 |
| saxpy 1e8 | 43.38 | 4.508 | 4.286 | 9.54 | 10.1 |
| mixed 1e4 | 0.0152 | 1.322 | 0.0152 | 0.01 | 1.00 |
| mixed 3e4 | 0.0455 | 1.297 | 0.0213 | 0.04 | 2.13 |
| mixed 1e5 | 0.152 | 1.336 | 0.020 | 0.12 | 7.59 |
| mixed 1e6 | 1.542 | 1.352 | 0.070 | 1.17 | 22.1 |
| mixed 1e7 | 15.15 | 1.630 | 0.437 | 9.33 | 34.7 |
| mixed 1e8 | 152.7 | 6.354 | 6.589 | 24.8 | 23.2 |

A thousand regions in a row, microseconds each (g++):

| n | sequential loop | per-statement threads | lane pool |
|---|---|---|---|
| 64 | 0.021 | 1338 | 0.021 |
| 1024 | 0.407 | 1413 | 0.406 |
| 16384 | 6.63 | 1413 | 3.98 |

**Crossover** — the smallest ladder size from which the region beat the loop by a quarter and kept
beating it at every larger size, identical under both compilers:

| | saxpy (cheap) | mixed (dear) |
|---|---|---|
| per-statement threads | 1e7 | 3e6 |
| lane pool | 1e5 | 3e4 |

A region cost about 1.3 ms before, whatever its size, because it created and joined sixty-three
threads. It now costs nothing at all below sixteen thousand elements, where it is compiled as the
loop it replaces, and about 0.45 µs for each further lane it engages above that. The crossover
fell by a hundredfold for both bodies. At 1e8 elements the two runtimes are level: there the
thread creation was already amortized, and `mixed` at 1e8 is 4% slower with the pool, which is
within this machine's run-to-run spread at three repetitions.

## What was not measured

- **One machine, one architecture.** Nothing here says anything about x86-64, about more than one
  socket or memory domain, or about a machine whose cores are shared with another process.
- **No tuned baseline.** The comparison is against the sequential loop the same source would
  otherwise write, which is what a CAIRN `for` compiles to. No OpenMP, TBB or hand-written thread
  pool was built with equal flags and equal safety boundaries, so this is not a claim that CAIRN's
  host regions are fast, only that they cost far less than they did and that they no longer lose
  to the loop they replace.
- **Two cheap bodies.** Both lane bodies read and write one element and nothing else. A body that
  allocates, locks or takes wildly different time per index is not represented, though the runtime
  is tested for those in `tests/native/parallel_runtime.cpp`.
- **Regions back to back.** Every timed block runs its regions one after another, so the workers
  are awake. A worker that has been idle for more than about sixty microseconds goes to sleep, and
  waking one was measured separately at about twenty-five microseconds; a two-lane region whose
  helper had gone to sleep cost about 10 µs against about 4 µs awake. That cost is not in the
  table. It is bounded, it is paid once per burst of regions, and it is the reason the runtime
  wakes only the lanes a region can use rather than all of them.
- **Not the device.** Host regions only. `evidence/v0_8_0/gpu/benchmark.json` measures CUDA lanes;
  nothing here was re-measured against them, and that file's `host_parallel_ms` column is the
  1.1 runtime and was deliberately left as it was recorded.
- **Not a statistical study.** Medians of a few runs on one afternoon, with no confidence
  intervals. Rows within about a tenth of each other should be read as equal.

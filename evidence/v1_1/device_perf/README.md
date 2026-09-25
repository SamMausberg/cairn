# Device overhead: where CAIRN's time went against plain CUDA

CAIRN kernels timed beside hand-written CUDA of the same computations on one GPU: how much of the gap was kernel time, how much was host synchronization CAIRN added, and what the fixes of this track changed. Everything here ran on one machine through `bench/device`, under the device protocol below. Nothing here is a claim about another GPU, another host, or kernels and sizes not listed.

## What ran

The machine: an NVIDIA GeForce RTX 5070 Ti (sm_120, 70 SMs, 16 GB; driver 596.49, CUDA driver and runtime API 13.2) that also drives the Windows display, under WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2) on an AMD Ryzen 7 7800X3D (16 threads, 70 GB). nvcc 13.2.78 with g++ 13.3.0 as its host compiler; clang 21.1.8 for the host tests. Other agents loaded the CPU throughout, at times to a load average of several hundred, and the owner used the GPU between some batches.

The pairs are in `bench/device`, whose README says what each times. The CAIRN side is built by CAIRN's own device command line (`projects/toolchain.py`, `--fmad=false` included), the CUDA side by `nvcc -O3 -arch=sm_120`. Both link into one program that times them on one stream, interleaved: a warm-up call each, then 60 rounds (30 for transpose and stencil, 200 for overheads) of one timed call of each variant in turn, then bursts of ten calls. `gpu_us` is the median time between CUDA events recorded on the caller's stream before and after one call, as a leaderboard harness measures; `burst_us` the median per call of ten calls back to back; `host_us` the host's time until the stream had finished the call's work. The CAIRN library runs on the harness's stream through `NAME_device_stream`, and through `cq_NAME` where the build gives one. Every result is checked: sums against the exact sum, transposes and stencils element for element against the CUDA side, layer norms within 7.9e-8.

The device protocol: one process at a time under `/tmp/cairn-gpu.lock`, each under a 30-second timeout (every one took under 7 seconds), 12 seconds of rest between processes, a GPU utilization check before each batch (wait five minutes while it is above 10 percent), and after each process and each batch a search of the Windows System log for display-driver events (`nvlddmkm`, `Display`, ids 4101, 153, 14 and 13) and of the Application log for `LiveKernelEvent`. `batch.sh` is the script, `runs/batchN/batchN.log` each batch's record, and the empty `*_events.txt` files are the searches, which found nothing. Eight batches ran 66 processes; batch 8's first attempt waited out three utilization checks and was stopped before any device process ran (`runs/batch8/batch8_first_attempt.log`), then ran; batch 2's final search was run by hand after an edit to the script cut its tail short, and found nothing too. No device trap, death test, Compute Sanitizer or Nsight Compute ran.

| Batch | What ran | Record |
|---|---|---|
| 1 | every pair, before any fix | `runs/batch1` |
| 2 | before and one-wait builds; the device multiply check on 1.6 million operand pairs, old and new | `runs/batch2` |
| 3 | the collector at five sizes; the multiply fix and a lifted grid cap, per pair | `runs/batch3` |
| 4 | where typed PTX fails; the reduction at every step, PTX left out | `runs/batch4` |
| 5 | the PTX fix; the reduction with every fix; a graph capture | `runs/batch5` |
| 6 | before against after for every pair; the capture again; `tests/language/test_assembly.py`'s device program | `runs/batch6` |
| 7 | the reduction with `load_wide`, and a second sample of every pair after the fixes | `runs/batch7` |
| 8 | `examples/reduction`'s one-launch sums against one-pass CUDA and the two-pass CAIRN sum, twice | `runs/batch8` |

## Host round trips per kind of device work

Each operation's CUDA calls in one steady-state execution of its checked entry, counted by the suite's host machine for `runtime/cairn_exec.hpp` (`calls/`: `ops.cairn`, `main.cpp`, and the counts before and after). A host wait is a `cudaStreamSynchronize`. Every other call returns without waiting for the device; a launch also calls `cudaGetLastError`, and the first launch of each `parallel` kernel in a process reads its attributes once. The buffer also costs an allocation and a free each call.

| Work | Launches | CUB calls | Copies | Memsets | Event records, stream waits on an event | Host waits before | Host waits after (`cf_`) | Host waits (`cq_`) |
|---|---|---|---|---|---|---|---|---|
| `parallel` region | 1 | 0 | 0 | 0 | 0, 0 | 1 | 1 | 0 |
| two regions in one function | 2 | 0 | 0 | 0 | 0, 0 | 2 | 1 | 0 |
| cooperative region | 1 | 0 | 0 | 0 | 0, 0 | 1 | 1 | 0 |
| `transfer` host to device | 0 | 0 | 1 | 0 | 0, 0 | 1 | 1 | no entry |
| `transfer` device to host | 0 | 0 | 1 | 0 | 0, 0 | 1 | 1 | no entry |
| `transfer` device to device | 0 | 0 | 1 | 0 | 0, 0 | 1 | 1 | 0 |
| device `reduce` (total to the host) | 0 | 1 | 1 | 0 | 1, 1 | 1 | 1 | no entry |
| device `scan` | 1 | 1 | 1 | 0 | 1, 1 | 1 | 1 | no entry |
| device `compact` | 2 | 1 | 2 | 0 | 1, 1 | 1 | 1 | no entry |
| `buffer ...@device = zeroed` and two regions | 2 | 0 | 0 | 1 | 0, 0 | 3 | 3 | no entry |
| `spawn parallel` and its `wait` | 1 | 0 | 0 | 0 | 0, 0 | 1 | 1 | no entry |

CAIRN promises that the host never observes a result, or goes on past device work, before a guard that failed has stopped the process. A region, a cooperative region and a copy between device views therefore need no wait of their own when nothing the host reads comes before the next wait. A copy to or from host memory, a reduction, scan or compaction whose result returns, a buffer's zeroing and release, and a ticket's `wait` do need theirs. The reduction's event record and stream-wait order work on one stream and cost no round trip, and a compaction's two one-element copies back could be one.

On this machine a round trip costs far more than the device card assumes (`perf/profiles/rtx-5070-ti.json` says 8 us for a launch and its wait). On the RTX 5070 Ti with driver 596.49 under WSL2, an empty launch and a `cudaStreamSynchronize` took 84 to 109 us of host time (median of 200; 35 to 46 us at best), a 4-byte copy back and a wait 92 to 177 us, and a thousand empty launches back to back 11 to 25 us each on the GPU's clock (`runs/batch2`, `runs/batch6`, `overheads_*.json`). The host was loaded by other work throughout, so these host times are an upper range for this machine, not its floor. A wait also leaves the stream idle until the host queues the next work, so in `gpu_us` each wait a CAIRN call made cost 40 to 200 us.

## The owner's reduction

A sum of 2^26 f32 (256 MB), about the scale where the owner measured 660 us against 282 us. The CUDA side is the design the owner wanted, one pass with 16-byte `__ldcg` loads and a last-block finish, and each step toward the design CAIRN forced: two passes, then scalar loads, then one element a thread, then CAIRN's zeroed shared array and barrier, then its 65,535-block grid. The CAIRN side is that two-pass design written four ways: scalar loads with every access checked, 16-byte loads through unsafe typed PTX as the owner wrote it, 16-byte loads through `load_wide` (`reduce_wide.cairn`, once the KERNELS track had landed it), and one element a thread. `gpu_us`, median, with `burst_us` in parentheses; `gpu_us` is the primary measure, since the host was loaded. In batch 4 each build ran in its own process, and the CUDA rows are from the one-wait build's; batches 6 and 7 ran two processes each, and give both where they differ.

| | Batch 4 | Batch 5 | Batch 6 | Batch 7 |
|---|---|---|---|---|
| CUDA, one pass, 16-byte loads, last-block finish | 334 (352) | 337 (327) | 336 to 399 | 335 to 340 |
| CUDA, two passes, 16-byte loads | 344 (358) | 339 (329) | 338 to 349 | 337 to 345 |
| CUDA, two passes, scalar loads | 353 (362) | 353 (344) | 353 to 365 | 363 to 366 |
| CAIRN scalar, before: a wait after each pass | 679 (1363) | | 656 (1531) | |
| CAIRN scalar, one wait (`cf_`) | 474 (735) | 466 (513) | 550 (592) | 560 to 591 |
| CAIRN scalar, no wait (`cq_`), before the multiply fix | 413 (398) | | | |
| CAIRN scalar, no wait, after it | 406 (358) | 393 (350) | 446 (419) | 407 to 449 |
| CAIRN 16-byte PTX, before the PTX fix | did not launch | | | |
| CAIRN 16-byte PTX, one wait (`cf_`) | | 418 (461) | 432 (663) | 418 to 431 |
| CAIRN 16-byte PTX, no wait (`cq_`) | | 341 (330) | 344 (385) | 338 to 342 |
| CAIRN 16-byte `load_wide`, one wait (`cf_`) | | | | 425 to 434 |
| CAIRN 16-byte `load_wide`, no wait (`cq_`) | | | | 346 to 348 |
| CUDA per element, zeroed and a barrier, grid capped at 65,535 | 440 (470) | 438 (429) | 440 to 448 | 438 to 443 |
| CUDA per element, zeroed and a barrier, a block per 256 elements | 503 (531) | 503 (493) | 505 to 507 | 507 |
| CAIRN per element, no wait, grid capped | 513 (552) | 514 (509) | 529 (581) | 518 to 529 |
| CAIRN per element, no wait, grid lifted to 2^31 - 1 | 581 (577) | | | |
| CAIRN `reduce` collector, total to the host | 490 to 522 | 504 | 517 to 534 | 507 to 528 |
| CUB `DeviceReduce::Sum`, total to the host | 474 to 538 | 492 | 503 to 519 | 484 to 504 |

What each cause cost, and what it costs now. The expectations are the estimates this track made from the round-trip counts and the SASS below before the first device run, recorded here afterwards.

| Cause | Expected | Measured before | Now |
|---|---|---|---|
| A host wait after each pass: the stream idles until the host has seen it finish and queued what follows | 50 to 150 us a wait | 656 to 679 us with two waits, against 393 to 413 us with none | `cf_` waits once (466 to 550 us); `cq_` waits never and is within 3 percent of one-pass CUDA with 16-byte loads |
| A checked 64-bit multiply in the index, tested by a 64-bit division | 10 to 40 percent of the kernel | scalar kernel 398 us a call in bursts; hot loop 56 SASS instructions and a call, CUDA's 9 | 350 to 358 us; 33 instructions, no call |
| Typed PTX written in a region's body did not launch | not expected; found by running | "invalid device function" (cooperative region) or "invalid resource handle" (`parallel` region) | launches; 338 to 344 us |
| Scalar loads, since CAIRN had no safe 16-byte load | 5 to 20 percent | CUDA +14 to +26 us (4 to 8 percent); CAIRN scalar against its PTX, both without a wait, +52 to +107 us | `load_wide[4]` (KERNELS track, 499454f) says it safely: 346 to 348 us without a wait, 1 to 3 percent over the PTX, for the guards each load keeps |
| Two passes, since CAIRN has no device atomics for a last-block finish | 5 to 25 us | CUDA +2 to +13 us (1 to 4 percent) | open, for the KERNELS track |
| A shared array zeroed although every element is written | under 5 percent, more for one element a thread | one element a thread: +28 to +35 us (6 to 7 percent); a grid-stride block zeroes once, not measurable | open, for the KERNELS track |
| The grid capped at 65,535 blocks, blocks past it looping | 5 to 15 percent for one element a thread | the cap is faster: CUDA 440 us capped against 503 us with a block per 256 elements, CAIRN 513 against 581 us lifted | kept, for this measurement |
| CAIRN's own checks of every index, with scalar loads | not separated in advance | scalar without a wait, after the multiply fix: 393 to 446 us against CUDA's 353 to 365 us | open: the facts cannot yet show `(k * g + b) * 256 + t` in range |

## The owner's design, written safely

`examples/reduction` (KERNELS track, 2c44875) writes the design the owner wanted with nothing unsafe: one launch, 16-byte `load_wide` streaming loads, a shared array with no zero fill, and either a finish in the block that ends last (`sum`) or each block's sum added into `out[0]` atomically (`sum_unordered`). The `reduction` pair compiles it with `reduce_wide.cairn` as one CAIRN program and times it beside one-pass CUDA, interleaved, at 2^26 f32 (`runs/batch8`, two processes, `gpu_us` median):

| | First process | Second process |
|---|---|---|
| CUDA, one pass, 16-byte `__ldcg` loads, last-block finish | 342 | 344 |
| CAIRN `sum_unordered` through `cq_`, with a 4-byte memset of `out[0]` before each call | 344 | 340 |
| CAIRN two-pass `load_wide` sum through `cq_` | 346 | 349 |
| CAIRN `sum` (one launch, finish) through `cf_`, one wait | 394 (min 317) | 407 (min 319) |
| CAIRN `sum_unordered` through `cf_`, one wait | 412 | 475 |
| a 4-byte `cudaMemsetAsync` alone | 11 | 10 |

Through `cq_`, the owner's design in safe CAIRN ran level with the hand-written CUDA, the atomic variant within 1 percent either way, and the two-pass one within 2 percent. `sum` had no `cq_` entry when this ran: its finish's counter was the execution context's scratch, allocated by the call, so its row held `gpu_alloc` (`E-ENQUEUE`). Through `cf_` it paid the one wait, 50 to 65 us over CUDA at the median, although its fastest calls (317 to 319 us) beat CUDA's fastest (325 to 330 us). The KERNELS track has since moved the counter into the module's global memory (ef8535e), which gives `sum` an enqueued entry; that entry has not been timed.

## The other pairs

`gpu_us`, median, batch 6 unless marked. "Before" is the tree this track started from; "after", with every fix.

| Pair | Size | CUDA | CAIRN before (`cf_`) | CAIRN after (`cf_`) | CAIRN after (`cq_`) |
|---|---|---|---|---|---|
| saxpy | 2^22 | 37 to 50 | 113 | 78 | 39 |
| saxpy | 2^24 | 263 | 368 | 348 | 266 |
| saxpy twice | 2^22 | 70 to 92 | 209 | 110 | 75 |
| saxpy twice | 2^24 | 523 to 525 | 716 | 678 | 526 |
| layer norm, 4096 rows of 4096 | 2^24 | 201 | 306 (batch 2) | 291 | 234 |
| transpose | 8192 x 8192 | 768 | 928 (batch 2) | 1218, min 811 | 792 |
| stencil, `parallel` | 8192 x 4096 | 391 | 460 (batch 2) | 568, min 411 | 376 |
| stencil, cooperative | 8192 x 4096 | 391 | 568 (batch 2) | 759, min 484 | 446 |

`cq_` puts saxpy level with CUDA, and saxpy twice, which waited twice before, too. The synchronous entries of single-region functions still wait once, and their times varied by up to a third between batches; the enqueued calls and the CUDA side, at the larger sizes, by under 10 percent. Without a wait, the kernels themselves compare, CAIRN against CUDA: layer norm 234 against 201 us (246 before the multiply fix, batch 2), transpose 792 against 768 (844 before), stencil 376 against 391 as a `parallel` region and 446 as a cooperative one (495 before). Batch 7's second sample gave layer norm 214 against 177, transpose 791 against 766, and stencil 374 and 457 against 363.

`capture.cu` begins a capture in `cudaStreamCaptureModeGlobal` as the process's first CUDA work and calls two enqueued entries inside it: two `parallel` regions, whose first launch in a process reads their kernels' attributes, and the two-pass sum. The capture ended without error with the four kernels as its nodes, in batch 5 and again in batch 6, and the replayed graph's sum was exact (`runs/batch6/capture_final.json`). Replaying the graph took as long as calling the entries (462 against 453 us).

## What the SASS says

`sass_before.json` and `sass_after.json` hold every kernel's reading (`python3 bench/device/device.py sass`). The hot loop is the smallest backward branch around a global load.

| Pair | Kernel | Instructions | Hot loop | Calls | Traps | Registers | Global loads |
|---|---|---|---|---|---|---|---|
| reduce | CAIRN `block_sums` | 225 → 146 | 56 → 33 | 1 → 0 | 3 | 28 → 20 | 1 |
| reduce | CAIRN `block_sums_wide` | 419 → 251 | 106 → 46 | 4 → 0 | 8 | 32 → 22 | 2 (1 wide) |
| reduce | CAIRN `final_sum` | 207 → 128 | 43 → 20 | 1 → 0 | 1 | 30 → 20 | 1 |
| reduce | CUDA `partial_scalar` | 71 | 9 | 0 | 0 | 18 | 1 |
| reduce | CUDA `partial_v4` | 243 | 10 | 1 | 0 | 36 | 6 (5 wide) |
| saxpy | CAIRN `saxpy` | 34 | 14 | 0 | 0 | 14 | 2 |
| saxpy | CUDA `saxpy` | 24 | none | 0 | 0 | 12 | 2 |
| layer norm | CAIRN `layernorm` | 843 → 688 | 64 → 32 | 7 → 4 | 12 | 32 → 26 | 5 |
| layer norm | CUDA `layernorm` | 1007 | 6 | 4 | 0 | 40 | 157 |
| transpose | CAIRN `transpose` | 956 → 438 | 855 → 313 | 17 → 1 | 13 | 40 | 4 |
| transpose | CUDA `transpose` | 77 | none | 0 | 0 | 26 | 4 |
| stencil | CAIRN `jacobi_blocks` | 303 → 238 | 215 → 148 | 3 → 1 | 14 | 36 → 32 | 5 |
| stencil | CAIRN `jacobi` | 168 | 80 | 1 | 5 | 26 | 5 |
| stencil | CUDA `jacobi` | 55 | none | 0 | 0 | 16 | 5 |

The calls before the fix were 64-bit divisions: a checked multiply tested its product by dividing it again, which the optimizer removed for some multiplies and kept for others, such as those by a constant power of two. Those left are a layer norm's float division and square root (CUDA's layer norm makes four calls too), the 64-bit `%` and `/` that split a flat block number into two for a two-dimensional cooperative region, and the stencil's `i % w`. The hot loops left are CAIRN's checks of each index: overflow of `k * 256 + t` and of the row offset, and each view's bounds. CUDA's layer norm unrolls its loops (157 load instructions against CAIRN's 5); CAIRN's, holding a trap on each path, does not. `--fmad=false` keeps CAIRN's `a * x + y` two instructions where nvcc fuses CUDA's into one; that costs nothing measurable in a kernel that waits on memory.

## The fixes

- `85afa89`: a function whose effect row lets the host observe no device memory before it returns runs its device work held: one wait when it returns instead of one per operation. Its library header adds `cq_NAME(stream, ...)`, which queues the same work on the caller's stream and returns without waiting, allocating or making a stream or an event; the rule and where a failed guard is then observed are in [devices.md](../../../docs/devices.md#one-wait-or-none).
- `1469c25`: a checked multiply in a device lane tests the product's high half (`mul.hi`) instead of dividing. The trap condition is unchanged: `tests/runtime/device_arithmetic.cpp` holds the device branch to the host compiler's `__builtin_mul_overflow` on 1.16 million cases on the host, and `tests/runtime/gpu_arithmetic.cu` did the same for 1.6 million operand pairs on this GPU in batch 2, old and new alike, without a difference.
- `d6781cf`: typed PTX in a region's body evaluates its inputs in both of nvcc's passes, so the lambda captures the same variables on the host as on the device. Before, the host pass never read a view only the PTX used, and the kernel did not launch; a PTX statement in a `kernel fn` was not affected. `tests/language/test_assembly.py`'s device program (a 16-byte load in a lane, `brev` in a lane and in a cooperative region) exited 0 in batch 6, and `diagnostics/` holds the four-way reproduction of batch 4 and 5.
- `6224ae4`: an atomic update in a device lane, which the KERNELS track made possible meanwhile, is device work: it no longer keeps a function from the one wait or from `cq_NAME`. One in host code or a host lane still does, since another host thread sees it.

The grid cap stays at 65,535 blocks. A lifted cap (2^31 - 1) made the one-element-a-thread designs 13 percent slower in both CUDA and CAIRN, and changed nothing measurable elsewhere; a grid-stride loop past the cap costs a compare a block.

## What remains

Through the enqueued entry, and at the sizes above, CAIRN's kernels ran level with hand-written CUDA for saxpy, within 3 percent for the owner's reduction with 16-byte loads (typed PTX or `load_wide`) and for the transpose, and within 4 percent either way for the `parallel` stencil. Its layer norm took 16 to 21 percent longer, its cooperative stencil 14 to 26 percent, and its reduction with scalar loads 11 to 24 percent. The cause of each is the checked index arithmetic in its loops, which the checker's facts cannot yet show in range, and the 64-bit division that splits a two-dimensional block number. Through the synchronous `cf_` entry a call still pays one host round trip, 40 to 200 us on this WSL2 host, since the language returns to a C caller only once the device work is done.

For the KERNELS track: a finish's counter allocated outside the call was the last thing between `examples/reduction`'s `sum` and the CUDA time, and ef8535e has since done that (not timed here). Of the rest, in order of what they cost the owner's reduction here: safe 16-byte loads (52 to 107 us, since each scalar load pays CAIRN's index checks too; `load_wide` now closes this to within 3 percent), shared arrays without the zero fill (6 to 7 percent, in designs of one element a thread only), device atomics for a one-pass finish (1 to 4 percent). Host synchronization, 210 to 270 us of the scalar design's 320 to 340 us gap, is what `cq_` removes.

## What did not run

`tests/runtime/gpu_arithmetic.cu` and the device program of `tests/language/test_assembly.py` ran by hand under the protocol, as their `make gpu` tests run them. The rest of `make gpu` did not run: no device test of a failing guard, of queued work, of a scan or compaction, of a transfer from the library side, and no Compute Sanitizer. One GPU, one host, one driver. Under WSL2 every CUDA call goes through the Windows driver, and no native Linux host was measured, where a wait may cost less. The host was loaded by other agents' test runs throughout, so host times are noisy and `gpu_us` is the measure to read. Medians of 30 to 200 calls; the synchronous entries varied by up to a third between batches, so the tables give each batch rather than one number. Batch 6's second reduction run overwrote the first's file, so batch 6 has one. The owner's own program was not available; the reduction here is written to their description. Nothing was profiled: attribution comes from the counted calls, the SASS and the timings above.

## Waits placed between observations (2026-09-25)

A run of device work now waits once, before the first thing the host observes after it, where it waited after each operation unless the whole function observed nothing ([devices.md](../../../docs/devices.md#one-wait-or-none), `compiler/lower/execution.py`). Nothing in this section ran on a GPU: the counts come from the suite's host machine for `runtime/cairn_exec.hpp` (`tests/runtime/gpu_host.hpp`), which now also keeps, for every stream, how much of its queued work a wait has covered, and counts as `early` each copy to or from host memory, and each release of device memory, made while another stream held work nobody had waited for.

| Program | Host waits before | Host waits after | `early` | Record |
|---|---|---|---|---|
| `tests/runtime/test_execution.py`'s pipeline, per pass from the second on | 9 | 8 | 0 | `waits/counts.json` |
| `stages` of the same file: a copy in, two regions, a reduction the host reads, two regions, a copy back, per call | 7 | 5 | 0 | the test, under g++ and clang++ |

In the pipeline, `shuttle`'s two planned regions share one wait, taken by the copy back to host memory; `pass` has one region before each result the host reads, so it saves nothing and is emitted as before. `stages` was built twice from one emission, once with its held run and once without it, which is how it was emitted before; both computed the same results and left nothing unwaited when they returned. With a run held open by hand, a copy to host memory on another stream was counted as early, so the count can see the case it rules out. `waits/counts.json` was written by `tools/checks/execution_counts.py --out evidence/v1_1/device_perf/waits` under g++ 13.3 and clang 21, with the sm_120 object built by nvcc 13.2; that object is compiled, never run. Of the 2,128 programs `tools/checks/emission_identity.py` covers, three emit differently, each gaining a held run: `examples/reduction/gpu.toml`'s `check`, the pipeline's `shuttle` and a test program of `tests/projects/test_emulation.py`. No time was measured.

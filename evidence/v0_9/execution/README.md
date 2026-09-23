# Device execution on the thread's context

What a repeated device pipeline makes, allocates and waits for once generated code runs on the calling thread's execution context (`runtime/cairn_exec.hpp`). Nothing here ran on a GPU.

## What ran

The pipeline of `tests/runtime/test_execution.py`: per pass, `pass` runs a region, a checked `reduce`, an exclusive `scan` and a `compact` over 20,011 `u32` elements, and `shuttle` queues a transfer and a region after it, waits for both, runs a `vector 4` region and a `stage 1` region, and transfers back. The generated C++ was compiled against `tests/runtime/gpu_host.hpp`, a host machine for `cairn_exec.hpp` that runs lanes on the host and counts every stream, event, allocation, launch, copy and wait, and run for 20 passes under g++ 13.3 and clang 21 at `-O2`. Every pass's results matched a plain C++ computation of the same pipeline.

`counts.json` holds every row. The counts are cumulative:

| After | Streams | Events | Owner allocations | Arena allocations | Frees | Launches | Copies | Stream waits | Event waits |
|---|---|---|---|---|---|---|---|---|---|
| the six owners | 1 | 2 | 6 | 0 | 0 | 0 | 0 | 6 | 0 |
| pass 1 | 2 | 3 | 6 | 3 | 2 | 7 | 6 | 15 | 2 |
| pass 2 | 2 | 3 | 6 | 3 | 2 | 14 | 12 | 24 | 2 |
| pass 20 | 2 | 3 | 6 | 3 | 2 | 140 | 120 | 186 | 2 |

The first pass makes the second stream (two tickets are live at once) and grows the arena three times, for the reduction, the scan and the compaction, freeing the smaller arena each time after a wait for its last user (the two event waits). From the second pass on, each pass makes nothing, allocates nothing, frees nothing and waits for its stream nine times: once for each of the four synchronous operations of `pass`, the two tickets, the two planned regions and the transfer back. The host machine has no whole-device wait, so no operation of `cairn_exec.hpp` can make one; a call the lowering made to anything else would not have compiled against it.

The same program with a `main`, built by `cairn`'s own device command line for `sm_120` with nvcc 13.2 and g++ as host compiler, compiled to an object. `counts.json` lists the CUDA runtime symbols it references: `cudaStreamSynchronize`, `cudaMemcpyAsync`, `cudaMemsetAsync`, one `cudaStreamCreate` site, and neither `cudaDeviceSynchronize` nor a synchronous `cudaMemcpy` or `cudaMemset`.

## What the lowering did before

Read from the lowering and runtime at `533de2f`, not measured: each pass would have made two streams and two events (one per ticket) and destroyed them at their waits, made seven device allocations and freed them (the reduction's cell and storage, the scan's copy and storage, the compaction's flags, offsets and storage), waited for the whole device eight times (the region, the scan twice, the compaction three times, the two planned regions) and made five synchronous copies.

## What did not run

No device run: the CUDA build compiles for sm_120 and has not run on a GPU, and `make gpu` has not been run since. The host machine counts the calls the runtime makes; it does not show what they cost on a device, and no time was measured. A device `mma_unordered` still waits for the whole device (`cairn_tensor.hpp`). The machine was shared with other agents while this ran.

`python3 tools/checks/execution_counts.py` writes `counts.json` again, and `tests/runtime/test_execution.py` checks the same counts in the suite.

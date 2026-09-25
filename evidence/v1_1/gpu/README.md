# The 1.1.0 device session

The release's one run of `make gpu`, on 25 September 2026, with nothing else running on the machine: every test in the suite that runs device code, and afterwards `bench/device`'s `reduction` pair. It was the first time the device features added after 0.8.3 ran on a GPU. Three tests failed; two were defects, fixed in #123 before the release, and one was a tool that cannot instrument this device.

## The machine

An AMD Ryzen 7 7800X3D (16 threads) under WSL2 (Linux 6.18), with an NVIDIA GeForce RTX 5070 Ti (compute capability 12.0, 70 SMs) as the display GPU, Windows driver 596.49, CUDA 13.2 (`nvcc` V13.2.78) and its Compute Sanitizer. The GPU read 2 percent busy and 31 C before the session. After each run, the Windows System log held no `nvlddmkm` or display event and the Application log no `LiveKernelEvent`.

## What ran

1. `make gpu` at commit `23f4c98` (main after #120), from 18:21 UTC: `pytest -p no:xdist --device-runs` over the Makefile's `GPU_TESTS`, which collected the 52 tests that reach the device. 46 passed, 3 failed and 3 skipped, in 4 min 51 s. The skips are the tests that trap on the device on purpose, which run only with `CAIRN_GPU_TRAPS=1`, left unset. `make_gpu.txt` is the whole output. The target stopped at pytest's failure, so its second command, `bench/gpu/parallel_gpu.py`, did not run.
2. `capture_probe.cu`, one process, to find which call a CUDA graph capture refused.
3. The three failed tests again, from the fix branch of #123: 2 passed and 1 skipped.
4. `bench/device/device.py run reduction`, one process under the device lock, from the same branch: `reduction.json`.

## The three failures

`tests/runtime/test_finish_counter.py::test_every_launch_finishes_once_on_a_device_replayed_and_direct_at_once` failed with `cairn: cuda: operation not permitted when stream is capturing`. The probe showed that during a global capture `cudaStreamGetId` fails and ends the capture, while `cudaStreamGetCaptureInfo` and `cudaFuncGetAttributes` do not:

```text
cudaStreamGetId during capture           operation not permitted when stream is capturing
cudaStreamGetCaptureInfo during capture  no error
cudaFuncGetAttributes during capture     no error
first launch during capture              operation failed due to a previous error during capture
```

A launch with a finish claims its counter by the stream's id (`runtime/cairn_coop.hpp`), so no `cq_` entry of a function with a finish could be captured. The host stand-in had treated that query as one a capture allows. #123 names a capturing stream by its handle and makes the stand-in refuse the id during a capture; with the fix the test passed on the GPU: a graph of two enqueued sums replayed four times beside direct sums on another stream, then an empty grid, the legacy default stream and the checked entry, each total exact.

`tests/verification/test_device_validation.py::test_each_sanitizer_tool_is_a_result_of_its_own_on_the_device` failed because `initcheck` printed "Failed to initialize WDDM debugger interface" and "Device not supported": Compute Sanitizer cannot instrument this device under WSL2 without the WDDM debugger interface enabled. The validator counted that as the program's errors. #123 reports such a tool as `unavailable`, and the test skips with that reason; no Compute Sanitizer tool has checked device code here.

`tests/projects/test_demos.py::test_the_device_plate_has_the_host_bits` asked `build` for a 600 s timeout, which `build` refuses; it had never run. With 300 s it passed: the heat-plate sweep of `demos/numeric` on the GPU gave the same bits as its host build, in 7.2 ms for 200 sweeps of 1024 x 1024 cells against 60.2 ms on host lanes, one run (`numeric_device.json`).

## What passed

The other 46 tests ran device code and checked its results: the modules `GPU_TESTS` names at that commit, among them the first device runs of wide loads and stores, atomic updates, a cooperative region's finish, shared arrays nobody zeroes, warp votes, the `cq_` entries of `tests/runtime/test_enqueue.py` including a CUDA graph capture, `reduce op out[k]` over device views, and the device paths, plans, staging, tensor-core tiles, scans and foreign kernels that last ran on a GPU at 0.8.3 or never.

`make_gpu_tests.txt`, added on 2026-09-25, names each of the 52 tests with its result, from `make_gpu.txt` and a collection of the same list at `23f4c98`; nothing was run to make it. By it the passing tests include `examples/cooperative`'s device configuration, whose row sums go through `pipeline` stages at depths 2 and 3; the tensor-core tile kernels of `tests/soundness/test_tensor_kernels.py`, which index shared memory through layouts in code (`T.at`); and `examples/apps/gpu_pipeline` and `tests/runtime/test_execution.py`'s pipeline, which run a device `compact`. [RUN_NOTES.md](../RUN_NOTES.md) listed those three as not run on the GPU, which was wrong.

## The reduction pair

Median over 60 timed calls, interleaved, GPU time from events on the caller's stream:

| n (f32) | hand-written CUDA, one pass | `sum` through `cq_sum` | `sum` through `cf_sum` | `sum_unordered` through `cq_` | two-pass `load_wide` through `cq_` |
|---|---|---|---|---|---|
| 2^24 | 89.4 us | 91.9 us | 102.9 us | 96.7 us | 92.6 us |
| 2^26 | 329.1 us | 330.7 us | 331.3 us | 338.5 us | 332.9 us |

`examples/reduction`'s `sum`, written without `unsafe`, ran through `cq_sum` within 0.5 percent of the hand-written kernel at 2^26 and 2.8 percent at 2^24. Every total but `sum_unordered`'s was exact; its relative error was 2.4e-7, within its stated bound. One machine, one session; `host_us` and `burst_us` are in the record.

## What did not run

Tests that trap on the device on purpose, Compute Sanitizer on device code, `bench/gpu/parallel_gpu.py`, and every `bench/device` pair but `reduction`. #104's placement of waits was checked by the tests above, not timed.

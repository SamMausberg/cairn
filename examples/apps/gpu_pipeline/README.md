# gpu_pipeline

Four device stages over 2^22 values, each one statement, verified against the same four stages run on the host. Every phase is timed with `io.monotonic_ns`, which is CLOCK_MONOTONIC through `clock_gettime`.

```sh
cairn run examples/apps/gpu_pipeline --timeout 240
```

```text
gpu_pipeline: 4194304 values, kept 524288
gpu_pipeline: host pipeline   3485 us
gpu_pipeline: upload          125 us
gpu_pipeline: device map      305 us
gpu_pipeline: device compact  600 us
gpu_pipeline: device reduce   155 us
gpu_pipeline: download        1647 us
gpu_pipeline: device result matches the host
```

Needs nvcc and a CUDA device.

## What it demonstrates

The contracted forms run on the device unchanged. `compact out for i in m where ... yield ...` becomes CUB stream compaction when its output is a `@device` view and a plain loop when it is not, from the same source, and the stable-prefix contract holds on both. `reduce add_wrap ...` becomes a device tree reduction or a host in-order fold.

Why `add_wrap` and not `+`. The device folds in an unspecified association order, so only an operator that is exact under any association may be offered; checked `+` would make the trap depend on the order. The host and device sums are therefore equal by construction, and the program asserts it.

Compacted prefixes are parts. `keep_device` returns how many values it selected, and the sum of the selection is `sum_device(dev_used, dev_kept[0..dev_used])`, a part with one dynamic guard, since the buffer's own extent is the capacity and not the count.

Verification is elementwise. The kept prefix is downloaded and compared with the host's, and the two counts and the two sums must agree. The mixing function `mix` is pure and shared by the host loop and the device lanes.

## Effect rows worth noticing

```text
map_device   ffi_precondition, par:device, read:src, trap, write:out
keep_device  ffi_precondition, gpu_alloc, gpu_free, par:device, read:src, trap, write:out
sum_device   ffi_precondition, gpu_alloc, gpu_free, par:device, read:src, trap
main         ... alloc, free, gpu_alloc, gpu_free, io, par:device, transfer:h2d, transfer:d2h
```

The three stages differ only in what they read and write. `keep_device` and `sum_device` also say `gpu_alloc` and `gpu_free`, which is CUB's own temporary storage for the scan and the tree reduction: the device buffers the program itself asks for are not the only device memory in the row, and the row says so. `main` carries no `par:host`, because the host `reduce` emits a sequential fold.

## What the timings say

Measured 2026-09-19 on a GH200 with CUDA 12.8. The device does map, compact and reduce in about 1 ms against 3.5 ms for the host pipeline, but the download of the whole capacity costs 1.6 ms on its own. On this machine, moving results is more expensive than computing them, which is exactly the cost the language insists you write down as a `transfer`.

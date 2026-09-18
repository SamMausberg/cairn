# gpu_pipeline — upload, map, compact, reduce, download

Four device stages over 2²² values, each one statement, verified against the same four stages
run on the host. Every phase is timed with `io.monotonic_ns` (CLOCK_MONOTONIC through
`clock_gettime`).

```
cd /home/ubuntu/cairn && .venv/bin/python bin/cairn run examples/apps/gpu_pipeline --timeout 240
gpu_pipeline: 4194304 values, kept 524288
gpu_pipeline: host pipeline   3440 us
gpu_pipeline: upload          123 us
gpu_pipeline: device map      283 us
gpu_pipeline: device compact  616 us
gpu_pipeline: device reduce   140 us
gpu_pipeline: download        1649 us
gpu_pipeline: device result matches the host
```

Needs nvcc and a CUDA device.

## What it demonstrates

* **The contracted forms run on the device unchanged.** `compact out for i in m where ... yield
  ...` becomes CUB stream compaction when its output is a `@device` view and a plain loop when
  it is not — same source, and the stable-prefix contract holds on both. `reduce add_wrap ...`
  becomes a device tree reduction or a host in-order fold.
* **Why `add_wrap` and not `+`.** The device folds in an unspecified association order, so only
  an operator that is exact under any association may be offered; checked `+` would make the
  trap depend on the order. The host and device sums are therefore equal by construction, and
  the program asserts it.
* **Compacted prefixes are parts.** `keep_device` returns how many values it selected; the sum
  of the selection is `sum_device(dev_used, dev_kept[0..dev_used])` — a part with one dynamic
  guard, since the buffer's own extent is the capacity, not the count.
* **Verification is elementwise.** The kept prefix is downloaded and compared with the host's,
  and the two counts and the two sums must agree. The mixing function `mix` is pure and shared
  by the host loop and the device lanes.

## Effect rows worth noticing

```
map_device   read:src, write:out, trap, par:device
keep_device  read:src, write:out, trap, par:device
sum_device   read:src, trap, par:device
main         gpu_alloc, gpu_free, transfer:h2d, transfer:d2h, par:device, par:host, io, alloc
```

`par:host` in `main` comes from the *host* `reduce`, which the compiler currently reports as a
parallel region even though it emits a sequential fold — see the issue list. `gpu_alloc` and
`gpu_free` are the three device buffers; there is no hidden staging buffer anywhere in the row.

## What the timings say

The device does map + compact + reduce in about 1 ms against 3.4 ms for the host pipeline, but
the download of the whole capacity costs 1.6 ms on its own — on this machine, moving results is
more expensive than computing them, which is exactly the cost the language insists you write
down as a `transfer`.

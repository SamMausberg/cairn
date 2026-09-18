# simulator — one stencil, three back ends, bitwise agreement

A 2-D Jacobi heat sweep over a 1024×1024 grid, 16 sweeps, run three ways: an ordinary loop,
host lanes (`parallel` over `@host` views) and device lanes (`parallel` over `@device` views).
The program then compares all three element by element and fails if any bit differs.

```
cd /home/ubuntu/cairn && .venv/bin/python bin/cairn run examples/apps/simulator --timeout 240
simulator: 1024x1024 grid, 16 Jacobi sweeps
simulator: sequential 37171 us
simulator: threads    23996 us
simulator: device up  275251 us
simulator: device     466 us
simulator: all three back ends agree bit for bit
```

Needs nvcc and a CUDA device: any `@device` view sends the whole program through nvcc.

## What it demonstrates

* **Placement is the only difference.** `step_loop`, `step_threads` and `step_device` have the
  same body. The third one's views say `@device`, and that alone makes its lanes a kernel.
* **Bitwise agreement is a claim the language can keep.** The arithmetic lives in one function,
  `blend(up, down, left, right) = 0.25 * (up + down + left + right)`, compiled for both host and
  device from one definition. The build forbids contraction and reassociation on both sides
  (`-ffp-contract=off`, `--fmad=false`), so "the same expression" really is the same operations
  in the same order. `blend` and `interior` are pure, which is what lets a device lane call them.
* **Ping-pong without moving an owner.** Each round runs two sweeps, `a -> b` then `b -> a`, so
  the two `buffer`s never have to be swapped (a scoped buffer is a view, not a movable owner)
  and both keep the extent identity `m` that the callee's `[m]` demands.
* **`transfer` is the only crossing.** Two explicit copies, one in and one out; the effect row
  shows `transfer:h2d` and `transfer:d2h` and nothing else touches the boundary.

## Effect rows worth noticing

```
blend         (empty)
interior      local_read, local_write, trap
step_loop     read:src, write:out, trap
step_threads  read:src, write:out, trap, par:host
step_device   read:src, write:out, trap, par:device
main          ... gpu_alloc, gpu_free, transfer:h2d, transfer:d2h, par:host, par:device
```

`blend` has an empty row: it reads nothing, writes nothing and cannot trap. The three sweeps
differ in exactly one effect. `trap` on the others is the bounds and division guards, which stay
in the device build too.

## What the timings say

Host threads beat the sequential loop by only ~1.6× on 64 cores because every `parallel`
statement creates and joins its threads — 16 sweeps means 16 thread teams, and the kernel is
memory bound anyway. The device sweeps are ~80× faster than the sequential loop, but the first
device allocation pays ~275 ms to create the CUDA context, which the program reports separately
rather than hiding inside the measurement.

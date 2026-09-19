# simulator

A 2-D Jacobi heat sweep over a 1024x1024 grid, 16 sweeps, run three ways: an ordinary loop, host lanes (`parallel` over `@host` views) and device lanes (`parallel` over `@device` views). The program then compares all three element by element and fails if any bit differs.

```sh
cairn run examples/apps/simulator --timeout 240
```

```text
simulator: 1024x1024 grid, 16 Jacobi sweeps
simulator: sequential 38291 us
simulator: threads    3628 us
simulator: device up  283130 us
simulator: device     448 us
simulator: all three back ends agree bit for bit
```

Needs nvcc and a CUDA device: any `@device` view sends the whole program through nvcc.

## What it demonstrates

Placement is the only difference. `step_loop`, `step_threads` and `step_device` have the same body. The third one's views say `@device`, and that alone makes its lanes a kernel.

Bitwise agreement is a claim the language can keep. The arithmetic lives in one function, `blend(up, down, left, right) = 0.25 * (up + down + left + right)`, compiled for both host and device from one definition. The build forbids contraction and reassociation on both sides (`-ffp-contract=off`, `--fmad=false`), so the same expression really is the same operations in the same order. `blend` and `interior` are pure, which is what lets a device lane call them.

Ping-pong without moving an owner. Each round runs two sweeps, `a -> b` then `b -> a`, so the two `buffer`s never have to be swapped (a scoped buffer is a view, not a movable owner) and both keep the extent identity `m` that the callee's `[m]` demands.

`transfer` is the only crossing: two explicit copies, one in and one out. The effect row shows `transfer:h2d` and `transfer:d2h`, and nothing else touches the boundary.

## Effect rows worth noticing

```text
blend         (empty)
interior      trap
step_loop     ffi_precondition, read:src, trap, write:out
step_threads  ffi_precondition, par:host, read:src, trap, write:out
step_device   ffi_precondition, par:device, read:src, trap, write:out
main          ... gpu_alloc, gpu_free, transfer:h2d, transfer:d2h, par:host, par:device
```

`blend` has an empty row: it reads nothing, writes nothing and cannot trap. The three sweeps differ in exactly one effect. `trap` on the others is the bounds and division guards, which stay in the device build too.

## What the timings say

Measured 2026-09-19 on a GH200, 64 cores, CUDA 12.8, clang 15.0.7. Host threads beat the sequential loop by about 10x. Sixteen sweeps are sixteen regions, and they are cheap because the first one builds the lane pool and the other fifteen reuse it; under the 1.1 runtime, which created and joined a thread team per statement, the same program measured 24.0 ms instead of 3.6 ms, for about 1.6x. It is not about 64x because the kernel is memory bound. The device sweeps are about 85x faster than the sequential loop, but the first device allocation pays 283 ms to create the CUDA context, which the program reports separately rather than hiding inside the measurement.

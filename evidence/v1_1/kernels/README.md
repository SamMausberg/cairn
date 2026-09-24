# A one-pass reduction in safe CAIRN

What the kernels track added, checked on one machine: wide loads and stores (`load_wide[K]`, `store_wide`, a `Cache` hint), atomic updates of one element, a cooperative region's finish (`then threads t in T { }`) and shared arrays nobody zeroes, and `examples/reduction`, the f32 sum an agent had written with `unsafe` typed PTX and two launches, rewritten with none of either. Nothing here ran on a GPU.

## What ran

On one x86-64 machine under WSL2 (Linux 6.18), shared with four other agents, with clang++ 21, g++ 13.3 and CUDA 13.2 (nvcc, ptxas, cuobjdump). nvcc compiled for `sm_120` and nothing was launched.

`examples/reduction` runs both sums, the one-pass `sum` and `sum_unordered`, on host threads and holds them to a plain loop: exactly on whole numbers every partial sum of which `f32` holds, and within `(n + g) * 2^-23` of the sum of magnitudes on fractions, for 0 to 30,000 elements and 1 to 4 blocks. `tests/projects/test_reduction_example.py` runs that program under the thread sanitizer with clang++ and g++, runs the device configuration emulated on host threads under both compilers, and compiles the device kernels for `sm_120`.

`sum_sm_120.sass` is the one-pass sum's kernel as cuobjdump prints it, the encodings dropped, from `cairn build examples/reduction/gpu.toml --device-target sm_120` and `cuobjdump -sass` of the artifact. Its grid-stride loop is one `LDG.E.EF.128` a step, the evict-first 128-bit load `Cache.streaming` asks for, then four `FADD`s. Around it are the guards the language keeps: the checked `4 * q`, the bounds test `4 * q + 4 <= n`, and the alignment test, which nvcc reduces to one uniform test of the base pointer because the index is a multiple of four. The warp sums are five `SHFL.BFLY` each, the block counts itself with one `ATOMG.E.ADD` between two `MEMBAR.SC.GPU`, and ptxas reports a 0-byte stack frame with no spills. No instruction touches local memory.

The same file of kernels also holds `sum_unordered`, whose blocks add into `out[0]` with one `REDG.E.ADD.F32.FTZ.RN` each; the flush to zero of subnormals is in the contract `docs/numerics.md` states for `atomic_add_unordered`.

The rest of the track is held by the suite. `tests/soundness/test_expressiveness.py` compiles every row of the table in `docs/devices.md`. `test_wide.py`, `test_atomics.py`, `test_finish.py` and `test_written.py` each hold rejections naming their codes, native runs under clang++ and g++, the sanitizer that bites (address and undefined-behaviour for the guards, thread for atomics, the finish and shared memory), an oracle shown to bite by taking a barrier, an atomic or a write out of the emitted C++, emulated device runs, and the SASS or PTX each feature compiles to.

## What did not run

No device run: nothing here shows what a GPU computes, how fast, or that the fences order what the PTX memory model says they order on real hardware. How fast the one-pass sum runs, beside the CUDA kernel and the two-pass CAIRN kernel it replaces, is unmeasured; device timing is the DEVICE-PERF track's, under `make gpu`. The emulated runs are evidence about host threads, and the float sums they compare use orders the host takes.

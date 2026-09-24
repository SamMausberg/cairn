# Device benchmark

`parallel_gpu.cu` times saxpy (f32), a dot-product `reduce` (f32) and an even-number compaction (u64) three ways: a sequential host loop, `cr::par::run` on the host lanes, and the device, where device times are given kernel-only and end to end with transfers. Every case is checked against the sequential result before it is timed. `parallel_gpu.py` compiles it with `nvcc`, runs it and writes `results/gpu/benchmark.json` with the environment.

It runs code on a CUDA device, so only `make gpu` runs it, the owner's target that sets `CAIRN_GPU_TESTS=1` and holds `/tmp/cairn-gpu.lock` (docs/internals.md, Testing). It needs `nvcc` and a GPU, and writes nothing when either is missing. A release's copy of the record under `evidence/` is made by the release collector, never by this script.

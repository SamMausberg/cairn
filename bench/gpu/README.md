# Device benchmark

`parallel_gpu.cu` times three kernels three ways each: saxpy (f32), a dot product as a `reduce` (f32), and a compaction that keeps the even numbers (u64). The three ways are a sequential host loop, `cr::par::run` on the host lanes, and the device, where each device time is given for the kernel alone and end to end with transfers. Every case is checked against the sequential result before it is timed. `parallel_gpu.py` compiles it with `nvcc`, runs it, and writes `results/gpu/benchmark.json` with the environment.

It runs code on a CUDA device, so only `make gpu` runs it. That is the owner's target, which sets `CAIRN_GPU_TESTS=1` and holds `/tmp/cairn-gpu.lock` ([docs/internals.md](../../docs/internals.md#testing)). It needs `nvcc` and a GPU, and it writes nothing when either is missing. The release collector makes a release's copy of the record under `evidence/`, and this script never does.

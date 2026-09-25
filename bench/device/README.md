# Device pairs

Each pair is a CAIRN program and the hand-written CUDA of the same computation, built into one program that times both on one stream, interleaved. They answer where a CAIRN kernel's time goes against CUDA written plainly: in the kernel, or in the host's round trips around it.

| Pair | CAIRN | CUDA |
|---|---|---|
| `overheads` | a region of 256 elements, two in a row, a device `reduce` whose total returns | an empty launch, a launch and a wait, a 4-byte copy back, the same small kernel |
| `reduce` | a sum of f32 in two passes (scalar loads; 16-byte loads through unsafe typed PTX; one element a thread), and the `reduce` collector | one pass with 16-byte `__ldcg` loads and a last-block finish, two passes with and without them, CUB (`reduce_base.cuh`) |
| `reduce_wide` | the same two-pass sum with its 16-byte loads written with `load_wide[4]`, and in one launch with a finish; it needs a compiler that has both | the one-pass and two-pass designs with 16-byte loads |
| `reduction` | `examples/reduction`'s one-launch sums (a finish, through `cf_sum` and `cq_sum`, and an atomic add) and `reduce_wide.cairn`'s two-pass sum, compiled as one program | the one-pass design |
| `saxpy` | `parallel i in n { out[i] = a * x[i] + y[i]; }`, once and twice in a row | one thread an element |
| `layernorm` | a cooperative region, a block of 256 threads a row | the same algorithm |
| `transpose` | `examples/cooperative`'s tile transpose | the classic padded 32 x 32 tile |
| `stencil` | a Jacobi sweep as a `parallel` region and as a cooperative region | a 2D grid of 32 x 8 blocks |
| `capture` | two regions and the two-pass sum through their enqueued entries, captured in a CUDA graph as the process's first CUDA work | two kernels captured the same way |

Each program prints one JSON record: per variant and size, the median over the timed calls of `gpu_us` (events on the caller's stream around one call, as a leaderboard harness measures), `host_us` (until the stream has finished what the call queued), `return_us` (until the call returned), and `burst_us` (ten calls back to back, per call). `bench.cuh` says exactly what each is. The CAIRN side runs on the harness's stream through `NAME_device_stream`, and through the enqueued entries `cq_NAME` where the compiler gives them. Results are checked: sums against the exact sum, transposes and stencils element for element against the CUDA side, layer norms within float rounding.

```sh
python3 bench/device/device.py build              # every pair for sm_120, under results/device/; nothing runs
python3 bench/device/device.py sass reduce        # each kernel's instructions, hot loop, registers, local memory
CAIRN_GPU_TESTS=1 python3 bench/device/device.py run reduce   # one pair on the GPU, under the device lock
```

`run PAIR REPS TEXT` also leaves out every variant whose `side/name` holds TEXT. `build` and `sass` need nvcc and cuobjdump and launch nothing. `run` runs code on the GPU: it refuses without `CAIRN_GPU_TESTS=1`, holds `/tmp/cairn-gpu.lock`, and stops the program after 30 seconds. Each program runs for a few seconds. `--src DIR` builds the CAIRN side with the compiler and runtime under another `src`, so two revisions can be timed against the same hand-written side. The CUDA side is compiled with `nvcc -O3 -arch=sm_120` and its CUB in a namespace of its own; the CAIRN side with CAIRN's own device command line, `--fmad=false` included.

Recorded runs, with the machine and every command, are in [evidence/v1_1/device_perf](../../evidence/v1_1/device_perf/README.md).

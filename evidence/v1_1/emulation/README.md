# Device code emulated on the host

What `--emulate` ran: device programs judged against `sm_120` and built by the host compiler, their device work on host threads ([devices.md](../../../docs/devices.md#emulating-device-code-on-the-host)). Nothing here ran on a GPU.

## What ran

On one x86-64 machine under WSL2 (Linux 6.18), shared with four other agents, with clang++ 21 and g++ 13.3; nvcc 13.2 was installed and did not take part in any emulated build.

`python3 tools/checks/emulation_runs.py` built each device example with `cairn`'s own emulated command line under both compilers and ran it; `runs.json` holds every row. Each example holds its device results to the host's own loops and says so on its last line:

| Example | What it checks | clang++ | g++ |
|---|---|---|---|
| `examples/cooperative/gpu.toml` | a tiled transpose, block sums and a two-stage pipeline's row sums, bit for bit against plain loops | exit 0 | exit 0 |
| `examples/apps/gpu_pipeline` | a map, a compaction and a reduction over 2^22 values against the host stage for stage | exit 0 | exit 0 |
| `examples/apps/matmul/gpu.toml` | a 256 x 512 x 1024 `mma_unordered` against the contract's bound | exit 0 | exit 0 |
| `examples/apps/analytics/gpu.toml` | device queries, a `kernel fn` and a queued pipeline against the host bit for bit | exit 0 | exit 0 |
| `examples/apps/simulator` | 16 Jacobi sweeps, sequential, on host threads and on the device, bit for bit | exit 0 | exit 0 |
| `demos/numeric/gpu.toml` | 200 sweeps of a 1024 x 1024 plate against the host lanes bit for bit | exit 0 | exit 0 |

The same script validated a cooperative kernel whose grid counts only whole tiles of 64 against its lane-region reference. Without `--emulate` the answer is `unknown`. With it, the kernel fails after 3 cases, shrunk to `n = 1, out = [0], x = [1]`, and the corrected kernel passes all 48 cases as `finite-tested-emulated`.

`tests/projects/test_emulation.py`, `tests/verification/test_emulated_validation.py` and `tests/soundness/test_tensor_kernels.py` hold these and more in the suite: the address, undefined-behaviour and leak sanitizers on emulated builds, the thread sanitizer on emulated cooperative regions, both tensor-core kernels and two tasks queuing device work at once, a guard and a barrier taken out of the emitted C++ that the sanitizers then report, and every refusal with `E-EMULATE`.

`cairn emit` of eleven device programs (the six above, `examples/cooperative/tuned.toml`, the three files of `examples/tensor` and `examples/foreign/device`) printed the same bytes under the compiler at `91e2eb0` and with `--emulate` added (`emit.txt`), which leaves the emitter alone: an emulated build compiles the device build's program.

## What did not run

No device run: nothing here shows that a device computes what the emulation computed. Where the two may differ is stated in [numerics.md](../../../docs/numerics.md#emulated-device-runs): float reductions and scans, whose order the device leaves unspecified, and the tensor-core multiply, within its contract's bound. nvcc and ptxas did not run on these builds, so nothing they alone would refuse was checked. The times the examples print measure host threads and are not device timings.

# What the compatibility jobs found

The jobs `.github/workflows/ci.yml` gained on 24 September 2026 compile device code under two CUDA toolkits with both of nvcc's host compilers, test the oldest supported and the newest compilers, every supported Python, an AArch64 host and the installed package ([internals.md](../../../docs/internals.md#continuous-integration)). This is what they ran and what they found in their first runs. Nothing ran on a GPU: no runner has one, and no job sets `CAIRN_GPU_TESTS`.

## What ran

GitHub-hosted runners, images of 20 September 2026: ubuntu-24.04, ubuntu-22.04, ubuntu-26.04 and ubuntu-24.04-arm. Run [36049684776](https://github.com/SamMausberg/cairn/actions/runs/36049684776), at 78d9f40, is the first with every new job complete:

| Job | Toolchain | Tests |
|---|---|---|
| device, four | nvcc 12.9.86 and 13.2.86 from NVIDIA's repository, each with g++ 13.3.0 and clang 18.1.3 as host compiler | 951 passed, 33 skipped in each; every device example built for its two targets, 20 builds each, none refused |
| compilers, oldest | g++ 11.4.0, clang 13.0.1, Python 3.11.16, Linux 5.15 headers | 2424 passed, 104 skipped, 1 failed (below) |
| compilers, newest | g++ 15.2.0, clang 23.1.3 from apt.llvm.org, Python 3.14.7 | 2419 passed, 104 skipped |
| python, three | Python 3.11.16, 3.13.15 and 3.14.7 | 3844 passed, 51 skipped in each |
| arm | g++ and clang of ubuntu-24.04-arm, qemu-system-aarch64 | 1338 passed, 80 skipped; the freestanding image, 15 passed and none skipped |
| package | the sdist and the wheel built from it, Python 3.12.14 | `cairn doctor`, `check`, `run`, `test` and `build` of a copied example and an emulated device run from the installed wheel; the plugin's MCP server from a clean copy |

Each device job runs every test that needs nvcc (`make device-build`) and builds each device example for two of sm_80, sm_90a, sm_100a and sm_120, so each toolkit builds every example for all four.

## What they found

Every one was a real difference between compilers, toolkits, kernels or hosts, and each is fixed in the file that owns it.

| Found | Where | Fixed in |
|---|---|---|
| `cairn_io.hpp` names `__kernel_timespec`, which Linux 5.15's `io_uring.h` does not declare, so no program with an I/O ring built on Ubuntu 22.04 | GCC 11 and Clang 13 in an ubuntu:22.04 container, before the job ran | 1a47148 |
| the target tests expected CUDA 13's target list: 12.9 still builds sm_101a and has no sm_110 | an nvcc 12.9 container | 85d03ed |
| cuobjdump needs nvdisasm to print instructions, and a vendored kernel's name needs cu++filt | an nvcc 12.9 container | the device job installs both |
| CUDA 12.9 writes a 32-bit PTX access `.f32` where 13.2 writes `.b32`, which two tests read literally | device, CUDA 12.9, both hosts | 6a3c69c |
| on an AArch64 host every prediction says the packaged profile measured another architecture, which a scan test did not allow | arm | 97f6220 |
| Clang 13 words a failed static assertion `static_assert failed`, which a layout test did not match; the first fix's pattern missed it too | compilers, oldest | d2d3346, b24787c, eecbf66 |
| rules_cairn ran a checkout's `bin/cairn` under the `python3` on Bazel's action `PATH`, which is 3.10 on Ubuntu 22.04 | compilers, oldest | eb5e22a |
| GCC 10 has no `__builtin_bit_cast` and Clang 12 refuses `-Wno-unused-but-set-variable`, which the toolchain passes | containers; they set the floor at GCC 11 and Clang 13 | 1f0446f states it in README.md |

The containers ran on the reference machine (x86-64, WSL2, Linux 6.18) under Docker, with address randomness turned off for the sanitized builds, as the oldest job turns it down on its runner.

## What did not run

No device code ran anywhere. The toolkits between 12.9 and 13.2 and after 13.2, GCC 12 to 14 and Clang 14 to 22 apart from the runners' defaults, and the weekly scheduled run, have not run. The `ci-passed` check has not yet been green on `main`. The runs failed first on the findings above, then on tests landed beside them, each fixed as it was found:

| Run | Commit | Red on |
|---|---|---|
| [36051605615](https://github.com/SamMausberg/cairn/actions/runs/36051605615) | b80fa92 | a device test module missing from `DEVICE_TESTS` (fixed in 5b6e5bd); the tune budget test where no nvcc is installed (77f2ed3, ac9f472); a thread sanitizer test of a cooperative finish (4528029) |
| [36053292974](https://github.com/SamMausberg/cairn/actions/runs/36053292974) | a0361bf | the tune budget test; the kept-compile test over the harness manifests, fixed beside it; a PTX byte store CUDA 12.9 spells `.u8` (fc8d9ce) |
| [36055094583](https://github.com/SamMausberg/cairn/actions/runs/36055094583) | 76f9971 | only that byte store, in the two CUDA 12.9 jobs; every other job that had finished passed |

The run of fc8d9ce and later had not finished when this record was written.

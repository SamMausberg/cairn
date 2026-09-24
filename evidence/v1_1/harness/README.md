# Benchmark submissions

What `cairn export --harness` wrote and how far each submission got without a GPU ([tools.md](../../../docs/tools.md#cairn-export---harness)): built as its benchmark builds it, against a CUDA build of torch, and, for host-view kernels, run on CPU tensors against the benchmark's reference. Nothing here ran on a GPU, under an official evaluator or on a leaderboard.

## What ran

On one x86-64 machine under WSL2 (Linux 6.18), shared with four other agents, at `ed322b2`, with g++ 13.3, nvcc 13.2 (V13.2.78) and Python 3.12.3. Torch came from two wheels in a scratch virtual environment, never a CAIRN dependency. torch 2.9.0+cpu, from PyTorch's CPU index, ran every step. torch 2.9.0+cu130 (`torch-2.9.0+cu130-cp312-cp312-manylinux_2_28_x86_64.whl`, sha256 `cc241ffb20428f6a44c299ca06b934445606cf1fa48f3b68ef3af0a04c86bc3b`) was unpacked for its headers and libraries, compiled and linked against, and never imported. sol-execbench 1.0.2 was installed from its repository with `--no-deps`, for its pydantic models.

The adapter follows these upstream revisions, which `src/cairn/projects/harness.py` pins and `tools/checks/harness_records.py` checks against its clones:

| Benchmark | Repository | Commit |
|---|---|---|
| SOL-ExecBench | NVIDIA/SOL-ExecBench | `a9fa0804c793d438e70850c33fe34426e66d53dd` |
| GPU MODE | gpu-mode/reference-kernels, and gpu-mode/popcorn-cli for the commands | `f3295bb6bd559bae4659d2d8fc45edb1394251b1`, `036b3ead604f1e9f404cbee5cf0e48377ef0766f` |
| KernelBench | ScalingIntelligence/KernelBench | `423217d9fda91e0c2d67e4a43bf62f96f6d104f1` |

`tests/projects/test_harness.py` and `tests/projects/test_harness_torch.py` ran under the CPU torch at `c7a4821`, with `CAIRN_TORCH_CUDA_ROOT` naming the cu130 wheel's torch directory: 37 passed, none skipped. The torch half built and ran these:

| Test | What it showed |
|---|---|
| KernelBench | a host-view ReLU's `ModelNew`, built by `load_inline`, equals `torch.relu` on a 64 x 1000 tensor holding a NaN, `-0.0` and `-inf`, and refuses a double, a transposed and a flattened tensor by name |
| GPU MODE | a host-view `a * x + y` returns the task's own output tensor, bit-equal to `1.5 * x + y`, and refuses an output that overlaps an input and a mismatched dimension |
| SOL-ExecBench | `solution.json` staged as `driver/templates/build_ext.py` stages it and built by `torch.utils.cpp_extension.load` takes its output last, returns nothing, and equals the reference's `run` |
| schema | `sol_execbench.core.Solution` accepts `solution.json` |
| device program | a device kernel's program, with `torch/types.h`, `cuda.h` and `cuda_runtime.h` put before it as `load_inline` puts them, compiles with nvcc for sm_100a against the CPU wheel's headers |
| device binding | the binding compiles against the cu130 headers twice: calling `cq_axpy` on torch's stream, and calling `cf_doubled` with the stream bound, since its transfer from host memory lists it under `E-ENQUEUE` |

`python tools/checks/harness_records.py --upstream DIR --torch-root DIR` wrote `builds.json`. It writes torch's own ninja file for each submission, points it at the cu130 wheel's headers and libraries, and builds it. Every build compiled and linked, and nothing it built was loaded:

| Submission | CAIRN function | Entry | Also checked | Build |
|---|---|---|---|---|
| SOL-ExecBench `rmsnorm_h4096` (`examples/cuda_cpp/rmsnorm`) | an RMSNorm in a cooperative region | `cq_rmsnorm` | `Solution` and `Definition` accept it | nvcc and g++, sm_100a, linked |
| SOL-ExecBench FLUX RoPE (`examples/cuda_cpp/flux_rope`) | the imported signature, body empty | `cf_reference` | `Solution` and `Definition` accept it | g++, linked |
| SOL-ExecBench `gqa_paged_decode_h32_kv8_d128_ps1` | the imported signature, body empty | `cf_reference` | `Solution` and `Definition` accept it | g++, linked |
| SOL-ExecBench Gemma 3 SwiGLU | the imported signature, body empty | `cf_reference` | `Solution` and `Definition` accept it | g++, linked |
| GPU MODE `vectoradd_v2` (`examples/harness`) | `vectoradd` over f16 | `cq_vectoradd` | KernelBench's static checker: valid, no warning | nvcc and g++ through `load_inline`, sm_100a, linked |
| KernelBench level 1, problem 19 (`examples/harness`) | `relu` | `cq_relu` | KernelBench's static checker: valid, no warning | nvcc and g++ through `load_inline`, sm_100a, linked |

The four SOL-ExecBench projects came from `cairn new --from-sol-execbench` on the pinned repository's definitions, whose workloads state no tolerance but SOL-ExecBench's default, so each `policy.json` holds 0.01 and 0.01. A reference whose body is empty does no device work, so its library is host code, and it is built and linked as the benchmark would build it.

The same script built the RMSNorm project with `--emulate`, its device work on host threads, and called it through its ctypes binding on random CPU tensors (`torch.randn`, seed 200). Against the definition's own PyTorch `run`, under its tolerance, every element of the nine workloads of at most 256 rows (1, 7, 15, 16, 34, 63, 64, 79 and 170) agreed, with a largest error of 0.0156 and more than 99.99 percent of the elements bit-equal. The kernel sums each row in its block's tree and torch in its own order, which can round the rest differently.

## What did not run

No device run. No extension built here was loaded, so nothing shows that the device submissions compute their answers on a GPU, and the emulated RMSNorm is evidence about the host emulation only. The five workloads of 8,804 to 14,509 rows were not checked, since an emulated cooperative region runs each block's threads as host threads.

No official evaluator ran: not `sol-execbench`, not KernelBench's `scripts/run_and_check.py`, not GPU MODE's `eval.py` or `popcorn`. Their build steps were reproduced with torch's own build functions at 2.9.0, not run as their scripts. Nothing was submitted, and no time, score or speedup was measured, so nothing here says how a submission would rank.

The graph-capture safety of `cq_NAME` on the first call of each kernel in a process is being checked by the device-performance work, and nothing here claims it.

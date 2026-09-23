# Layouts and tensor-core fragments

Two matrix multiplies written in CAIRN with layouts and tensor-core fragments, `examples/tensor/tile64.cairn` and `examples/tensor/tile32.cairn`, and one transpose through a shared tile stored three ways, `examples/tensor/transpose.cairn`. The runtime header `cairn_tensor.hpp` was not edited for either multiply. The device builds compile for sm_120 and have not run on a GPU.

Machine: one x86-64 host with an RTX 5070 Ti (sm_120) under WSL2, shared with five other agents while this ran. g++ 13.3, clang 21.1.8, CUDA 13.2. Nothing here was timed.

| Kernel | Output tile | Warps | Stages | Fragments | A layout |
|---|---|---|---|---|---|
| `tile64` | 64 x 64 | 4, each 2 x 2 fragments | 2 | WMMA 16 x 16 x 16, f16 | `pad(rows(128, 32), 8)`, both stages |
| `tile32` | 64 x 32 | 8, each 1 x 2 fragments | 1 | `mma.sync` 16 x 8 x 16, bf16 | `swizzle(rows(64, 32), 2, 3, 3)` |

Both add `a * b` into `c` in place, with the signature of `mma_unordered(m, n, k, c, a, b)`.

## What ran on the host

`host_gcc.json` and `host_clang.json` hold one run of each kernel under each compiler, on 16 shapes: six fixed (1 x 1 x 1, whole tiles, a tail in every direction, `m = 0`, `k = 0`) and ten generated with sides up to 200 and `k` up to 160, with entries that are multiples of 1/16. The host lowering runs every block's threads as real threads. Every output lay within `mma_unordered`'s bound, `(k + 1) * 2^-22 * (|c| + sum |a * b|)`, of the exact rational sum, and every output equalled the reference loop's bit for bit, since both add in increasing k. The largest error was 2.0% of the bound for `tile64` and 3.4% for `tile32`, over 115,648 and 119,628 outputs, the same under both compilers. `tests/soundness/test_tensor_kernels.py` repeats this on other generated shapes, runs both kernels and the transpose under the thread sanitizer, and holds the transpose's three layouts to the plain one.

That is finite testing on the host. The tensor cores add in an order of their own, and whether they meet the contract is `make gpu`'s question (`test_each_kernel_keeps_the_contract_on_the_device`), which has not run.

## What compiled for sm_120

`sm_120.json` records the device build of each kernel, compiled by the project's device command line with `-arch=sm_120 -cubin` and read back with `cuobjdump -sass`. `tile64` compiles to 16 `HMMA.16816.F32` fed by `LDSM`, 102 registers and 36,864 bytes of shared memory; `tile32` to 4 `HMMA.16816.F32.BF16` fed by `LDSM.16.M88.4` for its swizzled A and `LDSM.16.MT88.2` for B, 103 registers and 14,848 bytes. Neither spills. These are compiler observations, not measurements, and say nothing about speed.

## What the layouts established

`cairn explain examples/tensor/transpose.cairn` reports, for the spread that reads the tile a column at a time, 32 bank ways when the tile is row-major and 1 when it is padded or swizzled, and that moving values between the two spreads crosses warps (`shared`). Every spread in the three programs covers its tile exactly once, which the checker holds each declaration to; `proofs/Cairn/Layout.lean` proves what that gives writes through it, and `tools/checks/differential_layouts.py` compares the checker with the Lean definitions on generated layouts.

## After the review

An adversarial review found five programs the checker accepted and should not have, each now refused or guarded with a test in `tests/soundness/test_fragments.py` or `tests/language/test_layouts.py`. A fragment tile whose length is not a literal read past its end under AddressSanitizer; it now traps first, under both compilers. A layout with an offset of 2^64 or more, or a swizzle past bit 63, computed other offsets in C++ than the checker enumerated; both are `E-LAYOUT`. A fragment coordinate or fill value that differed within a warp was accepted, and is `E-COOP-WARP`. A region that loaded fragments from a `@device` view but indexed only host views ran on host threads; a fragment's view now places the region. The phase rule took a WMMA store's writer lanes from `mma.sync`'s share, which WMMA does not promise; a WMMA store is now the warp's write. After these changes both kernels still meet the contract on the host, and their sm_120 builds have the same instruction counts, registers and shared memory as above.

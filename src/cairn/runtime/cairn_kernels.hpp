// CAIRN device kernels: the device side of every region, in plain CUDA and with no execution context. A lane body
// runs in `lanes` (one index per thread, strided over a grid), `chunks` (W adjacent indices over one aligned chunk)
// or `staged_lanes` (a block's tile in shared memory between two barriers); `fire`, `fire_vector` and `fire_staged`
// launch one on a stream the caller names and do not wait. This is the device implementation of a generated
// program: cairn_gpu.hpp adds the CAIRN launch wrappers (cairn_exec.hpp's operations on the thread's context), and
// an application that launches on its own streams can call these instead. Any CUDA error aborts the process.
//
// Build (CUDA 12.8 with CUB from the toolkit and CUDA 13.2 with CCCL 3 both pass; g++ and clang++
// as -ccbin alike). One command; the lines below are joined by spaces:
//
//   nvcc -std=c++20 -O3 --fmad=false -arch=sm_120
//        --extended-lambda --expt-relaxed-constexpr -Werror all-warnings
//        -Xcompiler -Wall,-Wextra,-Werror,-Wno-unused-parameter,-Wno-unused-variable,
//                   -Wno-unused-but-set-variable,-fexceptions,-fno-rtti,
//                   -ffp-contract=off,-fno-fast-math
//        prog.cu -o prog
//
// (-Xcompiler takes one comma separated word: the three indented lines are one argument.)
//
// -fexceptions is the one departure from the host contract, and only for a device program's host
// pass: CCCL 3 (CUDA 13) reaches thrust/system/cuda/detail/util.h from CUB's dispatch headers,
// and its throw sites and system_error.inl's catch are unguarded, so -fno-exceptions refuses to
// parse them. Nothing in this runtime throws; every guard still aborts the process.
//
// --fmad=false is the device half of -ffp-contract=off: no contraction is ever authorized.
// --expt-relaxed-constexpr is required, not cosmetic: without it std::numeric_limits<T>::min()
// and std::in_range in cr::divide/cr::convert are host-only and nvcc merely warns (#20013-D).
// -Werror all-warnings promotes nvcc's own host/device warnings to errors; it is what turns a
// silently skipped guard into a build failure. -arch names the device target the build resolved
// (projects/target.py), never native. `#include <cub/cub.cuh>` is NOT usable: it drags in Thrust's
// system_error.inl, which needs -fexceptions. The four narrow CUB headers below do not.
//
// Device trap mechanism (measured, see tests/runtime/gpu_runtime.cu):
//   __trap()          chosen. The kernel dies, the context is poisoned, and every later call on
//                     it - the next sync, wait or copy - returns cudaErrorLaunchFailure, which
//                     cr::gpu::check turns into std::abort. No hang, no wrong answer, one PTX
//                     instruction on the failure path only, and NDEBUG cannot remove it.
//   assert(false)     works (cudaErrorAssert) but -DNDEBUG deletes it: measured, the kernel ran
//                     on and reported success. It also prints one line per failing lane.
//   __builtin_trap()  unusable: nvcc's device pass treats it as a host call (#20011-D) and drops
//                     it. Measured: the out of bounds kernel completed and sync said "no error".
//   fault word in unified memory: cannot stop the faulting lane, since cr::trap() is [[noreturn]]
//                     and cr::at() must still return a reference, so the bad access happens
//                     anyway. Rejected: it turns a guard into a race with a corrupted write.
#pragma once
#include <cstddef>
#include <cstdio>
#include <cuda_runtime.h>
#include "cairn_runtime.hpp"
namespace cr::gpu {

inline void check(cudaError_t e) noexcept {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "cairn: cuda: %s\n", cudaGetErrorString(e));
  trap();
}

// A region's default block; the most blocks one launch takes, past which blocks loop (the device allows 2^31 - 1, and
// a cooperative region of short blocks ran faster capped here than with a block of its own for each block of the
// region: evidence/v1_1/device_perf); a warp.
constexpr unsigned BLOCK = 256, MAX_GRID = 65535, WARP = 32;

// One lane per i, strided so that n is limited by memory rather than by a grid dimension. Every i below n runs
// exactly once whatever the grid and block, which is why a plan may choose them: `block` threads a block, a grid
// that gives each thread about `per_lane` indices, and the stride loop unrolled `U` times.
template<unsigned U, class F> __global__ void lanes(std::size_t n, F body) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x;
#pragma unroll U
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += step) body(i);
}
// The most threads a block of a kernel can hold, read once per kernel: a planned block the kernel's registers
// cannot fill runs in whole warps as wide as fit, and computes the same.
template<class K> inline unsigned most(K kernel) noexcept {
  cudaFuncAttributes held{};
  check(cudaFuncGetAttributes(&held, kernel));
  return unsigned(held.maxThreadsPerBlock) / WARP * WARP;
}
template<unsigned U, class F> inline unsigned widest() noexcept {
  static const unsigned held = most(lanes<U, F>);
  return held;
}
inline unsigned grid(std::size_t n, std::size_t each) noexcept {
  const std::size_t g = (n + each - 1) / each;
  return g < MAX_GRID ? unsigned(g) : MAX_GRID;
}
template<unsigned U = 1, class F>
inline void fire(std::size_t n, const F& body, cudaStream_t s, unsigned block = BLOCK, std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  if(!n) return;
  if(block > widest<U, F>()) block = widest<U, F>();
  lanes<U><<<grid(n, std::size_t(block) * per_lane), block, 0, s>>>(n, body);
  check(cudaGetLastError());
}

// `plan f { vector W; }`: W adjacent indices per lane over chunks (Chunk in cairn_exec.hpp), the tail one at a time.
template<unsigned W, unsigned U, class F, class G> __global__ void chunks(std::size_t n, F scalar, G chunk) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x * W;
#pragma unroll U
  for(std::size_t i = (blockIdx.x * std::size_t(blockDim.x) + threadIdx.x) * W; i < n; i += step) {
    if(n - i >= W) chunk(i);
    else for(std::size_t k = i; k < n; ++k) scalar(k);
  }
}
template<unsigned W, unsigned U, class F, class G>
inline void fire_vector(std::size_t n, F scalar, G chunk, cudaStream_t s, unsigned block, std::size_t per_lane) noexcept {
  static const unsigned held = most(chunks<W, U, F, G>);
  if(block > held) block = held;
  chunks<W, U><<<grid(n, std::size_t(block) * per_lane * W), block, 0, s>>>(n, scalar, chunk);
  check(cudaGetLastError());
}

// `plan f { stage R; }`: a block runs its indices tile by tile, loading each tile into shared memory between two
// barriers every thread reaches (the rule is in cairn_exec.hpp's run_staged).
template<std::size_t R, unsigned U, class L, class F> __global__ void staged_lanes(std::size_t n, L load, F body) {
  extern __shared__ __align__(16) unsigned char cr_shared[];
  const std::size_t b = blockDim.x, w = b + 2 * R, t = threadIdx.x, step = std::size_t(gridDim.x) * b;
#pragma unroll U
  for(std::size_t base = blockIdx.x * b; base < n; base += step) {
    __syncthreads();  // every thread is done with the last tile before this one overwrites it
    load(base, w, t, b, cr_shared);
    __syncthreads();
    if(base + t < n) body(base + t, base, w, cr_shared);
  }
}
template<std::size_t R, unsigned U, class L, class F, class S>
inline void fire_staged(std::size_t n, L load, F body, S bytes, cudaStream_t s, unsigned block,
                        std::size_t per_lane) noexcept {
  static const unsigned held = most(staged_lanes<R, U, L, F>);
  if(block > held) block = held;
  const std::size_t shared = bytes(std::size_t(block) + 2 * R);
  if(shared > 48 * 1024)  // past the default a kernel must ask for its dynamic shared memory
    check(cudaFuncSetAttribute(staged_lanes<R, U, L, F>, cudaFuncAttributeMaxDynamicSharedMemorySize, int(shared)));
  staged_lanes<R, U><<<grid(n, std::size_t(block) * per_lane), block, shared, s>>>(n, load, body);
  check(cudaGetLastError());
}

}  // namespace cr::gpu

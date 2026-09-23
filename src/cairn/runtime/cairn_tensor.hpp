// The tensor-core multiply (compiler/tensor.py). mma_unordered(m, n, k, c, a, b) adds the product of the row-major
// m x k matrix a and the row-major k x n matrix b, both of one storage float, into the row-major m x n f32 matrix c.
// docs/numerics.md#the-tensor-core-multiply states its contract: each product a[i][p] * b[p][j] exact wherever f32
// holds it, each output the f32 sum of its k products and its old value, added in an order and grouping the
// hardware picks, and within (k + 1) * 2^-22 * (|c[i][j]| + the sum of |a[i][p] * b[p][j]| over p) of the exact
// result, its old value included.
//
// On the host the multiply is its own reference: every product, then every sum in increasing p. On the device it
// runs 64 x 64 output tiles, one block of four warps each, over k in steps of 32 staged through shared memory in two
// buffers, each warp adding 2 x 2 tensor-core 16 x 16 x 16 products per step. The tile's phases are written once,
// against the operations a Tile is given, so that tests/native/tensor_runtime.cpp runs every thread of a block phase
// by phase on the host, with a model of the tensor-core operations, and holds the tiling, the tails and the two
// stages to the reference exactly.
#pragma once
#include <cstddef>
#include <cstdint>
#include "cairn_float.hpp"

namespace cr::tensor {

// The extents the call wrote agree with its views: c holds m * n, a holds m * k and b holds k * n elements.
inline void shape(std::size_t m, std::size_t n, std::size_t k, std::size_t c, std::size_t a, std::size_t b) noexcept {
  if(cr::mul<std::size_t>(m, n) != c || cr::mul<std::size_t>(m, k) != a || cr::mul<std::size_t>(k, n) != b) trap();
}

// The reference, and the host lowering: for every output, its products added in increasing p after its old value.
// Looping p outside j keeps every output's order and lets the compiler move along a row of b.
template<class A> inline void reference(std::size_t m, std::size_t n, std::size_t k, float* c, const A* a,
                                        const A* b) noexcept {
  for(std::size_t i = 0; i < m; ++i)
    for(std::size_t p = 0; p < k; ++p) {
      const float x = static_cast<float>(a[i * k + p]);
      for(std::size_t j = 0; j < n; ++j) c[i * n + j] = c[i * n + j] + x * static_cast<float>(b[p * n + j]);
    }
}

template<class A> inline void multiply(std::size_t m, std::size_t n, std::size_t k, float* c, std::size_t cn,
                                       const A* a, std::size_t an, const A* b, std::size_t bn) noexcept {
  shape(m, n, k, cn, an, bn);
  reference(m, n, k, c, a, b);
}

// One output tile: BM x BN outputs, k in steps of BK, two stages of a and b in shared memory, THREADS threads in
// WARPS warps of 32, each warp owning a WM x WN square of 16 x 16 fragments. Rows are padded so that every
// fragment starts on 32 bytes, as the tensor-core loads require.
constexpr std::size_t BM = 64, BN = 64, BK = 32, THREADS = 128, WARPS = 4, WM = 2, WN = 2, F = 16;
constexpr std::size_t SA = BK + 8, SB = BN + 8, SC = BN + 4;
static_assert(WARPS * WM * WN * F * F == BM * BN && WARPS * 32 == THREADS, "the warps cover the tile once");

template<class Ops> struct Tile {
  using Stage = typename Ops::Stage;
  alignas(128) Stage as[2][BM][SA];
  alignas(128) Stage bs[2][BK][SB];
  alignas(128) float cs[BM][SC];

  // The old values of the tile's outputs, zero where the tile passes the matrix.
  CR_HD void load_c(std::size_t m, std::size_t n, const float* c, std::size_t row, std::size_t col, std::size_t t) {
    for(std::size_t e = t; e < BM * BN; e += THREADS) {
      const std::size_t i = e / BN, j = e % BN;
      cs[i][j] = row + i < m && col + j < n ? c[(row + i) * n + col + j] : 0.0f;
    }
  }
  // Stage s of a and b for the step starting at p0, zero past the matrix: a zero row or column of a tile adds 0 * 0
  // to an output inside it and nothing to an output outside it, which is never written back.
  template<class A> CR_HD void load_ab(Ops& ops, std::size_t m, std::size_t n, std::size_t k, const A* a,
                                       const A* b, std::size_t row, std::size_t col, std::size_t p0, unsigned s,
                                       std::size_t t) {
    for(std::size_t e = t; e < BM * BK; e += THREADS) {
      const std::size_t i = e / BK, p = e % BK;
      as[s][i][p] = row + i < m && p0 + p < k ? ops.widen(a[(row + i) * k + p0 + p]) : ops.zero();
    }
    for(std::size_t e = t; e < BK * BN; e += THREADS) {
      const std::size_t p = e / BN, j = e % BN;
      bs[s][p][j] = p0 + p < k && col + j < n ? ops.widen(b[(p0 + p) * n + col + j]) : ops.zero();
    }
  }
  // The same stage in chunks of 8 elements, 16 bytes of a 2-byte format, each copied whole or zero-filled whole:
  // where k and n are multiples of 8 and a and b sit on 16 bytes, every chunk starts on 16 bytes, and one that
  // starts inside a row ends inside it. On the device a chunk is one asynchronous copy.
  template<class A> CR_HD void load_chunks(Ops& ops, std::size_t m, std::size_t n, std::size_t k, const A* a,
                                           const A* b, std::size_t row, std::size_t col, std::size_t p0, unsigned s,
                                           std::size_t t) {
    for(std::size_t e = t; e < BM * BK / 8; e += THREADS) {
      const std::size_t i = e / (BK / 8), p = e % (BK / 8) * 8;
      const bool inside = row + i < m && p0 + p < k;
      ops.chunk(&as[s][i][p], inside ? &a[(row + i) * k + p0 + p] : a, inside);
    }
    for(std::size_t e = t; e < BK * BN / 8; e += THREADS) {
      const std::size_t p = e / (BN / 8), j = e % (BN / 8) * 8;
      const bool inside = p0 + p < k && col + j < n;
      ops.chunk(&bs[s][p][j], inside ? &b[(p0 + p) * n + col + j] : b, inside);
    }
  }
  // Warp w's fragments: rows WM * F * (w / 2), columns WN * F * (w % 2).
  CR_HD static std::size_t rows(std::size_t w) { return WM * F * (w / 2); }
  CR_HD static std::size_t cols(std::size_t w) { return WN * F * (w % 2); }
  // The tile's outputs back into c, only where they lie inside it.
  CR_HD void store_c(std::size_t m, std::size_t n, float* c, std::size_t row, std::size_t col, std::size_t t) {
    for(std::size_t e = t; e < BM * BN; e += THREADS) {
      const std::size_t i = e / BN, j = e % BN;
      if(row + i < m && col + j < n) c[(row + i) * n + col + j] = cs[i][j];
    }
  }
};

// The steps of k a tile takes, and how many tiles cover the output: every output lies in exactly one tile, and
// every p below k in exactly one step.
CR_HD inline std::size_t steps(std::size_t k) { return (k + BK - 1) / BK; }
CR_HD inline std::size_t tiles(std::size_t m, std::size_t n) { return ((m + BM - 1) / BM) * ((n + BN - 1) / BN); }

} // namespace cr::tensor

#if defined(__CUDACC__)
#include <cuda_bf16.h>
#include <cuda_fp16.h>
#include <cuda_pipeline.h>
#include <mma.h>
#include "cairn_gpu.hpp"

namespace cr::tensor {
namespace wmma = nvcuda::wmma;

// f16 and bf16 cross into shared memory as their own patterns; an 8-bit float widens to f16 exactly, since every
// value of f8e4m3 and f8e5m2 is an f16 (4 and 3 exponent and mantissa bits, and 5 and 2, into 5 and 10).
template<class A> struct Staged { using type = __half; };
template<> struct Staged<cr::bf16> { using type = __nv_bfloat16; };

template<class A> struct Device {
  using Stage = typename Staged<A>::type;
  __device__ Stage zero() const { return Stage(0.0f); }
  __device__ Stage widen(A x) const {
    if constexpr(std::is_same_v<A, cr::f16>) return __ushort_as_half(x.bits);
    else if constexpr(std::is_same_v<A, cr::bf16>) return __ushort_as_bfloat16(x.bits);
    else return __float2half_rn(static_cast<float>(x));  // exact: the value is an f16
  }
  // 16 bytes copied, or 16 zero bytes written reading nothing, in the thread's current asynchronous group.
  __device__ void chunk(Stage* to, const A* from, bool inside) const {
    __pipeline_memcpy_async(to, from, 16, inside ? 0 : 16);
  }
};

// One tile per block and step of the grid. With CHUNKS, a and b cross in asynchronous 16-byte copies, so the next
// stage's copies are in flight while the warps multiply the current one; without, every element is loaded and
// widened by the thread that stores it.
template<class A, bool CHUNKS> __global__ void __launch_bounds__(THREADS)
product(std::size_t m, std::size_t n, std::size_t k, float* c, const A* a, const A* b) {
  using Stage = typename Staged<A>::type;
  using T = Tile<Device<A>>;
  __shared__ T tile;
  Device<A> ops;
  const std::size_t t = threadIdx.x, w = t / 32, across = (n + BN - 1) / BN, last = steps(k);
  auto stage = [&](std::size_t row, std::size_t col, std::size_t step) {
    const unsigned s = unsigned(step & 1);
    if constexpr(CHUNKS) {
      tile.load_chunks(ops, m, n, k, a, b, row, col, step * BK, s, t);
      __pipeline_commit();
    } else {
      tile.load_ab(ops, m, n, k, a, b, row, col, step * BK, s, t);
    }
  };
  for(std::size_t at = blockIdx.x; at < tiles(m, n); at += gridDim.x) {  // the same trip count for the whole block
    const std::size_t row = at / across * BM, col = at % across * BN;
    tile.load_c(m, n, c, row, col, t);
    if(last) stage(row, col, 0);
    if constexpr(CHUNKS) __pipeline_wait_prior(0);
    __syncthreads();
    wmma::fragment<wmma::accumulator, F, F, F, float> acc[WM][WN];
    for(std::size_t i = 0; i < WM; ++i)
      for(std::size_t j = 0; j < WN; ++j)
        wmma::load_matrix_sync(acc[i][j], &tile.cs[T::rows(w) + i * F][T::cols(w) + j * F], SC, wmma::mem_row_major);
    for(std::size_t step = 0; step < last; ++step) {
      const unsigned s = unsigned(step & 1);
      const bool ahead = step + 1 < last;
      if(ahead) stage(row, col, step + 1);  // the other stage, which every warp finished with before the last barrier
      if constexpr(CHUNKS) {  // this stage's own copies are done, in every thread, before any warp reads them
        if(ahead) __pipeline_wait_prior(1); else __pipeline_wait_prior(0);
        __syncthreads();
      }
      for(std::size_t q = 0; q < BK; q += F) {
        wmma::fragment<wmma::matrix_a, F, F, F, Stage, wmma::row_major> fa[WM];
        wmma::fragment<wmma::matrix_b, F, F, F, Stage, wmma::row_major> fb[WN];
        for(std::size_t i = 0; i < WM; ++i) wmma::load_matrix_sync(fa[i], &tile.as[s][T::rows(w) + i * F][q], SA);
        for(std::size_t j = 0; j < WN; ++j) wmma::load_matrix_sync(fb[j], &tile.bs[s][q][T::cols(w) + j * F], SB);
        for(std::size_t i = 0; i < WM; ++i)
          for(std::size_t j = 0; j < WN; ++j) wmma::mma_sync(acc[i][j], fa[i], fb[j], acc[i][j]);
      }
      __syncthreads();  // every warp is done with this stage, and the next is written, before the next step
    }
    for(std::size_t i = 0; i < WM; ++i)
      for(std::size_t j = 0; j < WN; ++j)
        wmma::store_matrix_sync(&tile.cs[T::rows(w) + i * F][T::cols(w) + j * F], acc[i][j], SC, wmma::mem_row_major);
    __syncthreads();
    tile.store_c(m, n, c, row, col, t);
    __syncthreads();  // the tile's shared memory is free before the next tile loads into it
  }
}

// Chunks where they can be: a 2-byte format, k and n multiples of 8, and a and b on 16 bytes.
template<class A> inline bool chunked(std::size_t n, std::size_t k, const A* a, const A* b) noexcept {
  const auto on = [](const void* p) { return reinterpret_cast<std::uintptr_t>(p) % 16 == 0; };
  return sizeof(A) == 2 && n % 8 == 0 && k % 8 == 0 && on(a) && on(b);
}

template<class A> inline void launch(std::size_t m, std::size_t n, std::size_t k, float* c, std::size_t cn,
                                     const A* a, std::size_t an, const A* b, std::size_t bn) noexcept {
  shape(m, n, k, cn, an, bn);
  if(!m || !n) return;
  const std::size_t count = tiles(m, n);
  const unsigned grid = unsigned(count < gpu::MAX_GRID ? count : gpu::MAX_GRID);
  if constexpr(sizeof(A) == 2) {
    if(chunked(n, k, a, b)) product<A, true><<<grid, unsigned(THREADS)>>>(m, n, k, c, a, b);
    else product<A, false><<<grid, unsigned(THREADS)>>>(m, n, k, c, a, b);
  } else {
    product<A, false><<<grid, unsigned(THREADS)>>>(m, n, k, c, a, b);
  }
  gpu::check(cudaGetLastError());
  gpu::check(cudaDeviceSynchronize());
}

} // namespace cr::tensor
#endif

// Tensor-core fragments (compiler/fragments.py): one warp's operand A, operand B or accumulator for one matrix
// instruction shape, and what a warp does with them: fill, load through a layout, multiply-accumulate, store
// through a layout. docs/numerics.md#tensor-core-fragments states the rules.
//
// A fragment is a warp-uniform value: every lane of a warp names the same matrix and runs each operation on it.
// On the device it is the registers its family keeps it in, shared among the lanes as that family shares them. On
// the host every lane holds the whole matrix, so no operation needs another lane's registers, and a store writes
// only the elements the lane holds on the device (`holder`), so a warp's lanes write each element once between
// them and no two host threads write one element. `at(r, c)` is the layout the program named: the offset of
// element (r, c) of the fragment from the pointer an operation is given.
//
// Two families. WMMA (nvcuda::wmma, sm_75 and later for f16, sm_80 for bf16) leaves the lanes' share unspecified
// and loads through a pointer and a leading dimension, so its layout must be affine. mma.sync (PTX m16n8k16,
// sm_80 and later) has the share the PTX ISA states, and loads from shared memory with ldmatrix, which takes one
// row address per lane, so a swizzled tile whose rows keep 16-byte runs is readable. Each multiply-accumulate adds
// every product, exact in f32, to its output, each partial sum rounded to f32 in an order the family picks: the
// contract of mma_unordered, which the host keeps by adding in increasing k.
#pragma once
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include "cairn_float.hpp"

#if defined(__CUDACC__)
#include <cuda_bf16.h>
#include <cuda_fp16.h>
#include <mma.h>
#endif

namespace cr::frag {

enum class Role { a, b, acc };

template<Role R, int M, int N, int K> struct Shape {
  static constexpr int rows = R == Role::b ? K : M;
  static constexpr int cols = R == Role::a ? K : N;
  static constexpr int size = rows * cols;
};

// The lane that holds element (r, c) of an accumulator on the device: the PTX ISA's share for mma.sync m16n8k16
// (row r % 8 is lane group r % 8, the pair of columns c % 8 / 2 its lane within the group), and the share the host
// takes for WMMA's stores, where any one lane per element would do.
CR_HD constexpr unsigned holder(std::size_t r, std::size_t c) noexcept { return unsigned((r % 8) * 4 + (c % 8) / 2); }

// A WMMA fragment on the device must start on 32 bytes, and an affine layout's rows must be 16 bytes apart: a
// pointer the program computed at run time is checked here, on the host as on the device, so a misaligned one traps
// where the device would read what it may not.
CR_HD inline void aligned(const void* p, std::size_t bytes) noexcept {
  if(reinterpret_cast<std::uintptr_t>(p) % bytes) trap();
}

#if defined(__CUDA_ARCH__)
template<class T> struct Device { using type = __half; };
template<> struct Device<cr::bf16> { using type = __nv_bfloat16; };
__device__ inline unsigned lane() {
  unsigned l;
  asm volatile("mov.u32 %0, %%laneid;" : "=r"(l));
  return l;
}
#endif

// ---------------------------------------------------------------------------------------------------------------
// The host: every lane holds the whole fragment, widened exactly to f32.

template<Role R, class T, int M, int N, int K> struct Whole {
  float e[Shape<R, M, N, K>::size];
};

template<Role R, class T, int M, int N, int K> CR_HD void fill(Whole<R, T, M, N, K>& f, float x) noexcept {
  for(float& v : f.e) v = x;
}

template<Role R, class T, int M, int N, int K, class S, class At>
CR_HD void load(Whole<R, T, M, N, K>& f, const S* base, At at) noexcept {
  using F = Shape<R, M, N, K>;
  for(int r = 0; r < F::rows; ++r)
    for(int c = 0; c < F::cols; ++c) f.e[r * F::cols + c] = static_cast<float>(base[at(std::size_t(r), std::size_t(c))]);
}

// acc + a * b: every product exact in f32, then added to its output in increasing k, each sum rounded to f32.
template<class T, int M, int N, int K>
CR_HD void mma(Whole<Role::acc, float, M, N, K>& d, const Whole<Role::a, T, M, N, K>& a,
               const Whole<Role::b, T, M, N, K>& b) noexcept {
  for(int i = 0; i < M; ++i)
    for(int p = 0; p < K; ++p) {
      const float x = a.e[i * K + p];
      for(int j = 0; j < N; ++j) d.e[i * N + j] = d.e[i * N + j] + x * b.e[p * N + j];
    }
}

template<int M, int N, int K, class At>
CR_HD void store(const Whole<Role::acc, float, M, N, K>& f, float* base, At at, unsigned lane) noexcept {
  for(int r = 0; r < M; ++r)
    for(int c = 0; c < N; ++c)
      if(holder(std::size_t(r), std::size_t(c)) == lane % 32) base[at(std::size_t(r), std::size_t(c))] = f.e[r * N + c];
}

// ---------------------------------------------------------------------------------------------------------------
// WMMA on the device. A and B are row-major fragments, loaded from an affine row-major layout; the accumulator is
// loaded and stored either way round.

#if defined(__CUDA_ARCH__)
namespace wmma = nvcuda::wmma;

template<Role R, class T, int M, int N, int K> struct Wmma {
  using Use = std::conditional_t<R == Role::a, wmma::matrix_a,
                                 std::conditional_t<R == Role::b, wmma::matrix_b, wmma::accumulator>>;
  using Element = std::conditional_t<R == Role::acc, float, typename Device<T>::type>;
  using Major = std::conditional_t<R == Role::acc, void, wmma::row_major>;
  wmma::fragment<Use, M, N, K, Element, Major> f;
};

template<int M, int N, int K> __device__ void fill(Wmma<Role::acc, float, M, N, K>& f, float x) {
  wmma::fill_fragment(f.f, x);
}

template<Role R, class T, int M, int N, int K, class S>
__device__ void load(Wmma<R, T, M, N, K>& f, const S* at, std::size_t ld, bool rows) {
  aligned(at, 32);
  if constexpr(R == Role::acc) {
    wmma::load_matrix_sync(f.f, at, unsigned(ld), rows ? wmma::mem_row_major : wmma::mem_col_major);
  } else {
    wmma::load_matrix_sync(f.f, reinterpret_cast<const typename Device<T>::type*>(at), unsigned(ld));
  }
}

template<class T, int M, int N, int K>
__device__ void mma(Wmma<Role::acc, float, M, N, K>& d, const Wmma<Role::a, T, M, N, K>& a,
                    const Wmma<Role::b, T, M, N, K>& b) {
  wmma::mma_sync(d.f, a.f, b.f, d.f);
}

template<int M, int N, int K>
__device__ void store(const Wmma<Role::acc, float, M, N, K>& f, float* at, std::size_t ld, bool rows) {
  aligned(at, 32);
  wmma::store_matrix_sync(at, f.f, unsigned(ld), rows ? wmma::mem_row_major : wmma::mem_col_major);
}
#endif

// ---------------------------------------------------------------------------------------------------------------
// mma.sync m16n8k16 on the device: A is four registers of two halves each, B two, the accumulator four floats, each
// lane holding the elements the PTX ISA gives it (group g = lane / 4, t = lane % 4).

#if defined(__CUDA_ARCH__)
template<Role R, class T, int M, int N, int K> struct Mma;
template<class T> struct Mma<Role::a, T, 16, 8, 16> { std::uint32_t r[4]; };
template<class T> struct Mma<Role::b, T, 16, 8, 16> { std::uint32_t r[2]; };
template<class T> struct Mma<Role::acc, T, 16, 8, 16> { float c[4]; };

__device__ inline unsigned shared(const void* p) { return unsigned(__cvta_generic_to_shared(p)); }

// From shared memory: ldmatrix, each lane naming one 16-byte row: of A, row lane % 16 from column (lane / 16) * 8;
// of B, stored k by n, row lane % 16, transposed as it loads.
template<class T, class S, class At> __device__ void load_shared(Mma<Role::a, T, 16, 8, 16>& f, const S* base, At at) {
  const unsigned l = lane();
  const unsigned p = shared(base + at(l % 16, (l / 16) * 8));
  asm volatile("ldmatrix.sync.aligned.m8n8.x4.shared.b16 {%0, %1, %2, %3}, [%4];"
               : "=r"(f.r[0]), "=r"(f.r[1]), "=r"(f.r[2]), "=r"(f.r[3]) : "r"(p));
}
template<class T, class S, class At> __device__ void load_shared(Mma<Role::b, T, 16, 8, 16>& f, const S* base, At at) {
  const unsigned l = lane();
  const unsigned p = shared(base + at(l % 16, 0));
  asm volatile("ldmatrix.sync.aligned.m8n8.x2.trans.shared.b16 {%0, %1}, [%2];" : "=r"(f.r[0]), "=r"(f.r[1]) : "r"(p));
}

// From anywhere, a lane's own elements one by one: A's (g, 2t), (g, 2t + 1) in r[0], then g + 8, then the columns
// 8 on; B's (2t, g), (2t + 1, g) in r[0], then the rows 8 on.
template<class S> __device__ std::uint32_t pair(const S* base, std::size_t first, std::size_t second) {
  return std::uint32_t(base[first].bits) | (std::uint32_t(base[second].bits) << 16);
}
template<class T, class S, class At> __device__ void load(Mma<Role::a, T, 16, 8, 16>& f, const S* base, At at) {
  const unsigned g = lane() / 4, t = lane() % 4;
  for(unsigned k = 0; k < 4; ++k) {
    const unsigned r = g + 8 * (k % 2), c = 2 * t + 8 * (k / 2);
    f.r[k] = pair(base, at(r, c), at(r, c + 1));
  }
}
template<class T, class S, class At> __device__ void load(Mma<Role::b, T, 16, 8, 16>& f, const S* base, At at) {
  const unsigned g = lane() / 4, t = lane() % 4;
  for(unsigned k = 0; k < 2; ++k) f.r[k] = pair(base, at(2 * t + 8 * k, g), at(2 * t + 8 * k + 1, g));
}
template<class At> __device__ void load(Mma<Role::acc, float, 16, 8, 16>& f, const float* base, At at) {
  const unsigned g = lane() / 4, t = lane() % 4;
  for(unsigned v = 0; v < 4; ++v) f.c[v] = base[at(g + 8 * (v / 2), 2 * t + v % 2)];
}

__device__ inline void fill(Mma<Role::acc, float, 16, 8, 16>& f, float x) {
  for(float& v : f.c) v = x;
}

template<class T>
__device__ void mma(Mma<Role::acc, float, 16, 8, 16>& d, const Mma<Role::a, T, 16, 8, 16>& a,
                    const Mma<Role::b, T, 16, 8, 16>& b) {
  if constexpr(std::is_same_v<T, cr::bf16>) {
    asm volatile("mma.sync.aligned.m16n8k16.row.col.f32.bf16.bf16.f32 {%0, %1, %2, %3}, {%4, %5, %6, %7}, {%8, %9}, "
                 "{%0, %1, %2, %3};"
                 : "+f"(d.c[0]), "+f"(d.c[1]), "+f"(d.c[2]), "+f"(d.c[3])
                 : "r"(a.r[0]), "r"(a.r[1]), "r"(a.r[2]), "r"(a.r[3]), "r"(b.r[0]), "r"(b.r[1]));
  } else {
    asm volatile("mma.sync.aligned.m16n8k16.row.col.f32.f16.f16.f32 {%0, %1, %2, %3}, {%4, %5, %6, %7}, {%8, %9}, "
                 "{%0, %1, %2, %3};"
                 : "+f"(d.c[0]), "+f"(d.c[1]), "+f"(d.c[2]), "+f"(d.c[3])
                 : "r"(a.r[0]), "r"(a.r[1]), "r"(a.r[2]), "r"(a.r[3]), "r"(b.r[0]), "r"(b.r[1]));
  }
}

template<class At> __device__ void store(const Mma<Role::acc, float, 16, 8, 16>& f, float* base, At at) {
  const unsigned g = lane() / 4, t = lane() % 4;
  for(unsigned v = 0; v < 4; ++v) base[at(g + 8 * (v / 2), 2 * t + v % 2)] = f.c[v];
}
#endif

// ---------------------------------------------------------------------------------------------------------------
// What compiler/fragments.py emits: one spelling of each fragment and each operation for the host and the device.

#if defined(__CUDA_ARCH__)
template<Role R, class T, int M, int N, int K> using WmmaFragment = Wmma<R, T, M, N, K>;
template<Role R, class T, int M, int N, int K> using MmaFragment = Mma<R, T, M, N, K>;
#else
template<Role R, class T, int M, int N, int K> using WmmaFragment = Whole<R, T, M, N, K>;
template<Role R, class T, int M, int N, int K> using MmaFragment = Whole<R, T, M, N, K>;
#endif

template<class F> CR_HD F filled(float x) noexcept {
  F f;
  fill(f, x);
  return f;
}

// `ld` and `rows` are the layout's leading dimension and orientation where it is affine, which WMMA reads; `shared`
// says the tile is a block's shared array, which mma.sync reads with ldmatrix.
template<class F, class S, class At> CR_HD F loaded(const S* base, At at, std::size_t ld, bool rows, bool shared) noexcept {
  F f;
#if defined(__CUDA_ARCH__)
  if constexpr(requires { f.f; }) {
    load(f, base + at(0, 0), ld, rows);
  } else if(shared) {
    if constexpr(requires { load_shared(f, base, at); }) load_shared(f, base, at);
    else load(f, base, at);
  } else {
    load(f, base, at);
  }
#else
  (void)ld, (void)rows, (void)shared;
  load(f, base, at);
#endif
  return f;
}

// Lane l's value v of an mma.sync accumulator is element (l / 4 + 8 * (v / 2), 2 * (l % 4) + v % 2), as the PTX ISA
// shares an m16n8 accumulator. On the host a lane reads and writes that element of its whole copy: it is the one
// element of the copy the lane stores, and the one a multiply-accumulate needs for it, so the copy stays right where
// it matters.
CR_HD constexpr std::size_t element(unsigned lane, std::size_t v) noexcept {
  return (lane / 4 + 8 * (v / 2)) * 8 + 2 * (lane % 4) + v % 2;
}
template<class F> CR_HD float get(const F& f, std::size_t v, unsigned lane) noexcept {
  if(v >= 4) trap();
#if defined(__CUDA_ARCH__)
  (void)lane;
  return f.c[v];
#else
  return f.e[element(lane % 32, v)];
#endif
}
template<class F> CR_HD F set(F f, std::size_t v, float x, unsigned lane) noexcept {
  if(v >= 4) trap();
#if defined(__CUDA_ARCH__)
  (void)lane;
  f.c[v] = x;
#else
  f.e[element(lane % 32, v)] = x;
#endif
  return f;
}

template<class D, class A, class B> CR_HD D multiplied(D d, const A& a, const B& b) noexcept {
  mma(d, a, b);
  return d;
}

template<class F, class At>
CR_HD void stored(const F& f, float* base, At at, std::size_t ld, bool rows, unsigned lane) noexcept {
#if defined(__CUDA_ARCH__)
  (void)lane;
  if constexpr(requires { f.f; }) store(f, base + at(0, 0), ld, rows);
  else store(f, base, at);
#else
  (void)ld, (void)rows;
  store(f, base, at, lane);
#endif
}

}  // namespace cr::frag

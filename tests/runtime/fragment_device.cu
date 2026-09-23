// Every fragment operation of runtime/cairn_fragment.hpp in two kernels, compiled for sm_120 and inspected, never
// run: WMMA from padded shared tiles, and mma.sync with ldmatrix from a swizzled tile and element by element from a
// device view.
#include "cairn_runtime.hpp"
#include "cairn_fragment.hpp"

using namespace cr::frag;

__global__ void wmma_tile(float* c, const cr::f16* a, const cr::f16* b) {
  __shared__ alignas(128) cr::f16 as[16 * 24];
  __shared__ alignas(128) cr::f16 bs[16 * 24];
  for(int e = threadIdx.x; e < 256; e += 32) {
    as[e / 16 * 24 + e % 16] = a[e];
    bs[e / 16 * 24 + e % 16] = b[e];
  }
  __syncwarp();
  Wmma<Role::a, cr::f16, 16, 16, 16> fa;
  Wmma<Role::b, cr::f16, 16, 16, 16> fb;
  Wmma<Role::acc, float, 16, 16, 16> acc;
  fill(acc, 0.0f);
  load(fa, as, 24, true);
  load(fb, bs, 24, true);
  mma(acc, fa, fb);
  store(acc, c, 16, true);
}

__global__ void mma_tile(float* c, const cr::bf16* a, const cr::bf16* b) {
  __shared__ alignas(128) cr::bf16 as[16 * 16];
  __shared__ alignas(128) cr::bf16 bs[16 * 8];
  for(int e = threadIdx.x; e < 256; e += 32) as[e] = a[e];
  for(int e = threadIdx.x; e < 128; e += 32) bs[e] = b[e];
  __syncwarp();
  auto swz = [](std::size_t r, std::size_t col) { return (r * 16 + col) ^ (((r * 16 + col) >> 4) & 8); };
  auto rows8 = [](std::size_t r, std::size_t col) { return r * 8 + col; };
  Mma<Role::a, cr::bf16, 16, 8, 16> fa;
  Mma<Role::b, cr::bf16, 16, 8, 16> fb;
  Mma<Role::acc, float, 16, 8, 16> acc;
  fill(acc, 0.0f);
  load_shared(fa, as, swz);
  load_shared(fb, bs, rows8);
  mma(acc, fa, fb);
  load(fa, a, rows8);
  mma(acc, fa, fb);
  store(acc, c, rows8);
}

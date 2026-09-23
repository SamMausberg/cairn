// A 3-point blend over a shared-memory tile. Each block of 256 threads loads its 256 points and one halo
// point on each side once, waits, and then every thread reads its neighbours from shared memory rather than
// from global memory. out[i] = 0.25 x[i-1] + 0.5 x[i] + 0.25 x[i+1] inside, and out[i] = x[i] at both ends,
// summed left to right; built with --fmad=false, that is the same rounding as the plain loop.
// Launch it with 256 threads to a block and ceil(n / 256) blocks.
#include <cstddef>

constexpr unsigned TILE = 256;

__global__ void stencil_1d_tiled(std::size_t n, float* out, const float* x) {
  __shared__ float tile[TILE + 2];
  const std::size_t i = static_cast<std::size_t>(blockIdx.x) * TILE + threadIdx.x;
  const unsigned t = threadIdx.x + 1;
  if (i < n) {
    tile[t] = x[i];
    if (threadIdx.x == 0 && i > 0) tile[0] = x[i - 1];
    if (threadIdx.x == TILE - 1 && i + 1 < n) tile[TILE + 1] = x[i + 1];
  }
  __syncthreads();
  if (i >= n) return;
  if (i > 0 && i + 1 < n) {
    out[i] = 0.25f * tile[t - 1] + 0.5f * tile[t] + 0.25f * tile[t + 1];
  } else {
    out[i] = tile[t];
  }
}

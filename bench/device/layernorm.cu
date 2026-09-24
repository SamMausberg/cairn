// Layer normalization of each row, hand-written: the algorithm of layernorm.cairn, one block of 256 threads a row,
// the mean and then the variance through warp shuffles and eight warps' sums in shared memory.
#include <cmath>
#include <cstddef>
#include <cuda_runtime.h>
#include <vector>
#include "bench.cuh"
#include "layernorm.h"

namespace base {

__device__ inline float warp_sum(float v) {
  for(int m = 16; m; m >>= 1) v += __shfl_xor_sync(0xffffffffu, v, m);
  return v;
}

// The sum over the block, in every thread; every thread of the block must call it.
__device__ inline float block_sum(float v, float* warps, float* result) {
  v = warp_sum(v);
  if(threadIdx.x % 32 == 0) warps[threadIdx.x / 32] = v;
  __syncthreads();
  if(threadIdx.x < 32) {
    v = warp_sum(threadIdx.x < 8 ? warps[threadIdx.x] : 0.0f);
    if(threadIdx.x == 0) *result = v;
  }
  __syncthreads();
  return *result;
}

__global__ void __launch_bounds__(256) layernorm(std::size_t cols, const float* x, const float* gamma,
                                                 const float* beta, float* out) {
  __shared__ float warps[8], result;
  const float* row = x + blockIdx.x * cols;
  float sum = 0.0f;
  for(std::size_t c = threadIdx.x; c < cols; c += 256) sum += row[c];
  const float mean = block_sum(sum, warps, &result) / float(cols);
  float squares = 0.0f;
  for(std::size_t c = threadIdx.x; c < cols; c += 256) {
    const float d = row[c] - mean;
    squares += d * d;
  }
  const float scale = 1.0f / sqrtf(block_sum(squares, warps, &result) / float(cols) + 0.00001f);
  float* to = out + blockIdx.x * cols;
  for(std::size_t c = threadIdx.x; c < cols; c += 256) to[c] = (row[c] - mean) * scale * gamma[c] + beta[c];
}

}  // namespace base

__global__ void fill(float* x, std::size_t n, unsigned seed) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float(((i + seed) * 2654435761u) % 1024u) / 256.0f - 2.0f;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 60);
  const std::size_t cols = 4096, shapes[] = {1024, 4096};  // rows
  const std::size_t most = shapes[1] * cols;
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_layernorm_device_stream(s);
  float *x = nullptr, *gamma = nullptr, *beta = nullptr, *mine = nullptr, *theirs = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&gamma, cols * sizeof(float)));
  ok(cudaMalloc(&beta, cols * sizeof(float)));
  ok(cudaMalloc(&mine, most * sizeof(float)));
  ok(cudaMalloc(&theirs, most * sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most, 0);
  fill<<<16, 256, 0, s>>>(gamma, cols, 7);
  fill<<<16, 256, 0, s>>>(beta, cols, 11);
  ok(cudaStreamSynchronize(s));
  header("layernorm");
  std::vector<Series> all;
  std::vector<float> a(most), b(most);
  for(const std::size_t rows : shapes) {
    const std::size_t n = rows * cols;
    const std::size_t first = all.size();
    measure(s, n, {
        {"layernorm", "cuda", [&] { base::layernorm<<<unsigned(rows), 256, 0, s>>>(cols, x, gamma, beta, theirs); }},
        {"layernorm", "cairn", [&] { cf_layernorm(rows, cols, n, x, gamma, beta, mine); }},
#if BENCH_ENQUEUE
        {"layernorm_enqueued", "cairn", [&] { cq_layernorm(s, rows, cols, n, x, gamma, beta, mine); }},
#endif
    }, reps, all);
    // The two agree within float rounding: CAIRN rounds each product (--fmad=false), nvcc may fuse them.
    ok(cudaGetLastError());
    ok(cudaMemcpy(a.data(), mine, n * sizeof(float), cudaMemcpyDeviceToHost));
    ok(cudaMemcpy(b.data(), theirs, n * sizeof(float), cudaMemcpyDeviceToHost));
    double worst = 0.0;
    for(std::size_t i = 0; i < n; ++i) worst = std::max(worst, double(std::fabs(a[i] - b[i])) / (1.0 + std::fabs(b[i])));
    for(std::size_t k = first + 1; k < all.size(); ++k) all[k].error = worst;
  }
  footer(all);
  cairn_layernorm_device_stream(nullptr);
  for(float* p : {x, gamma, beta, mine, theirs}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

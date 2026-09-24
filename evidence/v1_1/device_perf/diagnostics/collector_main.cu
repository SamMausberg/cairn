#include <cstdio>
#include <cuda_runtime.h>
#include "diag.h"
__global__ void fill(float* x, unsigned* y, std::size_t n) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x) { x[i] = 1.0f; y[i] = 1u; }
}
int main() {
  const std::size_t most = std::size_t(1) << 24;
  float* x = nullptr; unsigned* y = nullptr;
  if(cudaMalloc(&x, most * sizeof(float)) != cudaSuccess || cudaMalloc(&y, most * sizeof(unsigned)) != cudaSuccess) return 2;
  fill<<<256, 256>>>(x, y, most);
  std::fprintf(stderr, "fill: %s\n", cudaGetErrorString(cudaDeviceSynchronize()));
  for(std::size_t n : {std::size_t(256), std::size_t(4096), std::size_t(1) << 16, std::size_t(1) << 20, most}) {
    std::fprintf(stderr, "u32 n=%zu\n", n);
    const unsigned long long t = cf_total_u32(n, y);
    std::fprintf(stderr, "  = %llu\n", t);
    std::fprintf(stderr, "f32 n=%zu\n", n);
    const float f = cf_total(n, x);
    std::fprintf(stderr, "  = %.1f\n", f);
  }
  std::printf("{\"diag\": \"ok\"}\n");
  return 0;
}

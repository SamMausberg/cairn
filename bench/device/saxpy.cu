// out = a * x + y, hand-written: one thread an element, as many blocks as the array needs.
#include <cstddef>
#include <functional>
#include <cuda_runtime.h>
#include <vector>
#include "bench.cuh"
#include "saxpy.h"

namespace base {

__global__ void saxpy(std::size_t n, float* out, const float* x, const float* y, float a) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(i < n) out[i] = a * x[i] + y[i];
}

void twice(cudaStream_t s, std::size_t n, float* out, const float* x, float* y, float a, float b) {
  const unsigned g = unsigned((n + 255) / 256);
  saxpy<<<g, 256, 0, s>>>(n, out, x, y, a);
  saxpy<<<g, 256, 0, s>>>(n, y, out, x, b);
}

}  // namespace base

__global__ void fill(float* x, std::size_t n, unsigned seed) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float(((i + seed) * 2654435761u) % 1024u) / 256.0f - 2.0f;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 60);
  const std::size_t sizes[] = {std::size_t(1) << 22, std::size_t(1) << 24}, most = sizes[1];
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_saxpy_device_stream(s);
  float *x = nullptr, *y = nullptr, *out = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&y, most * sizeof(float)));
  ok(cudaMalloc(&out, most * sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most, 0);
  fill<<<1024, 256, 0, s>>>(y, most, 5);
  ok(cudaStreamSynchronize(s));
  header("saxpy");
  std::vector<Series> all;
  for(const std::size_t n : sizes) {
    const unsigned g = unsigned((n + 255) / 256);
    measure(s, n, {
        {"saxpy", "cuda", [&] { base::saxpy<<<g, 256, 0, s>>>(n, out, x, y, 2.0f); }},
        {"saxpy", "cairn", [&] { cf_saxpy(n, out, x, y, 2.0f); }},
        {"saxpy_twice", "cuda", [&] { base::twice(s, n, out, x, y, 1.0f, -1.0f); }},
        {"saxpy_twice", "cairn", [&] { cf_saxpy_twice(n, out, x, y, 1.0f, -1.0f); }},
#if BENCH_ENQUEUE
        {"saxpy_enqueued", "cairn", [&] { cq_saxpy(s, n, out, x, y, 2.0f); }},
        {"saxpy_twice_enqueued", "cairn", [&] { cq_saxpy_twice(s, n, out, x, y, 1.0f, -1.0f); }},
#endif
    }, reps, all);
  }
  ok(cudaGetLastError());
  footer(all);
  cairn_saxpy_device_stream(nullptr);
  for(float* p : {x, y, out}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

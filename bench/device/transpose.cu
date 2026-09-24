// A matrix transposed through shared memory, hand-written: the tile of transpose.cairn, 32 x 32 elements moved by
// 32 x 8 threads, each tile row padded to 33 elements.
#include <cstddef>
#include <cuda_runtime.h>
#include <vector>
#include "bench.cuh"
#include "transpose.h"

namespace base {

__global__ void __launch_bounds__(256) transpose(float* out, const float* x, int w, int h) {
  __shared__ float tile[32][33];
  const int col = blockIdx.x * 32 + threadIdx.x, row = blockIdx.y * 32 + threadIdx.y;
  for(int k = 0; k < 32; k += 8) tile[threadIdx.y + k][threadIdx.x] = x[std::size_t(row + k) * w + col];
  __syncthreads();
  const int to_col = blockIdx.y * 32 + threadIdx.x, to_row = blockIdx.x * 32 + threadIdx.y;
  for(int k = 0; k < 32; k += 8) out[std::size_t(to_row + k) * h + to_col] = tile[threadIdx.x][threadIdx.y + k];
}

}  // namespace base

__global__ void fill(float* x, std::size_t n) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float(i % 65521u);
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 30);
  const std::size_t sides[] = {4096, 8192}, most = sides[1] * sides[1];
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_transpose_device_stream(s);
  float *x = nullptr, *mine = nullptr, *theirs = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&mine, most * sizeof(float)));
  ok(cudaMalloc(&theirs, most * sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most);
  ok(cudaStreamSynchronize(s));
  header("transpose");
  std::vector<Series> all;
  std::vector<float> a(most), b(most);
  for(const std::size_t side : sides) {
    const std::size_t n = side * side, g = side / 32;
    const std::size_t first = all.size();
    measure(s, n, {
        {"transpose", "cuda", [&] {
           base::transpose<<<dim3(unsigned(g), unsigned(g)), dim3(32, 8), 0, s>>>(theirs, x, int(side), int(side));
         }},
        {"transpose", "cairn", [&] { cf_transpose(g, g, n, mine, x); }},
#if BENCH_ENQUEUE
        {"transpose_enqueued", "cairn", [&] { cq_transpose(s, g, g, n, mine, x); }},
#endif
    }, reps, all);
    ok(cudaGetLastError());
    ok(cudaMemcpy(a.data(), mine, n * sizeof(float), cudaMemcpyDeviceToHost));
    ok(cudaMemcpy(b.data(), theirs, n * sizeof(float), cudaMemcpyDeviceToHost));
    std::size_t wrong = 0;
    for(std::size_t i = 0; i < n; ++i) wrong += a[i] != b[i] || b[i] != float(((i % side) * side + i / side) % 65521u);
    for(std::size_t k = first; k < all.size(); ++k) all[k].error = double(wrong);
  }
  footer(all);
  cairn_transpose_device_stream(nullptr);
  for(float* p : {x, mine, theirs}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

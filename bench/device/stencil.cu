// One Jacobi sweep of a w x h grid, hand-written: a thread an element in blocks of 32 x 8, the border copied.
#include <cstddef>
#include <cuda_runtime.h>
#include <functional>
#include <vector>
#include "bench.cuh"
#include "stencil.h"

namespace base {

__global__ void __launch_bounds__(256) jacobi(float* out, const float* x, int w, int h) {
  const int col = blockIdx.x * 32 + threadIdx.x, row = blockIdx.y * 8 + threadIdx.y;
  if(col >= w || row >= h) return;
  const std::size_t i = std::size_t(row) * w + col;
  if(row > 0 && row + 1 < h && col > 0 && col + 1 < w) out[i] = 0.25f * (x[i - 1] + x[i + 1] + x[i - w] + x[i + w]);
  else out[i] = x[i];
}

}  // namespace base

__global__ void fill(float* x, std::size_t n) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float((i * 2654435761u) % 1024u) / 64.0f;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 30);
  const std::size_t w = 4096, heights[] = {2048, 8192}, most = w * heights[1];
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_stencil_device_stream(s);
  float *x = nullptr, *mine = nullptr, *theirs = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&mine, most * sizeof(float)));
  ok(cudaMalloc(&theirs, most * sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most);
  ok(cudaStreamSynchronize(s));
  header("stencil");
  std::vector<Series> all;
  std::vector<float> a(most), b(most);
  for(const std::size_t h : heights) {
    const std::size_t n = w * h;
    const dim3 grid(unsigned(w / 32), unsigned(h / 8)), block(32, 8);
    const std::vector<Variant> variants = {
        {"jacobi", "cuda", [&] { base::jacobi<<<grid, block, 0, s>>>(theirs, x, int(w), int(h)); }},
        {"jacobi_parallel", "cairn", [&] { cf_jacobi(w, n, mine, x); }},
        {"jacobi_blocks", "cairn", [&] { cf_jacobi_blocks(w / 32, h / 8, n, mine, x); }},
#if BENCH_ENQUEUE
        {"jacobi_parallel_enqueued", "cairn", [&] { cq_jacobi(s, w, n, mine, x); }},
        {"jacobi_blocks_enqueued", "cairn", [&] { cq_jacobi_blocks(s, w / 32, h / 8, n, mine, x); }},
#endif
    };
    const std::size_t first = all.size();
    measure(s, n, variants, reps, all);
    ok(cudaGetLastError());
    ok(cudaMemcpy(b.data(), theirs, n * sizeof(float), cudaMemcpyDeviceToHost));
    for(std::size_t k = 1; k < variants.size(); ++k) {  // each CAIRN variant's result held to the hand-written one
      ok(cudaMemset(mine, 0, n * sizeof(float)));
      variants[k].call();
      ok(cudaStreamSynchronize(s));
      ok(cudaMemcpy(a.data(), mine, n * sizeof(float), cudaMemcpyDeviceToHost));
      std::size_t wrong = 0;
      for(std::size_t i = 0; i < n; ++i) wrong += a[i] != b[i];
      all[first + k].error = double(wrong);
    }
  }
  footer(all);
  cairn_stencil_device_stream(nullptr);
  for(float* p : {x, mine, theirs}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

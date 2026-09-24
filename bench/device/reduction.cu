// examples/reduction's one-launch sums beside the one-pass CUDA design they follow and the two-pass CAIRN sum of
// reduce_wide.cairn, compiled into the same CAIRN program. `sum` finishes in the block that ends last and is timed
// through its synchronous entry, as it was measured before ef8535e gave it an enqueued entry; `sum_unordered` adds each block's sum
// into out[0] atomically, so out[0] is zeroed on the stream before each of its calls, as a caller would.
#include <cmath>
#include "reduce_base.cuh"
#include "reduction.h"

__global__ void fill(float* x, std::size_t n) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float((i * 2654435761u) % 1024u) / 1024.0f;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 60);
  const std::size_t sizes[] = {std::size_t(1) << 24, std::size_t(1) << 26};
  int sms = 0;
  ok(cudaDeviceGetAttribute(&sms, cudaDevAttrMultiProcessorCount, 0));
  const std::size_t most = sizes[1], g = std::size_t(sms) * 6;  // every block resident at once: 6 of 256 threads an SM
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_reduction_device_stream(s);
  float *x = nullptr, *partial = nullptr, *out = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&partial, g * sizeof(float)));
  ok(cudaMalloc(&out, sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most);
  ok(cudaGetLastError());
  ok(cudaStreamSynchronize(s));
  header("reduction");
  std::vector<Series> all;
  for(const std::size_t n : sizes) {
    double exact = 0.0;
    for(std::size_t i = 0; i < n; ++i) exact += double((i * 2654435761u) % 1024u) / 1024.0;
    auto zero = [&] { ok(cudaMemsetAsync(out, 0, sizeof(float), s)); };
    const std::vector<Variant> variants = {
        {"one_pass_v4_lastblock", "cuda", [&] { base::sum_one_pass(s, x, n, g, partial, out); }},
        {"one_pass_finish", "cairn", [&] { cf_sum(n, x, g, partial, out); }},
        {"one_pass_atomic_enqueued", "cairn", [&] { zero(); cq_sum_unordered(s, n, x, g, out); }},
        {"one_pass_atomic", "cairn", [&] { zero(); cf_sum_unordered(n, x, g, out); }},
        {"two_pass_load_wide_enqueued", "cairn", [&] { cq_sum_wide(s, n, x, g, partial, out); }},
        {"memset_4_bytes", "cuda", [&] { zero(); }},
    };
    const std::size_t first = all.size();
    measure(s, n, variants, reps, all);
    for(std::size_t k = first; k + 1 < all.size(); ++k) {
      ok(cudaMemsetAsync(out, 0, sizeof(float), s));
      variants[k - first].call();
      float total = 0.0f;
      ok(cudaMemcpyAsync(&total, out, sizeof(float), cudaMemcpyDeviceToHost, s));
      ok(cudaStreamSynchronize(s));
      all[k].error = std::fabs(double(total) - exact) / exact;
    }
  }
  footer(all);
  cairn_reduction_device_stream(nullptr);
  for(float* p : {x, partial, out}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

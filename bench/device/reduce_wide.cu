// The sums of reduce_wide.cairn, their 16-byte loads written with load_wide, in two passes and in one launch with a
// finish, beside the hand-written designs of reduce_base.cuh that load 16 bytes at a time. The one-launch sum is timed
// through its synchronous entry, as it was measured before ef8535e gave a finish an enqueued entry.
#include <cmath>
#include "reduce_base.cuh"
#include "reduce_wide.h"

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
  cairn_reduce_wide_device_stream(s);
  float *x = nullptr, *partial = nullptr, *out = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&partial, g * sizeof(float)));
  ok(cudaMalloc(&out, sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most);
  ok(cudaGetLastError());
  ok(cudaStreamSynchronize(s));
  header("reduce_wide");
  std::vector<Series> all;
  for(const std::size_t n : sizes) {
    double exact = 0.0;
    for(std::size_t i = 0; i < n; ++i) exact += double((i * 2654435761u) % 1024u) / 1024.0;
    const std::vector<Variant> variants = {
        {"one_pass_v4_lastblock", "cuda", [&] { base::sum_one_pass(s, x, n, g, partial, out); }},
        {"two_pass_v4", "cuda", [&] { base::sum_two_pass_v4(s, x, n, g, partial, out); }},
        {"two_pass_load_wide", "cairn", [&] { cf_sum_wide(n, x, g, partial, out); }},
        {"two_pass_load_wide_enqueued", "cairn", [&] { cq_sum_wide(s, n, x, g, partial, out); }},
        {"one_pass_load_wide_finish", "cairn", [&] { cf_sum_one_pass(n, x, g, partial, out); }},
    };
    const std::size_t first = all.size();
    measure(s, n, variants, reps, all);
    for(std::size_t k = first; k < all.size(); ++k) {
      ok(cudaMemsetAsync(out, 0, sizeof(float), s));
      variants[k - first].call();
      float total = 0.0f;
      ok(cudaMemcpyAsync(&total, out, sizeof(float), cudaMemcpyDeviceToHost, s));
      ok(cudaStreamSynchronize(s));
      all[k].error = std::fabs(double(total) - exact) / exact;
    }
  }
  footer(all);
  cairn_reduce_wide_device_stream(nullptr);
  for(float* p : {x, partial, out}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

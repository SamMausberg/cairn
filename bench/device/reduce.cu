// The sums of reduce.cairn beside the hand-written ones of reduce_base.cuh.
#include "reduce_base.cuh"

// The pair's main: every variant at each size, interleaved, and each one's total held to the exact sum.
#include <cmath>
#include "reduce.h"

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
  cairn_reduce_device_stream(s);  // the CAIRN library's synchronous work runs on the harness's stream
  float *x = nullptr, *partial = nullptr, *out = nullptr;
  ok(cudaMalloc(&x, most * sizeof(float)));
  ok(cudaMalloc(&partial, (most / 256) * sizeof(float)));
  ok(cudaMalloc(&out, sizeof(float)));
  fill<<<1024, 256, 0, s>>>(x, most);
  ok(cudaGetLastError());
  ok(cudaStreamSynchronize(s));
  header("reduce");
  std::vector<Series> all;
  for(const std::size_t n : sizes) {
    double exact = 0.0;
    for(std::size_t i = 0; i < n; ++i) exact += double((i * 2654435761u) % 1024u) / 1024.0;
    const std::size_t each = (n + 255) / 256;
    float returned = 0.0f;  // what a collector gave back; the others leave their total in out[0]
    const std::vector<Variant> variants = {
        {"one_pass_v4_lastblock", "cuda", [&] { base::sum_one_pass(s, x, n, g, partial, out); }},
        {"two_pass_v4", "cuda", [&] { base::sum_two_pass_v4(s, x, n, g, partial, out); }},
        {"two_pass_scalar", "cuda", [&] { base::sum_two_pass(s, x, n, g, partial, out); }},
        {"two_pass_per_thread", "cuda", [&] { base::sum_per_thread(s, x, n, partial, out); }},
        {"two_pass_per_thread_zeroed", "cuda", [&] { base::sum_per_thread_zeroed(s, x, n, partial, out, 2147483647u); }},
        {"two_pass_per_thread_zeroed_capped", "cuda", [&] { base::sum_per_thread_zeroed(s, x, n, partial, out, 65535u); }},
        {"cub_to_host", "cuda", [&] { returned = base::sum_cub(s, x, n); }},
        {"two_pass_scalar", "cairn", [&] { cf_sum_two_pass(n, x, g, partial, out); }},
        {"two_pass_ptx_v4", "cairn", [&] { cf_sum_two_pass_ptx(n, x, g, partial, out); }},
        {"two_pass_per_thread", "cairn", [&] { cf_sum_per_thread(n, x, each, partial, out); }},
        {"collector_to_host", "cairn", [&] { returned = cf_sum_collector(n, x); }},
#if BENCH_ENQUEUE
        {"two_pass_scalar_enqueued", "cairn", [&] { cq_sum_two_pass(s, n, x, g, partial, out); }},
        {"two_pass_ptx_v4_enqueued", "cairn", [&] { cq_sum_two_pass_ptx(s, n, x, g, partial, out); }},
        {"two_pass_per_thread_enqueued", "cairn", [&] { cq_sum_per_thread(s, n, x, each, partial, out); }},
#endif
    };
    const std::size_t first = all.size();
    measure(s, n, variants, reps, all);
    for(std::size_t k = first; k < all.size(); ++k) {
      const Variant& v = *std::find_if(variants.begin(), variants.end(),
                                       [&](const Variant& v) { return v.name == all[k].variant && v.side == all[k].side; });
      returned = -1.0f;
      ok(cudaMemsetAsync(out, 0, sizeof(float), s));
      v.call();
      float total = returned;
      if(v.name.find("to_host") == std::string::npos)
        ok(cudaMemcpyAsync(&total, out, sizeof(float), cudaMemcpyDeviceToHost, s));
      ok(cudaStreamSynchronize(s));
      all[k].error = std::fabs(double(total) - exact) / exact;
    }
  }
  footer(all);
  cairn_reduce_device_stream(nullptr);
  for(float* p : {x, partial, out}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

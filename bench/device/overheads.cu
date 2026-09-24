// What reaching the device costs from this host, measured with the smallest work there is: an empty launch, a launch
// and a wait, a 4-byte copy back, and CAIRN's entries around a region of 256 elements.
#include <cstddef>
#include <cuda_runtime.h>
#include <functional>
#include <vector>
#include "bench.cuh"
#include "overheads.h"

__global__ void empty() {}
__global__ void touch(std::size_t n, float* x) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(i < n) x[i] = x[i] + 1.0f;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 200);
  const std::size_t n = 256;
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  cairn_overheads_device_stream(s);
  float *x = nullptr, *pinned = nullptr;
  ok(cudaMalloc(&x, n * sizeof(float)));
  ok(cudaMemset(x, 0, n * sizeof(float)));
  ok(cudaMallocHost(&pinned, sizeof(float)));
  float pageable = 0.0f;
  header("overheads");
  std::vector<Series> all;
  measure(s, n, {
      {"empty_launch", "cuda", [&] { empty<<<1, 32, 0, s>>>(); }},
      {"launch_and_wait", "cuda", [&] { empty<<<1, 32, 0, s>>>(); ok(cudaStreamSynchronize(s)); }},
      {"copy_4_bytes_pinned_and_wait", "cuda", [&] {
         ok(cudaMemcpyAsync(pinned, x, sizeof(float), cudaMemcpyDeviceToHost, s));
         ok(cudaStreamSynchronize(s));
       }},
      {"copy_4_bytes_pageable_and_wait", "cuda", [&] {
         ok(cudaMemcpyAsync(&pageable, x, sizeof(float), cudaMemcpyDeviceToHost, s));
         ok(cudaStreamSynchronize(s));
       }},
      {"touch", "cuda", [&] { touch<<<1, 256, 0, s>>>(n, x); }},
      {"touch", "cairn", [&] { cf_touch(n, x); }},
      {"touch_twice", "cuda", [&] { touch<<<1, 256, 0, s>>>(n, x); touch<<<1, 256, 0, s>>>(n, x); }},
      {"touch_twice", "cairn", [&] { cf_touch_twice(n, x); }},
      {"total_to_host", "cairn", [&] { pageable = cf_total(n, x); }},
#if BENCH_ENQUEUE
      {"touch_enqueued", "cairn", [&] { cq_touch(s, n, x); }},
      {"touch_twice_enqueued", "cairn", [&] { cq_touch_twice(s, n, x); }},
#endif
  }, reps, all);
  ok(cudaGetLastError());
  // A run of launches with no wait between: the stream's time per launch, and the host's time to queue one.
  Clock clock;
  const int burst = 1000;
  Series launches{"burst_of_1000_empty_launches", "cuda", std::size_t(burst)};
  for(int r = 0; r < 5; ++r)
    launches.add(clock.time(s, [&] {
      for(int k = 0; k < burst; ++k) empty<<<1, 32, 0, s>>>();
    }));
  all.push_back(launches);
  footer(all);
  cairn_overheads_device_stream(nullptr);
  ok(cudaFree(x));
  ok(cudaFreeHost(pinned));
  ok(cudaStreamDestroy(s));
  return 0;
}

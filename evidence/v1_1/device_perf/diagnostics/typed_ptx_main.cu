#include <cstdio>
#include <cuda_runtime.h>
#include "diag2.h"
// The same inline PTX in a plain CUDA kernel, compiled with plain nvcc flags.
__global__ void plain(const float* x, std::size_t chunks, float* out) {
  const std::size_t c = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(c >= chunks) return;
  float a0, a1, a2, a3;
  const unsigned long long offset = c * 16;
  asm volatile("{ .reg .u64 a; add.u64 a, %4, %5; ld.global.cg.v4.f32 {%0, %1, %2, %3}, [a]; }" : "=f"(a0), "=f"(a1), "=f"(a2), "=f"(a3) : "l"(x), "l"(offset) : "memory");
  out[c] = (a0 + a1) + (a2 + a3);
}
static void report(const char* what) {
  cudaError_t launch = cudaGetLastError();
  cudaError_t sync = cudaDeviceSynchronize();
  std::printf("%-16s launch: %s, sync: %s\n", what, cudaGetErrorString(launch), cudaGetErrorString(sync));
  std::fflush(stdout);
}
int main(int argc, char** argv) {
  const std::size_t n = 1 << 20, chunks = n / 4, g = chunks / 32;
  float *x = nullptr, *out = nullptr;
  cudaMalloc(&x, n * sizeof(float));
  cudaMalloc(&out, n * sizeof(float));
  cudaMemset(x, 0, n * sizeof(float));
  plain<<<unsigned(chunks / 256), 256>>>(x, chunks, out);
  report("plain_cuda");
  // Each CAIRN entry aborts the process on an error, so the one to try comes from the command line.
  const int which = argc > 1 ? std::atoi(argv[1]) : 0;
  if(which == 0) { cf_scalar_coop(n, x, g, out); report("scalar_coop"); }
  if(which == 1) { cf_wide_lanes(n, x, chunks, out); report("wide_lanes"); }
  if(which == 2) { cf_wide_kernel_fn(n, x, g, out); report("wide_kernel_fn"); }
  if(which == 3) { cf_wide_coop(n, x, g, out); report("wide_coop"); }
  std::printf("done\n");
  return 0;
}

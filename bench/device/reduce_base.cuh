// Hand-written CUDA sums of a large f32 array: the designs reduce.cairn and reduce_wide.cairn are held to, and the
// steps between the design CAIRN forced and the one its author wanted. Each writes the total to out[0].
//
//   one_pass     16-byte __ldcg loads in a grid-stride loop, a block sum to partial[b], and the last block to finish
//                adds the partial sums (a device atomic counts finished blocks): one launch.
//   two_pass_v4  the same loads and block sums, then a second launch of one block adds the partial sums.
//   two_pass     scalar loads, otherwise two_pass_v4.
//   per_thread   one element per thread, a block per 256 elements, then the second launch: examples/cooperative's
//                block_sums at scale. Its zeroed variants add what CAIRN's cooperative region does around the body:
//                a zeroed shared array and a barrier, and, capped, a grid of 65,535 blocks that loop.
//   cub          cub::DeviceReduce::Sum with its storage allocated once, and the total copied to the host. The pairs
//                compile with THRUST_CUB_WRAPPED_NAMESPACE (device.py), so these CUB kernels are not the CAIRN side's.
#pragma once
#include <cstddef>
#include <string>
#include <cub/device/device_reduce.cuh>
#include <cuda_runtime.h>
#include "bench.cuh"

namespace base {

__device__ inline float warp_sum(float v) {
  for(int m = 16; m; m >>= 1) v += __shfl_xor_sync(0xffffffffu, v, m);
  return v;
}

// The sum over the block, in thread 0; every thread of the block must call it.
template<int THREADS> __device__ inline float block_sum(float v) {
  __shared__ float warps[THREADS / 32];
  v = warp_sum(v);
  if(threadIdx.x % 32 == 0) warps[threadIdx.x / 32] = v;
  __syncthreads();
  v = threadIdx.x < THREADS / 32 ? warps[threadIdx.x] : 0.0f;
  return threadIdx.x < 32 ? warp_sum(v) : v;
}

__device__ inline float chunk_sum(const float* x, std::size_t n) {
  const std::size_t chunks = n / 4, step = std::size_t(gridDim.x) * blockDim.x;
  const float4* x4 = reinterpret_cast<const float4*>(x);
  float sum = 0.0f;
  for(std::size_t c = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; c < chunks; c += step) {
    const float4 v = __ldcg(x4 + c);
    sum += (v.x + v.y) + (v.z + v.w);
  }
  const std::size_t tail = chunks * 4 + blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(tail < n) sum += x[tail];
  return sum;
}

__device__ unsigned finished = 0;

__global__ void __launch_bounds__(256) one_pass(const float* x, std::size_t n, float* partial, float* out) {
  const float total = block_sum<256>(chunk_sum(x, n));
  __shared__ bool last;
  if(threadIdx.x == 0) {
    partial[blockIdx.x] = total;
    __threadfence();
    last = atomicAdd(&finished, 1u) == gridDim.x - 1;
  }
  __syncthreads();
  if(!last) return;
  float sum = 0.0f;
  for(unsigned b = threadIdx.x; b < gridDim.x; b += blockDim.x) sum += __ldcg(partial + b);
  sum = block_sum<256>(sum);
  if(threadIdx.x == 0) {
    *out = sum;
    finished = 0;
  }
}

__global__ void __launch_bounds__(256) partial_v4(const float* x, std::size_t n, float* partial) {
  const float total = block_sum<256>(chunk_sum(x, n));
  if(threadIdx.x == 0) partial[blockIdx.x] = total;
}

__global__ void __launch_bounds__(256) partial_scalar(const float* x, std::size_t n, float* partial) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x;
  float sum = 0.0f;
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += step) sum += x[i];
  const float total = block_sum<256>(sum);
  if(threadIdx.x == 0) partial[blockIdx.x] = total;
}

__global__ void __launch_bounds__(256) partial_per_thread(const float* x, std::size_t n, float* partial) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  const float total = block_sum<256>(i < n ? x[i] : 0.0f);
  if(threadIdx.x == 0) partial[blockIdx.x] = total;
}

// The per-thread design as CAIRN's cooperative region runs it: a shared array zeroed and a barrier before the body,
// and, capped, a grid of at most 65,535 blocks that loop over the rest with another barrier between blocks.
__global__ void __launch_bounds__(256) partial_zeroed(const float* x, std::size_t n, std::size_t g, float* partial) {
  __shared__ float scratch[32];
  for(std::size_t b = blockIdx.x; b < g; b += gridDim.x) {
    if(threadIdx.x < 32) scratch[threadIdx.x] = 0.0f;
    __syncthreads();
    const std::size_t i = b * blockDim.x + threadIdx.x;
    float v = warp_sum(i < n ? x[i] : 0.0f);
    if(threadIdx.x % 32 == 0) scratch[threadIdx.x / 32] = v;
    __syncthreads();
    if(threadIdx.x < 32) {
      v = warp_sum(threadIdx.x < 8 ? scratch[threadIdx.x] : 0.0f);
      if(threadIdx.x == 0) partial[b] = v;
    }
    if(b + gridDim.x < g) __syncthreads();
  }
}

__global__ void __launch_bounds__(1024) finish(const float* partial, std::size_t g, float* out) {
  float sum = 0.0f;
  for(std::size_t i = threadIdx.x; i < g; i += 1024) sum += partial[i];
  sum = block_sum<1024>(sum);
  if(threadIdx.x == 0) *out = sum;
}

void sum_one_pass(cudaStream_t s, const float* x, std::size_t n, std::size_t g, float* partial, float* out) {
  one_pass<<<unsigned(g), 256, 0, s>>>(x, n, partial, out);
}
void sum_two_pass_v4(cudaStream_t s, const float* x, std::size_t n, std::size_t g, float* partial, float* out) {
  partial_v4<<<unsigned(g), 256, 0, s>>>(x, n, partial);
  finish<<<1, 1024, 0, s>>>(partial, g, out);
}
void sum_two_pass(cudaStream_t s, const float* x, std::size_t n, std::size_t g, float* partial, float* out) {
  partial_scalar<<<unsigned(g), 256, 0, s>>>(x, n, partial);
  finish<<<1, 1024, 0, s>>>(partial, g, out);
}
void sum_per_thread(cudaStream_t s, const float* x, std::size_t n, float* partial, float* out) {
  const std::size_t g = (n + 255) / 256;
  partial_per_thread<<<unsigned(g), 256, 0, s>>>(x, n, partial);
  finish<<<1, 1024, 0, s>>>(partial, g, out);
}

void sum_per_thread_zeroed(cudaStream_t s, const float* x, std::size_t n, float* partial, float* out, unsigned most) {
  const std::size_t g = (n + 255) / 256;
  partial_zeroed<<<unsigned(g < most ? g : most), 256, 0, s>>>(x, n, g, partial);
  finish<<<1, 1024, 0, s>>>(partial, g, out);
}

// CUB's storage is sized and allocated once, outside the timed calls; the total crosses back to the host.
struct Cub {
  void* temp = nullptr;
  std::size_t bytes = 0;
  float* cell = nullptr;
  explicit Cub(std::size_t n) {
    ok(cudaMalloc(&cell, sizeof(float)));
    ok(CUB_NS_QUALIFIER::DeviceReduce::Sum(nullptr, bytes, static_cast<const float*>(nullptr), cell, n));
    ok(cudaMalloc(&temp, bytes ? bytes : 1));
  }
  ~Cub() {
    cudaFree(temp);
    cudaFree(cell);
  }
  float operator()(cudaStream_t s, const float* x, std::size_t n) {
    ok(CUB_NS_QUALIFIER::DeviceReduce::Sum(temp, bytes, x, cell, n, s));
    float host = 0.0f;
    ok(cudaMemcpyAsync(&host, cell, sizeof(float), cudaMemcpyDeviceToHost, s));
    ok(cudaStreamSynchronize(s));
    return host;
  }
};
float sum_cub(cudaStream_t s, const float* x, std::size_t n) {
  static Cub* held[2] = {nullptr, nullptr};  // one per size the main times
  Cub*& mine = held[n > (std::size_t(1) << 24)];
  if(!mine) mine = new Cub(n);
  return (*mine)(s, x, n);
}

}  // namespace base

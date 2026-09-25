#include <cstdio>
#include <cuda_runtime.h>
__device__ unsigned long long table[16];
__global__ void touch(unsigned long long* out) { atomicAdd(&table[0], 1ull); out[0] = table[0]; }
#define SAY(what, call) do { cudaError_t e = (call); std::printf("%-40s %s\n", what, cudaGetErrorString(e)); } while(0)
int main() {
  unsigned long long* out; cudaStream_t a;
  SAY("cudaMalloc", cudaMalloc(&out, 8));
  SAY("cudaStreamCreate", cudaStreamCreate(&a));
  SAY("BeginCapture global", cudaStreamBeginCapture(a, cudaStreamCaptureModeGlobal));
  unsigned long long id = 0; SAY("cudaStreamGetId during capture", cudaStreamGetId(a, &id));
  cudaStreamCaptureStatus st; unsigned long long cid = 0;
  SAY("cudaStreamGetCaptureInfo during capture", cudaStreamGetCaptureInfo(a, &st, &cid));
  cudaFuncAttributes attr{}; SAY("cudaFuncGetAttributes during capture", cudaFuncGetAttributes(&attr, touch));
  touch<<<1, 1, 0, a>>>(out); SAY("first launch during capture", cudaGetLastError());
  cudaGraph_t g; SAY("EndCapture", cudaStreamEndCapture(a, &g));
  return 0;
}

// CAIRN's enqueued entries captured in a CUDA graph, as PyTorch and other callers capture their kernels. The very
// first CUDA work of the process is a capture in cudaStreamCaptureModeGlobal, which refuses any call that would wait,
// allocate or otherwise act outside the stream: the capture has to end cleanly with the entries' kernels in it. The
// graph is then replayed and checked, and timed against calling the entries directly and against the hand-written
// kernels captured the same way.
#include <cmath>
#include <cstddef>
#include <cuda_runtime.h>
#include <string>
#include <vector>
#include "bench.cuh"
#include "capture.h"

namespace base {

__global__ void scale(std::size_t n, float* tmp, const float* x) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(i < n) tmp[i] = 2.0f * x[i];
}
__global__ void shift(std::size_t n, float* out, const float* tmp) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(i < n) out[i] = tmp[i] + 1.0f;
}

}  // namespace base

__global__ void fill(float* x, std::size_t n) {
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += std::size_t(gridDim.x) * blockDim.x)
    x[i] = float(i % 1024) / 1024.0f;
}

static cudaGraphExec_t instantiate(cudaGraph_t graph) {
  cudaGraphExec_t exec{};
  ok(cudaGraphInstantiate(&exec, graph, 0));
  return exec;
}

int main(int argc, char** argv) {
  const int reps = reps_from(argc, argv, 60);
  const std::size_t n = std::size_t(1) << 24;
  int sms = 0;
  ok(cudaDeviceGetAttribute(&sms, cudaDevAttrMultiProcessorCount, 0));
  const std::size_t g = std::size_t(sms) * 6;
  float *x = nullptr, *tmp = nullptr, *out = nullptr, *partial = nullptr, *total = nullptr;
  ok(cudaMalloc(&x, n * sizeof(float)));
  ok(cudaMalloc(&tmp, n * sizeof(float)));
  ok(cudaMalloc(&out, n * sizeof(float)));
  ok(cudaMalloc(&partial, g * sizeof(float)));
  ok(cudaMalloc(&total, sizeof(float)));
  cudaStream_t s{};
  ok(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking));
  // The first CAIRN calls of the process, inside a global-mode capture.
  cudaGraph_t cairn_graph{};
  ok(cudaStreamBeginCapture(s, cudaStreamCaptureModeGlobal));
  cq_scale_shift(s, n, out, x, tmp);
  cq_sum(s, n, out, g, partial, total);
  const cudaError_t ended = cudaStreamEndCapture(s, &cairn_graph);
  std::fprintf(stderr, "capture of the enqueued entries: %s\n", cudaGetErrorString(ended));
  ok(ended);
  std::size_t nodes = 0;
  ok(cudaGraphGetNodes(cairn_graph, nullptr, &nodes));
  cudaGraphExec_t cairn_exec = instantiate(cairn_graph);
  fill<<<1024, 256, 0, s>>>(x, n);
  ok(cudaGetLastError());
  ok(cudaGraphLaunch(cairn_exec, s));
  float host = 0.0f;
  ok(cudaMemcpyAsync(&host, total, sizeof(float), cudaMemcpyDeviceToHost, s));
  ok(cudaStreamSynchronize(s));
  double exact = 0.0;
  for(std::size_t i = 0; i < n; ++i) exact += 2.0 * double(i % 1024) / 1024.0 + 1.0;
  const double error = std::fabs(double(host) - exact) / exact;
  // The hand-written kernels, captured the same way.
  const unsigned blocks = unsigned((n + 255) / 256);
  cudaGraph_t cuda_graph{};
  ok(cudaStreamBeginCapture(s, cudaStreamCaptureModeGlobal));
  base::scale<<<blocks, 256, 0, s>>>(n, tmp, x);
  base::shift<<<blocks, 256, 0, s>>>(n, out, tmp);
  ok(cudaStreamEndCapture(s, &cuda_graph));
  cudaGraphExec_t cuda_exec = instantiate(cuda_graph);
  header("capture", " \"cairn_graph_nodes\": " + std::to_string(nodes) + ", \"first_calls_captured\": \"" +
                        cudaGetErrorString(ended) + "\",");
  std::vector<Series> all;
  measure(s, n, {
      {"scale_shift_graph", "cuda", [&] { ok(cudaGraphLaunch(cuda_exec, s)); }},
      {"scale_shift_and_sum_graph", "cairn", [&] { ok(cudaGraphLaunch(cairn_exec, s)); }},
      {"scale_shift_and_sum_enqueued", "cairn", [&] {
         cq_scale_shift(s, n, out, x, tmp);
         cq_sum(s, n, out, g, partial, total);
       }},
      {"scale_shift_enqueued", "cairn", [&] { cq_scale_shift(s, n, out, x, tmp); }},
  }, reps, all);
  all[1].error = error;
  footer(all);
  ok(cudaGraphExecDestroy(cairn_exec));
  ok(cudaGraphExecDestroy(cuda_exec));
  ok(cudaGraphDestroy(cairn_graph));
  ok(cudaGraphDestroy(cuda_graph));
  for(float* p : {x, tmp, out, partial, total}) ok(cudaFree(p));
  ok(cudaStreamDestroy(s));
  return 0;
}

// CAIRN device runtime: scoped device owners, lane launches, linear stream tickets, reduce and
// stable compaction. The body of `parallel i in n` is the same lambda the host path runs; only
// the entry point differs. Every entry point is synchronous unless its name says otherwise, and
// any CUDA error - including a guard that fired in a lane - aborts the process.
//
// Build (GH200, CUDA 12.8, CUB from the toolkit; -ccbin g++-11, g++-12 and clang++-15 all pass).
// One command; the lines below are joined by spaces:
//
//   nvcc -std=c++20 -O3 --fmad=false -arch=sm_90
//        --extended-lambda --expt-relaxed-constexpr -Werror all-warnings
//        -Xcompiler -Wall,-Wextra,-Werror,-Wno-unused-parameter,-Wno-unused-variable,
//                   -Wno-unused-but-set-variable,-fno-exceptions,-fno-rtti,
//                   -ffp-contract=off,-fno-fast-math
//        prog.cu -o prog
//
// (-Xcompiler takes one comma separated word: the three indented lines are one argument.)
//
// --fmad=false is the device half of -ffp-contract=off: no contraction is ever authorized.
// --expt-relaxed-constexpr is required, not cosmetic: without it std::numeric_limits<T>::min()
// and std::in_range in cr::divide/cr::convert are host-only and nvcc merely warns (#20013-D).
// -Werror all-warnings promotes nvcc's own host/device warnings to errors; it is what turns a
// silently skipped guard into a build failure. -arch=native works too but pins the build to the
// machine that ran it. `#include <cub/cub.cuh>` is NOT usable: it drags in Thrust's
// system_error.inl, which needs -fexceptions. The four narrow CUB headers below do not.
//
// Device trap mechanism (measured, see tests/native/gpu_runtime.cu):
//   __trap()          chosen. The kernel dies, the context is poisoned, and every later call on
//                     it - the next sync, wait or copy - returns cudaErrorLaunchFailure, which
//                     cr::gpu::check turns into std::abort. No hang, no wrong answer, one PTX
//                     instruction on the failure path only, and NDEBUG cannot remove it.
//   assert(false)     works (cudaErrorAssert) but -DNDEBUG deletes it: measured, the kernel ran
//                     on and reported success. It also prints one line per failing lane.
//   __builtin_trap()  unusable: nvcc's device pass treats it as a host call (#20011-D) and drops
//                     it. Measured: the out of bounds kernel completed and sync said "no error".
//   fault word in unified memory: cannot stop the faulting lane, since cr::trap() is [[noreturn]]
//                     and cr::at() must still return a reference, so the bad access happens
//                     anyway. Rejected: it turns a guard into a race with a corrupted write.
#pragma once
#include <cstdio>
#include <cstring>
#include <cub/device/device_reduce.cuh>
#include <cub/device/device_scan.cuh>
#include <cub/iterator/counting_input_iterator.cuh>
#include <cub/iterator/transform_input_iterator.cuh>
#include <cuda_runtime.h>
#include "cairn_runtime.hpp"
namespace cr::gpu {

inline void check(cudaError_t e) noexcept {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "cairn: cuda: %s\n", cudaGetErrorString(e));
  trap();
}
template<class T> inline std::size_t bytes(std::size_t n) noexcept {
  if(n > static_cast<std::size_t>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T)) trap();
  return n * sizeof(T);
}

// Scoped owners, like cr::Buffer: zero initialized, released at scope exit, never copied, moved
// or returned. A failed allocation traps; n == 0 is legal and owns nothing.
enum class Where { device, pinned, unified };
template<class T, Where W> class Owner final {
  T* p_ = nullptr;
public:
  explicit Owner(std::size_t n) noexcept {
    static_assert(std::is_trivially_copyable_v<T>);  // Zeroed bytes are a value; scalars, and the runtime's Sum.
    const std::size_t b = bytes<T>(n);
    if(!b) return;
    void* q = nullptr;
    if constexpr(W == Where::device) check(cudaMalloc(&q, b));
    else if constexpr(W == Where::pinned) check(cudaMallocHost(&q, b));
    else check(cudaMallocManaged(&q, b));
    p_ = static_cast<T*>(q);
    if constexpr(W == Where::pinned) std::memset(p_, 0, b); else check(cudaMemset(p_, 0, b));
  }
  ~Owner() noexcept {
    if(!p_) return;
    if constexpr(W == Where::pinned) check(cudaFreeHost(p_)); else check(cudaFree(p_));
  }
  Owner(const Owner&) = delete;
  Owner& operator=(const Owner&) = delete;
  Owner(Owner&&) = delete;
  Owner& operator=(Owner&&) = delete;
  T* data() const noexcept { return p_; }
};
template<class T> using Buffer = Owner<T, Where::device>;
template<class T> using Pinned = Owner<T, Where::pinned>;
template<class T> using Unified = Owner<T, Where::unified>;

enum class Dir { h2d, d2h, d2d, h2h };
inline cudaMemcpyKind kind(Dir d) noexcept {
  switch(d) {
    case Dir::h2d: return cudaMemcpyHostToDevice;
    case Dir::d2h: return cudaMemcpyDeviceToHost;
    case Dir::d2d: return cudaMemcpyDeviceToDevice;
    case Dir::h2h: return cudaMemcpyHostToHost;
  }
  trap();
}
template<class T> inline void copy(T* dst, const T* src, std::size_t n, Dir d) noexcept {
  if(n) check(cudaMemcpy(dst, src, bytes<T>(n), kind(d)));
}

// One lane per i, strided so that n is limited by memory rather than by a grid dimension.
constexpr unsigned BLOCK = 256, MAX_GRID = 65535;
template<class F> __global__ void lanes(std::size_t n, F body) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x;
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += step) body(i);
}
template<class F> inline void fire(std::size_t n, const F& body, cudaStream_t s) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  if(!n) return;
  const std::size_t g = (n + BLOCK - 1) / BLOCK;
  lanes<<<(g < MAX_GRID ? unsigned(g) : MAX_GRID), BLOCK, 0, s>>>(n, body);
  check(cudaGetLastError());
}
template<class F> inline void launch(std::size_t n, F body) noexcept {
  fire(n, body, nullptr);
  check(cudaDeviceSynchronize());
}

// A linear ticket owning one stream: exactly one wait() consumes it, and dropping it unawaited
// traps. Ordering between tickets is recorded on the device through an event, never by stopping
// the host. The lane body needs no keep-alive: CUDA copies kernel arguments at launch.
class Ticket final {
  cudaStream_t s_ = nullptr;
  cudaEvent_t e_ = nullptr;
public:
  Ticket() noexcept {
    check(cudaStreamCreate(&s_));
    check(cudaEventCreateWithFlags(&e_, cudaEventDisableTiming));
  }
  Ticket(Ticket&& o) noexcept : s_(o.s_), e_(o.e_) { o.s_ = nullptr; o.e_ = nullptr; }
  Ticket(const Ticket&) = delete;
  Ticket& operator=(const Ticket&) = delete;
  Ticket& operator=(Ticket&&) = delete;  // a linear value is initialized, never overwritten
  ~Ticket() noexcept { if(s_) trap(); }
  cudaStream_t stream() const noexcept { return s_; }
  void follow(const Ticket& dep) const noexcept {
    check(cudaEventRecord(dep.e_, dep.s_));
    check(cudaStreamWaitEvent(s_, dep.e_, 0));
  }
  void wait() && noexcept { finish(); }
  void finish() noexcept {
    check(cudaStreamSynchronize(s_));
    check(cudaEventDestroy(e_));
    check(cudaStreamDestroy(s_));
    s_ = nullptr;
    e_ = nullptr;
  }
};
inline void wait(Ticket&& t) noexcept { t.finish(); }
// Work queued on a stream of its own, after the work of every ticket in `after...`.
template<class F, class... After> inline Ticket launch_async(std::size_t n, F body, const After&... after) noexcept {
  Ticket t;
  (t.follow(after), ...);
  fire(n, body, t.stream());
  return t;
}
template<class F> inline Ticket launch_after(const Ticket& dep, std::size_t n, F body) noexcept {
  Ticket t;
  t.follow(dep);
  fire(n, body, t.stream());
  return t;
}
template<class T, class... After>
inline Ticket copy_async(T* dst, const T* src, std::size_t n, Dir d, const After&... after) noexcept {
  Ticket t;
  (t.follow(after), ...);
  if(n) check(cudaMemcpyAsync(dst, src, bytes<T>(n), kind(d), t.stream()));
  return t;
}
template<class T>
inline Ticket copy_after(const Ticket& dep, T* dst, const T* src, std::size_t n, Dir d) noexcept {
  Ticket t;
  t.follow(dep);
  if(n) check(cudaMemcpyAsync(dst, src, bytes<T>(n), kind(d), t.stream()));
  return t;
}

// CUB asks for the return type of the combining operator in host code, which nvcc refuses to
// infer for an extended __device__ lambda. Naming T here lets the emitter pass a plain lambda.
template<class T, class Op> struct Binary {
  Op op;
  CR_DEVICE T operator()(T a, T b) const { return op(a, b); }
};

// Transform-reduce of value(i) over [0,n). Association order is unspecified by contract, so the
// result is exact for associative integer ops and within the usual tolerance for float sums.
template<class T, class Op, class F> inline T reduce(std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) return identity;
  using Index = cub::CountingInputIterator<std::size_t>;
  cub::TransformInputIterator<T, F, Index> in(Index(0), value);
  const Binary<T, Op> fold{op};
  Buffer<T> out(1);
  std::size_t need = 0;
  check(cub::DeviceReduce::Reduce(nullptr, need, in, out.data(), n, fold, identity));
  Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
  check(cub::DeviceReduce::Reduce(temp.data(), need, in, out.data(), n, fold, identity));
  T host = identity;
  copy(&host, out.data(), std::size_t(1), Dir::d2h);
  return host;
}

// Stable compaction: value(i) for each selected i, in order, into the prefix of out; the tail of
// out is left alone. pred runs exactly once per i (its answer is kept), value only when selected.
template<class T, class P, class F>
inline std::size_t compact(T* out, std::size_t n, P pred, F value) noexcept {
  if(!n) return 0;
  Buffer<unsigned char> keep(n);
  Buffer<std::size_t> off(n);
  unsigned char* k = keep.data();
  std::size_t* o = off.data();
  launch(n, [=] CR_DEVICE(std::size_t i) { k[i] = pred(i) ? 1 : 0; });
  std::size_t need = 0;
  check(cub::DeviceScan::ExclusiveSum(nullptr, need, k, o, n));
  {
    Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
    check(cub::DeviceScan::ExclusiveSum(temp.data(), need, k, o, n));
    check(cudaDeviceSynchronize());
  }
  launch(n, [=] CR_DEVICE(std::size_t i) { if(k[i]) out[o[i]] = value(i); });
  std::size_t last = 0;
  unsigned char tail = 0;
  copy(&last, o + (n - 1), std::size_t(1), Dir::d2h);
  copy(&tail, k + (n - 1), std::size_t(1), Dir::d2h);
  return last + (tail ? 1 : 0);
}
} // namespace cr::gpu

// CAIRN device runtime: CUDA as the machine of cairn_exec.hpp, which is what generated code calls, and the older
// synchronous entry points kept beside it. The body of `parallel i in n` is the same lambda the host path runs; only
// the entry point differs. Every entry point is synchronous unless its name says otherwise, and any CUDA error -
// including a guard that fired in a lane - aborts the process. cairn_exec.hpp's operations wait for their own
// stream; the older ones below (launch, launch_vector, launch_staged, reduce, scan, compact, Ticket) wait for the
// whole device or make a stream per ticket, as they always did, and generated code no longer calls them.
//
// Build (CUDA 12.8 with CUB from the toolkit and CUDA 13.2 with CCCL 3 both pass; g++ and clang++
// as -ccbin alike). One command; the lines below are joined by spaces:
//
//   nvcc -std=c++20 -O3 --fmad=false -arch=sm_120
//        --extended-lambda --expt-relaxed-constexpr -Werror all-warnings
//        -Xcompiler -Wall,-Wextra,-Werror,-Wno-unused-parameter,-Wno-unused-variable,
//                   -Wno-unused-but-set-variable,-fexceptions,-fno-rtti,
//                   -ffp-contract=off,-fno-fast-math
//        prog.cu -o prog
//
// (-Xcompiler takes one comma separated word: the three indented lines are one argument.)
//
// -fexceptions is the one departure from the host contract, and only for a device program's host
// pass: CCCL 3 (CUDA 13) reaches thrust/system/cuda/detail/util.h from CUB's dispatch headers,
// and its throw sites and system_error.inl's catch are unguarded, so -fno-exceptions refuses to
// parse them. Nothing in this runtime throws; every guard still aborts the process.
//
// --fmad=false is the device half of -ffp-contract=off: no contraction is ever authorized.
// --expt-relaxed-constexpr is required, not cosmetic: without it std::numeric_limits<T>::min()
// and std::in_range in cr::divide/cr::convert are host-only and nvcc merely warns (#20013-D).
// -Werror all-warnings promotes nvcc's own host/device warnings to errors; it is what turns a
// silently skipped guard into a build failure. -arch names the device target the build resolved
// (projects/target.py), never native. `#include <cub/cub.cuh>` is NOT usable: it drags in Thrust's
// system_error.inl, which needs -fexceptions. The four narrow CUB headers below do not.
//
// Device trap mechanism (measured, see tests/runtime/gpu_runtime.cu):
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
#include <cuda_runtime.h>
#include <iterator>
#include "cairn_reuse.hpp"
#include "cairn_runtime.hpp"
namespace cr::gpu {

inline void check(cudaError_t e) noexcept {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "cairn: cuda: %s\n", cudaGetErrorString(e));
  trap();
}
// A release at process exit may find the CUDA runtime already unloaded: a thread's context can end after it, and
// nothing is left to release then.
inline void released(cudaError_t e) noexcept {
  if(e != cudaErrorCudartUnloading) check(e);
}
template<class T> inline std::size_t bytes(std::size_t n) noexcept { return reuse::span<T>(n); }
using reuse::aligned;
using reuse::ALIGN;
using reuse::Binary;
using reuse::BLOCK;
using reuse::Dir;
using reuse::Indexed;
using reuse::MAX_GRID;
using reuse::plain;
using reuse::WARP;
using reuse::Where;

inline cudaMemcpyKind kind(Dir d) noexcept {
  switch(d) {
    case Dir::h2d: return cudaMemcpyHostToDevice;
    case Dir::d2h: return cudaMemcpyDeviceToHost;
    case Dir::d2d: return cudaMemcpyDeviceToDevice;
    case Dir::h2h: return cudaMemcpyHostToHost;
  }
  trap();
}

// One lane per i, strided so that n is limited by memory rather than by a grid dimension. Every i below n runs
// exactly once whatever the grid and block, which is why a plan may choose them: `block` threads a block, a grid
// that gives each thread about `per_lane` indices, and the stride loop unrolled `U` times.
template<unsigned U, class F> __global__ void lanes(std::size_t n, F body) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x;
#pragma unroll U
  for(std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x; i < n; i += step) body(i);
}
// The most threads a block of a kernel can hold, read once per kernel: a planned block the kernel's registers
// cannot fill runs in whole warps as wide as fit, and computes the same.
template<class K> inline unsigned most(K kernel) noexcept {
  cudaFuncAttributes held{};
  check(cudaFuncGetAttributes(&held, kernel));
  return unsigned(held.maxThreadsPerBlock) / WARP * WARP;
}
template<unsigned U, class F> inline unsigned widest() noexcept {
  static const unsigned held = most(lanes<U, F>);
  return held;
}
inline unsigned grid(std::size_t n, std::size_t each) noexcept {
  const std::size_t g = (n + each - 1) / each;
  return g < MAX_GRID ? unsigned(g) : MAX_GRID;
}
template<unsigned U = 1, class F>
inline void fire(std::size_t n, const F& body, cudaStream_t s, unsigned block = BLOCK, std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F>, "a lane body crosses over as kernel arguments");
  if(!n) return;
  if(block > widest<U, F>()) block = widest<U, F>();
  lanes<U><<<grid(n, std::size_t(block) * per_lane), block, 0, s>>>(n, body);
  check(cudaGetLastError());
}

// `plan f { vector W; }`: W adjacent indices per lane over chunks (Chunk in cairn_exec.hpp), the tail one at a time.
template<unsigned W, unsigned U, class F, class G> __global__ void chunks(std::size_t n, F scalar, G chunk) {
  const std::size_t step = std::size_t(gridDim.x) * blockDim.x * W;
#pragma unroll U
  for(std::size_t i = (blockIdx.x * std::size_t(blockDim.x) + threadIdx.x) * W; i < n; i += step) {
    if(n - i >= W) chunk(i);
    else for(std::size_t k = i; k < n; ++k) scalar(k);
  }
}
template<unsigned W, unsigned U, class F, class G>
inline void fire_vector(std::size_t n, F scalar, G chunk, cudaStream_t s, unsigned block, std::size_t per_lane) noexcept {
  static const unsigned held = most(chunks<W, U, F, G>);
  if(block > held) block = held;
  chunks<W, U><<<grid(n, std::size_t(block) * per_lane * W), block, 0, s>>>(n, scalar, chunk);
  check(cudaGetLastError());
}

// `plan f { stage R; }`: a block runs its indices tile by tile, loading each tile into shared memory between two
// barriers every thread reaches (the rule is in cairn_exec.hpp's run_staged).
template<std::size_t R, unsigned U, class L, class F> __global__ void staged_lanes(std::size_t n, L load, F body) {
  extern __shared__ __align__(16) unsigned char cr_shared[];
  const std::size_t b = blockDim.x, w = b + 2 * R, t = threadIdx.x, step = std::size_t(gridDim.x) * b;
#pragma unroll U
  for(std::size_t base = blockIdx.x * b; base < n; base += step) {
    __syncthreads();  // every thread is done with the last tile before this one overwrites it
    load(base, w, t, b, cr_shared);
    __syncthreads();
    if(base + t < n) body(base + t, base, w, cr_shared);
  }
}
template<std::size_t R, unsigned U, class L, class F, class S>
inline void fire_staged(std::size_t n, L load, F body, S bytes, cudaStream_t s, unsigned block,
                        std::size_t per_lane) noexcept {
  static const unsigned held = most(staged_lanes<R, U, L, F>);
  if(block > held) block = held;
  const std::size_t shared = bytes(std::size_t(block) + 2 * R);
  if(shared > 48 * 1024)  // past the default a kernel must ask for its dynamic shared memory
    check(cudaFuncSetAttribute(staged_lanes<R, U, L, F>, cudaFuncAttributeMaxDynamicSharedMemorySize, int(shared)));
  staged_lanes<R, U><<<grid(n, std::size_t(block) * per_lane), block, shared, s>>>(n, load, body);
  check(cudaGetLastError());
}

// CUDA as the machine an execution context and cairn_exec.hpp's operations run on. Each member is one CUDA call:
// nothing here waits for the whole device, and nothing makes a stream but make_stream.
struct Cuda {
  using Stream = cudaStream_t;
  using Event = cudaEvent_t;
  Stream make_stream() noexcept {
    cudaStream_t s = nullptr;
    check(cudaStreamCreate(&s));  // a blocking stream: ordered after work on the legacy default stream
    return s;
  }
  void destroy_stream(Stream s) noexcept { released(cudaStreamDestroy(s)); }
  Event make_event() noexcept {
    cudaEvent_t e = nullptr;
    check(cudaEventCreateWithFlags(&e, cudaEventDisableTiming));
    return e;
  }
  void destroy_event(Event e) noexcept { released(cudaEventDestroy(e)); }
  void record(Event e, Stream s) noexcept { check(cudaEventRecord(e, s)); }
  void wait_event(Stream s, Event e) noexcept { check(cudaStreamWaitEvent(s, e, 0)); }
  void sync_stream(Stream s) noexcept { check(cudaStreamSynchronize(s)); }
  void sync_event(Event e) noexcept { released(cudaEventSynchronize(e)); }
  void* alloc(std::size_t b) noexcept {
    void* p = nullptr;
    check(cudaMalloc(&p, b));
    return p;
  }
  void free(void* p) noexcept { released(cudaFree(p)); }
  void* alloc_async(std::size_t b, Stream s) noexcept {
    void* p = nullptr;
    check(cudaMallocAsync(&p, b, s));
    return p;
  }
  void free_async(void* p, Stream s) noexcept { check(cudaFreeAsync(p, s)); }
  void* allocate(Where w, std::size_t b) noexcept {
    void* p = nullptr;
    if(w == Where::device) check(cudaMalloc(&p, b));
    else if(w == Where::pinned) check(cudaMallocHost(&p, b));
    else check(cudaMallocManaged(&p, b));
    return p;
  }
  void release(Where w, void* p) noexcept { check(w == Where::pinned ? cudaFreeHost(p) : cudaFree(p)); }
  void zero(void* p, std::size_t b, Stream s) noexcept { check(cudaMemsetAsync(p, 0, b, s)); }
  void copy(void* dst, const void* src, std::size_t b, Dir d, Stream s) noexcept {
    check(cudaMemcpyAsync(dst, src, b, kind(d), s));
  }
  template<unsigned U, class F>
  void lanes(std::size_t n, const F& body, Stream s, unsigned block, std::size_t per_lane) noexcept {
    fire<U>(n, body, s, block, per_lane);
  }
  template<unsigned W, unsigned U, class F, class G>
  void vector_lanes(std::size_t n, F scalar, G chunk, Stream s, unsigned block, std::size_t per_lane) noexcept {
    fire_vector<W, U>(n, scalar, chunk, s, block, per_lane);
  }
  template<std::size_t R, unsigned U, class L, class F, class S>
  void staged_lanes(std::size_t n, L load, F body, S bytes, Stream s, unsigned block, std::size_t per_lane) noexcept {
    fire_staged<R, U>(n, load, body, bytes, s, block, per_lane);
  }
  // CUB's calls: with a null `temp` each only says how many bytes of temporary storage it needs.
  template<class In, class Out, class Fold, class T>
  void reduce(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Fold fold, T identity, Stream s) noexcept {
    check(cub::DeviceReduce::Reduce(temp, bytes, in, out, n, fold, identity, s));
  }
  template<class In, class Out, class Fold>
  void inclusive_scan(void* temp, std::size_t& bytes, In in, Out out, Fold fold, std::size_t n, Stream s) noexcept {
    check(cub::DeviceScan::InclusiveScan(temp, bytes, in, out, fold, n, s));
  }
  template<class In, class Out>
  void exclusive_sum(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Stream s) noexcept {
    check(cub::DeviceScan::ExclusiveSum(temp, bytes, in, out, n, s));
  }
};
using Machine = Cuda;
}  // namespace cr::gpu

#include "cairn_exec.hpp"

namespace cr::gpu {

// The older synchronous entry points, as they were: the legacy default stream, then a wait for the whole device.
template<class T> inline void copy(T* dst, const T* src, std::size_t n, Dir d) noexcept {
  if(n) check(cudaMemcpy(dst, src, bytes<T>(n), kind(d)));
}
template<unsigned U = 1, class F>
inline void launch(std::size_t n, F body, unsigned block = BLOCK, std::size_t per_lane = 1) noexcept {
  fire<U>(n, body, nullptr, block, per_lane);
  check(cudaDeviceSynchronize());
}
template<unsigned W, unsigned U = 1, class F, class G>
inline void launch_vector(std::size_t n, bool whole, F scalar, G chunk, unsigned block = BLOCK,
                          std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<F> && std::is_trivially_copyable_v<G>, "lanes cross as arguments");
  if(!whole) return launch<U>(n, scalar, block, per_lane);  // a pointer off its chunk's width: the scalar lanes
  if(!n) return;
  fire_vector<W, U>(n, scalar, chunk, nullptr, block, per_lane);
  check(cudaDeviceSynchronize());
}
template<std::size_t R, unsigned U = 1, class L, class F, class S>
inline void launch_staged(std::size_t n, L load, F body, S bytes, unsigned block = BLOCK,
                          std::size_t per_lane = 1) noexcept {
  static_assert(std::is_trivially_copyable_v<L> && std::is_trivially_copyable_v<F>, "lanes cross as arguments");
  if(!n) return;
  fire_staged<R, U>(n, load, body, bytes, nullptr, block, per_lane);
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
  // An event that completes with everything queued on this ticket so far, for other work to be ordered after.
  cudaEvent_t mark() const noexcept {
    check(cudaEventRecord(e_, s_));
    return e_;
  }
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

// Transform-reduce of value(i) over [0,n). Association order is unspecified by contract, so the
// result is exact for associative integer ops and within the usual tolerance for float sums.
template<class T, class Op, class F> inline T reduce(std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) return identity;
  const Indexed<T, F> in{value};
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

// Scan of value(i) over [0,n) into out: out[i] is op over value(0..i], or over value(0..i) when Exclusive, and
// the answer is op over every value. The association order is CUB's, exact for the integer operators the language
// admits here. The inclusive scan lands in device scratch first, so a value(i) that reads out[i] reads it before
// anything writes it; one more pass writes out from the scratch, shifted by one place when Exclusive.
template<bool Exclusive, class T, class R, class Op, class F>
inline T scan(R* out, std::size_t n, T identity, Op op, F value) noexcept {
  if(!n) return identity;
  const Indexed<T, F> in{value};
  const Binary<T, Op> fold{op};
  Buffer<T> held(n);
  T* h = held.data();
  std::size_t need = 0;
  check(cub::DeviceScan::InclusiveScan(nullptr, need, in, h, fold, n));
  {
    Buffer<char> temp(need ? need : 1);  // a null temp pointer would mean "query" to CUB
    check(cub::DeviceScan::InclusiveScan(temp.data(), need, in, h, fold, n));
    check(cudaDeviceSynchronize());
  }
  launch(n, [=] CR_DEVICE(std::size_t i) { out[i] = plain(Exclusive ? (i ? h[i - 1] : identity) : h[i]); });
  T total = identity;
  copy(&total, h + (n - 1), std::size_t(1), Dir::d2h);
  return total;
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
}  // namespace cr::gpu

// CAIRN device runtime: CUDA as the machine of cairn_exec.hpp, which is what generated code calls, and the older
// synchronous entry points kept beside it. The kernels themselves are cairn_kernels.hpp's; its opening note says
// how a device program is built and how a guard that fires in a lane stops it. cairn_exec.hpp's operations wait
// for their own stream; the older ones below (launch, launch_vector, launch_staged, Ticket) and in cairn_cub.hpp
// (reduce, scan, compact) wait for the whole device or make a stream per ticket, as they always did, and generated
// code no longer calls them. Every entry point is synchronous unless its name says otherwise, and any CUDA error
// aborts the process. CUB is not read here: cairn_cub.hpp brings it, only to a program with a device collector.
#pragma once
#if !defined(CAIRN_EMULATE)  // an emulated build reads cairn_emulate.hpp, its host machine, before the program
#include <cstdio>
#include <cstring>
#include <cuda_runtime.h>
#include <iterator>
#include "cairn_kernels.hpp"
#include "cairn_reuse.hpp"
#include "cairn_runtime.hpp"
namespace cr::gpu {
static_assert(BLOCK == reuse::BLOCK && MAX_GRID == reuse::MAX_GRID && WARP == reuse::WARP);

// A release at process exit may find the CUDA runtime already unloaded: a thread's context can end after it, and
// nothing is left to release then.
inline void released(cudaError_t e) noexcept {
  if(e != cudaErrorCudartUnloading) check(e);
}
template<class T> inline std::size_t bytes(std::size_t n) noexcept { return reuse::span<T>(n); }
using reuse::aligned;
using reuse::ALIGN;
using reuse::Binary;
using reuse::Dir;
using reuse::Indexed;
using reuse::plain;
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
  // What names a stream for the life of the process, which a stream capture refuses to answer (cudaStreamGetId fails
  // during a global capture and ends it, on the RTX 5070 Ti under CUDA 13.2); what names it among the streams alive
  // now, which asks CUDA nothing; and whether it is capturing into a graph, with the capture sequence's id, which a
  // capture allows.
  unsigned long long handle(Stream s) noexcept { return reinterpret_cast<std::uintptr_t>(s); }
  unsigned long long stream_id(Stream s) noexcept {
    unsigned long long id = 0;
    check(cudaStreamGetId(s, &id));
    return id;
  }
  bool capturing(Stream s, unsigned long long* id) noexcept {
    cudaStreamCaptureStatus status = cudaStreamCaptureStatusNone;
    check(cudaStreamGetCaptureInfo(s, &status, id));
    return status != cudaStreamCaptureStatusNone;
  }
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
  // CUB's calls, defined in cairn_cub.hpp: with a null `temp` each only says how many bytes of temporary storage it
  // needs. A program without a device collector never instantiates them, so it never reads CUB.
  template<class In, class Out, class Fold, class T>
  void reduce(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Fold fold, T identity, Stream s) noexcept;
  template<class In, class Out, class Fold>
  void inclusive_scan(void* temp, std::size_t& bytes, In in, Out out, Fold fold, std::size_t n, Stream s) noexcept;
  template<class In, class Out>
  void exclusive_sum(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Stream s) noexcept;
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
}  // namespace cr::gpu
#endif

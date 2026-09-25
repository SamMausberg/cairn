// A host machine for cairn_exec.hpp, so generated device programs build with g++ or clang++ and run without a device: a
// test writes `#include "gpu_host.hpp"` where the program says `#include "cairn_gpu.hpp"`. Everything the lowering
// calls is then cairn_exec.hpp's own code over this machine, and a call the lowering made to anything else would not
// compile. Streams and events are records. A launch runs its lanes at once, in the order a grid of `block` threads
// striding over n visits them, a staged block loads its whole tile before any thread's body (the tile is its own
// exactly sized allocation, filled with a pattern first, so a read outside what the block loaded reads the pattern and
// one past the tile is an AddressSanitizer error), and a copy is a memcpy. Every stream and event made, every
// allocation, launch, copy and wait is counted in cr::gpu::counted, which a test reads between calls. The machine has
// no whole-device wait to count: no operation of cairn_exec.hpp can ask for one. While `capturing` is set, as a CUDA
// graph capture would be, every call a capture refuses (a wait, a stream, an event, an allocation, a release or a
// stream's id) is counted again as `refused`. Each thread also keeps, for every stream it queued on, how much of that
// work a wait has covered, and `early` counts each time the host reads or writes memory across a copy, or releases
// device memory, while another stream still holds work nobody waited for.
#pragma once
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "cairn_reuse.hpp"
#include "cairn_runtime.hpp"
namespace cr::gpu {
using reuse::Dir;
using reuse::Where;

struct Counts {  // atomic, since every host thread has an execution context of its own
  std::atomic<std::size_t> streams{0}, streams_destroyed{0}, events{0}, events_destroyed{0};
  std::atomic<std::size_t> allocations{0}, scratch_allocations{0}, frees{0};
  std::atomic<std::size_t> launches{0}, copies{0}, library_calls{0}, stream_waits{0}, event_waits{0}, records{0};
  std::atomic<const void*> last_stream{nullptr};  // the stream the last launch or copy was queued on
  std::atomic<std::size_t> most_by_one_thread{0};  // the most streams any one host thread has made
  std::atomic<bool> capturing{false};
  std::atomic<unsigned long long> capture{0};  // the capture sequence's id while capturing
  std::atomic<std::size_t> refused{0};  // calls made while capturing that a stream capture would refuse
  std::atomic<std::size_t> early{0};    // host observations made while another stream held unwaited work
};
inline Counts counted;
inline thread_local std::size_t made_here = 0;

struct HostStream {
  std::size_t id;
};
struct HostEvent {
  std::size_t id;
  const HostStream* on = nullptr;  // the stream it was last recorded on, and how much of that stream's work it marks
  std::size_t mark = 0;
};
struct Pending {
  const HostStream* stream = nullptr;  // by address, never dereferenced: a caller's stream may be gone
  std::size_t queued = 0, waited = 0;  // operations queued on it, and how many of them a wait has covered
};
// This thread's streams, in plain storage with no destructor, so the context a thread's exit destroys still reaches it.
inline thread_local Pending pending[64];
inline thread_local std::size_t pending_streams = 0;
inline Pending& pending_on(const HostStream* s) noexcept {
  for(std::size_t k = 0; k < pending_streams; ++k)
    if(pending[k].stream == s) return pending[k];
  if(pending_streams == 64) trap();
  pending[pending_streams] = Pending{s, 0, 0};
  return pending[pending_streams++];
}

// Work queued on any stream of this thread that no wait has covered yet.
inline std::size_t unwaited() noexcept {
  std::size_t n = 0;
  for(std::size_t k = 0; k < pending_streams; ++k) n += pending[k].queued - pending[k].waited;
  return n;
}

inline void capture_refuses() noexcept {
  if(counted.capturing) ++counted.refused;
}

struct Host {
  using Stream = HostStream*;
  using Event = HostEvent*;
  Stream make_stream() noexcept {
    capture_refuses();
    const std::size_t mine = ++made_here;
    for(std::size_t most = counted.most_by_one_thread; mine > most && !counted.most_by_one_thread.compare_exchange_weak(most, mine);) {
    }
    return new HostStream{++counted.streams};
  }
  void destroy_stream(Stream s) noexcept {
    ++counted.streams_destroyed;
    Pending& gone = pending_on(s);
    gone = pending[--pending_streams];  // its address may be a new stream's next
    delete s;
  }
  Event make_event() noexcept {
    capture_refuses();
    return new HostEvent{++counted.events};
  }
  void destroy_event(Event e) noexcept {
    ++counted.events_destroyed;
    delete e;
  }
  void record(Event e, Stream s) noexcept {
    ++counted.records;
    e->on = s;
    e->mark = pending_on(s).queued;
  }
  void wait_event(Stream, Event) noexcept {}
  // A stream's id for the process, which a capture refuses as CUDA does; its handle, and whether a capture is on,
  // which a capture allows.
  unsigned long long handle(Stream s) noexcept { return reinterpret_cast<std::uintptr_t>(s); }
  unsigned long long stream_id(Stream s) noexcept {
    capture_refuses();
    return reinterpret_cast<std::uintptr_t>(s);
  }
  bool capturing(Stream, unsigned long long* id) noexcept {
    *id = counted.capture;
    return counted.capturing;
  }
  void sync_stream(Stream s) noexcept {
    capture_refuses();
    ++counted.stream_waits;
    pending_on(s).waited = pending_on(s).queued;
  }
  void sync_event(Event e) noexcept {
    capture_refuses();
    ++counted.event_waits;
    if(e->on) pending_on(e->on).waited = std::max(pending_on(e->on).waited, e->mark);
  }
  // The host is about to see memory through work on `s`: every other stream's work must have been waited for.
  static void observes(Stream s) noexcept {
    for(std::size_t k = 0; k < pending_streams; ++k)
      if(pending[k].stream != s && pending[k].queued != pending[k].waited) {
        ++counted.early;
        return;
      }
  }
  static void* storage(std::size_t b) noexcept {
    void* p = std::aligned_alloc(reuse::ALIGN, reuse::aligned(b ? b : 1));
    if(!p) trap();
    return p;
  }
  void* alloc(std::size_t b) noexcept {
    capture_refuses();
    ++counted.scratch_allocations;
    return storage(b);
  }
  void free(void* p) noexcept {
    capture_refuses();
    ++counted.frees;
    std::free(p);
  }
  void* alloc_async(std::size_t b, Stream) noexcept { return alloc(b); }
  void free_async(void* p, Stream) noexcept { free(p); }
  void* allocate(Where, std::size_t b) noexcept {
    capture_refuses();
    ++counted.allocations;
    return storage(b);
  }
  void release(Where, void* p) noexcept {
    capture_refuses();
    observes(nullptr);
    ++counted.frees;
    std::free(p);
  }
  void zero(void* p, std::size_t b, Stream s) noexcept {
    queued(s);
    std::memset(p, 0, b);
  }
  void copy(void* dst, const void* src, std::size_t b, Dir d, Stream s) noexcept {
    if(d == Dir::h2d || d == Dir::d2h) observes(s);
    queued(s);
    ++counted.copies;
    std::memmove(dst, src, b);
  }
  static void queued(Stream s) noexcept {
    counted.last_stream = s;
    ++pending_on(s).queued;
  }
  static std::size_t threads(std::size_t n, std::size_t each, unsigned block) noexcept {
    const std::size_t g = (n + each - 1) / each;
    return (g < reuse::MAX_GRID ? g : reuse::MAX_GRID) * block;
  }
  template<unsigned U, class F>
  void lanes(std::size_t n, const F& body, Stream s, unsigned block, std::size_t per_lane) noexcept {
    queued(s);
    ++counted.launches;
    const std::size_t all = threads(n, std::size_t(block) * per_lane, block);
    for(std::size_t t = 0; t < all; ++t)
      for(std::size_t i = t; i < n; i += all) body(i);
  }
  template<unsigned W, unsigned U, class F, class G>
  void vector_lanes(std::size_t n, F scalar, G chunk, Stream s, unsigned block, std::size_t per_lane) noexcept {
    queued(s);
    ++counted.launches;
    const std::size_t all = threads(n, std::size_t(block) * per_lane * W, block);
    for(std::size_t t = 0; t < all; ++t)
      for(std::size_t i = t * W; i < n; i += all * W) {
        if(n - i >= W) chunk(i);
        else for(std::size_t k = i; k < n; ++k) scalar(k);
      }
  }
  template<std::size_t R, unsigned U, class L, class F, class S>
  void staged_lanes(std::size_t n, L load, F body, S bytes, Stream s, unsigned block, std::size_t) noexcept {
    queued(s);
    ++counted.launches;
    const std::size_t w = block + 2 * R;
    for(std::size_t base = 0; base < n; base += block) {
      std::vector<unsigned char> tile(bytes(w));
      std::memset(tile.data(), 0x7f, tile.size());  // a float of about 3.4e38: nothing the stencils compute
      for(std::size_t t = 0; t < block; ++t) load(base, w, t, std::size_t(block), tile.data());
      for(std::size_t t = 0; t < block; ++t)
        if(base + t < n) body(base + t, base, w, tile.data());
    }
  }
  // What CUB's calls compute, in order, and a request for temporary storage that grows with n as CUB's does, so
  // the arena is exercised: with a null `temp` a call only says how many bytes it needs.
  static std::size_t scratch(std::size_t n) noexcept { return 512 + n / 8; }
  template<class In, class Out, class Fold, class T>
  void reduce(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Fold fold, T identity, Stream s) noexcept {
    if(!temp) {
      bytes = scratch(n);
      return;
    }
    queued(s);
    ++counted.library_calls;
    T total = identity;
    for(std::size_t i = 0; i < n; ++i) total = fold(total, in[static_cast<std::ptrdiff_t>(i)]);
    *out = total;
  }
  template<class In, class Out, class Fold>
  void inclusive_scan(void* temp, std::size_t& bytes, In in, Out out, Fold fold, std::size_t n, Stream s) noexcept {
    if(!temp) {
      bytes = scratch(n);
      return;
    }
    queued(s);
    ++counted.library_calls;
    for(std::size_t i = 0; i < n; ++i)
      out[i] = i ? fold(out[i - 1], in[static_cast<std::ptrdiff_t>(i)]) : in[static_cast<std::ptrdiff_t>(i)];
  }
  template<class In, class Out>
  void exclusive_sum(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Stream s) noexcept {
    if(!temp) {
      bytes = scratch(n);
      return;
    }
    queued(s);
    ++counted.library_calls;
    std::size_t total = 0;
    for(std::size_t i = 0; i < n; ++i) {
      out[i] = total;
      total += in[i];
    }
  }
};
using Machine = Host;
}  // namespace cr::gpu

#include "cairn_exec.hpp"

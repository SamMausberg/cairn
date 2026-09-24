// CAIRN device emulation: the machine cairn_exec.hpp runs device work on when a device program is built for the host
// (`cairn build --emulate`, projects/emulation.py). The build defines CAIRN_EMULATE, which leaves CUDA out of
// cairn_gpu.hpp, and reads this header before the program's first line (toolchain.emulated), so the host compiler
// builds the C++ nvcc would have built, byte for byte, and only the machine under cairn_exec.hpp differs. It is a correctness tool: it runs every region, collector, cooperative region and
// multiply of a device program on host threads, and it is not a way to time device code.
//
// Device, pinned and unified memory are host memory of exactly the bytes asked for, on the device's 256-byte alignment
// and zeroed where the device zeroes it, so the address sanitizer sees a read one element past a device array. A copy
// in any direction is a memcpy, so the sanitizer also sees one whose sides overlap. A stream and an event are records,
// and every operation runs to completion when it is queued: queued work has finished by its `spawn`, in program order,
// which is one of the orders `after` and the leases allow. A region's lanes run on the host lane pool
// (cairn_parallel.hpp), a staged region's blocks each load their whole tile before any lane reads it, a reduction,
// scan and compaction fold in index order, a cooperative region runs each block's threads as real threads meeting at
// a std::barrier (cairn_coop.hpp), and a tensor-core multiply is the host's reference loop, in increasing k. A guard
// that fails in a lane aborts the process from the thread that ran it, as the host's own regions do.
#pragma once
#if defined(__CUDACC__) || !defined(CAIRN_EMULATE)
#error "cairn_emulate.hpp runs device work on the host: build with the host compiler and CAIRN_EMULATE, not nvcc."
#endif
#include <cstdlib>
#include <cstring>
#include <vector>
#include "cairn_parallel.hpp"
#include "cairn_reuse.hpp"
#include "cairn_runtime.hpp"
namespace cr::gpu {
using reuse::Dir;
using reuse::Where;
constexpr unsigned BLOCK = reuse::BLOCK, MAX_GRID = reuse::MAX_GRID, WARP = reuse::WARP;

struct EmulatedStream {
  unsigned char unused;
};
struct EmulatedEvent {
  unsigned char unused;
};

struct Emulated {
  using Stream = EmulatedStream*;
  using Event = EmulatedEvent*;
  Stream make_stream() noexcept { return made<EmulatedStream>(); }
  void destroy_stream(Stream s) noexcept { delete s; }
  Event make_event() noexcept { return made<EmulatedEvent>(); }
  void destroy_event(Event e) noexcept { delete e; }
  // Work has finished when it is queued, so nothing is ever waited for.
  void record(Event, Stream) noexcept {}
  void wait_event(Stream, Event) noexcept {}
  void sync_stream(Stream) noexcept {}
  void sync_event(Event) noexcept {}
  void* alloc(std::size_t b) noexcept { return storage(b); }
  void free(void* p) noexcept { std::free(p); }
  void* alloc_async(std::size_t b, Stream) noexcept { return storage(b); }
  void free_async(void* p, Stream) noexcept { std::free(p); }
  void* allocate(Where, std::size_t b) noexcept { return storage(b); }
  void release(Where, void* p) noexcept { std::free(p); }
  void zero(void* p, std::size_t b, Stream) noexcept { std::memset(p, 0, b); }
  void copy(void* dst, const void* src, std::size_t b, Dir, Stream) noexcept { std::memcpy(dst, src, b); }

  // Every index below n once, on the lane pool, whatever block and indices per thread a plan chose.
  template<unsigned U, class F> void lanes(std::size_t n, const F& body, Stream, unsigned, std::size_t) noexcept {
    par::run(n, [&body](std::size_t i) noexcept { body(i); });
  }
  // Whole chunks of W indices where the scalar lanes would have run them one by one; the tail one index at a time.
  template<unsigned W, unsigned U, class F, class G>
  void vector_lanes(std::size_t n, F scalar, G chunk, Stream, unsigned, std::size_t) noexcept {
    par::run(n / W, [&chunk](std::size_t c) noexcept { chunk(c * W); }, W);
    for(std::size_t i = n / W * W; i < n; ++i) scalar(i);
  }
  // A block's tile is its own allocation of exactly its size, filled with a pattern before the block loads it, so a
  // lane that read an element its block did not load would read the pattern, and one past the tile is an address
  // sanitizer error. Every thread of a block loads before any thread's body runs, as the device's barrier orders it.
  template<std::size_t R, unsigned U, class L, class F, class S>
  void staged_lanes(std::size_t n, L load, F body, S bytes, Stream, unsigned block, std::size_t) noexcept {
    const std::size_t w = block + 2 * R, blocks = (n + block - 1) / block;
    par::run(blocks, [&](std::size_t k) noexcept {
      const std::size_t base = k * block;
      std::vector<unsigned char> tile(bytes(w));
      std::memset(tile.data(), 0x7f, tile.size());
      for(std::size_t t = 0; t < block; ++t) load(base, w, t, std::size_t(block), tile.data());
      for(std::size_t t = 0; t < block && base + t < n; ++t) body(base + t, base, w, tile.data());
    }, block);
  }
  // What CUB's calls compute, in index order. With a null `temp` a call only says how much scratch it needs: none.
  template<class In, class Out, class Fold, class T>
  void reduce(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Fold fold, T identity, Stream) noexcept {
    if(!temp) {
      bytes = 0;
      return;
    }
    T total = identity;
    for(std::size_t i = 0; i < n; ++i) total = fold(total, in[static_cast<std::ptrdiff_t>(i)]);
    *out = total;
  }
  template<class In, class Out, class Fold>
  void inclusive_scan(void* temp, std::size_t& bytes, In in, Out out, Fold fold, std::size_t n, Stream) noexcept {
    if(!temp) {
      bytes = 0;
      return;
    }
    for(std::size_t i = 0; i < n; ++i)
      out[i] = i ? fold(out[i - 1], in[static_cast<std::ptrdiff_t>(i)]) : in[static_cast<std::ptrdiff_t>(i)];
  }
  template<class In, class Out>
  void exclusive_sum(void* temp, std::size_t& bytes, In in, Out out, std::size_t n, Stream) noexcept {
    if(!temp) {
      bytes = 0;
      return;
    }
    std::size_t total = 0;
    for(std::size_t i = 0; i < n; ++i) {
      out[i] = total;
      total += in[i];
    }
  }

private:
  template<class T> static T* made() noexcept {
    T* made = new(std::nothrow) T{};
    if(!made) trap();
    return made;
  }
  static void* storage(std::size_t b) noexcept {
    void* p = nullptr;
    if(posix_memalign(&p, reuse::ALIGN, b ? b : 1) != 0) trap();
    return p;
  }
};
using Machine = Emulated;
}  // namespace cr::gpu

#include "cairn_exec.hpp"
#include "cairn_coop.hpp"
#include "cairn_tensor.hpp"

namespace cr::coop {
// A device region's body takes the host's block context: each block's threads are host threads at a std::barrier,
// two blocks at a time, on the calling thread's synchronous lane, as the device launch runs on its stream.
using Device = Host;
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, class F>
inline void launch(gpu::Context& ctx, std::size_t grid, F body) noexcept {
  static_assert(BYTES <= 48 * 1024, "static shared memory holds 48 KiB");
  reuse::synchronous(ctx, [&](gpu::Machine::Stream) { run<THREADS, BYTES, ZERO>(grid, body); });
}
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, std::size_t FINISH = BYTES, class F, class G>
inline void launch_then(gpu::Context& ctx, std::size_t grid, F body, G finish) noexcept {
  static_assert(BYTES <= 48 * 1024, "static shared memory holds 48 KiB");
  reuse::synchronous(ctx, [&](gpu::Machine::Stream) { run_then<THREADS, BYTES, ZERO, FINISH>(grid, body, finish); });
}
}  // namespace cr::coop

namespace cr::tensor {
// The multiply the tensor cores run on the device, as the host runs it: every product, then every sum in increasing k.
template<class A> inline void launch(gpu::Context& ctx, std::size_t m, std::size_t n, std::size_t k, float* c,
                                     std::size_t cn, const A* a, std::size_t an, const A* b, std::size_t bn) noexcept {
  reuse::synchronous(ctx, [&](gpu::Machine::Stream) { multiply(m, n, k, c, cn, a, an, b, bn); });
}
}  // namespace cr::tensor

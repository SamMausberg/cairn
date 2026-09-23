// Cooperative regions (compiler/cooperative.py): `blocks b in G threads t in T { }` runs G blocks of T threads, the
// threads of a block sharing BYTES of zeroed memory and meeting at its barriers. The compiler writes the region's
// body once, as a lambda taking the block's context, its block number and its thread number; the context is what
// differs between the host and the device.
//
// Host: every block's T threads are real threads, and a barrier is a std::barrier they all arrive at. TEAMS blocks
// run at once, each team of T threads taking blocks k, k + TEAMS, ...: the shared memory is zeroed before each block
// and every thread is done with it before the next, so a block cannot see another's. This lowering exists so that the
// phase rule is checked on real executions by the thread sanitizer; it creates T * TEAMS threads per region and is
// not a fast path. A warp operation exchanges values through the warp's own slots between two waits at the warp's
// own barrier, in the order the device's butterfly uses.
//
// Device: one kernel launch of min(G, 65535) blocks of T threads, each looping over its share of the G blocks in the
// same number of steps, BYTES of static shared memory zeroed at each step, barriers as __syncthreads and warp
// operations as __shfl_*_sync over the full warp, on the calling thread's execution context: the call returns once
// that stream has run the region, as a `parallel` region does, and nothing waits for the rest of the device.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include "cairn_runtime.hpp"

namespace cr::coop {
inline constexpr unsigned WARP = 32;
inline constexpr std::size_t TEAMS = 2;  // blocks a host region runs at once

// A value's bits in one 64-bit slot, and back: how the host moves any shuffled scalar between threads.
template<class T> CR_HD inline std::uint64_t bits(T v) noexcept {
  std::uint64_t b = 0;
  std::memcpy(&b, &v, sizeof(T));
  return b;
}
template<class T> CR_HD inline T unbits(std::uint64_t b) noexcept {
  T v;
  std::memcpy(&v, &b, sizeof(T));
  return v;
}
}  // namespace cr::coop

#if !defined(__CUDA_ARCH__)
#include <barrier>
#include <cstdio>
#include <memory>
#include <pthread.h>
#include <vector>

namespace cr::coop {

// A block's threads all run at once, up to 2048 of them for two blocks of 1024, so each gets a 256 KiB stack rather
// than the default 8 MiB: two teams of 1024 then hold 512 MiB of address space, inside `cairn run`'s 1 GiB limit.
inline constexpr std::size_t STACK = 256 * 1024;

// Start `f` on a thread of its own; `f` outlives it. A region whose threads cannot all start cannot run at all.
template<class F> inline pthread_t start(F* f) noexcept {
  pthread_attr_t attributes;
  pthread_t id{};
  bool ok = pthread_attr_init(&attributes) == 0 && pthread_attr_setstacksize(&attributes, STACK) == 0;
  ok = ok && pthread_create(&id, &attributes, [](void* p) -> void* { (*static_cast<F*>(p))(); return nullptr; }, f) == 0;
  pthread_attr_destroy(&attributes);
  if(!ok) {
    std::fputs("cairn: a cooperative region could not start its threads\n", stderr);
    trap();
  }
  return id;
}

// One warp's exchange: each thread's slot, and the barrier its 32 threads meet at around a read of the slots.
struct Warp {
  std::uint64_t slot[WARP] = {};
  std::barrier<> gate{WARP};
};

class Host {
  std::barrier<>& gate_;
  Warp* warps_;
  std::size_t t_;

  template<class T> T exchange(T v, std::size_t from) noexcept {
    Warp& w = warps_[t_ / WARP];
    w.slot[t_ % WARP] = bits(v);
    w.gate.arrive_and_wait();  // every thread of the warp has published
    const T got = unbits<T>(w.slot[from]);
    w.gate.arrive_and_wait();  // every thread has read before any slot changes again
    return got;
  }

public:
  unsigned char* const shared;
  Host(std::barrier<>& gate, Warp* warps, unsigned char* memory, std::size_t t) noexcept
      : gate_(gate), warps_(warps), t_(t), shared(memory) {}
  void sync() noexcept { gate_.arrive_and_wait(); }
  std::size_t lane() const noexcept { return t_ % WARP; }
  template<class T> T shuffle(T v, std::size_t from) noexcept {
    if(from >= WARP) trap();
    return exchange(v, from);
  }
  template<class T> T shuffle_xor(T v, std::size_t mask) noexcept {
    if(mask >= WARP) trap();
    return exchange(v, lane() ^ mask);
  }
  template<class T> T shuffle_down(T v, std::size_t delta) noexcept {
    if(delta >= WARP) trap();
    const std::size_t from = lane() + delta;
    return exchange(v, from < WARP ? from : lane());
  }
  template<class T, class Op> T reduce(T v, Op op) noexcept {
    for(unsigned m = WARP / 2; m; m /= 2) v = op(v, exchange(v, lane() ^ m));
    return v;
  }
};

template<unsigned THREADS, std::size_t BYTES> struct Team {
  alignas(16) unsigned char shared[BYTES ? BYTES : 16] = {};
  std::barrier<> gate{THREADS};
  Warp warps[THREADS / WARP];
};

template<unsigned THREADS, std::size_t BYTES, class F> inline void run(std::size_t grid, F body) noexcept {
  static_assert(THREADS % WARP == 0 && THREADS >= WARP && THREADS <= 1024, "a block runs whole warps");
  if(!grid) return;
  const std::size_t teams = grid < TEAMS ? grid : TEAMS;
  std::vector<std::unique_ptr<Team<THREADS, BYTES>>> held;
  for(std::size_t k = 0; k < teams; ++k) held.push_back(std::make_unique<Team<THREADS, BYTES>>());
  auto one = [&body, &held, grid, teams](std::size_t k, std::size_t t) noexcept {
    Team<THREADS, BYTES>& team = *held[k];
    Host context(team.gate, team.warps, team.shared, t);
    for(std::size_t b = k; b < grid; b += teams) {
      if(t == 0) std::memset(team.shared, 0, sizeof(team.shared));
      team.gate.arrive_and_wait();  // the block's memory is zeroed before any thread uses it
      body(context, b, t);
      team.gate.arrive_and_wait();  // every thread is done with it before the next block zeroes it
    }
  };
  struct Thread {
    decltype(one)* run;
    std::size_t k, t;
    void operator()() const noexcept { (*run)(k, t); }
  };
  std::vector<Thread> work;
  work.reserve(teams * THREADS);  // never reallocated: each thread holds the address of its own entry
  for(std::size_t k = 0; k < teams; ++k)
    for(std::size_t t = 0; t < THREADS; ++t) work.push_back(Thread{&one, k, t});
  std::vector<pthread_t> threads;
  threads.reserve(work.size());
  for(Thread& w : work) threads.push_back(start(&w));
  for(pthread_t id : threads) pthread_join(id, nullptr);
}

}  // namespace cr::coop
#endif

#if defined(__CUDACC__)
#include "cairn_gpu.hpp"

namespace cr::coop {

class Device {
  // What a shuffle moves: 32-bit and 64-bit values as themselves, a bool or anything narrower as 32 bits.
  template<class T, class S> __device__ static T through(T v, S step) {
    if constexpr(std::is_same_v<T, bool>) return step(static_cast<unsigned>(v)) != 0u;
    else if constexpr(sizeof(T) < 4) return static_cast<T>(step(static_cast<unsigned>(v)));
    else return step(v);
  }

public:
  unsigned char* const shared;
  __device__ explicit Device(unsigned char* memory) : shared(memory) {}
  __device__ void sync() const { __syncthreads(); }
  __device__ std::size_t lane() const { return threadIdx.x % WARP; }
  template<class T> __device__ T shuffle(T v, std::size_t from) const {
    if(from >= WARP) trap();
    return through(v, [=](auto x) { return __shfl_sync(0xffffffffu, x, int(from)); });
  }
  template<class T> __device__ T shuffle_xor(T v, std::size_t mask) const {
    if(mask >= WARP) trap();
    return through(v, [=](auto x) { return __shfl_xor_sync(0xffffffffu, x, int(mask)); });
  }
  template<class T> __device__ T shuffle_down(T v, std::size_t delta) const {
    if(delta >= WARP) trap();
    return through(v, [=](auto x) { return __shfl_down_sync(0xffffffffu, x, unsigned(delta)); });
  }
  template<class T, class Op> __device__ T reduce(T v, Op op) const {
    for(unsigned m = WARP / 2; m; m /= 2) v = op(v, shuffle_xor(v, m));
    return v;
  }
};

template<unsigned THREADS, std::size_t BYTES, class F> __global__ void __launch_bounds__(THREADS)
blocks(std::size_t grid, F body) {
  __shared__ __align__(16) unsigned char memory[BYTES ? BYTES : 16];
  Device context(memory);
  const std::size_t t = threadIdx.x;
  for(std::size_t b = blockIdx.x; b < grid; b += gridDim.x) {  // the same trip count for the whole block
    for(std::size_t e = t; e < BYTES; e += THREADS) memory[e] = 0;
    __syncthreads();
    body(context, b, t);
    __syncthreads();  // every thread is done with the memory before the next block zeroes it
  }
}

// On the calling thread's execution context (cairn_exec.hpp), returning once its stream has run the region.
template<unsigned THREADS, std::size_t BYTES, class F>
inline void launch(gpu::Context& ctx, std::size_t grid, F body) noexcept {
  static_assert(THREADS % WARP == 0 && THREADS >= WARP && THREADS <= 1024, "a block runs whole warps");
  static_assert(BYTES <= 48 * 1024, "static shared memory holds 48 KiB");
  static_assert(std::is_trivially_copyable_v<F>, "the body crosses over as a kernel argument");
  if(!grid) return;
  const unsigned g = grid < gpu::MAX_GRID ? unsigned(grid) : gpu::MAX_GRID;
  reuse::synchronous(ctx, [&](cudaStream_t s) {
    blocks<THREADS, BYTES><<<g, THREADS, 0, s>>>(grid, body);
    gpu::check(cudaGetLastError());
  });
}

}  // namespace cr::coop
#endif

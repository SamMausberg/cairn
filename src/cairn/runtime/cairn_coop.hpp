// Cooperative regions (compiler/cooperative/cooperative.py): `blocks b in G threads t in T { }` runs G blocks of T threads, the
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
//
// A region with a finish runs it once, after every block: on the host as one more team, on the device in the block that
// finishes last, still one launch (compiler/cooperative/finish.py). The device counts its finished blocks in a word of a table in
// the module's own global memory, so a call allocates nothing for it (Finishes, below).
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <tuple>
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

// Pipeline stages (compiler/cooperative/pipelines.py): D stages of S elements in the block's shared memory, filled in ring order
// and read in the same order. The checker has shown every fill lands in a stage nobody still reads and every read
// follows its stage's wait, so the stage an operation means is the count of fills, or of waits, modulo D. Each thread
// keeps both counts, and since every thread reaches every operation, they agree.
template<class T, std::size_t S, std::size_t D> class Stages {
  static constexpr std::size_t STRIDE = (S * sizeof(T) + 15) / 16 * 16 / sizeof(T);  // each stage on 16 bytes
  T* base_;
  std::size_t filled_ = 0, waited_ = 0;

public:
  CR_HD explicit Stages(unsigned char* memory) noexcept : base_(reinterpret_cast<T*>(memory)) {}
  // The next stage receives from[start .. start + count) and zeros past count; each thread copies its own elements.
  template<class Block> CR_HD void fill(Block& block, const T* from, std::size_t length, std::size_t start,
                                        std::size_t count) noexcept {
    if(count > S || start > length || count > length - start) trap();
    T* to = base_ + (filled_ % D) * STRIDE;
    for(std::size_t e = block.thread(); e < S; e += block.threads())
      block.copy(to + e, from + start + (e < count ? e : 0), e < count);
    block.commit();
    ++filled_;
  }
  // The oldest stage in flight has landed, in every thread; at most PENDING later fills stay in flight.
  template<std::size_t PENDING, class Block> CR_HD const T* wait(Block& block) noexcept {
    block.template drain<PENDING>();
    block.sync();
    return base_ + (waited_++ % D) * STRIDE;
  }
};

// The word a launch with a finish counts its finished blocks in holds the launch's tag, a number no other launch has,
// above the count of blocks that have arrived; it is zero while no launch holds it. The first block to arrive claims
// it, the others add one under the same tag, and the block that brings the count to the grid's is the last, which
// puts the word back to zero once the finish has run. A block that finds another launch's tag there traps rather than
// count with it: Finishes gives two launches one word only when their stream keeps them apart, so that happens only
// after it gave the word of a stream used long ago to another while that stream's last launch was still running.
inline constexpr unsigned COUNTED = 20;  // the low bits count blocks, of which a launch has at most 65535
inline constexpr std::uint64_t TAGS = std::uint64_t(1) << (64 - COUNTED);
inline constexpr unsigned SLOTS = 4096;  // words in the table, 32 KiB of each module's global memory

CR_HD inline bool arrive(unsigned long long* word, std::uint64_t tag, unsigned grid) noexcept {
  const unsigned long long mine = static_cast<unsigned long long>(tag) << COUNTED, count = (1ull << COUNTED) - 1;
#if defined(__CUDA_ARCH__)
  unsigned long long old = atomicCAS(word, 0ull, mine + 1);
  if(old != 0 && old >> COUNTED == tag) old = atomicAdd(word, 1ull);
#else
  std::atomic_ref<unsigned long long> held(*word);
  unsigned long long old = 0;
  if(!held.compare_exchange_strong(old, mine + 1, std::memory_order_acq_rel) && old >> COUNTED == tag)
    old = held.fetch_add(1, std::memory_order_acq_rel);
#endif
  if(old != 0 && old >> COUNTED != tag) trap();  // another launch still counts in this word
  return (old & count) + 1 == grid;
}
CR_HD inline void depart(unsigned long long* word) noexcept {
#if defined(__CUDA_ARCH__)
  atomicExch(word, 0ull);
#else
  std::atomic_ref<unsigned long long>(*word).store(0, std::memory_order_release);
#endif
}

struct Claim {
  unsigned slot;      // the word, in the table of SLOTS
  std::uint64_t tag;  // this launch's, never another's
};

// Which word each launch with a finish counts in. Launches queued on one stream run one after another, so each stream
// keeps a word of its own, named by the id CUDA gives a stream for the life of the process. A captured launch keeps
// a word for its stream within that capture sequence, and keeps it for good: its CUDA graph replays the launch, word
// and tag included, whenever and on whatever stream it is launched, and the graph's own launches run one after
// another. Two instances of one graph running at once share the word, as they share every array they write. When
// every word is held, the stream that claimed one longest ago gives its word up; when every word belongs to a
// capture, the process has captured more launches with a finish than the table holds, and traps.
template<unsigned N> class Finishes final {
public:
  Claim claim(unsigned long long stream, bool captured, unsigned long long capture) noexcept {
    const std::lock_guard<std::mutex> hold(lock_);
    if(++tags_ == TAGS) trap();
    const Key key{stream, captured, captured ? capture : 0};
    auto found = held_.find(key);
    if(found == held_.end()) found = held_.emplace(key, Held{free(), 0}).first;
    found->second.used = tags_;
    return {found->second.slot, tags_};
  }

private:
  using Key = std::tuple<unsigned long long, bool, unsigned long long>;  // stream, captured, capture sequence
  struct Held {
    unsigned slot;
    std::uint64_t used;  // the tag of its last launch
  };
  unsigned free() noexcept {
    if(made_ < N) return made_++;
    auto oldest = held_.end();
    for(auto it = held_.begin(); it != held_.end(); ++it)
      if(!std::get<1>(it->first) && (oldest == held_.end() || it->second.used < oldest->second.used)) oldest = it;
    if(oldest == held_.end()) {
      std::fputs("cairn: every counter of a region with a finish belongs to a captured graph\n", stderr);
      trap();
    }
    const unsigned slot = oldest->second.slot;
    held_.erase(oldest);
    return slot;
  }
  std::mutex lock_;
  std::map<Key, Held> held_;
  unsigned made_ = 0;
  std::uint64_t tags_ = 0;
};
inline Finishes<SLOTS>& finishes() noexcept {
  static Finishes<SLOTS>* const table = new Finishes<SLOTS>;  // never destroyed: a thread may claim during exit
  return *table;
}
// The claim for a launch on `stream` of the machine `api` (cairn_gpu.hpp, or a host stand-in).
template<class Api> inline Claim claim(Api& api, typename Api::Stream stream) noexcept {
  unsigned long long capture = 0;
  const bool captured = api.capturing(stream, &capture);
  // A capture refuses the stream's id, so a captured launch names its stream by its handle: no two streams alive in
  // one capture sequence share one, and the key holds the sequence too.
  return finishes().claim(captured ? api.handle(stream) : api.stream_id(stream), captured, capture);
}
// A launch with a finish, queued as synchronous work of the execution context `ctx` (cairn_reuse.hpp, found by
// argument lookup): `fire(stream, claim)` launches it on the lane's stream with the word claimed there. The device
// launch below and the host stand-in (tests/runtime/coop_host.hpp) both come through here, so what the stand-in
// counts a call making, and not making, is what the launch makes.
template<class Context, class Fire> inline void queue_then(Context& ctx, Fire fire) noexcept {
  synchronous(ctx, [&](auto stream) { fire(stream, claim(ctx.api(), stream)); });
}
}  // namespace cr::coop

#if !defined(__CUDA_ARCH__)
#include <barrier>
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

// One warp's exchange: two banks of each thread's slot, which its exchanges use in turn, and the barrier its 32
// threads meet at once each has published. A thread writes a bank again two exchanges later, past the barrier of the
// exchange between, which no thread reaches before it has read that bank: so one barrier an exchange is enough.
struct Warp {
  std::uint64_t slot[2][WARP] = {};
  std::barrier<> gate{WARP};
};

class Host {
  std::barrier<>& gate_;
  Warp* warps_;
  std::size_t t_, count_;
  unsigned bank_ = 0;  // the bank this thread's last exchange used; the warp's threads exchange in step

  // `word` published as this thread's slot, once every thread of the warp has published its own: that bank.
  const std::uint64_t* published(std::uint64_t word) noexcept {
    Warp& w = warps_[t_ / WARP];
    bank_ ^= 1;
    w.slot[bank_][t_ % WARP] = word;
    w.gate.arrive_and_wait();
    return w.slot[bank_];
  }
  template<class T> T exchange(T v, std::size_t from) noexcept { return unbits<T>(published(bits(v))[from]); }

public:
  unsigned char* const shared;
  Host(std::barrier<>& gate, Warp* warps, unsigned char* memory, std::size_t t, std::size_t count) noexcept
      : gate_(gate), warps_(warps), t_(t), count_(count), shared(memory) {}
  void sync() noexcept { gate_.arrive_and_wait(); }
  std::size_t lane() const noexcept { return t_ % WARP; }
  std::size_t thread() const noexcept { return t_; }
  std::size_t threads() const noexcept { return count_; }
  // A stage element lands at once, copied by this thread; the wait's barrier is what makes it every thread's.
  template<class T> void copy(T* to, const T* from, bool inside) noexcept { *to = inside ? *from : T{}; }
  void commit() noexcept {}
  template<std::size_t PENDING> void drain() noexcept {}
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
  template<class T> T shuffle_up(T v, std::size_t delta) noexcept {
    if(delta >= WARP) trap();
    return exchange(v, lane() >= delta ? lane() - delta : lane());
  }
  // The butterfly the device runs, five shuffles of v = op(v, partner's v), worked out by each thread for every lane
  // from one exchange of the starting values: the same operations in the same order, so the same bits, and one
  // barrier in place of five.
  template<class T, class Op> T reduce(T v, Op op) noexcept {
    const std::uint64_t* slots = published(bits(v));
    T lanes[WARP], next[WARP];
    for(unsigned l = 0; l < WARP; ++l) lanes[l] = unbits<T>(slots[l]);
    for(unsigned m = WARP / 2; m; m /= 2) {
      for(unsigned l = 0; l < WARP; ++l) next[l] = op(lanes[l], lanes[l ^ m]);
      for(unsigned l = 0; l < WARP; ++l) lanes[l] = next[l];
    }
    return lanes[lane()];
  }
  // A vote: every thread of the warp publishes a word and reads all 32, and bit l of the answer is `same(lane l's)`.
  template<class Same> std::uint32_t gather(std::uint64_t mine, Same same) noexcept {
    const std::uint64_t* slots = published(mine);
    std::uint32_t found = 0;
    for(unsigned l = 0; l < WARP; ++l) found |= std::uint32_t(same(slots[l])) << l;
    return found;
  }
  std::uint32_t warp_ballot(bool c) noexcept { return gather(c, [](std::uint64_t s) { return s != 0; }); }
  bool warp_any(bool c) noexcept { return warp_ballot(c) != 0; }
  bool warp_all(bool c) noexcept { return warp_ballot(c) == 0xffffffffu; }
  template<class T> std::uint32_t warp_match(T v) noexcept {
    const std::uint64_t b = bits(v);
    return gather(b, [b](std::uint64_t s) { return s == b; });
  }
};

template<unsigned THREADS, std::size_t BYTES> struct Team {
  alignas(128) unsigned char shared[BYTES ? BYTES : 16] = {};
  std::barrier<> gate{THREADS};
  Warp warps[THREADS / WARP];
};

// ZERO: the leading bytes a block's start zeroes; the arrays declared without `= zeroed` lie past them. The host fills
// those with a pattern where each block starts, so a read the checker let through before a write would show.
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, class F>
inline void run(std::size_t grid, F body) noexcept {
  static_assert(THREADS % WARP == 0 && THREADS >= WARP && THREADS <= 1024, "a block runs whole warps");
  if(!grid) return;
  const std::size_t teams = grid < TEAMS ? grid : TEAMS;
  std::vector<std::unique_ptr<Team<THREADS, BYTES>>> held;
  for(std::size_t k = 0; k < teams; ++k) held.push_back(std::make_unique<Team<THREADS, BYTES>>());
  auto one = [&body, &held, grid, teams](std::size_t k, std::size_t t) noexcept {
    Team<THREADS, BYTES>& team = *held[k];
    Host context(team.gate, team.warps, team.shared, t, THREADS);
    for(std::size_t b = k; b < grid; b += teams) {
      if(t == 0) {
        if constexpr(ZERO > 0) std::memset(team.shared, 0, ZERO);
        if constexpr(BYTES > ZERO) std::memset(team.shared + ZERO, 0x7f, BYTES - ZERO);
      }
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

// A region with a finish (compiler/cooperative/finish.py): every block, then, once every block's threads have been joined, one
// team of the same threads runs the finish, as block 0 of a grid of one. It runs when the grid is empty too.
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, std::size_t FINISH = BYTES, class F, class G>
inline void run_then(std::size_t grid, F body, G finish) noexcept {
  run<THREADS, BYTES, ZERO>(grid, body);
  run<THREADS, BYTES, FINISH>(1, finish);
}

}  // namespace cr::coop
#endif

#if defined(__CUDACC__)
#include <cuda_pipeline.h>
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
  __device__ std::size_t thread() const { return threadIdx.x; }
  __device__ std::size_t threads() const { return blockDim.x; }
  // One asynchronous copy of an element into the stage, or its size in zeros, in this thread's current group.
  template<class T> __device__ void copy(T* to, const T* from, bool inside) const {
    static_assert(sizeof(T) == 4 || sizeof(T) == 8, "cp.async moves 4, 8 or 16 bytes");
    __pipeline_memcpy_async(to, from, sizeof(T), inside ? 0 : sizeof(T));
  }
  __device__ void commit() const { __pipeline_commit(); }
  template<std::size_t PENDING> __device__ void drain() const { __pipeline_wait_prior(PENDING); }
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
  template<class T> __device__ T shuffle_up(T v, std::size_t delta) const {
    if(delta >= WARP) trap();
    return through(v, [=](auto x) { return __shfl_up_sync(0xffffffffu, x, unsigned(delta)); });
  }
  template<class T, class Op> __device__ T reduce(T v, Op op) const {
    for(unsigned m = WARP / 2; m; m /= 2) v = op(v, shuffle_xor(v, m));
    return v;
  }
  __device__ std::uint32_t warp_ballot(bool c) const { return __ballot_sync(0xffffffffu, c); }
  __device__ bool warp_any(bool c) const { return __any_sync(0xffffffffu, c) != 0; }
  __device__ bool warp_all(bool c) const { return __all_sync(0xffffffffu, c) != 0; }
  template<class T> __device__ std::uint32_t warp_match(T v) const {
    if constexpr(std::is_same_v<T, std::size_t>) return __match_any_sync(0xffffffffu, (unsigned long long)v);
    else return __match_any_sync(0xffffffffu, v);
  }
};

template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO, class F> __global__ void __launch_bounds__(THREADS)
blocks(std::size_t grid, F body) {
  __shared__ __align__(128) unsigned char memory[BYTES ? BYTES : 16];
  Device context(memory);
  const std::size_t t = threadIdx.x;
  for(std::size_t b = blockIdx.x; b < grid; b += gridDim.x) {  // the same trip count for the whole block
    if constexpr(ZERO > 0)  // a region without zeroed arrays zeroes nothing; nvcc refuses the comparison with 0
      for(std::size_t e = t; e < ZERO; e += THREADS) memory[e] = 0;
    __syncthreads();
    body(context, b, t);
    __pipeline_wait_prior(0);  // a stage's fills still in flight land before the memory is zeroed again
    __syncthreads();  // every thread is done with the memory before the next block zeroes it
  }
}

// The words launches with a finish count their blocks in: the module's global memory, zero when it loads.
__device__ inline unsigned long long* counters() {
  static unsigned long long words[SLOTS];
  return words;
}

// A region with a finish, as one launch. Each block, once it has run its share of the grid, counts itself in its
// launch's word: its threads' writes are ordered before thread 0's fence by the loop's last barrier, the fence makes
// them visible device-wide, and the atomic add counts the block, as cooperative groups' grid barrier does. The block
// that brings the count to the grid's knows every other block has finished; after a second fence it zeroes its shared
// memory, runs the finish as block 0 of a grid of one, and puts the word back to zero for the next launch.
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO, std::size_t FINISH, class F, class G>
__global__ void __launch_bounds__(THREADS)
blocks_then(std::size_t grid, F body, G finish, unsigned slot, std::uint64_t tag) {
  __shared__ __align__(128) unsigned char memory[BYTES ? BYTES : 16];
  __shared__ bool last;
  Device context(memory);
  const std::size_t t = threadIdx.x;
  if(slot >= SLOTS) trap();
  unsigned long long* const word = counters() + slot;
  for(std::size_t b = blockIdx.x; b < grid; b += gridDim.x) {
    if constexpr(ZERO > 0)
      for(std::size_t e = t; e < ZERO; e += THREADS) memory[e] = 0;
    __syncthreads();
    body(context, b, t);
    __pipeline_wait_prior(0);
    __syncthreads();
  }
  if(t == 0) {
    __threadfence();  // this block's writes, every thread's, before it is counted
    last = arrive(word, tag, gridDim.x);
    if(last) __threadfence();  // every other block's writes, before the finish reads them
  }
  __syncthreads();
  if(!last) return;
  if constexpr(FINISH > 0)
    for(std::size_t e = t; e < FINISH; e += THREADS) memory[e] = 0;
  __syncthreads();
  finish(context, 0, t);
  __pipeline_wait_prior(0);
  if(t == 0) depart(word);  // every block has counted itself: nothing else touches it before this launch ends
}

// On the calling thread's execution context (cairn_exec.hpp), returning once its stream has run the region.
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, class F>
inline void launch(gpu::Context& ctx, std::size_t grid, F body) noexcept {
  static_assert(THREADS % WARP == 0 && THREADS >= WARP && THREADS <= 1024, "a block runs whole warps");
  static_assert(BYTES <= 48 * 1024, "static shared memory holds 48 KiB");
  static_assert(std::is_trivially_copyable_v<F>, "the body crosses over as a kernel argument");
  if(!grid) return;
  const unsigned g = grid < gpu::MAX_GRID ? unsigned(grid) : gpu::MAX_GRID;
  reuse::synchronous(ctx, [&](cudaStream_t s) {
    blocks<THREADS, BYTES, ZERO><<<g, THREADS, 0, s>>>(grid, body);
    gpu::check(cudaGetLastError());
  });
}
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, std::size_t FINISH = BYTES, class F, class G>
inline void launch_then(gpu::Context& ctx, std::size_t grid, F body, G finish) noexcept {
  static_assert(THREADS % WARP == 0 && THREADS >= WARP && THREADS <= 1024, "a block runs whole warps");
  static_assert(BYTES <= 48 * 1024, "static shared memory holds 48 KiB");
  static_assert(std::is_trivially_copyable_v<F> && std::is_trivially_copyable_v<G>, "the bodies cross as arguments");
  const unsigned g = grid < 1 ? 1u : grid < gpu::MAX_GRID ? unsigned(grid) : gpu::MAX_GRID;  // the finish runs once
  queue_then(ctx, [&](cudaStream_t s, Claim held) {
    blocks_then<THREADS, BYTES, ZERO, FINISH><<<g, THREADS, 0, s>>>(grid, body, finish, held.slot, held.tag);
    gpu::check(cudaGetLastError());
  });
}

}  // namespace cr::coop
#endif

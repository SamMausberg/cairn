// CAIRN host parallelism: a lane pool the first region builds, mutexes, atomics; tickets are in cairn_tasks.hpp.
// The first `parallel` statement of a process creates the lanes; every later one reuses them, so
// a region costs a publication and a wake rather than a thread. The pool is built on first use
// and never destroyed: an exit handler stops and joins its threads, after which a region still
// runs correctly on the thread that started it. Pairs with cairn_gpu.hpp, which runs the same
// lambda body on device lanes.
#pragma once
#if defined(CAIRN_FREESTANDING)
#error "cairn_parallel.hpp is hosted: a freestanding image has no threads, and toolchain.audit_effects rejects every effect that reaches this header."
#endif
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <new>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>
#include "cairn_runtime.hpp"
namespace cr::par {

// The lane pool. Its whole job is coverage and completion: the language has already proved the
// lanes race free, so nothing here orders one lane against another.
namespace lanes {

// GRAIN is the fewest elements worth handing to another thread and CUTOFF = 2*GRAIN is therefore
// the smallest region that can use more than one lane; below it `run` is the loop it replaces, so
// a small region neither publishes nor wakes anybody. SPLIT claims per lane let an uneven body
// balance itself. Chosen by measurement on a 64-core GH200 (evidence/v0_8_2/host_regions): engaging
// one more lane cost about 0.45us there, and 8192 elements of the cheapest body measured (a
// vectorized saxpy, about 0.11ns an element) is about twice that, so a lane always earns its
// keep. Larger grains helped that cheapest body more and cost a dearer one its win at thirty
// thousand elements; smaller grains made every cheap region slower than the loop. Above four
// claims a lane the shared claim counter began to cost more than the balance it bought.
inline constexpr std::size_t GRAIN = 8192;
inline constexpr std::size_t SPLIT = 2;
inline constexpr std::size_t CUTOFF = 2 * GRAIN;
// How long a thread waits before it pays for a futex: workers between regions, submitters waiting
// for a straggling lane. Reading a shared line costs nothing while nobody writes it, so the first
// spell is plain reads; the yields after it hand the core over on a machine with more lanes than
// cores. Both are bounded, so a thread that is not wanted always ends up asleep. SPIN_READS was
// measured at about a third of a nanosecond each here, so a worker stays hot for roughly sixty
// microseconds after a region: long enough to bridge regions written in a loop, and waking one
// that has gone cold was measured at about twenty-five microseconds.
inline constexpr unsigned SPIN_READS = 200000;
inline constexpr unsigned SPIN_YIELDS = 8;
inline constexpr std::size_t MOST_LANES = 1024;  // A validated CAIRN_LANES cannot exceed this.
// How many indices of `weight` elements each make up `elements` elements' work, rounded up; weight is at least 1.
inline constexpr std::size_t indices(std::size_t elements, std::size_t weight) noexcept {
  return weight >= elements ? 1 : (elements + weight - 1) / weight;
}

// Wait for `ready` without a syscall, then with a few, then give up. Sixty-four threads calling
// sched_yield between regions was measured to cost more than the regions they were waiting for,
// and so was an acquiring load in the spin itself: `ready` reads relaxed and is only a hint, so
// whoever acts on a true answer takes the mutex or reads again with acquire. The signal fence
// emits no instruction; it is what stops a compiler hoisting the whole spin into one load, which
// clang was measured to do.
template<class P> inline bool linger(P ready, unsigned reads = SPIN_READS) noexcept {
  for(unsigned turn = 0; turn < reads; ++turn) {
    if(ready()) return true;
    std::atomic_signal_fence(std::memory_order_seq_cst);
  }
  for(unsigned turn = 0; turn < SPIN_YIELDS; ++turn) {
    if(ready()) return true;
    std::this_thread::yield();
  }
  return ready();
}

// A whole chunk crosses the type boundary, never one index: inside the thunk the lane body is an
// ordinary inlined call in an ordinary loop, so a region's elements cost what the loop it replaces
// costs. Erasing per index instead was measured to add about a third of a nanosecond to every one.
using Call = void (*)(void*, std::size_t, std::size_t) noexcept;

// One berth per worker, so a region wakes the lanes it can use and not the ones it cannot. A
// single shared condition variable cannot choose whom it wakes: notify_all was measured to cost a
// two-lane region ninety microseconds once the other sixty-two workers had gone to sleep, because
// every one of them woke and queued for the one mutex. Each berth is a cache line of its own.
struct alignas(64) Berth {
  std::mutex m;
  std::condition_variable cv;
  std::atomic<bool> parked{false};
};

// A region's indices are cut into one home per lane it can use, each with a counter of its own. A lane claims
// from its own home first and then from what is left of the others, so a lane that runs the same region again
// runs the same indices again, from its own core's cache, while the home of a lane that is busy or asleep is
// still taken by whoever is free. With one counter for the whole region, which lane claimed which chunk changed
// from one region to the next, and saxpy over a million floats ran 2.8 times slower than OpenMP's static schedule,
// which keeps each thread on one range (evidence/v1_0/bench). proofs/Cairn/Region.lean is this protocol.
inline constexpr std::size_t HOMES = 64;
// Where home h begins, of `homes` cut from [0, n): the homes are consecutive, differ in length by at most one,
// and home `homes` begins at n.
inline constexpr std::size_t cut(std::size_t n, std::size_t homes, std::size_t h) noexcept {
  return n / homes * h + (h < n % homes ? h : n % homes);
}
// A home's counter is read and bumped through std::atomic_ref, so a region's unused homes stay unwritten: only
// the ones a region uses are set, before it is published.
struct alignas(64) Home {
  std::size_t next;  // the next index of this home no lane has claimed
  std::size_t end;   // one past the home's last index
  std::size_t claim(std::size_t grain) noexcept {
    return std::atomic_ref<std::size_t>(next).fetch_add(grain, std::memory_order_relaxed);
  }
  bool open() noexcept { return std::atomic_ref<std::size_t>(next).load(std::memory_order_relaxed) < end; }
};

// One region, on the stack of the thread that started it. Workers reach it only while they hold a
// reference (`users`), and the submitter returns only once that count is back to zero, so the
// frame outlives every thread that can touch it. Nothing here is allocated.
struct Region {
  Call call = nullptr;
  void* body = nullptr;
  std::size_t grain = 1;
  std::size_t helpers = 0;  // workers 0..helpers-1 may join; the rest leave a small region alone
  std::size_t homes = 1;    // homes 0..homes-1 are in use
  Region* older = nullptr;  // regions started by other threads, newest first; guarded by m_
  std::atomic<std::size_t> users{0};  // workers currently inside this region
  Home home[HOMES];
  bool open() noexcept {  // Is any index unclaimed? A stale answer costs a worker one look, never an index.
    for(std::size_t h = 0; h < homes; ++h)
      if(home[h].open()) return true;
    return false;
  }
};

class Pool final {
public:
  Pool() noexcept = default;
  Pool(const Pool&) = delete;
  Pool& operator=(const Pool&) = delete;
  Pool(Pool&&) = delete;
  Pool& operator=(Pool&&) = delete;
  std::size_t lanes() const noexcept { return lanes_.load(std::memory_order_acquire); }

  // CAIRN_LANES fixes the lane count for a test or a reproducible measurement. It is a plain
  // decimal count from 1 to MOST_LANES; anything else is a mistake the process should not survive.
  static std::size_t configured() noexcept {
    const char* text = std::getenv("CAIRN_LANES");
    if(text == nullptr || *text == '\0') {
      const unsigned found = std::thread::hardware_concurrency();
      return found != 0 ? std::size_t(found) : std::size_t(1);
    }
    std::size_t want = 0;
    for(const char* p = text; *p != '\0'; ++p) {
      if(*p < '0' || *p > '9') trap();
      want = want * 10 + std::size_t(*p - '0');
      if(want > MOST_LANES) trap();
    }
    if(want == 0) trap();
    return want;
  }

  void start() noexcept {
    const std::size_t want = configured();
    lanes_.store(want, std::memory_order_release);
    if(want < 2) return;  // one lane: every region runs on the thread that started it
    berths_ = want - 1;
    berth_ = new(std::nothrow) Berth[berths_];
    if(berth_ == nullptr) trap();
    crew_.reserve(berths_);
    for(std::size_t k = 0; k < berths_; ++k) crew_.emplace_back([this, k]() noexcept { serve(k); });
  }

  // Stop and join the workers. Called from an exit handler, and safe to call while other threads
  // are inside a region: a worker leaves only between chunks, and its submitter finishes the rest.
  void retire() noexcept {
    quit_.store(true, std::memory_order_seq_cst);
    rouse(berths_);
    for(std::thread& worker : crew_) worker.join();
    crew_.clear();
    lanes_.store(1, std::memory_order_release);  // a region started after this runs where it began
  }

  // Run one region. The submitting thread is always one of the lanes and can finish alone, so no
  // arrangement of busy, blocked or absent workers can deadlock a region.
  void execute(Region& region) noexcept {
    publish(region);
    drain(region, 0, region.homes - 1);  // home 0, then backward: the last homes' workers are woken last
    withdraw(region);
    settle(region);
  }

private:
  void publish(Region& region) noexcept {
    {
      std::lock_guard<std::mutex> hold(m_);
      region.older = head_;
      head_ = &region;
      open_.fetch_add(1, std::memory_order_relaxed);
      if(region.helpers > wanted_.load(std::memory_order_relaxed))
        wanted_.store(region.helpers, std::memory_order_release);
      // Sequentially consistent, and so is the parked flag it is paired with in rouse(): together
      // they are what stops a worker deciding to sleep just as a region it wanted arrives.
      epoch_.store(epoch_.load(std::memory_order_relaxed) + 1, std::memory_order_seq_cst);
    }
    rouse(region.helpers);
  }

  // Wake the first `many` workers, and only those that are actually asleep. Taking a berth's own
  // mutex before notifying holds off until a worker that has decided to sleep is inside wait().
  void rouse(std::size_t many) noexcept {
    if(many > berths_) many = berths_;
    for(std::size_t k = 0; k < many; ++k) {
      if(!berth_[k].parked.load(std::memory_order_seq_cst)) continue;
      berth_[k].m.lock();
      berth_[k].m.unlock();
      berth_[k].cv.notify_one();
    }
  }

  // Unlink first, then wait: after this no worker can join the region, so `users` only falls.
  void withdraw(Region& region) noexcept {
    std::lock_guard<std::mutex> hold(m_);
    for(Region** link = &head_; *link != nullptr; link = &(*link)->older)
      if(*link == &region) {
        *link = region.older;
        open_.fetch_sub(1, std::memory_order_relaxed);
        break;
      }
    std::size_t most = 0;
    for(Region* other = head_; other != nullptr; other = other->older)
      if(other->helpers > most) most = other->helpers;
    wanted_.store(most, std::memory_order_release);
  }

  // Wait for the workers still inside. A short spell of yielding covers the ordinary case, where
  // the stragglers are finishing a chunk; a lane body that blocks or takes its time pays a futex.
  void settle(Region& region) noexcept {
    if(linger([&region] { return region.users.load(std::memory_order_relaxed) == 0; }) &&
       region.users.load(std::memory_order_acquire) == 0)
      return;
    std::unique_lock<std::mutex> hold(m_);
    resting_.fetch_add(1, std::memory_order_seq_cst);
    rest_.wait(hold, [&region] { return region.users.load(std::memory_order_seq_cst) == 0; });
    resting_.fetch_sub(1, std::memory_order_relaxed);
  }

  Region* adopt(std::size_t k) noexcept {
    std::lock_guard<std::mutex> hold(m_);
    for(Region* region = head_; region != nullptr; region = region->older)
      if(k < region->helpers && region->open()) {
        region->users.fetch_add(1, std::memory_order_relaxed);  // m_ orders this against withdraw
        return region;
      }
    return nullptr;
  }

  // The last touch of the region: once `users` reaches zero the submitter may return and its frame
  // is gone, so everything below reads pool state only. The two seq_cst operations pair with the
  // two in settle(), so a submitter that decides to sleep cannot be left unwoken.
  void leave(Region& region) noexcept {
    if(region.users.fetch_sub(1, std::memory_order_seq_cst) != 1) return;
    if(resting_.load(std::memory_order_seq_cst) == 0) return;
    m_.lock();  // hold off until a submitter that has decided to sleep is actually inside wait()
    m_.unlock();
    rest_.notify_all();
  }

  // Claim chunks and run them until every home is used up, taking the homes in the order first, first + step,
  // first + 2 * step and so on around the region. Used by the submitter and by every worker.
  static void drain(Region& region, std::size_t first, std::size_t step) noexcept {
    for(std::size_t r = 0, h = first; r < region.homes; ++r, h = (h + step) % region.homes) {
      Home& home = region.home[h];
      while(home.open()) {
        const std::size_t begin = home.claim(region.grain);
        if(begin >= home.end) break;
        region.call(region.body, begin, home.end - begin < region.grain ? home.end : begin + region.grain);
      }
    }
  }

  void serve(std::size_t k) noexcept {
    for(;;) {
      const std::uint64_t seen = epoch_.load(std::memory_order_acquire);
      if(k < wanted_.load(std::memory_order_acquire)) {
        if(Region* region = adopt(k)) {
          drain(*region, (k + 1) % region->homes, 1);  // its own home, the one after the submitter's for worker 0
          leave(*region);
          // Look again only if there can be something to find. Missing work is never wrong -- the
          // thread that started a region can always finish it -- and this halves how often sixty
          // workers take the one lock, which was the largest cost a wide region paid.
          if(epoch_.load(std::memory_order_relaxed) != seen || open_.load(std::memory_order_relaxed) > 1)
            continue;
        }
      }
      if(quit_.load(std::memory_order_acquire)) return;
      if(linger([this, seen] {
           return epoch_.load(std::memory_order_relaxed) != seen || quit_.load(std::memory_order_relaxed);
         }))
        continue;
      Berth& berth = berth_[k];
      std::unique_lock<std::mutex> hold(berth.m);
      berth.parked.store(true, std::memory_order_seq_cst);
      berth.cv.wait(hold, [this, seen] {
        return epoch_.load(std::memory_order_seq_cst) != seen || quit_.load(std::memory_order_seq_cst);
      });
      berth.parked.store(false, std::memory_order_relaxed);
    }
  }

  std::mutex m_;
  std::condition_variable rest_;   // a submitter waiting for a straggling lane sleeps here
  Region* head_ = nullptr;         // active regions, newest first; guarded by m_
  std::vector<std::thread> crew_;  // two allocations, at the first region of the process
  Berth* berth_ = nullptr;         // one per worker; never freed, because the pool never is
  std::size_t berths_ = 0;
  std::atomic<std::size_t> lanes_{1};
  // Every idle worker spins on epoch_, so it is kept off the line the mutex and the list write.
  alignas(64) std::atomic<std::uint64_t> epoch_{0};  // bumped by every publication
  std::atomic<std::size_t> wanted_{0};               // most helpers any live region asked for
  std::atomic<std::size_t> open_{0};                 // regions currently linked
  std::atomic<std::size_t> resting_{0};              // submitters inside rest_.wait
  std::atomic<bool> quit_{false};
};

// Built by the first region that can use it and never destroyed, so no static destroyed later can
// find it gone. The exit handler only stops the threads; a region started after that still runs.
inline Pool& shared() noexcept {
  static Pool* const one = []() noexcept {
    Pool* made = new(std::nothrow) Pool();
    if(made == nullptr) trap();
    made->start();
    if(std::atexit(+[]() noexcept { shared().retire(); }) != 0) trap();
    return made;
  }();
  return *one;
}
} // namespace lanes

// Everything a wide region needs, kept out of run() so that a region below the cutoff compiles to
// the loop it replaces: only this function takes the body's address, and a body whose address is
// never taken stays in registers.
// `weight` is how many elements' work one index stands for: 1 for a lane, a block's length for a lane that owns
// a block or for a reduction's block. A claim covers at least GRAIN elements' work, and at most a SPLIT-th share.
// A plan's `grain` replaces that floor with a count of indices, and its `most` caps the lanes.
template<class F> void run_wide(std::size_t n, F& body, std::size_t weight = 1, std::size_t grain = 0,
                                std::size_t most = 0) noexcept {
  using Body = std::remove_reference_t<F>;
  lanes::Pool& pool = lanes::shared();
  const std::size_t least = grain ? grain : lanes::indices(lanes::GRAIN, weight);  // one claim's indices
  std::size_t use = n / least;
  if(use > pool.lanes()) use = pool.lanes();
  if(most && use > most) use = most;
  if(use < 2) {
    for(std::size_t i = 0; i < n; ++i) body(i);
    return;
  }
  lanes::Region region;
  region.call = [](void* held, std::size_t begin, std::size_t end) noexcept {
    Body& lane = *static_cast<Body*>(held);
    for(std::size_t i = begin; i < end; ++i) lane(i);
  };
  region.body = const_cast<void*>(static_cast<const void*>(std::addressof(body)));
  region.helpers = use - 1;
  region.grain = n / (use * lanes::SPLIT);
  if(region.grain < least) region.grain = least;
  region.homes = use < lanes::HOMES ? use : lanes::HOMES;
  for(std::size_t h = 0; h < region.homes; ++h)
    region.home[h] = {lanes::cut(n, region.homes, h), lanes::cut(n, region.homes, h + 1)};
  pool.execute(region);
}

// parallel i in n: body(i) runs once for every i below n, and run returns when all of them have.
// Which lane runs which index is not promised; the language has already made that unobservable.
// A region below the cutoff is exactly the loop it replaces: nothing is published and nothing woken.
template<class F>
inline void run(std::size_t n, F&& body, std::size_t weight = 1, std::size_t grain = 0, std::size_t most = 0) noexcept {
  if(n < 2 || most == 1 || (!grain && n < lanes::indices(lanes::CUTOFF, weight))) {
    for(std::size_t i = 0; i < n; ++i) body(i);
    return;
  }
  run_wide(n, body, weight, grain, most);
}

// reduce op parallel i in n yield value(i). The count alone fixes the blocks: one below CUTOFF, else n / GRAIN
// runs of consecutive indices up to BLOCKS of them, each at least GRAIN long. Each block folds in index order into
// a slot of its own and the slots fold in block order, so an associative operator gives the in-order answer on
// any number of lanes, and which lane ran a block never changes the grouping. The slots live in this frame.
inline constexpr std::size_t BLOCKS = 256;
template<class T, class C, class F> T reduce(std::size_t n, T identity, C combine, F value) noexcept {
  const std::size_t blocks = n < lanes::CUTOFF ? 1 : std::min(BLOCKS, n / lanes::GRAIN);
  T slot[BLOCKS];
  auto fold = [&](std::size_t b) noexcept {
    const std::size_t lo = n / blocks * b + std::min(b, n % blocks);
    const std::size_t hi = lo + n / blocks + (b < n % blocks);
    T acc = identity;
    for(std::size_t i = lo; i < hi; ++i) acc = combine(acc, value(i));
    slot[b] = acc;
  };
  run(blocks, fold, n / blocks + (n == 0));
  T total = identity;
  for(std::size_t b = 0; b < blocks; ++b) total = combine(total, slot[b]);
  return total;
}

// scan op parallel i in n yield value(i) into out: out[i] is op over value(0..i], or over value(0..i) when
// Exclusive, and the answer is op over every value. The blocks are reduce's. The first pass writes each block's
// own prefix into its elements and its total into a slot, reading value(i) before out[i] is written, so a value
// may read its own element. The slots then scan in block order into each block's offset, and the second pass
// combines every element of a block after the first with its offset. value(i) runs once for each i, and an
// associative operator gives the in-order answer on any number of lanes. The slots live in this frame.
template<bool Exclusive, class T, class C, class F> T scan(T* out, std::size_t n, T identity, C combine, F value) noexcept {
  const std::size_t blocks = n < lanes::CUTOFF ? 1 : std::min(BLOCKS, n / lanes::GRAIN);
  T slot[BLOCKS];
  auto own = [&](std::size_t b) noexcept {
    const std::size_t lo = n / blocks * b + std::min(b, n % blocks);
    const std::size_t hi = lo + n / blocks + (b < n % blocks);
    T acc = identity;
    for(std::size_t i = lo; i < hi; ++i) {
      const T y = value(i);
      if constexpr(Exclusive) {
        out[i] = acc;
        acc = combine(acc, y);
      } else {
        acc = combine(acc, y);
        out[i] = acc;
      }
    }
    slot[b] = acc;
  };
  run(blocks, own, n / blocks + (n == 0));
  T carry = identity;
  for(std::size_t b = 0; b < blocks; ++b) {
    const T mine = slot[b];
    slot[b] = carry;
    carry = combine(carry, mine);
  }
  if(blocks > 1) {
    auto shift = [&](std::size_t b) noexcept {
      if(!b) return;
      const std::size_t lo = n / blocks * b + std::min(b, n % blocks);
      const std::size_t hi = lo + n / blocks + (b < n % blocks);
      for(std::size_t i = lo; i < hi; ++i) out[i] = combine(slot[b], out[i]);
    };
    run(blocks, shift, n / blocks);
  }
  return carry;
}

// The mutex owns its value: with() is the only way in and no guard object escapes it.
template<class T> class Mutex final {
  T val_{};
  std::mutex m_;
  std::atomic<std::thread::id> owner_{};
public:
  Mutex() noexcept = default;
  explicit Mutex(T v) noexcept : val_(std::move(v)) {}
  Mutex(const Mutex&) = delete;
  Mutex& operator=(const Mutex&) = delete;
  Mutex(Mutex&&) = delete;
  Mutex& operator=(Mutex&&) = delete;
  template<class F> decltype(auto) with(F&& f) noexcept {
    if(owner_.load()==std::this_thread::get_id()) trap();  // Locking it again here would be undefined.
    std::lock_guard<std::mutex> hold(m_);
    struct Held { std::atomic<std::thread::id>& o; ~Held() { o.store({}); } } held{owner_};
    owner_.store(std::this_thread::get_id());
    return std::forward<F>(f)(val_);
  }
};

// The language has no implicit memory order, so every operation names one and an order that
// the operation cannot carry (a releasing load, an acquiring store) traps instead of being UB.
enum class Order { relaxed, acquire, release, acquire_release, seq_cst };
inline std::memory_order raw(Order o) noexcept {
  switch(o) {
    case Order::relaxed: return std::memory_order_relaxed;
    case Order::acquire: return std::memory_order_acquire;
    case Order::release: return std::memory_order_release;
    case Order::acquire_release: return std::memory_order_acq_rel;
    case Order::seq_cst: return std::memory_order_seq_cst;
  }
  trap();
}
template<class T> class Atomic final {
  std::atomic<T> a_;
  static void loading(Order o) noexcept { if(o == Order::release || o == Order::acquire_release) trap(); }
  static void storing(Order o) noexcept { if(o == Order::acquire || o == Order::acquire_release) trap(); }
public:
  explicit Atomic(T v = T{}) noexcept : a_(v) { static_assert(std::atomic<T>::is_always_lock_free); }
  Atomic(const Atomic&) = delete;
  Atomic& operator=(const Atomic&) = delete;
  T load(Order o) const noexcept { loading(o); return a_.load(raw(o)); }
  void store(T v, Order o) noexcept { storing(o); a_.store(v, raw(o)); }
  T swap(T v, Order o) noexcept { return a_.exchange(v, raw(o)); }
  T fetch_add(T v, Order o) noexcept { return a_.fetch_add(v, raw(o)); }
  T fetch_sub(T v, Order o) noexcept { return a_.fetch_sub(v, raw(o)); }
  T fetch_and(T v, Order o) noexcept { return a_.fetch_and(v, raw(o)); }
  T fetch_or(T v, Order o) noexcept { return a_.fetch_or(v, raw(o)); }
  T fetch_xor(T v, Order o) noexcept { return a_.fetch_xor(v, raw(o)); }
  bool compare_exchange(T& expected, T desired, Order success, Order failure) noexcept {
    loading(failure);
    return a_.compare_exchange_strong(expected, desired, raw(success), raw(failure));
  }
};
} // namespace cr::par
#include "cairn_tasks.hpp"  // tickets and task groups, on a crew of threads that waits as the lane pool does

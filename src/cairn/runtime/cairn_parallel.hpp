// CAIRN host parallelism: a lane pool the first region builds, tickets, mutexes, atomics.
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
// balance itself. Chosen by measurement on a 64-core GH200 (evidence/v1_2/host_regions): engaging
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
// which keeps each thread on one range (evidence/v1_4/bench). proofs/Cairn/Region.lean is this protocol.
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

// Task threads. A spawn takes a thread that is parked, or starts a new one when none is, so a task never
// waits for a thread: every task starts at once, as it would on a thread made for it, and no arrangement of
// tasks that block on one another, or that never end, can hold another back. Only the thread is reused. A
// thread whose task has ended lingers, then parks; the ticket's wait hands it back and the next spawn wakes
// it instead of paying for a new one, and a group keeps its berths' threads until its own wait. At most KEEP
// threads stay parked; one handed back beyond that ends. Tasks never run on the lane pool, so a lane may
// spawn a task and wait for it. The effect row still says what a task may block on; nothing here needs to
// read it, because nothing ever waits for a thread to come free.
namespace crew {
enum : unsigned { IDLE = 0, RUN = 1, DONE = 2, QUIT = 3 };
using Job = void (*)(void*) noexcept;

// One thread and what it is doing. A worker is freed only by the thread that ends it, after joining it,
// so every other thread may touch its state for as long as it holds the worker.
struct alignas(64) Worker {
  std::mutex m;
  std::condition_variable wake;  // the thread waits here for RUN or QUIT
  std::condition_variable done;  // whoever holds the worker waits here for DONE
  std::atomic<unsigned> state{IDLE};
  Job job = nullptr;
  void* arg = nullptr;
  Worker* next = nullptr;  // the parked stack, guarded by the crew's mutex
  std::thread thread;
};

inline void serve(Worker& w) noexcept {
  for(;;) {
    const auto called = [&w] {  // relaxed while it spins; the acquiring load below is what acts on it
      const unsigned s = w.state.load(std::memory_order_relaxed);
      return s == RUN || s == QUIT;
    };
    if(!lanes::linger(called)) {
      std::unique_lock<std::mutex> hold(w.m);
      w.wake.wait(hold, called);
    }
    if(w.state.load(std::memory_order_acquire) == QUIT) return;
    w.job(w.arg);
    {
      std::lock_guard<std::mutex> hold(w.m);
      w.state.store(DONE, std::memory_order_release);
    }
    w.done.notify_all();
  }
}

// Start `job(arg)` on a worker that is idle or whose last job is DONE.
inline void give(Worker& w, Job job, void* arg) noexcept {
  {
    std::lock_guard<std::mutex> hold(w.m);
    if(w.state.load(std::memory_order_relaxed) == RUN) trap();  // one job at a time
    w.job = job;
    w.arg = arg;
    w.state.store(RUN, std::memory_order_release);
  }
  w.wake.notify_one();
}

// Return once the worker's job has ended: after this the job touches nothing, and the worker may be given again.
inline void finish(Worker& w) noexcept {
  const auto ended = [&w] { return w.state.load(std::memory_order_relaxed) == DONE; };
  if(lanes::linger(ended) && w.state.load(std::memory_order_acquire) == DONE) return;
  std::unique_lock<std::mutex> hold(w.m);  // the mutex orders what the job wrote before the store of DONE
  w.done.wait(hold, ended);
}

inline void end(Worker* w) noexcept {
  {
    std::lock_guard<std::mutex> hold(w->m);
    w->state.store(QUIT, std::memory_order_release);
  }
  w->wake.notify_one();
  w->thread.join();
  delete w;
}

#define CAIRN_TASK_CREW 1  // lets a benchmark built against older headers tell the two apart

class Crew final {
public:
  // A parked worker, or a new one. Never waits: the thread a task needs exists when this returns.
  Worker* take() noexcept {
    {
      std::lock_guard<std::mutex> hold(m_);
      if(parked_ != nullptr) {
        Worker* w = parked_;
        parked_ = w->next;
        --count_;
        return w;
      }
    }
    Worker* w = new(std::nothrow) Worker;
    if(w == nullptr) trap();
    w->thread = std::thread([w]() noexcept { serve(*w); });
    started_.fetch_add(1, std::memory_order_relaxed);
    return w;
  }
  // A worker whose job has finished: park it for the next spawn, or end it when KEEP are already parked.
  void hand_back(Worker* w) noexcept {
    {
      std::lock_guard<std::mutex> hold(m_);
      if(!retired_ && count_ < keep_) {
        w->state.store(IDLE, std::memory_order_relaxed);
        w->next = parked_;
        parked_ = w;
        ++count_;
        return;
      }
    }
    end(w);
  }
  // At exit: end every parked worker. A worker still held belongs to a ticket or a group nobody waited, which
  // the language makes unreachable; one handed back after this ends instead of parking.
  void retire() noexcept {
    Worker* all;
    {
      std::lock_guard<std::mutex> hold(m_);
      retired_ = true;
      all = std::exchange(parked_, nullptr);
      count_ = 0;
    }
    while(all != nullptr) end(std::exchange(all, all->next));
  }
  std::size_t started() const noexcept { return started_.load(std::memory_order_relaxed); }
  std::size_t parked() noexcept {
    std::lock_guard<std::mutex> hold(m_);
    return count_;
  }
  // As many parked threads as cores: enough for a pipeline to find its threads warm, and bounded, because a
  // parked thread still holds its stack's address space under the run's memory limit.
  static std::size_t keep() noexcept {
    const unsigned found = std::thread::hardware_concurrency();
    return found != 0 ? std::size_t(found) : std::size_t(1);
  }

private:
  std::mutex m_;
  Worker* parked_ = nullptr;
  std::size_t count_ = 0;
  const std::size_t keep_ = keep();
  bool retired_ = false;
  std::atomic<std::size_t> started_{0};
};

// Built by the first spawn and never destroyed, like the lane pool; the exit handler ends the parked threads.
inline Crew& shared() noexcept {
  static Crew* const one = []() noexcept {
    Crew* made = new(std::nothrow) Crew();
    if(made == nullptr) trap();
    if(std::atexit(+[]() noexcept { shared().retire(); }) != 0) trap();
    return made;
  }();
  return *one;
}
}  // namespace crew

// A linear ticket: exactly one wait() consumes it. The result cell is owned separately from the ticket,
// so a move does not disturb the running task. A dropped ticket is a contract breach: the type system
// makes it unreachable, the runtime makes it loud. The task's captures end on its own thread when it
// returns, as they would have with a thread made for it.
template<class T> class Task final {
  using Slot = std::conditional_t<std::is_void_v<T>, char, T>;
  template<class F> struct Carry {
    F f;
    Slot* out;
    static void run(void* held) noexcept {
      Carry* c = static_cast<Carry*>(held);
      if constexpr(std::is_void_v<T>) c->f(); else *c->out = c->f();
      delete c;
    }
  };
  std::unique_ptr<Slot> slot_;
  crew::Worker* w_ = nullptr;
  template<class F> explicit Task(F&& f) noexcept : slot_(new(std::nothrow) Slot{}) {
    using Held = Carry<std::decay_t<F>>;
    Held* c = slot_ ? new(std::nothrow) Held{std::forward<F>(f), slot_.get()} : nullptr;
    if(c == nullptr) trap();
    w_ = crew::shared().take();
    crew::give(*w_, &Held::run, c);
  }
public:
  template<class F> static Task spawn(F&& f) noexcept { return Task(std::forward<F>(f)); }
  Task(Task&& o) noexcept : slot_(std::move(o.slot_)), w_(std::exchange(o.w_, nullptr)) {}
  Task(const Task&) = delete;
  Task& operator=(const Task&) = delete;
  Task& operator=(Task&&) = delete;  // a linear value is initialized, never overwritten
  ~Task() noexcept { if(w_) trap(); }
  T wait() && noexcept {
    if(!w_) trap();
    crew::finish(*w_);
    crew::shared().hand_back(std::exchange(w_, nullptr));
    if constexpr(!std::is_void_v<T>) return std::move(*slot_);
  }
};

// A task group: up to `capacity` tasks in flight at once, collected in the order they finish. Linear
// like a ticket: exactly one wait() consumes it, waiting for whatever still runs and dropping every result
// nobody collected. Everything it will ever hold is allocated here, once, and a berth keeps the thread its
// first task took until wait() hands it back, so a group refilled round after round starts no threads after
// its first; a group that is full or empty traps instead of growing or blocking forever. A task never runs
// on the lane pool, and only the owning thread submits, collects and waits, so `spare_`, `free_` and
// `workers_` need no lock; the done ring is what the tasks write.
template<class T> class Group final {
  using Slot = std::conditional_t<std::is_void_v<T>, char, T>;
  template<class F> struct Carry {
    F f;
    Group* g;
    std::size_t k;
    static void run(void* held) noexcept {
      Carry* c = static_cast<Carry*>(held);
      Group* const g = c->g;
      const std::size_t k = c->k;
      if constexpr(std::is_void_v<T>) c->f(); else g->slots_[k] = c->f();
      delete c;  // the task's captures end on its own thread
      std::lock_guard<std::mutex> hold(g->m_);
      g->done_[(g->head_ + g->finished_) % g->capacity_] = k;
      ++g->finished_;
      g->cv_.notify_one();  // under the lock: once it is released this task touches nothing of the group
    }
  };
  const std::size_t capacity_;
  std::unique_ptr<Slot[]> slots_;             // one result cell per berth; reset by wait()
  std::unique_ptr<crew::Worker*[]> workers_;  // the thread each berth has held since its first task
  std::unique_ptr<std::size_t[]> free_;       // berths nothing runs in, as a stack
  std::unique_ptr<std::size_t[]> done_;       // berths whose task has finished, as a ring; guarded by m_
  std::size_t spare_ = 0;                     // entries of free_
  std::size_t head_ = 0;                      // the next berth to collect from done_
  std::size_t finished_ = 0;                  // berths queued in done_ and not yet collected
  std::mutex m_;
  std::condition_variable cv_;
public:
  explicit Group(std::size_t capacity) noexcept
      : capacity_(capacity), slots_(new(std::nothrow) Slot[capacity]{}),
        workers_(new(std::nothrow) crew::Worker*[capacity]()), free_(new(std::nothrow) std::size_t[capacity]),
        done_(new(std::nothrow) std::size_t[capacity]) {
    if(!slots_ || !workers_ || !free_ || !done_) trap();
    for(std::size_t k = capacity; k-- > 0;) free_[spare_++] = k;
  }
  Group(const Group&) = delete;
  Group& operator=(const Group&) = delete;
  Group(Group&&) = delete;
  Group& operator=(Group&&) = delete;
  ~Group() noexcept { if(slots_) trap(); }  // The type system makes an unwaited group unreachable; be loud.
  template<class F> void submit(F&& f) noexcept {
    if(spare_ == 0) trap();  // capacity_ tasks are already outstanding
    const std::size_t k = free_[--spare_];
    using Held = Carry<std::decay_t<F>>;
    Held* c = new(std::nothrow) Held{std::forward<F>(f), this, k};
    if(c == nullptr) trap();
    if(workers_[k] == nullptr) workers_[k] = crew::shared().take();
    crew::give(*workers_[k], &Held::run, c);
  }
  T collect() noexcept {
    if(spare_ == capacity_) trap();  // nothing is outstanding
    std::size_t k;
    {
      std::unique_lock<std::mutex> hold(m_);
      cv_.wait(hold, [this] { return finished_ != 0; });
      k = done_[head_];
      head_ = (head_ + 1) % capacity_;
      --finished_;
    }
    crew::finish(*workers_[k]);  // it has finished: this only waits for its thread to say so
    free_[spare_++] = k;
    if constexpr(!std::is_void_v<T>) return std::exchange(slots_[k], Slot{});
  }
  void wait() && noexcept {
    for(std::size_t k = 0; k < capacity_; ++k)
      if(workers_[k] != nullptr) {
        crew::finish(*workers_[k]);
        crew::shared().hand_back(std::exchange(workers_[k], nullptr));
      }
    slots_.reset();  // every result nobody collected is released here
    spare_ = capacity_;
  }
};

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

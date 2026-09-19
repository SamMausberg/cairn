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

// Wait for `ready` without a syscall, then with a few, then give up. Sixty-four threads calling
// sched_yield between regions was measured to cost more than the regions they were waiting for,
// and so was an acquiring load in the spin itself: `ready` reads relaxed and is only a hint, so
// whoever acts on a true answer takes the mutex or reads again with acquire. The signal fence
// emits no instruction; it is what stops a compiler hoisting the whole spin into one load, which
// clang was measured to do.
template<class P> inline bool linger(P ready) noexcept {
  for(unsigned turn = 0; turn < SPIN_READS; ++turn) {
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

// One region, on the stack of the thread that started it. Workers reach it only while they hold a
// reference (`users`), and the submitter returns only once that count is back to zero, so the
// frame outlives every thread that can touch it. Nothing here is allocated.
struct Region {
  Call call = nullptr;
  void* body = nullptr;
  std::size_t n = 0;
  std::size_t grain = 1;
  std::size_t helpers = 0;  // workers 0..helpers-1 may join; the rest leave a small region alone
  Region* older = nullptr;  // regions started by other threads, newest first; guarded by m_
  std::atomic<std::size_t> next{0};   // the next index no lane has claimed
  std::atomic<std::size_t> users{0};  // workers currently inside this region
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
    drain(region);
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
      if(k < region->helpers && region->next.load(std::memory_order_relaxed) < region->n) {
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

  // Claim chunks and run them until nothing is left. Used by the submitter and by every worker.
  static void drain(Region& region) noexcept {
    while(region.next.load(std::memory_order_relaxed) < region.n) {
      const std::size_t begin = region.next.fetch_add(region.grain, std::memory_order_relaxed);
      if(begin >= region.n) return;
      const std::size_t end = region.n - begin < region.grain ? region.n : begin + region.grain;
      region.call(region.body, begin, end);
    }
  }

  void serve(std::size_t k) noexcept {
    for(;;) {
      const std::uint64_t seen = epoch_.load(std::memory_order_acquire);
      if(k < wanted_.load(std::memory_order_acquire)) {
        if(Region* region = adopt(k)) {
          drain(*region);
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
template<class F> void run_wide(std::size_t n, F& body) noexcept {
  using Body = std::remove_reference_t<F>;
  lanes::Pool& pool = lanes::shared();
  const std::size_t crew = pool.lanes();
  if(crew < 2) {
    for(std::size_t i = 0; i < n; ++i) body(i);
    return;
  }
  lanes::Region region;
  region.call = [](void* held, std::size_t begin, std::size_t end) noexcept {
    Body& lane = *static_cast<Body*>(held);
    for(std::size_t i = begin; i < end; ++i) lane(i);
  };
  region.body = const_cast<void*>(static_cast<const void*>(std::addressof(body)));
  region.n = n;
  std::size_t use = n / lanes::GRAIN;  // n >= CUTOFF, so this is at least two
  if(use > crew) use = crew;
  region.helpers = use - 1;
  region.grain = n / (use * lanes::SPLIT);
  if(region.grain < lanes::GRAIN) region.grain = lanes::GRAIN;
  pool.execute(region);
}

// parallel i in n: body(i) runs once for every i below n, and run returns when all of them have.
// Which lane runs which index is not promised; the language has already made that unobservable.
// A region below the cutoff is exactly the loop it replaces: nothing is published and nothing woken.
template<class F> inline void run(std::size_t n, F&& body) noexcept {
  if(n < lanes::CUTOFF) {
    for(std::size_t i = 0; i < n; ++i) body(i);
    return;
  }
  run_wide(n, body);
}

// A linear ticket: exactly one wait() consumes it. The result cell is owned separately from the
// ticket so a move does not disturb the running thread. A dropped ticket is a contract breach:
// the type system makes it unreachable, the runtime makes it loud. A task is a thread of its own,
// never a lane of the pool, so a lane may spawn one and wait for it without starving a region.
template<class T> class Task final {
  using Slot = std::conditional_t<std::is_void_v<T>, char, T>;
  std::unique_ptr<Slot> slot_;
  std::thread th_;
  template<class F> explicit Task(F&& f) noexcept : slot_(new(std::nothrow) Slot{}) {
    if(!slot_) trap();
    th_ = std::thread([p = slot_.get(), g = std::forward<F>(f)]() mutable {
      if constexpr(std::is_void_v<T>) { (void)p; g(); } else *p = g();
    });
  }
public:
  template<class F> static Task spawn(F&& f) noexcept { return Task(std::forward<F>(f)); }
  Task(Task&&) noexcept = default;
  Task(const Task&) = delete;
  Task& operator=(const Task&) = delete;
  Task& operator=(Task&&) = delete;  // a linear value is initialized, never overwritten
  ~Task() noexcept { if(th_.joinable()) trap(); }
  T wait() && noexcept {
    if(!th_.joinable()) trap();
    th_.join();
    if constexpr(!std::is_void_v<T>) return std::move(*slot_);
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

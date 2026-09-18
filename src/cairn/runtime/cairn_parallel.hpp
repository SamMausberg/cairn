// CAIRN host parallelism: threads a statement creates and joins, tickets, mutexes, atomics.
// No pool and no scheduler survive a statement, so the cost of parallelism stays where it is
// written. Pairs with cairn_gpu.hpp, which runs the same lambda body on device lanes.
#pragma once
#include <atomic>
#include <memory>
#include <mutex>
#include <new>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>
#include "cairn_runtime.hpp"
namespace cr::par {

// parallel i in n: one static contiguous chunk per thread. Every thread is created here and
// joined before the statement returns; the calling thread runs chunk 0, and n<2 creates none.
template<class F> inline void run(std::size_t n, F&& body) noexcept {
  if(n == 0) return;
  if(n == 1) { body(std::size_t(0)); return; }
  std::size_t t = std::thread::hardware_concurrency();
  if(t == 0) t = 1;
  if(t > n) t = n;
  const std::size_t chunk = n / t, extra = n % t;
  const auto part = [&](std::size_t k) noexcept {
    const std::size_t b = k * chunk + (k < extra ? k : extra), e = b + chunk + (k < extra ? 1 : 0);
    for(std::size_t i = b; i < e; ++i) body(i);
  };
  std::vector<std::thread> more;
  more.reserve(t - 1);
  for(std::size_t k = 1; k < t; ++k) more.emplace_back(part, k);
  part(0);
  for(auto& th : more) th.join();
}

// A linear ticket: exactly one wait() consumes it. The result cell is owned separately from the
// ticket so a move does not disturb the running thread. A dropped ticket is a contract breach:
// the type system makes it unreachable, the runtime makes it loud.
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
public:
  Mutex() noexcept = default;
  explicit Mutex(T v) noexcept : val_(std::move(v)) {}
  Mutex(const Mutex&) = delete;
  Mutex& operator=(const Mutex&) = delete;
  Mutex(Mutex&&) = delete;
  Mutex& operator=(Mutex&&) = delete;
  template<class F> decltype(auto) with(F&& f) noexcept {
    std::lock_guard<std::mutex> hold(m_);
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

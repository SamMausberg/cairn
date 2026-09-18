// Self-checking test for cr::par: exit 0 is a pass. With an argument it runs one death case,
// which must abort the process; tests/test_native_runtime.py drives those as subprocesses.
// Built with the language contract flags, so this file also proves the runtime headers still
// compile without CUDA present.
#include <cstdio>
#include <cstring>
#include <set>
#include <thread>
#include <vector>
#include "cairn_parallel.hpp"

using cr::par::Order;
static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

// Every index is visited exactly once, chunks are contiguous, the caller runs one of them, and
// n below 2 never leaves the calling thread.
static void test_run() {
  const std::size_t sizes[] = {0, 1, 2, 3, 63, 64, 65, 1000003};
  const std::size_t hw = std::thread::hardware_concurrency() ? std::thread::hardware_concurrency() : 1;
  for(std::size_t n : sizes) {
    std::vector<unsigned> seen(n, 0);
    std::vector<std::thread::id> who(n);
    cr::par::run(n, [&](std::size_t i) {
      seen[i] += 1;
      who[i] = std::this_thread::get_id();
    });
    std::size_t breaks = 0;
    for(std::size_t i = 0; i < n; ++i) {
      CHECK(seen[i] == 1);
      if(i && who[i] != who[i - 1]) ++breaks;
    }
    const std::set<std::thread::id> distinct(who.begin(), who.end());
    const std::size_t want = n < hw ? n : hw;
    CHECK(distinct.size() <= want);
    CHECK(breaks + 1 <= want || n == 0);  // contiguous chunks: one run of indices per thread
    if(n) CHECK(distinct.count(std::this_thread::get_id()) == 1);
    if(n && n < 2) CHECK(distinct.size() == 1);
  }
  std::size_t touched = 0;
  cr::par::run(0, [&](std::size_t) { ++touched; });
  CHECK(touched == 0);
}

static void test_task() {
  auto one = cr::par::Task<std::uint64_t>::spawn([] { return std::uint64_t(7); });
  CHECK(std::move(one).wait() == 7);
  int side = 0;
  auto none = cr::par::Task<void>::spawn([&] { side = 5; });
  std::move(none).wait();
  CHECK(side == 5);
  auto first = cr::par::Task<int>::spawn([] { return 3; });  // a move must not disturb the worker
  auto second = std::move(first);
  CHECK(std::move(second).wait() == 3);
  std::vector<cr::par::Task<std::uint64_t>> many;
  for(std::uint64_t k = 0; k < 32; ++k)
    many.push_back(cr::par::Task<std::uint64_t>::spawn([k] { return k * k; }));
  for(std::uint64_t k = 0; k < 32; ++k) CHECK(std::move(many[k]).wait() == k * k);
  auto outer = cr::par::Task<std::uint64_t>::spawn([] {  // a task may spawn a task
    auto inner = cr::par::Task<std::uint64_t>::spawn([] { return std::uint64_t(20); });
    return std::move(inner).wait() + 2;
  });
  CHECK(std::move(outer).wait() == 22);
}

static void test_mutex() {
  cr::par::Mutex<long> total(0);
  cr::par::run(4096, [&](std::size_t) {
    for(int k = 0; k < 64; ++k) total.with([](long& v) { v += 1; });
  });
  CHECK(total.with([](long& v) { return v; }) == 4096L * 64);
  CHECK(total.with([](long& v) { return v * 2; }) == 4096L * 128);
  long& escaped = total.with([](long& v) -> long& { return v; });  // decltype(auto) forwards refs
  CHECK(&escaped == &total.with([](long& v) -> long& { return v; }));
}

static void test_atomic() {
  cr::par::Atomic<std::uint64_t> counter(0);
  cr::par::run(4096, [&](std::size_t) {
    for(int k = 0; k < 64; ++k) counter.fetch_add(1, Order::relaxed);
  });
  CHECK(counter.load(Order::seq_cst) == 4096ull * 64);
  CHECK(counter.fetch_sub(4096ull * 64, Order::acquire_release) == 4096ull * 64);
  CHECK(counter.swap(0b1100, Order::seq_cst) == 0);
  CHECK(counter.fetch_and(0b1010, Order::relaxed) == 0b1100);
  CHECK(counter.fetch_or(0b0001, Order::relaxed) == 0b1000);
  CHECK(counter.fetch_xor(0b1111, Order::relaxed) == 0b1001);
  CHECK(counter.load(Order::relaxed) == 0b0110);
  std::uint64_t expected = 0b0110;
  CHECK(counter.compare_exchange(expected, 42, Order::acquire_release, Order::acquire));
  CHECK(!counter.compare_exchange(expected, 43, Order::seq_cst, Order::relaxed));
  CHECK(expected == 42);
  // release/acquire really publishes the payload written before the flag.
  cr::par::Atomic<int> flag(0);
  std::uint64_t payload = 0;
  auto writer = cr::par::Task<void>::spawn([&] {
    payload = 0xfeed;
    flag.store(1, Order::release);
  });
  auto reader = cr::par::Task<std::uint64_t>::spawn([&] {
    while(flag.load(Order::acquire) == 0) std::this_thread::yield();
    return payload;
  });
  std::move(writer).wait();
  CHECK(std::move(reader).wait() == 0xfeed);
  // contended compare_exchange: every thread must win exactly its own increment
  cr::par::Atomic<std::uint64_t> cas(0);
  cr::par::run(1024, [&](std::size_t) {
    std::uint64_t seen = cas.load(Order::relaxed);
    while(!cas.compare_exchange(seen, seen + 1, Order::acquire_release, Order::relaxed)) {}
  });
  CHECK(cas.load(Order::seq_cst) == 1024);
}

// Each death case must abort. The type system makes them unreachable; the runtime makes them loud.
static int death(const char* name) {
  if(!std::strcmp(name, "task_dropped")) {
    auto t = cr::par::Task<int>::spawn([] { return 1; });
    (void)t;  // never waited: ~Task must trap
  } else if(!std::strcmp(name, "task_waited_twice")) {
    auto t = cr::par::Task<int>::spawn([] { return 1; });
    (void)std::move(t).wait();
    (void)std::move(t).wait();
  } else if(!std::strcmp(name, "store_acquire")) {
    cr::par::Atomic<int> a(0);
    a.store(1, Order::acquire);
  } else if(!std::strcmp(name, "load_release")) {
    cr::par::Atomic<int> a(0);
    (void)a.load(Order::release);
  } else if(!std::strcmp(name, "cas_failure_release")) {
    cr::par::Atomic<int> a(0);
    int want = 0;
    (void)a.compare_exchange(want, 1, Order::seq_cst, Order::release);
  } else if(!std::strcmp(name, "host_overflow")) {
    cr::par::run(2, [](std::size_t i) { (void)cr::add<std::uint64_t>(~std::uint64_t(0), i + 1); });
  } else {
    std::fprintf(stderr, "unknown death case %s\n", name);
    return 2;
  }
  std::fprintf(stderr, "death case %s did not abort\n", name);
  return 3;
}

int main(int argc, char** argv) {
  static const char* cases[] = {"task_dropped", "task_waited_twice",   "store_acquire",
                                "load_release", "cas_failure_release", "host_overflow"};
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    for(const char* c : cases) std::printf("%s\n", c);
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_run();
  test_task();
  test_mutex();
  test_atomic();
  std::printf("parallel_runtime: %s after %ld checks\n", failures ? "FAILED" : "ok", checked);
  return failures ? 1 : 0;
}

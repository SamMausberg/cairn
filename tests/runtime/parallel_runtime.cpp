// Self-checking test for cr::par: exit 0 is a pass. With an argument it runs one death case,
// which must abort the process; tests/runtime/test_native_runtime.py drives those as subprocesses.
// Built with the language contract flags, so this file also proves the runtime headers still
// compile without CUDA present. The lane pool is what most of this exercises: coverage and
// exactly-once at many sizes, regions started at once from several threads, thousands of tiny
// regions, a lane that blocks, and a lane that fails a guard. The task threads are the rest:
// that they are reused, and that reuse never makes one task wait for another to give a thread up.
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <set>
#include <thread>
#include <utility>
#include <vector>
#include "cairn_parallel.hpp"

using cr::par::Order;
static const std::size_t CUT = cr::par::lanes::CUTOFF;
static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

static std::size_t lane_count() { return cr::par::lanes::Pool::configured(); }

// Every index is visited exactly once, no more than one lane per configured lane appears, and a
// region below the cutoff never leaves the calling thread. Which lane runs which index is not part
// of the contract, so nothing here asks for contiguous chunks.
static void test_run() {
  std::vector<std::size_t> sizes = {0, 1, 2, 3, 63, 64, 65, CUT - 1, CUT, CUT + 1, 2 * CUT + 7, 1000003};
  std::uint64_t seed = 0x9e3779b97f4a7c15ull;
  for(int k = 0; k < 32; ++k) {  // random sizes, so no ladder of round numbers hides a case
    seed ^= seed << 13;
    seed ^= seed >> 7;
    seed ^= seed << 17;
    sizes.push_back(seed % 200003);
  }
  for(std::size_t n : sizes) {
    std::vector<unsigned> seen(n, 0);
    std::vector<std::thread::id> who(n);
    cr::par::run(n, [&](std::size_t i) noexcept {
      seen[i] += 1;
      who[i] = std::this_thread::get_id();
    });
    std::size_t once = 0;
    for(std::size_t i = 0; i < n; ++i) once += seen[i] == 1;
    CHECK(once == n);
    const std::set<std::thread::id> distinct(who.begin(), who.end());
    CHECK(distinct.size() <= (n < lane_count() ? n : lane_count()));
    if(n != 0 && n < CUT) {
      CHECK(distinct.size() == 1);
      CHECK(*distinct.begin() == std::this_thread::get_id());
    }
  }
  std::size_t touched = 0;
  cr::par::run(0, [&](std::size_t) noexcept { ++touched; });
  CHECK(touched == 0);
}

// Eight threads each run their own regions at the same time over their own arrays. The pool serves
// whichever of them it can and every one of them can finish alone, so all eight must come out whole.
static void test_regions_from_several_threads() {
  const std::size_t threads = 8, rounds = 8, n = 3 * CUT + 11;
  std::vector<std::vector<unsigned>> seen(threads, std::vector<unsigned>(n, 0));
  std::vector<cr::par::Task<std::uint64_t>> runners;
  for(std::size_t t = 0; t < threads; ++t) {
    unsigned* mine = seen[t].data();
    runners.push_back(cr::par::Task<std::uint64_t>::spawn([=] {
      for(std::size_t round = 0; round < rounds; ++round)
        cr::par::run(n, [mine](std::size_t i) noexcept { mine[i] += 1; });
      std::uint64_t right = 0;
      for(std::size_t i = 0; i < n; ++i) right += mine[i] == rounds;
      return right;
    }));
  }
  for(std::size_t t = 0; t < threads; ++t) CHECK(std::move(runners[t]).wait() == n);
}

// Thousands of regions in a row, above the cutoff and below it: the pool must be reused, not rebuilt.
static void test_many_regions() {
  const std::size_t n = CUT + 1, rounds = 2000;
  std::vector<std::uint64_t> tally(n, 0);
  std::uint64_t* tp = tally.data();
  for(std::size_t r = 0; r < rounds; ++r) cr::par::run(n, [tp](std::size_t i) noexcept { tp[i] += 1; });
  std::size_t right = 0;
  for(std::size_t i = 0; i < n; ++i) right += tally[i] == rounds;
  CHECK(right == n);
  std::uint64_t tiny[7] = {0, 0, 0, 0, 0, 0, 0};
  for(std::size_t r = 0; r < 5000; ++r) cr::par::run(7, [&tiny](std::size_t i) noexcept { tiny[i] += 1; });
  for(std::uint64_t v : tiny) CHECK(v == 5000);
}

// One lane takes far longer than the others, so the thread that started the region stops spinning
// and sleeps. It must still be woken, and the region must still be complete when run returns.
static void test_a_slow_lane_still_completes() {
  const std::size_t n = 4 * CUT;
  std::vector<unsigned> seen(n, 0);
  unsigned* sp = seen.data();
  cr::par::run(n, [=](std::size_t i) noexcept {
    sp[i] += 1;
    if(i == n / 2) std::this_thread::sleep_for(std::chrono::milliseconds(30));
  });
  std::size_t right = 0;
  for(std::size_t i = 0; i < n; ++i) right += seen[i] == 1;
  CHECK(right == n);
}

// A lane may call a function that spawns a task and waits for it, and that task may itself run a
// region. The worker running the lane is blocked meanwhile, so this deadlocks unless every region
// can be finished by the thread that started it.
static void test_a_lane_may_wait_for_a_task() {
  const std::size_t n = 8 * CUT;
  std::vector<std::uint64_t> out(n, 0);
  std::uint64_t* op = out.data();
  cr::par::run(n, [op](std::size_t i) noexcept {
    if(i % CUT != 0) {
      op[i] = std::uint64_t(i) + 1;
      return;
    }
    auto inner = cr::par::Task<std::uint64_t>::spawn([i] {
      std::vector<std::uint64_t> part(CUT + 3, 0);
      std::uint64_t* pp = part.data();
      cr::par::run(part.size(), [pp](std::size_t j) noexcept { pp[j] = 1; });
      std::uint64_t total = 0;
      for(std::uint64_t v : part) total += v;
      return total == CUT + 3 ? std::uint64_t(i) + 1 : std::uint64_t(0);
    });
    op[i] = std::move(inner).wait();
  });
  std::size_t right = 0;
  for(std::size_t i = 0; i < n; ++i) right += out[i] == std::uint64_t(i) + 1;
  CHECK(right == n);
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

// A thread whose task was waited is parked and taken by the next spawn: a thousand spawns in a row
// start no thread once one is warm, and a group refilled fifty times starts at most one per berth.
static void test_task_threads_are_reused() {
  cr::par::crew::Crew& crew = cr::par::crew::shared();
  auto warm = cr::par::Task<int>::spawn([] { return 1; });
  CHECK(std::move(warm).wait() == 1);
  std::size_t mark = crew.started();
  std::uint64_t total = 0;
  for(std::uint64_t k = 0; k < 1000; ++k) {
    auto t = cr::par::Task<std::uint64_t>::spawn([k] { return k * 2; });
    total += std::move(t).wait();
  }
  CHECK(total == 999 * 1000);
  CHECK(crew.started() == mark);
  mark = crew.started();
  cr::par::Group<std::uint64_t> g(8);
  total = 0;
  for(std::uint64_t round = 0; round < 50; ++round) {
    for(std::uint64_t k = 0; k < 8; ++k) g.submit([round, k] { return round * 8 + k; });
    for(int k = 0; k < 8; ++k) total += g.collect();
  }
  std::move(g).wait();
  CHECK(total == 399 * 400 / 2);
  CHECK(crew.started() - mark <= 8);
  CHECK(crew.parked() <= cr::par::crew::Crew::keep());
}

// What a task captured ends on the task's own thread, when the task returns, before wait() does.
struct Mark {
  std::thread::id* where;
  explicit Mark(std::thread::id* w) : where(w) {}
  Mark(Mark&& o) noexcept : where(std::exchange(o.where, nullptr)) {}
  ~Mark() { if(where) *where = std::this_thread::get_id(); }
};
static void test_captures_end_on_the_task_thread() {
  std::thread::id where{};
  auto t = cr::par::Task<int>::spawn([m = Mark(&where)] { return m.where != nullptr ? 1 : 0; });
  CHECK(std::move(t).wait() == 1);
  CHECK(where != std::thread::id{} && where != std::this_thread::get_id());
}

static std::uint64_t chain(std::size_t depth) {
  if(depth == 0) return 1;
  auto t = cr::par::Task<std::uint64_t>::spawn([depth] { return chain(depth - 1); });
  return std::move(t).wait() + 1;
}

// Each of these deadlocks under a pool that holds a fixed number of threads, however large: a chain of
// tasks each waiting for the one it started, deeper than the pool; a task waiting for a flag that a task
// spawned after it sets, on a pool of one; and a group whose tasks meet at a barrier only all of them
// running at once can pass. A spawn here never waits for a thread, so all three finish.
static void test_no_task_waits_for_a_thread() {
  CHECK(chain(64) == 65);
  cr::par::Atomic<int> flag(0);
  auto early = cr::par::Task<int>::spawn([&] {
    while(flag.load(Order::acquire) == 0) std::this_thread::yield();
    return 1;
  });
  auto late = cr::par::Task<void>::spawn([&] { flag.store(1, Order::release); });
  std::move(late).wait();
  CHECK(std::move(early).wait() == 1);
  const std::size_t width = 32;
  cr::par::Group<int> g(width);
  cr::par::Atomic<std::size_t> arrived(0);
  for(std::size_t k = 0; k < width; ++k)
    g.submit([&] {
      arrived.fetch_add(1, Order::acquire_release);
      while(arrived.load(Order::acquire) < width) std::this_thread::yield();
      return 1;
    });
  int met = 0;
  for(std::size_t k = 0; k < width; ++k) met += g.collect();
  std::move(g).wait();
  CHECK(met == int(width));
  // A group waited with results nobody collected still hands every thread back.
  cr::par::Group<std::uint64_t> left(4);
  for(std::uint64_t k = 0; k < 4; ++k) left.submit([k] { return k; });
  CHECK(left.collect() < 4);
  std::move(left).wait();
  CHECK(cr::par::crew::shared().parked() <= cr::par::crew::Crew::keep());
}

static void test_mutex() {
  cr::par::Mutex<long> total(0);
  cr::par::run(4096, [&](std::size_t) noexcept {
    for(int k = 0; k < 64; ++k) total.with([](long& v) { v += 1; });
  });
  CHECK(total.with([](long& v) { return v; }) == 4096L * 64);
  CHECK(total.with([](long& v) { return v * 2; }) == 4096L * 128);
  long& escaped = total.with([](long& v) -> long& { return v; });  // decltype(auto) forwards refs
  CHECK(&escaped == &total.with([](long& v) -> long& { return v; }));
}

static void test_atomic() {
  cr::par::Atomic<std::uint64_t> counter(0);
  cr::par::run(4096, [&](std::size_t) noexcept {
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
  // contended compare_exchange: every lane must win exactly its own increment
  cr::par::Atomic<std::uint64_t> cas(0);
  cr::par::run(CUT * 2, [&](std::size_t) noexcept {
    std::uint64_t seen = cas.load(Order::relaxed);
    while(!cas.compare_exchange(seen, seen + 1, Order::acquire_release, Order::relaxed)) {}
  });
  CHECK(cas.load(Order::seq_cst) == CUT * 2);
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
    cr::par::run(2, [](std::size_t i) noexcept { (void)cr::add<std::uint64_t>(~std::uint64_t(0), i + 1); });
  } else if(!std::strcmp(name, "worker_overflow")) {
    // A guard that fails in a lane must end the process wherever that lane ran. With more than one
    // lane the calling thread is excluded by name, so only a pool worker can reach the failure, and
    // the caller parks in its first chunk so that a loaded machine cannot let it finish every chunk
    // before a worker wakes. The park is bounded: a pool that never runs a chunk still fails the case.
    const bool alone = lane_count() < 2;
    const std::thread::id caller = std::this_thread::get_id();
    bool parked = false;  // touched only by the calling thread
    cr::par::run(64 * CUT, [alone, caller, &parked](std::size_t i) noexcept {
      if(alone || std::this_thread::get_id() != caller) (void)cr::add<std::uint64_t>(~std::uint64_t(0), i + 1);
      else if(!parked) {
        parked = true;
        std::this_thread::sleep_for(std::chrono::seconds(60));
      }
    });
  } else if(!std::strcmp(name, "lanes_not_a_number") || !std::strcmp(name, "lanes_zero") ||
            !std::strcmp(name, "lanes_too_many")) {
    const char* bad = !std::strcmp(name, "lanes_zero") ? "0"
                      : !std::strcmp(name, "lanes_too_many") ? "100000"
                                                             : "many";
    setenv("CAIRN_LANES", bad, 1);  // read once, when the first region builds the pool
    cr::par::run(CUT, [](std::size_t) noexcept {});
  } else {
    std::fprintf(stderr, "unknown death case %s\n", name);
    return 2;
  }
  std::fprintf(stderr, "death case %s did not abort\n", name);
  return 3;
}

int main(int argc, char** argv) {
  static const char* cases[] = {"task_dropped",      "task_waited_twice", "store_acquire",
                                "load_release",      "cas_failure_release", "host_overflow",
                                "worker_overflow",   "lanes_not_a_number", "lanes_zero",
                                "lanes_too_many"};
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    for(const char* c : cases) std::printf("%s\n", c);
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_run();
  test_regions_from_several_threads();
  test_many_regions();
  test_a_slow_lane_still_completes();
  test_a_lane_may_wait_for_a_task();
  test_task();
  test_task_threads_are_reused();
  test_captures_end_on_the_task_thread();
  test_no_task_waits_for_a_thread();
  test_mutex();
  test_atomic();
  // Returning from main is the last check: the exit handlers must stop and join every lane and every
  // parked task thread, with no hang and nothing left for a sanitizer to report.
  std::printf("parallel_runtime: %s after %ld checks on %zu lanes\n", failures ? "FAILED" : "ok", checked,
              lane_count());
  return failures ? 1 : 0;
}

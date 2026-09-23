// Repeated task pipelines against cr::par::Task and cr::par::Group, the code `spawn` and `spawn ... into g`
// lower to. Each case repeats one round many times and reports the median nanoseconds a round took over
// several blocks, with the round's allocations and synchronization included: what a warm pipeline pays.
// Prints one JSON object; every case checks its own result, and a disagreement is recorded, never hidden.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <vector>
#include "cairn_parallel.hpp"

namespace {
using Clock = std::chrono::steady_clock;
// Threads started so far, where the runtime keeps count; the thread-per-task runtime starts one per task.
long long started() {
#if defined(CAIRN_TASK_CREW)
  return (long long)cr::par::crew::shared().started();
#else
  return -1;
#endif
}
constexpr int BLOCKS = 7;
bool agreed = true;

template<class Round> double median_ns(std::size_t rounds, Round round) {
  std::vector<double> per;
  for(int b = 0; b < BLOCKS; ++b) {
    const auto t0 = Clock::now();
    for(std::size_t r = 0; r < rounds; ++r) round(r);
    const auto t1 = Clock::now();
    per.push_back(std::chrono::duration<double, std::nano>(t1 - t0).count() / double(rounds));
  }
  std::sort(per.begin(), per.end());
  return per[per.size() / 2];
}

// spawn one task and wait for it: the whole cost of a ticket, round after round.
double spawn_wait(std::size_t rounds) {
  std::uint64_t total = 0, want = 0;
  const double ns = median_ns(rounds, [&](std::size_t r) {
    auto t = cr::par::Task<std::uint64_t>::spawn([r]() noexcept { return std::uint64_t(r) * 3 + 1; });
    total += std::move(t).wait();
  });
  for(int b = 0; b < BLOCKS; ++b)
    for(std::size_t r = 0; r < rounds; ++r) want += std::uint64_t(r) * 3 + 1;
  agreed = agreed && total == want;
  return ns;
}

// Two tasks fill and sum the two halves of one array, then both are waited: a fork-join step of a pipeline.
double fork_join(std::size_t rounds, std::size_t n) {
  std::vector<std::uint64_t> data(n);
  std::uint64_t total = 0;
  const double ns = median_ns(rounds, [&](std::size_t r) {
    std::uint64_t* lo = data.data();
    std::uint64_t* hi = data.data() + n / 2;
    const std::size_t half = n / 2;
    auto half_sum = [](std::uint64_t* part, std::size_t count, std::uint64_t base) noexcept {
      std::uint64_t s = 0;
      for(std::size_t i = 0; i < count; ++i) {
        part[i] = base + i;
        s += part[i];
      }
      return s;
    };
    auto left = cr::par::Task<std::uint64_t>::spawn([=]() noexcept { return half_sum(lo, half, r); });
    auto right = cr::par::Task<std::uint64_t>::spawn([=]() noexcept { return half_sum(hi, half, r + half); });
    total += std::move(left).wait() + std::move(right).wait();
  });
  std::uint64_t want = 0;
  for(int b = 0; b < BLOCKS; ++b)
    for(std::size_t r = 0; r < rounds; ++r)
      for(std::size_t i = 0; i < 2 * (n / 2); ++i) want += r + i;
  agreed = agreed && total == want;
  return ns;
}

// One group of `width` berths, filled and collected in full each round: what a server's batch loop does.
double group_round(std::size_t rounds, std::size_t width) {
  cr::par::Group<std::uint64_t> g(width);
  std::uint64_t total = 0;
  const double ns = median_ns(rounds, [&](std::size_t r) {
    for(std::size_t k = 0; k < width; ++k) g.submit([r, k]() noexcept { return std::uint64_t(r + k); });
    for(std::size_t k = 0; k < width; ++k) total += g.collect();
  });
  std::move(g).wait();
  std::uint64_t want = 0;
  for(int b = 0; b < BLOCKS; ++b)
    for(std::size_t r = 0; r < rounds; ++r)
      for(std::size_t k = 0; k < width; ++k) want += r + k;
  agreed = agreed && total == want;
  return ns;
}

// A task that spawns a task that spawns a task, `depth` deep, each waiting for the one it started.
std::uint64_t chain(std::size_t depth) {
  if(depth == 0) return 1;
  auto t = cr::par::Task<std::uint64_t>::spawn([depth]() noexcept { return chain(depth - 1); });
  return std::move(t).wait() + 1;
}
double nested(std::size_t rounds, std::size_t depth) {
  std::uint64_t total = 0;
  const double ns = median_ns(rounds, [&](std::size_t) { total += chain(depth); });
  agreed = agreed && total == std::uint64_t(BLOCKS) * rounds * (depth + 1);
  return ns;
}
}  // namespace

int main() {
  long long mark = started();
  long long threads[4];
  const auto count = [&](int k) {
    const long long now = started();
    threads[k] = mark < 0 ? -1 : now - mark;
    mark = now;
  };
  const double a = spawn_wait(4000);
  count(0);
  const double b = fork_join(2000, 4096);
  count(1);
  const double c = group_round(500, 8);
  count(2);
  const double d = nested(200, 16);
  count(3);
  std::printf("{\"cases\": {\"spawn_wait\": %.1f, \"fork_join_4096\": %.1f, \"group_round_8\": %.1f, \"nested_16\": %.1f},"
              " \"threads_started\": {\"spawn_wait\": %lld, \"fork_join_4096\": %lld, \"group_round_8\": %lld,"
              " \"nested_16\": %lld}, \"unit\": \"median ns per round over %d blocks\", \"every_case_agreed\": %s}\n",
              a, b, c, d, threads[0], threads[1], threads[2], threads[3], BLOCKS, agreed ? "true" : "false");
  return agreed ? 0 : 1;
}

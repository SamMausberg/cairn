#include <algorithm>
#include <barrier>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

namespace {

constexpr std::size_t kBlockSize = 256;

// Number of Hillis-Steele scan steps needed so that 2^steps >= len.
std::size_t stepsFor(std::size_t len) {
  std::size_t steps = 0;
  while ((std::size_t{1} << steps) < len) ++steps;
  return steps;
}

}  // namespace

int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  std::size_t n;
  if (!(std::cin >> n)) return 0;

  std::vector<std::uint64_t> input(n);
  for (std::size_t i = 0; i < n; ++i) {
    unsigned long long v;
    std::cin >> v;
    input[i] = static_cast<std::uint64_t>(v);
  }

  if (n == 0) return 0;

  std::vector<std::uint64_t> output(n);
  const std::size_t numBlocks = (n + kBlockSize - 1) / kBlockSize;

  // The team: each thread scans a contiguous lane of a block, then the
  // team combines the per-lane totals across the shared array with a
  // barrier-separated step scan, exactly as a GPU block would combine
  // the partial sums produced by its lanes. A small, fixed team size
  // avoids pathological OS scheduling when it happens to match the
  // machine's core count exactly.
  const std::size_t numThreads = std::min<std::size_t>({8, kBlockSize, n});

  std::barrier sync_point(static_cast<std::ptrdiff_t>(numThreads));

  // Shared scratch array of per-lane block totals (the array the team
  // cooperatively scans in steps).
  std::uint64_t laneA[kBlockSize];
  std::uint64_t laneB[kBlockSize];
  std::uint64_t runningOffset = 0;

  auto worker = [&](std::size_t t) {
    const std::size_t chunk = (kBlockSize + numThreads - 1) / numThreads;

    for (std::size_t b = 0; b < numBlocks; ++b) {
      const std::size_t base = b * kBlockSize;
      const std::size_t blockLen = std::min(kBlockSize, n - base);

      const std::size_t start = std::min(t * chunk, blockLen);
      const std::size_t end = std::min(start + chunk, blockLen);

      std::uint64_t lane_sum = 0;
      for (std::size_t i = start; i < end; ++i) {
        lane_sum += input[base + i];
        output[base + i] = lane_sum;  // local (within-lane) inclusive scan
      }
      laneA[t] = lane_sum;

      sync_point.arrive_and_wait();

      // Scan the per-lane totals held in the shared array, in steps,
      // with every thread of the team waiting at a barrier before any
      // thread starts the next step.
      std::uint64_t *cur = laneA;
      std::uint64_t *nxt = laneB;
      const std::size_t steps = stepsFor(numThreads);
      for (std::size_t s = 0; s < steps; ++s) {
        const std::size_t d = std::size_t{1} << s;
        std::uint64_t val = cur[t];
        if (t >= d) val += cur[t - d];
        nxt[t] = val;
        sync_point.arrive_and_wait();
        std::swap(cur, nxt);
      }

      const std::uint64_t offset = runningOffset + (cur[t] - lane_sum);
      for (std::size_t i = start; i < end; ++i) output[base + i] += offset;

      sync_point.arrive_and_wait();

      if (t == 0) runningOffset += cur[numThreads - 1];
    }
  };

  std::vector<std::thread> threads;
  threads.reserve(numThreads);
  for (std::size_t t = 0; t < numThreads; ++t) threads.emplace_back(worker, t);
  for (auto &th : threads) th.join();

  std::string out;
  out.reserve(n * 12);
  for (std::size_t i = 0; i < n; ++i) {
    out += std::to_string(output[i]);
    out += '\n';
  }
  std::cout << out;

  return 0;
}

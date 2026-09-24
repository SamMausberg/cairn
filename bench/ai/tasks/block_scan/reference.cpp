// Running totals as a GPU computes them: a team of 256 threads scans each block of 256 values in a shared array,
// meeting at a barrier around every step, and the block offsets are added after the team is done.
#include <barrier>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

constexpr std::size_t kBlock = 256;

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t n = 0;
  if (!(std::cin >> n)) return 1;
  std::vector<std::uint64_t> x(n);
  for (auto& v : x) std::cin >> v;

  std::size_t blocks = (n + kBlock - 1) / kBlock;
  std::vector<std::uint64_t> out(n), totals(blocks);
  std::vector<std::uint64_t> shared(kBlock);
  std::barrier team(kBlock);
  std::vector<std::thread> threads;
  for (std::size_t t = 0; t < kBlock; ++t) {
    threads.emplace_back([&, t] {
      for (std::size_t b = 0; b < blocks; ++b) {
        std::size_t i = b * kBlock + t;
        shared[t] = i < n ? x[i] : 0;
        team.arrive_and_wait();
        for (std::size_t d = 1; d < kBlock; d *= 2) {
          std::uint64_t v = t >= d ? shared[t - d] : 0;
          team.arrive_and_wait();  // every thread has read before any writes
          shared[t] += v;
          team.arrive_and_wait();  // every thread has written before the next step reads
        }
        if (i < n) out[i] = shared[t];
        if (t == kBlock - 1) totals[b] = shared[t];
        team.arrive_and_wait();  // the block is out before the next one overwrites the shared array
      }
    });
  }
  for (auto& t : threads) t.join();

  std::uint64_t before = 0;
  std::string text;
  for (std::size_t b = 0; b < blocks; ++b) {
    for (std::size_t i = b * kBlock; i < n && i < (b + 1) * kBlock; ++i) text += std::to_string(out[i] + before) + '\n';
    before += totals[b];
  }
  std::cout << text;
  return 0;
}

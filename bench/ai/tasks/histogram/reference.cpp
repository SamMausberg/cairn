#include <array>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t n = 0;
  unsigned shift = 0;
  if (!(std::cin >> n >> shift)) return 1;
  std::vector<std::uint32_t> values(n);
  for (auto& v : values) std::cin >> v;

  // Each worker counts its own contiguous part into its own row; the rows are added after every join.
  constexpr std::size_t workers = 4;
  std::vector<std::array<std::uint64_t, 256>> partial(workers);
  std::vector<std::thread> threads;
  for (std::size_t w = 0; w < workers; ++w) {
    threads.emplace_back([&, w] {
      auto& counts = partial[w];
      counts.fill(0);
      for (std::size_t i = n * w / workers; i < n * (w + 1) / workers; ++i) ++counts[(values[i] >> shift) & 255];
    });
  }
  for (auto& t : threads) t.join();
  for (int b = 0; b < 256; ++b) {
    std::uint64_t total = 0;
    for (const auto& counts : partial) total += counts[b];
    if (total != 0) std::cout << b << ' ' << total << '\n';
  }
  return 0;
}

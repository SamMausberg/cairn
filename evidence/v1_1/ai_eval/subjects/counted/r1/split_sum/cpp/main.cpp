// Adds n unsigned 64-bit values modulo 2^64, one contiguous chunk per thread.
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

static std::uint64_t chunk_sum(const std::vector<std::uint64_t>& values, std::size_t lo, std::size_t hi) {
  std::uint64_t total = 0;
  for (std::size_t i = lo; i < hi; ++i) total += values[i];  // unsigned: wraps modulo 2^64
  return total;
}

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t k = 0, n = 0;
  if (!(std::cin >> k >> n)) return 1;
  std::vector<std::uint64_t> values(n);
  for (auto& v : values) std::cin >> v;

  std::vector<std::uint64_t> partial(k);
  std::vector<std::thread> threads;
  std::size_t base = n / k;
  std::size_t rem = n % k;
  std::size_t lo = 0;
  for (std::size_t j = 0; j < k; ++j) {
    std::size_t size = base + (j < rem ? 1 : 0);
    std::size_t hi = lo + size;
    threads.emplace_back([&, j, lo, hi] { partial[j] = chunk_sum(values, lo, hi); });
    lo = hi;
  }
  for (auto& t : threads) t.join();

  std::uint64_t total = 0;
  for (std::uint64_t p : partial) total += p;
  std::cout << "sum " << total << '\n';
  return 0;
}

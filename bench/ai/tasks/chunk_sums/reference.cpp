#include <cstdint>
#include <iostream>
#include <optional>
#include <thread>
#include <vector>

// The left-to-right sum of values[lo, hi), or nothing when a partial sum leaves the int64_t range.
static std::optional<std::int64_t> checked_sum(const std::vector<std::int64_t>& values, std::size_t lo, std::size_t hi) {
  std::int64_t total = 0;
  for (std::size_t i = lo; i < hi; ++i)
    if (__builtin_add_overflow(total, values[i], &total)) return std::nullopt;
  return total;
}

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t k = 0, n = 0;
  if (!(std::cin >> k >> n)) return 1;
  std::vector<std::int64_t> values(n);
  for (auto& v : values) std::cin >> v;

  std::vector<std::optional<std::int64_t>> sums(k);
  std::vector<std::thread> threads;
  for (std::size_t j = 0; j < k; ++j)
    threads.emplace_back([&, j] { sums[j] = checked_sum(values, j * n / k, (j + 1) * n / k); });
  for (auto& t : threads) t.join();

  bool broken = false;
  std::int64_t total = 0;
  for (std::size_t j = 0; j < k; ++j) {
    if (!sums[j]) {
      std::cout << "chunk " << j << " overflow\n";
      broken = true;
      continue;
    }
    std::cout << "chunk " << j << ' ' << *sums[j] << '\n';
    if (!broken && __builtin_add_overflow(total, *sums[j], &total)) broken = true;
  }
  if (broken)
    std::cout << "total overflow\n";
  else
    std::cout << "total " << total << '\n';
  return 0;
}

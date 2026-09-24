// Tallies the values above a threshold on k threads, one contiguous chunk each.
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

struct Tally {
  std::uint64_t above = 0, sum = 0, max = 0;
};

static void tally_chunk(const std::vector<std::uint64_t>& values, std::size_t lo, std::size_t hi, std::uint64_t t, Tally& out) {
  for (std::size_t i = lo; i < hi; ++i) {
    if (values[i] > t) {
      out.above += 1;
      out.sum += values[i];  // unsigned: wraps modulo 2^64
    }
    if (values[i] > out.max) out.max = values[i];
  }
}

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t k = 0, n = 0;
  std::uint64_t t = 0;
  if (!(std::cin >> k >> n >> t)) return 1;
  std::vector<std::uint64_t> values(n);
  for (auto& v : values) std::cin >> v;

  std::vector<Tally> partials(k);
  std::vector<std::thread> threads;
  for (std::size_t j = 0; j < k; ++j) {
    std::size_t lo = n * j / k, hi = n * (j + 1) / k;
    threads.emplace_back([&, lo, hi, j] { tally_chunk(values, lo, hi, t, partials[j]); });
  }
  for (auto& th : threads) th.join();

  Tally total;
  for (const auto& p : partials) {
    total.above += p.above;
    total.sum += p.sum;
    if (p.max > total.max) total.max = p.max;
  }

  std::cout << "above " << total.above << "\nsum " << total.sum << '\n';
  if (n == 0) std::cout << "max none\n";
  else std::cout << "max " << total.max << '\n';
  return 0;
}

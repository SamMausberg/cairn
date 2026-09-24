// Primes up to N by a segmented sieve: each thread sieves its own segment in storage of its own.
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

struct Segment {
  std::uint64_t count = 0, sum = 0, first = 0, last = 0, gap = 0, gap_at = 0;
};

// The primes in [lo, hi), sieved by the base primes up to the square root of the bound.
static Segment sieve_segment(std::uint64_t lo, std::uint64_t hi, const std::vector<std::uint64_t>& base) {
  Segment s;
  if (lo >= hi) return s;
  std::vector<char> composite(hi - lo, 0);
  for (std::uint64_t p : base) {
    if (p * p >= hi) break;
    std::uint64_t start = std::max(p * p, (lo + p - 1) / p * p);
    for (std::uint64_t m = start; m < hi; m += p) composite[m - lo] = 1;
  }
  for (std::uint64_t v = std::max<std::uint64_t>(lo, 2); v < hi; ++v) {
    if (composite[v - lo]) continue;
    if (s.count == 0) s.first = v;
    else if (v - s.last > s.gap) s.gap = v - s.last, s.gap_at = s.last;
    s.last = v;
    ++s.count;
    s.sum += v;
  }
  return s;
}

int main() {
  std::uint64_t n = 0;
  if (!(std::cin >> n)) return 1;
  std::uint64_t root = 1;
  while ((root + 1) * (root + 1) <= n) ++root;
  std::vector<std::uint64_t> base;
  std::vector<char> small(root + 1, 0);
  for (std::uint64_t p = 2; p <= root; ++p) {
    if (small[p]) continue;
    base.push_back(p);
    for (std::uint64_t m = p * p; m <= root; m += p) small[m] = 1;
  }

  constexpr std::uint64_t workers = 8;
  std::vector<Segment> parts(workers);
  std::vector<std::thread> threads;
  for (std::uint64_t w = 0; w < workers; ++w) {
    std::uint64_t lo = (n + 1) * w / workers, hi = (n + 1) * (w + 1) / workers;
    threads.emplace_back([&, w, lo, hi] { parts[w] = sieve_segment(lo, hi, base); });
  }
  for (auto& t : threads) t.join();

  Segment all;
  for (const Segment& s : parts) {
    if (s.count == 0) continue;
    if (all.count != 0 && s.first - all.last > all.gap) all.gap = s.first - all.last, all.gap_at = all.last;
    if (s.gap > all.gap) all.gap = s.gap, all.gap_at = s.gap_at;
    if (all.count == 0) all.first = s.first;
    all.last = s.last;
    all.count += s.count;
    all.sum += s.sum;
  }
  std::cout << "count " << all.count << "\nsum " << all.sum << '\n';
  if (all.count == 0) std::cout << "last none\n";
  else std::cout << "last " << all.last << '\n';
  if (all.count < 2) std::cout << "gap none\n";
  else std::cout << "gap " << all.gap << ' ' << all.gap_at << '\n';
  return 0;
}

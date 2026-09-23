// Summarizes runs of equal readings in a sorted list: each distinct reading with its count.
#include <cstdint>
#include <iostream>
#include <vector>

struct Run {
  std::int64_t value;
  std::uint64_t count;
};

static std::vector<Run> runs(const std::vector<std::int64_t>& readings) {
  std::vector<Run> out;
  std::size_t n = readings.size();
  std::uint64_t count = 1;
  for (std::size_t i = 0; i < n - 1; ++i) {
    if (readings[i + 1] == readings[i]) {
      count += 1;
    } else {
      out.push_back({readings[i], count});
      count = 1;
    }
  }
  out.push_back({readings[n - 1], count});
  return out;
}

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t n = 0;
  if (!(std::cin >> n)) return 1;
  std::vector<std::int64_t> readings(n);
  for (auto& r : readings) std::cin >> r;
  std::vector<Run> summary = runs(readings);
  for (const Run& r : summary) std::cout << r.value << " x" << r.count << '\n';
  std::cout << "distinct " << summary.size() << '\n';
  return 0;
}

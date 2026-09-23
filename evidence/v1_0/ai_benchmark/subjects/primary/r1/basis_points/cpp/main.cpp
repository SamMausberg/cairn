// Reports each usage as a share of its quota in basis points, rounded down.
#include <cstdint>
#include <iostream>

// floor(used * 10000 / total).
static std::uint64_t basis_points(std::uint64_t used, std::uint64_t total) {
  return static_cast<std::uint64_t>(
      static_cast<unsigned __int128>(used) * 10000 / total);
}

int main() {
  std::ios::sync_with_stdio(false);
  std::size_t m = 0;
  if (!(std::cin >> m)) return 1;
  for (std::size_t i = 0; i < m; ++i) {
    std::uint64_t used = 0, total = 0;
    std::cin >> used >> total;
    if (total == 0)
      std::cout << "undefined\n";
    else
      std::cout << basis_points(used, total) << " bp\n";
  }
  return 0;
}

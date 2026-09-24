#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <string>
#include <thread>
#include <vector>

// Reads the whole of standard input.
static std::string read_input() {
  std::string data;
  char buf[1 << 16];
  size_t n;
  while ((n = std::fread(buf, 1, sizeof(buf), stdin)) > 0) data.append(buf, n);
  return data;
}

// Parses the next unsigned integer starting at pos, advancing pos past it.
static uint64_t parse_uint(const std::string& s, size_t& pos) {
  while (pos < s.size() && (s[pos] == ' ' || s[pos] == '\t' || s[pos] == '\n' || s[pos] == '\r')) ++pos;
  uint64_t value = 0;
  while (pos < s.size() && s[pos] >= '0' && s[pos] <= '9') {
    value = value * 10 + static_cast<uint64_t>(s[pos] - '0');
    ++pos;
  }
  return value;
}

int main() {
  const std::string input = read_input();
  size_t pos = 0;
  const uint64_t n = parse_uint(input, pos);
  const uint64_t shift = parse_uint(input, pos);

  std::vector<uint32_t> values(n);
  for (uint64_t i = 0; i < n; ++i) values[i] = static_cast<uint32_t>(parse_uint(input, pos));

  const unsigned hw = std::thread::hardware_concurrency();
  unsigned num_threads = std::max(2u, hw == 0 ? 4u : hw);
  if (n > 0 && static_cast<uint64_t>(num_threads) > n) num_threads = static_cast<unsigned>(n);
  if (num_threads < 2) num_threads = 2;

  std::vector<std::array<uint64_t, 256>> partial(num_threads);
  for (auto& arr : partial) arr.fill(0);

  std::vector<std::thread> threads;
  threads.reserve(num_threads);
  const uint64_t chunk = n / num_threads;
  const uint64_t remainder = n % num_threads;
  uint64_t start = 0;
  for (unsigned t = 0; t < num_threads; ++t) {
    uint64_t count = chunk + (t < remainder ? 1 : 0);
    uint64_t end = start + count;
    threads.emplace_back([&values, &partial, t, start, end, shift]() {
      auto& local = partial[t];
      for (uint64_t i = start; i < end; ++i) {
        unsigned bin = static_cast<unsigned>((values[i] >> shift) & 255u);
        ++local[bin];
      }
    });
    start = end;
  }
  for (auto& th : threads) th.join();

  std::array<uint64_t, 256> total{};
  for (const auto& arr : partial)
    for (int b = 0; b < 256; ++b) total[b] += arr[b];

  std::string out;
  char line[32];
  for (int b = 0; b < 256; ++b) {
    if (total[b] != 0) {
      int len = std::snprintf(line, sizeof(line), "%d %llu\n", b, static_cast<unsigned long long>(total[b]));
      out.append(line, len);
    }
  }
  std::fwrite(out.data(), 1, out.size(), stdout);
  return 0;
}

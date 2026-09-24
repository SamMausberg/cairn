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
  while ((n = fread(buf, 1, sizeof(buf), stdin)) > 0) data.append(buf, n);
  return data;
}

// Parses whitespace-separated unsigned decimal integers from a buffer.
class Parser {
 public:
  explicit Parser(const std::string &s) : p_(s.data()), end_(s.data() + s.size()) {}

  bool next(uint64_t &out) {
    while (p_ < end_ && (*p_ < '0' || *p_ > '9')) ++p_;
    if (p_ >= end_) return false;
    uint64_t v = 0;
    while (p_ < end_ && *p_ >= '0' && *p_ <= '9') {
      v = v * 10 + static_cast<uint64_t>(*p_ - '0');
      ++p_;
    }
    out = v;
    return true;
  }

 private:
  const char *p_;
  const char *end_;
};

int main() {
  std::string input = read_input();
  Parser parser(input);

  uint64_t n_raw = 0, shift_raw = 0;
  parser.next(n_raw);
  parser.next(shift_raw);
  const size_t n = static_cast<size_t>(n_raw);
  const uint32_t shift = static_cast<uint32_t>(shift_raw);

  std::vector<uint32_t> values(n);
  for (size_t i = 0; i < n; ++i) {
    uint64_t v = 0;
    parser.next(v);
    values[i] = static_cast<uint32_t>(v);
  }

  unsigned hc = std::thread::hardware_concurrency();
  if (hc < 2) hc = 2;
  const unsigned num_threads = std::min(hc, 8u);

  std::vector<std::array<uint64_t, 256>> local(num_threads);
  for (auto &hist : local) hist.fill(0);

  std::vector<std::thread> threads;
  threads.reserve(num_threads);
  for (unsigned t = 0; t < num_threads; ++t) {
    const size_t start = n * t / num_threads;
    const size_t end = n * (t + 1) / num_threads;
    threads.emplace_back([&values, &local, t, start, end, shift] {
      auto &hist = local[t];
      for (size_t i = start; i < end; ++i) {
        const uint32_t bin = (values[i] >> shift) & 255u;
        ++hist[bin];
      }
    });
  }
  for (auto &th : threads) th.join();

  std::array<uint64_t, 256> total{};
  for (const auto &hist : local) {
    for (size_t b = 0; b < 256; ++b) total[b] += hist[b];
  }

  for (size_t b = 0; b < 256; ++b) {
    if (total[b] != 0) {
      std::printf("%zu %llu\n", b, static_cast<unsigned long long>(total[b]));
    }
  }

  return 0;
}

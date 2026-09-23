#include <cstdint>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

static int nibble(char c) { return c <= '9' ? c - '0' : c - 'a' + 10; }

int main() {
  std::size_t cap = 0;
  std::string hex;
  std::cin >> cap;
  std::cin >> hex;  // empty when the second line is
  std::vector<std::uint8_t> in(hex.size() / 2);
  for (std::size_t i = 0; i < in.size(); ++i) in[i] = static_cast<std::uint8_t>(nibble(hex[2 * i]) * 16 + nibble(hex[2 * i + 1]));

  std::vector<std::uint8_t> out;
  out.reserve(cap);
  std::size_t pos = 0;
  while (pos < in.size()) {
    std::size_t c = in[pos];
    std::size_t body = c < 128 ? c + 1 : 1;
    if (in.size() - pos - 1 < body) {
      std::cout << "err truncated " << pos << '\n';
      return 0;
    }
    std::size_t run = c < 128 ? c + 1 : c - 126;
    if (run > cap - out.size()) {
      std::cout << "err full " << pos << '\n';
      return 0;
    }
    if (c < 128)
      out.insert(out.end(), in.begin() + pos + 1, in.begin() + pos + 1 + run);
    else
      out.insert(out.end(), run, in[pos + 1]);
    pos += 1 + body;
  }
  std::uint64_t h = 14695981039346656037ull;
  for (std::uint8_t b : out) h = (h ^ b) * 1099511628211ull;
  std::printf("ok %zu %016llx\n", out.size(), static_cast<unsigned long long>(h));
  return 0;
}

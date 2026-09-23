// Decodes a stream of unsigned LEB128 integers given as hexadecimal on standard input.
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

static int nibble(char c) { return c <= '9' ? c - '0' : c - 'a' + 10; }

static std::vector<std::uint8_t> from_hex(const std::string& hex) {
  std::vector<std::uint8_t> bytes(hex.size() / 2);
  for (std::size_t i = 0; i < bytes.size(); ++i)
    bytes[i] = static_cast<std::uint8_t>(nibble(hex[2 * i]) * 16 + nibble(hex[2 * i + 1]));
  return bytes;
}

enum class Status { ok, truncated, overflow };

// Decodes the integer that starts at `pos`, advancing `pos` past it.
static Status decode(const std::vector<std::uint8_t>& bytes, std::size_t& pos, std::uint64_t& value) {
  value = 0;
  for (unsigned i = 0;; ++i) {
    std::uint8_t b = bytes[pos];
    if (i == 9 && b > 1) return Status::overflow;
    value |= static_cast<std::uint64_t>(b & 0x7f) << (7 * i);
    pos += 1;
    if (b < 0x80) return Status::ok;
  }
}

int main() {
  std::string hex;
  std::cin >> hex;
  std::vector<std::uint8_t> bytes = from_hex(hex);
  std::size_t pos = 0;
  while (pos < bytes.size()) {
    std::size_t start = pos;
    std::uint64_t value = 0;
    Status status = decode(bytes, pos, value);
    if (status == Status::truncated) {
      std::cout << "err truncated " << start << '\n';
      return 0;
    }
    if (status == Status::overflow) {
      std::cout << "err overflow " << start << '\n';
      return 0;
    }
    std::cout << value << '\n';
  }
  return 0;
}

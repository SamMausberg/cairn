// Prints field k of every row of a comma-separated file.
#include <cstdlib>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

// Writes field k of the row text[lo, hi), where hi is the row's newline or the end of the input.
static void print_field(const std::vector<char>& text, std::size_t lo, std::size_t hi, std::size_t k) {
  std::size_t start = lo;
  for (std::size_t f = 0; f < k; ++f) {
    while (start < hi && text[start] != ',') ++start;
    if (start == hi) {
      std::cout << "none\n";
      return;
    }
    ++start;  // past the comma
  }
  std::size_t end = start;
  while (text[end] != ',' && text[end] != '\n') ++end;
  std::cout.write(text.data() + start, static_cast<std::streamsize>(end - start));
  std::cout << '\n';
}

int main() {
  std::vector<char> text((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
  std::size_t pos = 0;
  while (pos < text.size() && text[pos] != '\n') ++pos;
  std::size_t k = std::strtoul(std::string(text.begin(), text.begin() + pos).c_str(), nullptr, 10);
  pos += 1;  // past the first line's newline
  while (pos < text.size()) {
    std::size_t hi = pos;
    while (hi < text.size() && text[hi] != '\n') ++hi;
    print_field(text, pos, hi, k);
    pos = hi + 1;
  }
  return 0;
}

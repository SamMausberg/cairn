#include <cstdint>
#include <iostream>
#include <iterator>
#include <string>
#include <string_view>
#include <vector>

struct Error {
  std::size_t column;
  const char* kind;
};

// A field that is not empty and holds only digits (and, for a price, points). `start` is its column.
static bool digits(std::string_view f, std::size_t start, bool price, Error& e) {
  if (f.empty()) return e = {start, "empty"}, false;
  for (std::size_t i = 0; i < f.size(); ++i) {
    bool ok = (f[i] >= '0' && f[i] <= '9') || (price && f[i] == '.');
    if (!ok) return e = {start + i, "digit"}, false;
  }
  return true;
}

// The decimal value of `f`, which holds only digits, or false when it passes `limit`.
static bool value(std::string_view f, std::uint64_t limit, std::uint64_t& out) {
  std::uint64_t v = 0;
  for (char c : f) {
    std::uint64_t d = static_cast<std::uint64_t>(c - '0');
    if (v > (limit - d) / 10) return false;
    v = v * 10 + d;
  }
  out = v;
  return true;
}

static std::string record(std::size_t number, std::string_view line) {
  std::vector<std::string_view> fields;
  std::size_t from = 0;
  for (;;) {
    std::size_t comma = line.find(',', from);
    if (comma == std::string_view::npos) {
      fields.push_back(line.substr(from));
      break;
    }
    fields.push_back(line.substr(from, comma - from));
    from = comma + 1;
  }
  auto err = [&](std::size_t column, const char* kind) {
    return "err " + std::to_string(number) + " " + std::to_string(column) + " " + kind;
  };
  if (fields.size() < 3) return err(line.size() + 1, "missing");
  if (fields.size() > 3) return err(fields[0].size() + fields[1].size() + fields[2].size() + 3, "extra");

  const std::uint64_t limits[2] = {4294967295u, 65535u};
  std::uint64_t values[3] = {};
  std::size_t start = 1;
  Error e{};
  for (int i = 0; i < 2; ++i) {
    if (!digits(fields[i], start, false, e)) return err(e.column, e.kind);
    if (!value(fields[i], limits[i], values[i])) return err(start, "range");
    start += fields[i].size() + 1;
  }
  std::string_view price = fields[2];
  if (!digits(price, start, true, e)) return err(e.column, e.kind);
  std::size_t dot = price.find('.');
  if (dot == 0 || dot == std::string_view::npos || price.size() - dot != 3 || price.find('.', dot + 1) != std::string_view::npos)
    return err(start, "format");
  std::uint64_t whole = 0, cents = 0;
  const std::uint64_t max = 9223372036854775807u;
  if (!value(price.substr(0, dot), max / 100, whole)) return err(start, "range");
  value(price.substr(dot + 1), 99, cents);
  if (whole * 100 > max - cents) return err(start, "range");
  return "ok " + std::to_string(values[0]) + " " + std::to_string(values[1]) + " " + std::to_string(whole * 100 + cents);
}

int main() {
  std::string input((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
  std::string_view rest(input);
  std::size_t number = 0;
  while (!rest.empty()) {
    std::size_t end = rest.find('\n');
    std::string_view line = rest.substr(0, end);
    std::cout << record(++number, line) << '\n';
    if (end == std::string_view::npos) break;
    rest.remove_prefix(end + 1);
  }
  return 0;
}

#include <cstdio>
#include <iostream>
#include <iterator>
#include <string>
#include <string_view>
#include <vector>

// Reads the whole of standard input.
static std::string read_input() { return std::string(std::istreambuf_iterator<char>(std::cin), {}); }

// Strips leading '0' bytes from a digit string. Returns "" if the value is zero.
static std::string_view strip_leading_zeros(std::string_view s) {
  size_t j = 0;
  while (j < s.size() && s[j] == '0') ++j;
  return s.substr(j);
}

// True if the decimal digit string (no leading zeros, may be empty meaning 0)
// represents a value greater than max_str (also no leading zeros).
static bool exceeds_max(std::string_view digits, std::string_view max_str) {
  std::string_view stripped = strip_leading_zeros(digits);
  if (stripped.size() != max_str.size()) return stripped.size() > max_str.size();
  return stripped > max_str;
}

int main() {
  std::string input = read_input();

  std::vector<std::string_view> lines;
  {
    std::string_view sv(input);
    size_t pos = 0;
    while (pos < sv.size()) {
      size_t nl = sv.find('\n', pos);
      if (nl == std::string_view::npos) {
        lines.push_back(sv.substr(pos));
        pos = sv.size();
      } else {
        lines.push_back(sv.substr(pos, nl - pos));
        pos = nl + 1;
      }
    }
  }

  static const std::string_view ID_MAX = "4294967295";
  static const std::string_view QTY_MAX = "65535";
  static const std::string_view CENTS_MAX = "9223372036854775807";

  std::string out;
  out.reserve(input.size() * 2);

  for (size_t li = 0; li < lines.size(); ++li) {
    std::string_view line = lines[li];
    size_t line_no = li + 1;

    // Find comma positions (0-based).
    std::vector<size_t> commas;
    for (size_t i = 0; i < line.size(); ++i) {
      if (line[i] == ',') {
        commas.push_back(i);
        if (commas.size() > 2) break;
      }
    }

    if (commas.size() < 2) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(line.size() + 1) + " missing\n";
      continue;
    }
    if (commas.size() > 2) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(commas[2] + 1) + " extra\n";
      continue;
    }

    // Field boundaries, 1-based columns, half-open [start, end).
    size_t id_start = 1, id_end = commas[0] + 1;
    size_t qty_start = commas[0] + 2, qty_end = commas[1] + 1;
    size_t price_start = commas[1] + 2, price_end = line.size() + 1;

    std::string_view id_field = line.substr(id_start - 1, id_end - id_start);
    std::string_view qty_field = line.substr(qty_start - 1, qty_end - qty_start);
    std::string_view price_field = line.substr(price_start - 1, price_end - price_start);

    // --- id ---
    if (id_field.empty()) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(id_start) + " empty\n";
      continue;
    }
    {
      bool bad = false;
      for (size_t i = 0; i < id_field.size(); ++i) {
        if (id_field[i] < '0' || id_field[i] > '9') {
          out += "err " + std::to_string(line_no) + " " + std::to_string(id_start + i) + " digit\n";
          bad = true;
          break;
        }
      }
      if (bad) continue;
    }
    if (exceeds_max(id_field, ID_MAX)) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(id_start) + " range\n";
      continue;
    }

    // --- qty ---
    if (qty_field.empty()) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(qty_start) + " empty\n";
      continue;
    }
    {
      bool bad = false;
      for (size_t i = 0; i < qty_field.size(); ++i) {
        if (qty_field[i] < '0' || qty_field[i] > '9') {
          out += "err " + std::to_string(line_no) + " " + std::to_string(qty_start + i) + " digit\n";
          bad = true;
          break;
        }
      }
      if (bad) continue;
    }
    if (exceeds_max(qty_field, QTY_MAX)) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(qty_start) + " range\n";
      continue;
    }

    // --- price ---
    if (price_field.empty()) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(price_start) + " empty\n";
      continue;
    }
    {
      bool bad = false;
      for (size_t i = 0; i < price_field.size(); ++i) {
        char c = price_field[i];
        if (!((c >= '0' && c <= '9') || c == '.')) {
          out += "err " + std::to_string(line_no) + " " + std::to_string(price_start + i) + " digit\n";
          bad = true;
          break;
        }
      }
      if (bad) continue;
    }
    // Format check: one or more digits, one '.', exactly two digits.
    size_t dot_count = 0;
    size_t dot_pos = std::string_view::npos;
    for (size_t i = 0; i < price_field.size(); ++i) {
      if (price_field[i] == '.') {
        ++dot_count;
        if (dot_pos == std::string_view::npos) dot_pos = i;
      }
    }
    bool format_ok = false;
    std::string_view whole, frac;
    if (dot_count == 1) {
      whole = price_field.substr(0, dot_pos);
      frac = price_field.substr(dot_pos + 1);
      format_ok = !whole.empty() && frac.size() == 2;
    }
    if (!format_ok) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(price_start) + " format\n";
      continue;
    }
    std::string_view whole_stripped = strip_leading_zeros(whole);
    std::string cents_str;
    cents_str.reserve(whole_stripped.size() + 2);
    cents_str += whole_stripped;
    cents_str += frac;
    if (exceeds_max(cents_str, CENTS_MAX)) {
      out += "err " + std::to_string(line_no) + " " + std::to_string(price_start) + " range\n";
      continue;
    }

    std::string_view id_out = strip_leading_zeros(id_field);
    std::string_view qty_out = strip_leading_zeros(qty_field);
    std::string_view cents_out = strip_leading_zeros(cents_str);

    out += "ok ";
    out += (id_out.empty() ? "0" : std::string(id_out));
    out += ' ';
    out += (qty_out.empty() ? "0" : std::string(qty_out));
    out += ' ';
    out += (cents_out.empty() ? "0" : std::string(cents_out));
    out += '\n';
  }

  std::fwrite(out.data(), 1, out.size(), stdout);
  return 0;
}

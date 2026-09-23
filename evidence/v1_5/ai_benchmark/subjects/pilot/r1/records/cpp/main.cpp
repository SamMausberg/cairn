#include <cstdio>
#include <iostream>
#include <iterator>
#include <string>
#include <string_view>

static std::string read_input() {
  return std::string(std::istreambuf_iterator<char>(std::cin), {});
}

// Strips leading '0' bytes. Returns an empty view if the value is zero.
static std::string_view strip_zeros(std::string_view s) {
  size_t i = 0;
  while (i < s.size() && s[i] == '0') i++;
  return s.substr(i);
}

// Compares a stripped (no leading zero) decimal digit string against a
// known-in-range max digit string. True if value <= max.
static bool in_range(std::string_view stripped, std::string_view max_str) {
  if (stripped.size() != max_str.size()) return stripped.size() < max_str.size();
  return stripped <= max_str;
}

struct FieldResult {
  bool ok = false;
  std::string_view kind;  // valid when !ok
  size_t col = 0;          // valid when !ok
  std::string_view value;  // valid when ok: stripped digits (empty means 0)
};

// Checks id/qty fields: empty, digit, range.
static FieldResult check_int_field(std::string_view field, size_t start_col,
                                    std::string_view max_str) {
  FieldResult r;
  if (field.empty()) {
    r.kind = "empty";
    r.col = start_col;
    return r;
  }
  for (size_t i = 0; i < field.size(); i++) {
    if (field[i] < '0' || field[i] > '9') {
      r.kind = "digit";
      r.col = start_col + i;
      return r;
    }
  }
  std::string_view stripped = strip_zeros(field);
  if (!in_range(stripped, max_str)) {
    r.kind = "range";
    r.col = start_col;
    return r;
  }
  r.ok = true;
  r.value = stripped;
  return r;
}

// Checks the price field: empty, digit, format, range. cents_buf must
// outlive the returned value (used to hold the concatenated digits).
static FieldResult check_price_field(std::string_view field, size_t start_col,
                                      std::string& cents_buf) {
  FieldResult r;
  if (field.empty()) {
    r.kind = "empty";
    r.col = start_col;
    return r;
  }
  for (size_t i = 0; i < field.size(); i++) {
    char c = field[i];
    if (!((c >= '0' && c <= '9') || c == '.')) {
      r.kind = "digit";
      r.col = start_col + i;
      return r;
    }
  }
  size_t dot_count = 0;
  size_t dot_idx = 0;
  for (size_t i = 0; i < field.size(); i++) {
    if (field[i] == '.') {
      dot_count++;
      dot_idx = i;
    }
  }
  bool shape_ok = dot_count == 1 && dot_idx >= 1 &&
                  (field.size() - dot_idx - 1) == 2;
  if (!shape_ok) {
    r.kind = "format";
    r.col = start_col;
    return r;
  }
  cents_buf.clear();
  cents_buf.reserve(field.size() - 1);
  cents_buf.append(field.substr(0, dot_idx));
  cents_buf.append(field.substr(dot_idx + 1));
  std::string_view stripped = strip_zeros(cents_buf);
  static const std::string_view kMaxCents = "9223372036854775807";
  if (!in_range(stripped, kMaxCents)) {
    r.kind = "range";
    r.col = start_col;
    return r;
  }
  r.ok = true;
  r.value = stripped;
  return r;
}

static void process_line(std::string_view line, size_t line_no,
                          std::string& out) {
  static const std::string_view kMaxId = "4294967295";
  static const std::string_view kMaxQty = "65535";

  size_t comma[3];
  size_t comma_count = 0;
  for (size_t i = 0; i < line.size() && comma_count < 3; i++) {
    if (line[i] == ',') comma[comma_count++] = i;
  }

  if (comma_count < 2) {
    out += "err ";
    out += std::to_string(line_no);
    out += " ";
    out += std::to_string(line.size() + 1);
    out += " missing\n";
    return;
  }
  if (comma_count == 3) {
    out += "err ";
    out += std::to_string(line_no);
    out += " ";
    out += std::to_string(comma[2] + 1);
    out += " extra\n";
    return;
  }

  std::string_view id_f = line.substr(0, comma[0]);
  size_t id_col = 1;
  std::string_view qty_f = line.substr(comma[0] + 1, comma[1] - comma[0] - 1);
  size_t qty_col = comma[0] + 2;
  std::string_view price_f = line.substr(comma[1] + 1);
  size_t price_col = comma[1] + 2;

  FieldResult id_r = check_int_field(id_f, id_col, kMaxId);
  if (!id_r.ok) {
    out += "err ";
    out += std::to_string(line_no);
    out += " ";
    out += std::to_string(id_r.col);
    out += " ";
    out += id_r.kind;
    out += "\n";
    return;
  }
  FieldResult qty_r = check_int_field(qty_f, qty_col, kMaxQty);
  if (!qty_r.ok) {
    out += "err ";
    out += std::to_string(line_no);
    out += " ";
    out += std::to_string(qty_r.col);
    out += " ";
    out += qty_r.kind;
    out += "\n";
    return;
  }
  std::string cents_buf;
  FieldResult price_r = check_price_field(price_f, price_col, cents_buf);
  if (!price_r.ok) {
    out += "err ";
    out += std::to_string(line_no);
    out += " ";
    out += std::to_string(price_r.col);
    out += " ";
    out += price_r.kind;
    out += "\n";
    return;
  }

  out += "ok ";
  out += id_r.value.empty() ? std::string_view("0") : id_r.value;
  out += " ";
  out += qty_r.value.empty() ? std::string_view("0") : qty_r.value;
  out += " ";
  out += price_r.value.empty() ? std::string_view("0") : price_r.value;
  out += "\n";
}

int main() {
  std::ios::sync_with_stdio(false);
  std::string data = read_input();

  std::string out;
  out.reserve(data.size());

  size_t pos = 0;
  size_t line_no = 1;
  while (pos < data.size()) {
    size_t nl = data.find('\n', pos);
    std::string_view line;
    if (nl == std::string::npos) {
      line = std::string_view(data).substr(pos);
      pos = data.size();
    } else {
      line = std::string_view(data).substr(pos, nl - pos);
      pos = nl + 1;
    }
    process_line(line, line_no, out);
    line_no++;
  }

  std::fwrite(out.data(), 1, out.size(), stdout);
  return 0;
}

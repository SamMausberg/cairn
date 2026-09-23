#include <cstdint>
#include <iostream>
#include <numeric>
#include <sstream>
#include <string>
#include <vector>

// A slot keeps its generation after its buffer is freed, so an old handle can never name the next buffer.
struct Slot {
  std::uint64_t generation = 0;
  bool live = false;
  std::vector<std::uint8_t> bytes;
};

struct Pool {
  std::vector<Slot> slots;

  Slot* find(std::uint64_t i, std::uint64_t g) {
    if (i >= slots.size() || !slots[i].live || slots[i].generation != g) return nullptr;
    return &slots[i];
  }
};

int main() {
  std::ios::sync_with_stdio(false);
  Pool pool;
  std::string line;
  while (std::getline(std::cin, line)) {
    std::istringstream in(line);
    std::string op;
    in >> op;
    if (op == "new") {
      std::uint64_t size = 0;
      in >> size;
      std::size_t i = 0;
      while (i < pool.slots.size() && pool.slots[i].live) ++i;
      if (i == pool.slots.size()) pool.slots.emplace_back();
      Slot& s = pool.slots[i];
      s.generation += 1;
      s.live = true;
      s.bytes.assign(size, 0);
      std::cout << "h " << i << ' ' << s.generation << '\n';
    } else if (op == "set") {
      std::uint64_t i = 0, g = 0, off = 0, v = 0;
      in >> i >> g >> off >> v;
      Slot* s = pool.find(i, g);
      if (!s)
        std::cout << "stale\n";
      else if (off >= s->bytes.size())
        std::cout << "bounds\n";
      else {
        s->bytes[off] = static_cast<std::uint8_t>(v);
        std::cout << "ok\n";
      }
    } else if (op == "sum") {
      std::uint64_t i = 0, g = 0;
      in >> i >> g;
      Slot* s = pool.find(i, g);
      if (!s)
        std::cout << "stale\n";
      else
        std::cout << std::accumulate(s->bytes.begin(), s->bytes.end(), std::uint64_t{0}) << '\n';
    } else if (op == "move") {
      std::uint64_t i = 0, g = 0, j = 0, h = 0;
      in >> i >> g >> j >> h;
      Slot* from = pool.find(i, g);
      Slot* to = pool.find(j, h);
      if (!from || !to)
        std::cout << "stale\n";
      else if (from == to)
        std::cout << "same\n";
      else {
        to->bytes.insert(to->bytes.end(), from->bytes.begin(), from->bytes.end());
        from->bytes = {};
        from->live = false;
        std::cout << "ok " << to->bytes.size() << '\n';
      }
    } else if (op == "free") {
      std::uint64_t i = 0, g = 0;
      in >> i >> g;
      Slot* s = pool.find(i, g);
      if (!s)
        std::cout << "stale\n";
      else {
        s->bytes = {};
        s->live = false;
        std::cout << "ok\n";
      }
    }
  }
  std::uint64_t live = 0, bytes = 0;
  for (const Slot& s : pool.slots)
    if (s.live) live += 1, bytes += s.bytes.size();
  std::cout << "live " << live << ' ' << bytes << '\n';
  return 0;
}

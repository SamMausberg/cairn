#include <cstdint>
#include <iostream>
#include <queue>
#include <string>
#include <vector>

struct Slot {
    bool occupied = false;
    uint64_t gen = 0;
    std::vector<uint8_t> data;
};

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::vector<Slot> slots;
    std::priority_queue<uint64_t, std::vector<uint64_t>, std::greater<uint64_t>> freeList;

    auto isLive = [&](uint64_t idx, uint64_t gen) -> bool {
        return idx < slots.size() && slots[idx].occupied && slots[idx].gen == gen;
    };

    std::string cmd;
    std::string out;
    out.reserve(1 << 20);

    while (std::cin >> cmd) {
        if (cmd == "new") {
            uint64_t s;
            std::cin >> s;
            uint64_t idx;
            if (!freeList.empty()) {
                idx = freeList.top();
                freeList.pop();
            } else {
                idx = slots.size();
                slots.emplace_back();
            }
            slots[idx].occupied = true;
            slots[idx].gen += 1;
            slots[idx].data.assign(s, 0);
            out += "h ";
            out += std::to_string(idx);
            out += ' ';
            out += std::to_string(slots[idx].gen);
            out += '\n';
        } else if (cmd == "set") {
            uint64_t i, g, off, v;
            std::cin >> i >> g >> off >> v;
            if (!isLive(i, g)) {
                out += "stale\n";
            } else if (off >= slots[i].data.size()) {
                out += "bounds\n";
            } else {
                slots[i].data[off] = static_cast<uint8_t>(v);
                out += "ok\n";
            }
        } else if (cmd == "sum") {
            uint64_t i, g;
            std::cin >> i >> g;
            if (!isLive(i, g)) {
                out += "stale\n";
            } else {
                uint64_t total = 0;
                for (uint8_t b : slots[i].data) total += b;
                out += std::to_string(total);
                out += '\n';
            }
        } else if (cmd == "move") {
            uint64_t i, g, j, h;
            std::cin >> i >> g >> j >> h;
            if (!isLive(i, g) || !isLive(j, h)) {
                out += "stale\n";
            } else if (i == j && g == h) {
                out += "same\n";
            } else {
                auto &src = slots[i].data;
                auto &dst = slots[j].data;
                dst.insert(dst.end(), src.begin(), src.end());
                slots[i].occupied = false;
                slots[i].data.clear();
                slots[i].data.shrink_to_fit();
                freeList.push(i);
                out += "ok ";
                out += std::to_string(dst.size());
                out += '\n';
            }
        } else if (cmd == "free") {
            uint64_t i, g;
            std::cin >> i >> g;
            if (!isLive(i, g)) {
                out += "stale\n";
            } else {
                slots[i].occupied = false;
                slots[i].data.clear();
                slots[i].data.shrink_to_fit();
                freeList.push(i);
                out += "ok\n";
            }
        }
    }

    uint64_t liveCount = 0;
    uint64_t liveBytes = 0;
    for (auto &s : slots) {
        if (s.occupied) {
            liveCount += 1;
            liveBytes += s.data.size();
        }
    }
    out += "live ";
    out += std::to_string(liveCount);
    out += ' ';
    out += std::to_string(liveBytes);
    out += '\n';

    std::cout << out;
    return 0;
}

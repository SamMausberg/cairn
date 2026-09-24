#include <cstdint>
#include <iostream>
#include <set>
#include <string>
#include <vector>

struct Slot {
    uint64_t gen = 0;
    bool occupied = false;
    std::vector<uint8_t> data;
};

static bool isLive(const std::vector<Slot>& slots, uint64_t idx, uint64_t g) {
    return idx < slots.size() && slots[idx].occupied && slots[idx].gen == g;
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::vector<Slot> slots;
    std::set<uint64_t> freeSlots;

    uint64_t liveCount = 0;
    uint64_t liveBytes = 0;

    std::string cmd;
    while (std::cin >> cmd) {
        if (cmd == "new") {
            uint64_t s;
            std::cin >> s;
            uint64_t idx;
            if (!freeSlots.empty()) {
                idx = *freeSlots.begin();
                freeSlots.erase(freeSlots.begin());
            } else {
                idx = slots.size();
                slots.emplace_back();
            }
            slots[idx].gen += 1;
            slots[idx].occupied = true;
            slots[idx].data.assign(static_cast<size_t>(s), 0);
            liveCount += 1;
            liveBytes += s;
            std::cout << "h " << idx << " " << slots[idx].gen << "\n";
        } else if (cmd == "set") {
            uint64_t i, g, off, v;
            std::cin >> i >> g >> off >> v;
            if (!isLive(slots, i, g)) {
                std::cout << "stale\n";
            } else if (off >= slots[i].data.size()) {
                std::cout << "bounds\n";
            } else {
                slots[i].data[static_cast<size_t>(off)] = static_cast<uint8_t>(v);
                std::cout << "ok\n";
            }
        } else if (cmd == "sum") {
            uint64_t i, g;
            std::cin >> i >> g;
            if (!isLive(slots, i, g)) {
                std::cout << "stale\n";
            } else {
                uint64_t total = 0;
                for (uint8_t b : slots[i].data) total += b;
                std::cout << total << "\n";
            }
        } else if (cmd == "move") {
            uint64_t i, g, j, h;
            std::cin >> i >> g >> j >> h;
            bool srcLive = isLive(slots, i, g);
            bool dstLive = isLive(slots, j, h);
            if (!srcLive || !dstLive) {
                std::cout << "stale\n";
            } else if (i == j && g == h) {
                std::cout << "same\n";
            } else {
                auto& src = slots[i].data;
                auto& dst = slots[j].data;
                dst.insert(dst.end(), src.begin(), src.end());
                slots[i].occupied = false;
                slots[i].data.clear();
                slots[i].data.shrink_to_fit();
                freeSlots.insert(i);
                liveCount -= 1;
                std::cout << "ok " << dst.size() << "\n";
            }
        } else if (cmd == "free") {
            uint64_t i, g;
            std::cin >> i >> g;
            if (!isLive(slots, i, g)) {
                std::cout << "stale\n";
            } else {
                liveCount -= 1;
                liveBytes -= slots[i].data.size();
                slots[i].occupied = false;
                slots[i].data.clear();
                slots[i].data.shrink_to_fit();
                freeSlots.insert(i);
                std::cout << "ok\n";
            }
        }
    }

    std::cout << "live " << liveCount << " " << liveBytes << "\n";
    return 0;
}

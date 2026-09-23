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
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::vector<Slot> slots;
    std::priority_queue<uint64_t, std::vector<uint64_t>, std::greater<uint64_t>> freeSlots;

    std::string out;
    out.reserve(1 << 20);

    auto isLive = [&](uint64_t i, uint64_t g) -> bool {
        return i < slots.size() && slots[i].occupied && slots[i].gen == g;
    };

    auto freeSlot = [&](uint64_t i) {
        slots[i].occupied = false;
        slots[i].data.clear();
        slots[i].data.shrink_to_fit();
        freeSlots.push(i);
    };

    std::string cmd;
    while (std::cin >> cmd) {
        if (cmd == "new") {
            uint64_t S;
            std::cin >> S;
            uint64_t i;
            if (!freeSlots.empty()) {
                i = freeSlots.top();
                freeSlots.pop();
            } else {
                i = slots.size();
                slots.emplace_back();
            }
            slots[i].occupied = true;
            slots[i].gen += 1;
            slots[i].data.assign(S, 0);
            out += "h ";
            out += std::to_string(i);
            out += ' ';
            out += std::to_string(slots[i].gen);
            out += '\n';
        } else if (cmd == "set") {
            uint64_t I, G, OFF, V;
            std::cin >> I >> G >> OFF >> V;
            if (!isLive(I, G)) {
                out += "stale\n";
            } else if (OFF >= slots[I].data.size()) {
                out += "bounds\n";
            } else {
                slots[I].data[OFF] = static_cast<uint8_t>(V);
                out += "ok\n";
            }
        } else if (cmd == "sum") {
            uint64_t I, G;
            std::cin >> I >> G;
            if (!isLive(I, G)) {
                out += "stale\n";
            } else {
                uint64_t s = 0;
                for (uint8_t b : slots[I].data) s += b;
                out += std::to_string(s);
                out += '\n';
            }
        } else if (cmd == "move") {
            uint64_t I, G, J, H;
            std::cin >> I >> G >> J >> H;
            if (!isLive(I, G) || !isLive(J, H)) {
                out += "stale\n";
            } else if (I == J && G == H) {
                out += "same\n";
            } else {
                auto &src = slots[I].data;
                auto &dst = slots[J].data;
                dst.insert(dst.end(), src.begin(), src.end());
                uint64_t newSize = dst.size();
                freeSlot(I);
                out += "ok ";
                out += std::to_string(newSize);
                out += '\n';
            }
        } else if (cmd == "free") {
            uint64_t I, G;
            std::cin >> I >> G;
            if (!isLive(I, G)) {
                out += "stale\n";
            } else {
                freeSlot(I);
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

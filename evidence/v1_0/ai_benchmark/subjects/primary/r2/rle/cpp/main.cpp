#include <cstdint>
#include <cstdio>
#include <iostream>
#include <string>
#include <vector>

static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    return c - 'a' + 10;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    std::string capLine, hexLine;
    std::getline(std::cin, capLine);
    std::getline(std::cin, hexLine);

    uint64_t cap = 0;
    for (char c : capLine) {
        if (c < '0' || c > '9') continue;
        cap = cap * 10 + (uint64_t)(c - '0');
    }

    std::vector<uint8_t> in;
    in.reserve(hexLine.size() / 2);
    for (size_t i = 0; i + 1 < hexLine.size(); i += 2) {
        uint8_t b = (uint8_t)((hexVal(hexLine[i]) << 4) | hexVal(hexLine[i + 1]));
        in.push_back(b);
    }

    size_t n = in.size();
    size_t i = 0;
    uint64_t outLen = 0;
    uint64_t hash = 14695981039346656037ULL;

    while (i < n) {
        size_t offset = i;
        uint8_t c = in[i];
        if (c < 128) {
            size_t len = (size_t)c + 1;
            if (i + 1 + len > n) {
                std::cout << "err truncated " << offset << "\n";
                return 0;
            }
            if (outLen + len > cap) {
                std::cout << "err full " << offset << "\n";
                return 0;
            }
            for (size_t k = 0; k < len; k++) {
                uint8_t b = in[i + 1 + k];
                hash = (hash ^ b) * 1099511628211ULL;
            }
            outLen += len;
            i += 1 + len;
        } else {
            size_t count = (size_t)c - 126;
            if (i + 1 >= n) {
                std::cout << "err truncated " << offset << "\n";
                return 0;
            }
            if (outLen + count > cap) {
                std::cout << "err full " << offset << "\n";
                return 0;
            }
            uint8_t b = in[i + 1];
            for (size_t k = 0; k < count; k++) {
                hash = (hash ^ b) * 1099511628211ULL;
            }
            outLen += count;
            i += 2;
        }
    }

    char buf[17];
    std::snprintf(buf, sizeof(buf), "%016llx", (unsigned long long)hash);
    std::cout << "ok " << outLen << " " << buf << "\n";
    return 0;
}

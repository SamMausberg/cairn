#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include <iomanip>

static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    return c - 'a' + 10;
}

int main() {
    long long cap;
    std::cin >> cap;
    std::string hexLine;
    std::cin >> hexLine;
    if (!std::cin) {
        hexLine.clear();
    }

    std::vector<uint8_t> bytes;
    bytes.reserve(hexLine.size() / 2);
    for (size_t i = 0; i + 1 < hexLine.size(); i += 2) {
        uint8_t b = static_cast<uint8_t>((hexVal(hexLine[i]) << 4) | hexVal(hexLine[i + 1]));
        bytes.push_back(b);
    }

    size_t n = bytes.size();
    size_t i = 0;
    long long outLen = 0;
    uint64_t hash = 14695981039346656037ULL;
    const uint64_t prime = 1099511628211ULL;

    while (i < n) {
        size_t o = i;
        uint8_t c = bytes[i];
        i++;
        long long count;
        if (c < 128) {
            count = static_cast<long long>(c) + 1;
            if (i + static_cast<size_t>(count) > n) {
                std::cout << "err truncated " << o << "\n";
                return 0;
            }
            if (outLen + count > cap) {
                std::cout << "err full " << o << "\n";
                return 0;
            }
            for (long long k = 0; k < count; k++) {
                hash = (hash ^ bytes[i + k]) * prime;
            }
            outLen += count;
            i += static_cast<size_t>(count);
        } else {
            count = static_cast<long long>(c) - 126;
            if (i + 1 > n) {
                std::cout << "err truncated " << o << "\n";
                return 0;
            }
            uint8_t b = bytes[i];
            if (outLen + count > cap) {
                std::cout << "err full " << o << "\n";
                return 0;
            }
            for (long long k = 0; k < count; k++) {
                hash = (hash ^ b) * prime;
            }
            outLen += count;
            i += 1;
        }
    }

    std::cout << "ok " << outLen << " " << std::hex << std::setfill('0') << std::setw(16) << hash << "\n";
    return 0;
}

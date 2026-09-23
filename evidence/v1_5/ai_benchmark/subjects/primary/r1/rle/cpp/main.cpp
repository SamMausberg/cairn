#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
#include <cctype>

static int hexVal(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

int main() {
    long long cap;
    std::string capLine, hexLine;
    if (!std::getline(std::cin, capLine)) return 1;
    cap = std::stoll(capLine);
    if (!std::getline(std::cin, hexLine)) hexLine.clear();

    std::vector<uint8_t> bytes;
    bytes.reserve(hexLine.size() / 2);
    for (size_t i = 0; i + 1 < hexLine.size(); i += 2) {
        int hi = hexVal(hexLine[i]);
        int lo = hexVal(hexLine[i + 1]);
        if (hi < 0 || lo < 0) break;
        bytes.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }

    size_t pos = 0;
    long long outLen = 0;
    uint64_t hash = 14695981039346656037ULL;

    auto hashByte = [&](uint8_t b) {
        hash = (hash ^ b) * 1099511628211ULL;
    };

    while (pos < bytes.size()) {
        size_t offset = pos;
        uint8_t c = bytes[pos];
        if (c < 128) {
            long long len = static_cast<long long>(c) + 1;
            if (pos + 1 + static_cast<size_t>(len) > bytes.size()) {
                std::cout << "err truncated " << offset << "\n";
                return 0;
            }
            long long newLen = outLen + len;
            if (newLen > cap) {
                std::cout << "err full " << offset << "\n";
                return 0;
            }
            for (long long i = 0; i < len; i++) {
                hashByte(bytes[pos + 1 + static_cast<size_t>(i)]);
            }
            outLen = newLen;
            pos += 1 + static_cast<size_t>(len);
        } else {
            long long count = static_cast<long long>(c) - 126;
            if (pos + 1 >= bytes.size()) {
                std::cout << "err truncated " << offset << "\n";
                return 0;
            }
            uint8_t value = bytes[pos + 1];
            long long newLen = outLen + count;
            if (newLen > cap) {
                std::cout << "err full " << offset << "\n";
                return 0;
            }
            for (long long i = 0; i < count; i++) {
                hashByte(value);
            }
            outLen = newLen;
            pos += 2;
        }
    }

    char buf[17];
    std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(hash));
    std::cout << "ok " << outLen << " " << buf << "\n";
    return 0;
}

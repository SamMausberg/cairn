#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

namespace {

void sumChunk(const std::vector<int64_t>& values, size_t begin, size_t end,
              int64_t& outSum, uint8_t& outOverflow) {
    int64_t sum = 0;
    bool overflow = false;
    for (size_t i = begin; i < end; ++i) {
        int64_t next;
        if (__builtin_add_overflow(sum, values[i], &next)) {
            overflow = true;
            break;
        }
        sum = next;
    }
    outSum = sum;
    outOverflow = overflow ? 1 : 0;
}

}  // namespace

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int64_t k64 = 0, n64 = 0;
    if (!(std::cin >> k64 >> n64)) {
        return 1;
    }
    size_t k = static_cast<size_t>(k64);
    size_t n = static_cast<size_t>(n64);

    std::vector<int64_t> values(n);
    for (size_t i = 0; i < n; ++i) {
        std::cin >> values[i];
    }

    std::vector<int64_t> sums(k, 0);
    std::vector<uint8_t> overflowed(k, 0);
    std::vector<std::thread> threads;
    threads.reserve(k);

    for (size_t j = 0; j < k; ++j) {
        size_t begin = static_cast<size_t>((static_cast<int64_t>(j) * n64) / k64);
        size_t end = static_cast<size_t>((static_cast<int64_t>(j + 1) * n64) / k64);
        threads.emplace_back(sumChunk, std::cref(values), begin, end,
                              std::ref(sums[j]), std::ref(overflowed[j]));
    }

    for (auto& t : threads) {
        t.join();
    }

    bool totalOverflow = false;
    int64_t total = 0;
    for (size_t j = 0; j < k; ++j) {
        if (overflowed[j]) {
            std::cout << "chunk " << j << " overflow\n";
            totalOverflow = true;
        } else {
            std::cout << "chunk " << j << " " << sums[j] << "\n";
        }
        if (!totalOverflow) {
            int64_t next;
            if (__builtin_add_overflow(total, sums[j], &next)) {
                totalOverflow = true;
            } else {
                total = next;
            }
        }
    }

    if (totalOverflow) {
        std::cout << "total overflow\n";
    } else {
        std::cout << "total " << total << "\n";
    }

    return 0;
}

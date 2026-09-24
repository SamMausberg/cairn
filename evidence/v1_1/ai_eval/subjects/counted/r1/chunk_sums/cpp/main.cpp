#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

struct ChunkResult {
    int64_t sum = 0;
    bool overflow = false;
};

static ChunkResult sum_range(const std::vector<int64_t>& values, size_t begin, size_t end) {
    ChunkResult result;
    for (size_t i = begin; i < end; ++i) {
        if (__builtin_add_overflow(result.sum, values[i], &result.sum)) {
            result.overflow = true;
            break;
        }
    }
    return result;
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int64_t k = 0, n = 0;
    std::cin >> k >> n;

    std::vector<int64_t> values(static_cast<size_t>(n));
    for (int64_t i = 0; i < n; ++i) {
        std::cin >> values[static_cast<size_t>(i)];
    }

    std::vector<ChunkResult> results(static_cast<size_t>(k));
    std::vector<std::thread> threads;
    threads.reserve(static_cast<size_t>(k));

    for (int64_t j = 0; j < k; ++j) {
        size_t begin = static_cast<size_t>(j * n / k);
        size_t end = static_cast<size_t>((j + 1) * n / k);
        threads.emplace_back([&results, &values, j, begin, end]() {
            results[static_cast<size_t>(j)] = sum_range(values, begin, end);
        });
    }

    for (auto& t : threads) {
        t.join();
    }

    bool total_overflow = false;
    int64_t total = 0;
    for (int64_t j = 0; j < k; ++j) {
        const ChunkResult& r = results[static_cast<size_t>(j)];
        if (r.overflow) {
            std::cout << "chunk " << j << " overflow\n";
            total_overflow = true;
        } else {
            std::cout << "chunk " << j << " " << r.sum << "\n";
            if (!total_overflow) {
                if (__builtin_add_overflow(total, r.sum, &total)) {
                    total_overflow = true;
                }
            }
        }
    }

    if (total_overflow) {
        std::cout << "total overflow\n";
    } else {
        std::cout << "total " << total << "\n";
    }

    return 0;
}

#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

struct ChunkResult {
    int64_t sum = 0;
    bool overflow = false;
};

static void sumChunk(const int64_t* values, size_t count, ChunkResult* result) {
    int64_t sum = 0;
    bool overflow = false;
    for (size_t i = 0; i < count; ++i) {
        int64_t next;
        if (__builtin_add_overflow(sum, values[i], &next)) {
            overflow = true;
            break;
        }
        sum = next;
    }
    result->sum = sum;
    result->overflow = overflow;
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    long long k, n;
    if (!(std::cin >> k >> n)) {
        return 0;
    }

    std::vector<int64_t> values(static_cast<size_t>(n));
    for (long long i = 0; i < n; ++i) {
        std::cin >> values[static_cast<size_t>(i)];
    }

    std::vector<ChunkResult> results(static_cast<size_t>(k));
    std::vector<std::thread> threads;
    threads.reserve(static_cast<size_t>(k));

    for (long long j = 0; j < k; ++j) {
        size_t start = static_cast<size_t>((j * n) / k);
        size_t end = static_cast<size_t>(((j + 1) * n) / k);
        const int64_t* base = values.empty() ? nullptr : values.data() + start;
        size_t count = end - start;
        threads.emplace_back(sumChunk, base, count, &results[static_cast<size_t>(j)]);
    }

    for (auto& t : threads) {
        t.join();
    }

    bool anyOverflow = false;
    int64_t total = 0;
    for (long long j = 0; j < k; ++j) {
        const ChunkResult& r = results[static_cast<size_t>(j)];
        if (r.overflow) {
            std::cout << "chunk " << j << " overflow\n";
            anyOverflow = true;
        } else {
            std::cout << "chunk " << j << " " << r.sum << "\n";
        }

        if (!anyOverflow) {
            int64_t next;
            if (__builtin_add_overflow(total, r.sum, &next)) {
                anyOverflow = true;
            } else {
                total = next;
            }
        }
    }

    if (anyOverflow) {
        std::cout << "total overflow\n";
    } else {
        std::cout << "total " << total << "\n";
    }

    return 0;
}

#include <array>
#include <cstdint>
#include <cstdio>
#include <thread>
#include <vector>
#include <algorithm>

int main() {
    long long n;
    int shift;
    if (std::scanf("%lld %d", &n, &shift) != 2) {
        return 0;
    }

    std::vector<uint32_t> values(static_cast<size_t>(n));
    for (long long i = 0; i < n; ++i) {
        unsigned long v;
        std::scanf("%lu", &v);
        values[static_cast<size_t>(i)] = static_cast<uint32_t>(v);
    }

    unsigned hw = std::thread::hardware_concurrency();
    unsigned numThreads = hw >= 2 ? hw : 2;
    if (static_cast<unsigned long long>(n) < numThreads) {
        numThreads = n > 0 ? static_cast<unsigned>(n) : 1;
        if (numThreads < 2) numThreads = 2;
    }

    // Each thread writes to its own private histogram slot, avoiding races.
    std::vector<std::array<uint64_t, 256>> localCounts(numThreads);
    for (auto& arr : localCounts) arr.fill(0);

    std::vector<std::thread> threads;
    threads.reserve(numThreads);

    size_t total = values.size();
    size_t chunk = (total + numThreads - 1) / numThreads;

    for (unsigned t = 0; t < numThreads; ++t) {
        size_t begin = std::min(total, static_cast<size_t>(t) * chunk);
        size_t end = std::min(total, begin + chunk);
        threads.emplace_back([&values, &localCounts, t, begin, end, shift]() {
            auto& counts = localCounts[t];
            for (size_t i = begin; i < end; ++i) {
                uint32_t v = values[i];
                unsigned bin = (v >> shift) & 255u;
                counts[bin]++;
            }
        });
    }

    for (auto& th : threads) th.join();

    uint64_t totalCounts[256] = {0};
    for (const auto& arr : localCounts) {
        for (int b = 0; b < 256; ++b) totalCounts[b] += arr[b];
    }

    for (int b = 0; b < 256; ++b) {
        if (totalCounts[b] != 0) {
            std::printf("%d %llu\n", b, static_cast<unsigned long long>(totalCounts[b]));
        }
    }

    return 0;
}

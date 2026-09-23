#include <array>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <string>
#include <thread>
#include <vector>
#include <algorithm>

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    long long n;
    int shift;
    if (!(std::cin >> n >> shift)) {
        return 0;
    }

    std::vector<uint32_t> values(static_cast<size_t>(n));
    for (long long i = 0; i < n; ++i) {
        uint64_t v;
        std::cin >> v;
        values[static_cast<size_t>(i)] = static_cast<uint32_t>(v);
    }

    unsigned hc = std::thread::hardware_concurrency();
    if (hc == 0) hc = 2;
    unsigned num_threads = std::min<unsigned>(hc, 8);
    if (num_threads < 2) num_threads = 2;

    std::vector<std::array<long long, 256>> local_counts(num_threads);
    for (auto& arr : local_counts) arr.fill(0);

    std::vector<std::thread> threads;
    threads.reserve(num_threads);

    long long chunk = (n + static_cast<long long>(num_threads) - 1) / static_cast<long long>(num_threads);
    if (chunk < 1) chunk = 1;

    for (unsigned t = 0; t < num_threads; ++t) {
        long long begin = static_cast<long long>(t) * chunk;
        long long end = std::min(n, begin + chunk);
        threads.emplace_back([&values, &local_counts, t, begin, end, shift]() {
            auto& counts = local_counts[t];
            for (long long i = begin; i < end; ++i) {
                unsigned bin = (values[static_cast<size_t>(i)] >> shift) & 255u;
                counts[bin]++;
            }
        });
    }

    for (auto& th : threads) th.join();

    std::array<long long, 256> total{};
    total.fill(0);
    for (auto& arr : local_counts) {
        for (int b = 0; b < 256; ++b) total[b] += arr[b];
    }

    std::string out;
    out.reserve(256 * 8);
    char buf[32];
    for (int b = 0; b < 256; ++b) {
        if (total[b] != 0) {
            int len = std::snprintf(buf, sizeof(buf), "%d %lld\n", b, total[b]);
            out.append(buf, static_cast<size_t>(len));
        }
    }
    std::fwrite(out.data(), 1, out.size(), stdout);

    return 0;
}

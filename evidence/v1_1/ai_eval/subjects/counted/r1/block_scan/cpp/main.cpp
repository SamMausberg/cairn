#include <algorithm>
#include <barrier>
#include <cstdint>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr size_t kBlockSize = 256;
constexpr size_t kTeamSize = 8;

// A persistent team of threads processes every block of `v` in turn,
// sharing a scratch buffer and a per-thread chunk-total array. Within a
// block: each thread sequentially scans the chunk of the block it owns,
// then the whole team runs a barriered Hillis-Steele scan over the
// per-thread chunk totals (waiting at a barrier before every step), and
// finally each thread folds the resulting chunk offset and the running
// total of earlier blocks back into its own elements.
void scan(std::vector<uint64_t>& v, size_t n) {
    if (n == 0) return;

    const size_t teamSize = std::min(n, kTeamSize);
    const size_t numBlocks = (n + kBlockSize - 1) / kBlockSize;

    std::vector<uint64_t> buf(kBlockSize);
    std::vector<uint64_t> chunkTotal(teamSize);
    uint64_t sharedOffset = 0;
    std::barrier<> sync(static_cast<std::ptrdiff_t>(teamSize));

    auto worker = [&](size_t i) {
        for (size_t b = 0; b < numBlocks; ++b) {
            const size_t start = b * kBlockSize;
            const size_t len = std::min(kBlockSize, n - start);

            const size_t lo = i * len / teamSize;
            const size_t hi = (i + 1) * len / teamSize;

            uint64_t acc = 0;
            for (size_t k = lo; k < hi; ++k) {
                acc += v[start + k];
                buf[k] = acc;
            }
            chunkTotal[i] = (hi > lo) ? buf[hi - 1] : 0;
            sync.arrive_and_wait();

            for (size_t step = 1; step < teamSize; step *= 2) {
                sync.arrive_and_wait();
                uint64_t val = chunkTotal[i];
                if (i >= step) val += chunkTotal[i - step];
                sync.arrive_and_wait();
                chunkTotal[i] = val;
            }
            sync.arrive_and_wait();

            const uint64_t chunkOffset = (i == 0) ? 0 : chunkTotal[i - 1];
            for (size_t k = lo; k < hi; ++k) {
                v[start + k] = buf[k] + chunkOffset + sharedOffset;
            }
            sync.arrive_and_wait();
            if (i == teamSize - 1) sharedOffset += chunkTotal[teamSize - 1];
            sync.arrive_and_wait();
        }
    };

    std::vector<std::thread> threads;
    threads.reserve(teamSize);
    for (size_t i = 0; i < teamSize; ++i) threads.emplace_back(worker, i);
    for (auto& t : threads) t.join();
}

}  // namespace

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);

    size_t n;
    if (!(std::cin >> n)) return 0;

    std::vector<uint64_t> v(n);
    for (size_t i = 0; i < n; ++i) {
        uint64_t x;
        std::cin >> x;
        v[i] = x;
    }

    scan(v, n);

    std::string out;
    out.reserve(n * 12);
    for (size_t i = 0; i < n; ++i) {
        out += std::to_string(v[i]);
        out += '\n';
    }
    std::cout << out;
    return 0;
}

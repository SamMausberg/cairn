#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>

int main() {
    long long n;
    if (!(std::cin >> n)) {
        return 1;
    }
    if (n < 0) n = 0;

    if (n < 2) {
        std::cout << "count 0\n";
        std::cout << "sum 0\n";
        std::cout << "last none\n";
        std::cout << "gap none\n";
        return 0;
    }

    // is_composite[i] true means i is not prime. Index range [0, n].
    std::vector<uint8_t> is_composite(static_cast<size_t>(n) + 1, 0);
    is_composite[0] = 1;
    is_composite[1] = 1;

    // Base primes up to sqrt(n), computed single-threaded (small work).
    long long limit = static_cast<long long>(std::sqrt(static_cast<double>(n))) + 1;
    while (limit * limit > n) --limit;
    while ((limit + 1) * (limit + 1) <= n) ++limit;

    std::vector<uint8_t> base_composite(static_cast<size_t>(limit) + 1, 0);
    if (limit >= 0) {
        if (limit >= 0) base_composite[0] = 1;
        if (limit >= 1) base_composite[1] = 1;
    }
    for (long long p = 2; p * p <= limit; ++p) {
        if (!base_composite[p]) {
            for (long long m = p * p; m <= limit; m += p) {
                base_composite[m] = 1;
            }
        }
    }
    std::vector<long long> base_primes;
    for (long long p = 2; p <= limit; ++p) {
        if (!base_composite[p]) base_primes.push_back(p);
    }

    // Sieve the full range [2, n] in parallel, splitting into disjoint
    // segments so each thread writes to distinct array elements only.
    unsigned hc = std::thread::hardware_concurrency();
    unsigned num_threads = hc == 0 ? 4u : hc;
    num_threads = std::max(2u, std::min(num_threads, 8u));

    long long range = n - 2 + 1; // numbers from 2..n
    long long chunk = (range + num_threads - 1) / num_threads;
    if (chunk < 1) chunk = 1;

    std::vector<std::thread> threads;
    for (unsigned t = 0; t < num_threads; ++t) {
        long long lo = 2 + static_cast<long long>(t) * chunk;
        long long hi = std::min(n, lo + chunk - 1);
        if (lo > n) break;
        threads.emplace_back([lo, hi, &base_primes, &is_composite]() {
            for (long long p : base_primes) {
                long long start = ((lo + p - 1) / p) * p;
                if (start < 2 * p) start = 2 * p;
                for (long long m = start; m <= hi; m += p) {
                    is_composite[static_cast<size_t>(m)] = 1;
                }
            }
        });
    }
    for (auto& th : threads) th.join();

    // Single-threaded aggregation over the completed sieve.
    long long count = 0;
    unsigned long long sum = 0;
    long long last = -1;
    long long prev = -1;
    long long max_gap = -1;
    long long gap_prime = -1;

    for (long long i = 2; i <= n; ++i) {
        if (!is_composite[static_cast<size_t>(i)]) {
            ++count;
            sum += static_cast<unsigned long long>(i);
            last = i;
            if (prev != -1) {
                long long g = i - prev;
                if (g > max_gap) {
                    max_gap = g;
                    gap_prime = prev;
                }
            }
            prev = i;
        }
    }

    std::cout << "count " << count << "\n";
    std::cout << "sum " << sum << "\n";
    if (last == -1) {
        std::cout << "last none\n";
    } else {
        std::cout << "last " << last << "\n";
    }
    if (max_gap == -1) {
        std::cout << "gap none\n";
    } else {
        std::cout << "gap " << max_gap << " " << gap_prime << "\n";
    }

    return 0;
}

// histogram_u32: 256 bins over the low byte of a u32 stream.
//
// The kernel re-zeroes its bins before it counts, so apply() is idempotent and the back-to-back row
// may call it a thousand times and still check the result. Every count is an exact integer, so
// kernels/histogram_u32/oracle.py checks the bins without a tolerance.
//
// Read kernels/histogram_u32/kernel.cairn first. The CAIRN arm here is sequential because the
// language rejects the shared bin parallel shape with E-PARALLEL-RACE, so the like for like row is
// cairn against plain, and the omp and tbb rows record a capability rather than a scheduler.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The bin count is a literal in the CAIRN source, so the emitted C++ carries 256 as the length of
// every out access. Every arm names the same constant rather than repeating the number.
inline constexpr std::size_t BINS = 256;

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, out, x) in that order.
void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept;

struct Histogram {
  std::size_t n;
  Aligned<std::uint32_t> x;
  Aligned<std::uint64_t> out, want;

  explicit Histogram(std::size_t size) : n(size), x(size), out(BINS), want(BINS) {
    // Knuth's multiplicative constant, shifted so the low byte turns over quickly: at a hundred
    // thousand elements every one of the 256 bins holds between 389 and 393 counts.
    for(std::size_t i = 0; i < n; ++i) x.p[i] = std::uint32_t(std::uint64_t(i) * 2654435761ull >> 7);
    for(std::size_t b = 0; b < BINS; ++b) want.p[b] = 0;
    for(std::size_t i = 0; i < n; ++i) ++want.p[x.p[i] & 255u];  // the sequential result, in this process
  }

  void apply() { arm_run(n, out.p, x.p); }

  bool agrees() const { return std::equal(want.p, want.p + BINS, out.p); }

  // A count is a u64 and is printed as one. Sending it through a double would round a bin past
  // 2^53, and the dump is the only thing oracle.py ever sees.
  void dump() const {
    std::printf("[");
    for(std::size_t b = 0; b < BINS; ++b) std::printf("%s%llu", b ? ", " : "", (unsigned long long)out.p[b]);
    std::printf("]");
  }
};

using TheCase = Histogram;

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::TheCase>(argc, argv, "histogram_u32"); }

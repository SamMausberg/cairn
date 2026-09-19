// sum_u64_wrap: one u64, the wrapping sum of x[0..n). Wrapping addition over u64 is associative and
// commutative, so every association order gives the same total. That is what lets a parallel arm be
// the same function as the sequential one instead of an approximation of it, and it is why this
// kernel can be checked exactly rather than within a tolerance.
//
// The fill is mixed_u64's, so the two kernels share their input distribution and differ only in what
// they do with an element.
//
// One result and two CAIRN arms. The emitted reduce returns the total; the emitted atomic form takes
// a cr::par::Atomic instead and returns nothing. The atomic is that arm's own business, so arm_run
// carries the shape both can meet: read x, leave one u64 behind the pointer. Every arm assigns that
// u64 rather than accumulating into it, and apply() clears it first, so a thousand back to back
// calls leave what one call leaves.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. It is the emitted signature of cf_sum_u64_wrap with the return value moved
// into an out parameter, which is the one shape the reduce arm and the atomic arm share.
void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept;

struct SumWrap {
  std::size_t n;
  Aligned<std::uint64_t> x;
  std::uint64_t total;
  std::uint64_t want;

  explicit SumWrap(std::size_t size) : n(size), x(size), total(0), want(0) {
    for(std::size_t i = 0; i < n; ++i) {
      x.p[i] = std::uint64_t(i) * 2654435761ull + 1;
      want += x.p[i];  // the sequential total, computed in this process, wrapping as u64 does
    }
  }

  void apply() {
    total = 0;
    arm_run(n, x.p, &total);
  }

  bool agrees() const { return total == want; }

  // A scalar, so a JSON number and not an array. %llu and not a double: a u64 total fills all sixty
  // four bits and a double would round it.
  void dump() const { std::printf("%llu", static_cast<unsigned long long>(total)); }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::SumWrap>(argc, argv, "sum_u64_wrap"); }

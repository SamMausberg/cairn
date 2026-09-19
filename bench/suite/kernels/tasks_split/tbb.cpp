// The oneTBB baseline: tbb::parallel_invoke over the four parts.
//
// parallel_invoke is TBB's answer to this shape, and it is not a loop: there is no range to split,
// no partitioner and no grain, so this arm ignores BENCH_GRAIN_ROW and compiles to one program for
// every row. The four tasks go to the arena rather than to four threads of their own, which is the
// difference the table is here to price.
//
// Every part is guarded on the calling thread before the tasks start, which is where the emitter
// puts cr::part too. The boundaries are plain.cpp's, in the same places.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

static void fill_part(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept {
  BG_VIEW(out, n);
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = (seed + BG_CONVERT(std::uint64_t, i)) * 2654435761ULL;
}

void arm_run(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept {
  BG_VIEW(out, n);
  const std::size_t q1 = BG_DIVIDE(std::size_t, n, 4);
  const std::size_t q2 = BG_ADD(std::size_t, q1, q1);
  const std::size_t q3 = BG_ADD(std::size_t, q2, q1);
  const std::size_t rest = BG_SUB(std::size_t, n, q3);
  std::uint64_t* const pa = BG_PART(out, 0, q1, n, q1);
  std::uint64_t* const pb = BG_PART(out, q1, q2, n, q1);
  std::uint64_t* const pc = BG_PART(out, q2, q3, n, q1);
  std::uint64_t* const pd = BG_PART(out, q3, n, n, rest);
  const std::uint64_t sb = seed + BG_CONVERT(std::uint64_t, q1);
  const std::uint64_t sc = seed + BG_CONVERT(std::uint64_t, q2);
  const std::uint64_t sd = seed + BG_CONVERT(std::uint64_t, q3);
  tbb::parallel_invoke([=] { fill_part(q1, pa, seed); }, [=] { fill_part(q1, pb, sb); },
                       [=] { fill_part(q1, pc, sc); }, [=] { fill_part(rest, pd, sd); });
}

}  // namespace bench

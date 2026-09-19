// The oneTBB baseline: the same sum over a blocked range in an arena as wide as the lane pool.
//
// tbb_arm.hpp's tbb_for covers a parallel_for, and a reduction is a parallel_reduce, so the two
// grain rows are spelled out here the way tbb_for spells them and no other way: the default row
// hands TBB a plain range and lets auto_partitioner choose, the chunk row hands it CAIRN's claim
// size with simple_partitioner, which honours it exactly. TBB splits and joins in whatever order
// its steal decisions produce, which is sound here because wrapping addition is associative.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept {
  BG_VIEW(x, n);
  const auto fold = [=](const tbb::blocked_range<std::size_t>& r, std::uint64_t carried) {
    for(std::size_t i = r.begin(); i < r.end(); ++i) carried += BG_AT(x, i, n);
    return carried;
  };
  const auto join = [](std::uint64_t a, std::uint64_t b) { return static_cast<std::uint64_t>(a + b); };
  const std::size_t chunk = grain(n);
  if(chunk == 0) {
    *total = tbb::parallel_reduce(tbb::blocked_range<std::size_t>(0, n), std::uint64_t(0), fold, join);
    return;
  }
  *total = tbb::parallel_reduce(tbb::blocked_range<std::size_t>(0, n, chunk), std::uint64_t(0), fold, join,
                                tbb::simple_partitioner());
}

}  // namespace bench

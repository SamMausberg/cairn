// The oneTBB arm: parallel_reduce over a blocked range in an arena as wide as the lane pool.
//
// Like the OpenMP arm, this computes a different function from the one the CAIRN source names.
// parallel_reduce folds each body's range in order and then joins the bodies in whatever order the
// task graph finishes them, which is a reassociation of a non-associative operation. The kernel
// reports that difference rather than timing against it.
//
// tbb_arm.hpp's tbb_for carries the grain row for an element-wise loop; a reduction needs the same
// two rows around parallel_reduce, so they are written out here with the identical rule: the
// default row hands TBB a plain range and auto_partitioner, the chunk row hands it CAIRN's claim
// size with simple_partitioner, which honours it exactly.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

double arm_run(std::size_t n, const double* x, const double* y) noexcept {
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  const auto fold = [x, y, n](const tbb::blocked_range<std::size_t>& r, double carry) {
    double total = carry;
    for(std::size_t i = r.begin(); i < r.end(); ++i) total = total + BG_AT(x, i, n) * BG_AT(y, i, n);
    return total;
  };
  const auto join = [](double a, double b) { return a + b; };
  const std::size_t chunk = grain(n);
  if(chunk == 0) return tbb::parallel_reduce(tbb::blocked_range<std::size_t>(0, n), 0.0, fold, join);
  return tbb::parallel_reduce(tbb::blocked_range<std::size_t>(0, n, chunk), 0.0, fold, join, tbb::simple_partitioner());
}

}  // namespace bench

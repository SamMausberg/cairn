// The oneTBB baseline: the same loop over a blocked range in an arena as wide as the lane pool.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

void arm_run(std::size_t n, float* out, const float* x, const float* y, float a) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  BG_DISJOINT(out, n, x, n);
  BG_DISJOINT(out, n, y, n);
  tbb_for(n, [=](const tbb::blocked_range<std::size_t>& r) {
    for(std::size_t i = r.begin(); i < r.end(); ++i) BG_AT(out, i, n) = a * BG_AT(x, i, n) + BG_AT(y, i, n);
  });
}

}  // namespace bench

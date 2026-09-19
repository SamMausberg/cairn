// The oneTBB baseline: the same loop over a blocked range in an arena as wide as the lane pool.
//
// The boundary matches the receipt for stencil_1d exactly: view_entry 2 as the two entry views, the
// one writer pair as the one disjointness test, overflow 3 as two checked adds and one checked
// subtract, bounds 6 as six checked element accesses. The macro names are left out of this
// paragraph on purpose, because the harness counts the boundary by reading this file.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

void arm_run(std::size_t n, float* out, const float* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  tbb_for(n, [=](const tbb::blocked_range<std::size_t>& r) {
    for(std::size_t i = r.begin(); i < r.end(); ++i) {
      if(i > 0 && BG_ADD(std::size_t, i, 1) < n) {
        BG_AT(out, i, n) = blend3(BG_AT(x, BG_SUB(std::size_t, i, 1), n), BG_AT(x, i, n),
                                  BG_AT(x, BG_ADD(std::size_t, i, 1), n));
      } else {
        BG_AT(out, i, n) = BG_AT(x, i, n);
      }
    }
  });
}

}  // namespace bench

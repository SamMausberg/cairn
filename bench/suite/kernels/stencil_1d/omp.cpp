// The OpenMP baseline: the same loop, handed to a team as wide as the CAIRN lane pool.
//
// This arm is the out of place stencil, which is the shape CAIRN accepts too. The in place shape
// beside it in in_place_rejected.cairn compiles here without a word and runs as a data race; the
// suite keeps it as a rejected CAIRN file rather than as a second OpenMP arm, because a racing arm
// has no result to check.
//
// The boundary matches the receipt for stencil_1d exactly: view_entry 2 as the two entry views, the
// one writer pair as the one disjointness test, overflow 3 as two checked adds and one checked
// subtract, bounds 6 as six checked element accesses. The macro names are left out of this
// paragraph on purpose, because the harness counts the boundary by reading this file.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

void arm_run(std::size_t n, float* out, const float* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;
  BENCH_OMP_FOR
  for(std::size_t i = 0; i < n; ++i) {
    if(i > 0 && BG_ADD(std::size_t, i, 1) < n) {
      BG_AT(out, i, n) = blend3(BG_AT(x, BG_SUB(std::size_t, i, 1), n), BG_AT(x, i, n),
                                BG_AT(x, BG_ADD(std::size_t, i, 1), n));
    } else {
      BG_AT(out, i, n) = BG_AT(x, i, n);
    }
  }
}

}  // namespace bench

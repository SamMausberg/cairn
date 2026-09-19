// The sequential C++ baseline. One thread, the boundary that guards.hpp selects, nothing else.
//
// The boundary matches the receipt for stencil_1d exactly: view_entry 2 as the two entry views, the
// one writer pair as the one disjointness test, overflow 3 as two checked adds and one checked
// subtract, bounds 6 as six checked element accesses. The emitter tests i + 1 in the condition and
// forms i + 1 again inside the index, so this arm adds twice for the same reason and not by
// oversight. The macro names are left out of this paragraph on purpose, because the harness counts
// the boundary by reading this file.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

void arm_run(std::size_t n, float* out, const float* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
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

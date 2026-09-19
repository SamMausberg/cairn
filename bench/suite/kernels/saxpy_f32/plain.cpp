// The sequential C++ baseline. One thread, the boundary that guards.hpp selects, nothing else.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

void arm_run(std::size_t n, float* out, const float* x, const float* y, float a) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  BG_DISJOINT(out, n, x, n);
  BG_DISJOINT(out, n, y, n);
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = a * BG_AT(x, i, n) + BG_AT(y, i, n);
}

}  // namespace bench

// The sequential C++ baseline, and the only arm that computes the same function CAIRN does: one
// thread, left to right, the two boundaries guards.hpp selects, nothing else. Two cr::view and two
// cr::at, which is what the receipt records for cf_dot_f64.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

double arm_run(std::size_t n, const double* x, const double* y) noexcept {
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  double total = 0.0;
  for(std::size_t i = 0; i < n; ++i) total = total + BG_AT(x, i, n) * BG_AT(y, i, n);
  return total;
}

}  // namespace bench

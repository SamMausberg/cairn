// The second CAIRN arm: stencil_1d_wrap, the same function written with wrapping index arithmetic
// where the semantics permit it.
//
// The branch already admits only 0 < i and i + 1 < n, so on every index the region computes, i - 1
// and i + 1 cannot leave usize, and add_wrap and sub_wrap compute exactly what add and sub compute.
// What changes is the receipt: stencil_1d counts overflow 3, and stencil_1d_wrap counts no checked
// index arithmetic at all, keeping the same six element accesses. This arm is therefore the
// wrapping boundary row, and the harness records it as such rather than as a cheaper spelling of
// the checked one.
#include "case.hpp"

extern "C" void cf_stencil_1d_wrap(std::size_t n, float* out, const float* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn_wrap"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, float* out, const float* x) noexcept { cf_stencil_1d_wrap(n, out, x); }

}  // namespace bench

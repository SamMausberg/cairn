// The sequential C++ baseline. One thread, the boundary that guards.hpp selects, nothing else.
//
// The addition itself is an ordinary C++ wrapping +, as bench/cpu/reference.cpp writes it, because
// the emitter writes cr::add_wrap there and cr::add_wrap traps on nothing. The BG_* sites below are
// therefore the whole of this arm's boundary: one BG_VIEW and one BG_AT, matching the receipt's
// view_entry 1 and bounds 1 for both cf_sum_u64_wrap and cf_sum_u64_atomic. There is one array and
// no writer among the parameters, so the emitter writes no cr::disjoint and this arm carries none.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept {
  BG_VIEW(x, n);
  std::uint64_t carried = 0;
  for(std::size_t i = 0; i < n; ++i) carried += BG_AT(x, i, n);
  *total = carried;
}

}  // namespace bench

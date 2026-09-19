// The sequential C++ baseline. One thread, the boundary that guards.hpp selects, nothing else.
//
// This is the arm the CAIRN arm is compared against, because CAIRN's histogram is sequential too:
// the shared bin parallel shape is rejected with E-PARALLEL-RACE. The two sides run the same loop
// over the same bins with the same boundary, so their row is a like for like row.
//
// The boundary matches the receipt for histogram_u32 exactly: view_entry 2 as the two entry views,
// the one writer pair as the one disjointness test, bounds 4 as four checked element accesses (the
// zeroing store, the input read, and the read and the write of the counted bin), conversion 1 as
// the one checked narrowing. The macro names are left out of this paragraph on purpose, because the
// harness counts the boundary by reading this file.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept {
  BG_VIEW(out, BINS);
  BG_VIEW(x, n);
  BG_DISJOINT(out, BINS, x, n);
  for(std::size_t b = 0; b < BINS; ++b) BG_AT(out, b, BINS) = 0;
  for(std::size_t i = 0; i < n; ++i) {
    const std::size_t b = BG_CONVERT(std::size_t, std::uint32_t(BG_AT(x, i, n) & 255u));
    BG_AT(out, b, BINS) = BG_AT(out, b, BINS) + std::uint64_t(1);
  }
}

}  // namespace bench

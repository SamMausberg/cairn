// The oneTBB baseline: the same loop over a blocked range in an arena as wide as the lane pool.
//
// The shift is BG_SHR and not `>>` for the reason plain.cpp gives: cr::shr traps on a count at or
// beyond the width, so the receipt counts one `shift` site in `mix` and this arm carries one too.
#include "case.hpp"
#include "../../tbb_arm.hpp"

static std::uint64_t mixed(std::uint64_t v) noexcept {
  for(int k = 0; k < 8; ++k) {
    v ^= BG_SHR(std::uint64_t, v, 29);
    v *= 0xbf58476d1ce4e5b9ull;
  }
  return v;
}

namespace bench {

void arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  tbb_for(n, [=](const tbb::blocked_range<std::size_t>& r) {
    for(std::size_t i = r.begin(); i < r.end(); ++i) BG_AT(out, i, n) = mixed(BG_AT(x, i, n));
  });
}

}  // namespace bench

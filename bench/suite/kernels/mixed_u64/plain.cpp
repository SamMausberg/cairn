// The sequential C++ baseline. One thread, the boundary that guards.hpp selects, nothing else.
//
// The xor and the multiply are ordinary C++ wrapping operators, as bench/host/reference.cpp writes
// them, because cr::mul_wrap traps on nothing. The shift is different: cr::shr traps on a count at
// or beyond the width, so it is a boundary and the receipt counts it as one `shift` site in `mix`.
// It is written here as BG_SHR, once, in the one helper the loop calls, so this arm carries the
// receipt's count exactly: two BG_VIEW and one BG_DISJOINT for view_entry 2 and the emitter's one
// cr::disjoint, two BG_AT for bounds 2, and one BG_SHR for shift 1.
#include "case.hpp"

static std::uint64_t mixed(std::uint64_t v) noexcept {
  for(int k = 0; k < 8; ++k) {
    v ^= BG_SHR(std::uint64_t, v, 29);
    v *= 0xbf58476d1ce4e5b9ull;
  }
  return v;
}

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = mixed(BG_AT(x, i, n));
}

}  // namespace bench

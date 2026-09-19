// The OpenMP baseline: the same loop, handed to a team as wide as the CAIRN lane pool.
//
// The shift is BG_SHR and not `>>` for the reason plain.cpp gives: cr::shr traps on a count at or
// beyond the width, so the receipt counts one `shift` site in `mix` and this arm carries one too.
#include "case.hpp"
#include "../../omp_arm.hpp"

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
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;
  BENCH_OMP_FOR
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = mixed(BG_AT(x, i, n));
}

}  // namespace bench

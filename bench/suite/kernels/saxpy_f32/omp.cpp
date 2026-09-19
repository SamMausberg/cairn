// The OpenMP baseline: the same loop, handed to a team as wide as the CAIRN lane pool.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

void arm_run(std::size_t n, float* out, const float* x, const float* y, float a) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  BG_DISJOINT(out, n, x, n);
  BG_DISJOINT(out, n, y, n);
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;
  BENCH_OMP_FOR
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = a * BG_AT(x, i, n) + BG_AT(y, i, n);
}

}  // namespace bench

// The OpenMP arm: reduction(+ : total) over a team as wide as the CAIRN lane pool.
//
// This arm does not compute the kernel's function. OpenMP folds each lane's share and then combines
// the shares in an unspecified order, so the sum it returns is a different association of the same
// terms than the in-order fold, and floating point addition is not associative. That is the
// finding, not a defect: case.hpp says what agrees() tolerates and oracle.py reports the bits.
#include "case.hpp"
#include "../../omp_arm.hpp"

// bench.hpp's BENCH_OMP_FOR carries the grain row for every kernel, but it names no reduction
// clause and a reduction cannot do without one. This is that macro verbatim, schedule and all, with
// reduction(+ : total) added: the loop this kernel hands OpenMP is scheduled exactly as every other
// kernel's is, and the clause is the only difference.
#if BENCH_GRAIN_ROW == BENCH_GRAIN_CHUNK
#define DOT_OMP_FOR _Pragma("omp parallel for schedule(static, bench_chunk) reduction(+ : total)")
#elif BENCH_GRAIN_ROW == BENCH_GRAIN_CLAIM
#define DOT_OMP_FOR _Pragma("omp parallel for schedule(dynamic, bench_chunk) reduction(+ : total)")
#else
#define DOT_OMP_FOR _Pragma("omp parallel for reduction(+ : total)")
#endif

namespace bench {

double arm_run(std::size_t n, const double* x, const double* y) noexcept {
  BG_VIEW(x, n);
  BG_VIEW(y, n);
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;
  double total = 0.0;
  DOT_OMP_FOR
  for(std::size_t i = 0; i < n; ++i) total = total + BG_AT(x, i, n) * BG_AT(y, i, n);
  return total;
}

}  // namespace bench

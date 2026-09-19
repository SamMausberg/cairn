// The OpenMP baseline: the same sum, handed to a team as wide as the CAIRN lane pool.
//
// BENCH_OMP_FOR is a combined directive and a kernel cannot attach a reduction clause to it, so the
// clause is written out here in the shape `reduction(+:)` compiles to. The arm opens the region
// itself, each worker keeps a plain `part` declared inside it, BENCH_OMP_FOR_INNER shares out the
// elements on the grain row's schedule, and one atomic update per worker folds the partials at the
// end. Wrapping addition is associative and commutative, so neither the split nor the fold order
// changes the total.
//
// `part` is a local of the structured block, so it is private by construction and lives in a
// register: there is no per element bookkeeping, no thread local to look up, and no false sharing
// to pad against. Only the fold touches shared memory, once per worker per region.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept {
  BG_VIEW(x, n);
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;
  std::uint64_t carried = 0;
#pragma omp parallel
  {
    std::uint64_t part = 0;
    BENCH_OMP_FOR_INNER
    for(std::size_t i = 0; i < n; ++i) part += BG_AT(x, i, n);
#pragma omp atomic
    carried += part;
  }
  *total = carried;
}

}  // namespace bench

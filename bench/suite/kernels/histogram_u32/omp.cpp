// The OpenMP baseline: privatized bins per thread and a combine, which is the ordinary way to write
// a parallel histogram.
//
// This arm is here to record a capability, not to win a race. CAIRN rejects the shared bin parallel
// shape outright with E-PARALLEL-RACE, so there is no CAIRN function that does what this file does;
// the like for like row of this kernel is cairn against plain, and this row says what an ordinary
// toolchain offers that the language does not.
//
// The shape is the textbook one. The arm opens the region itself, each thread declares its own bin
// array on its own stack, BENCH_OMP_FOR_INNER shares the elements out with the row's schedule, and
// a critical section folds each thread's bins into one total before the region ends. Nothing is
// looked up per element, and a thread's bins die with the region, so there is no storage to zero on
// the next call.
//
// The boundary matches the receipt for histogram_u32 exactly: view_entry 2 as the two entry views,
// the one writer pair as the one disjointness test, bounds 4 as four checked element accesses (the
// input read, the read and the write of the counted bin, and the store of the combined bin),
// conversion 1 as the one checked narrowing. The zeroing store CAIRN writes into out is this arm's
// store of the combined bin, and a thread's own bins start at zero because the array is value
// initialized, so the two sides carry four checked accesses each and in the same roles. The macro
// names are left out of this paragraph on purpose, because the harness counts the boundary by
// reading this file.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept {
  BG_VIEW(out, BINS);
  BG_VIEW(x, n);
  BG_DISJOINT(out, BINS, x, n);
  const std::size_t bench_chunk = grain(n) ? grain(n) : 1;  // in scope at the pragma inside the region
  std::uint64_t total[BINS] = {};
#pragma omp parallel
  {
    std::uint64_t local[BINS] = {};  // one array per thread, on that thread's own stack
    BENCH_OMP_FOR_INNER
    for(std::size_t i = 0; i < n; ++i) {
      const std::size_t b = BG_CONVERT(std::size_t, std::uint32_t(BG_AT(x, i, n) & 255u));
      BG_AT(local, b, BINS) = BG_AT(local, b, BINS) + std::uint64_t(1);
    }
#pragma omp critical
    for(std::size_t b = 0; b < BINS; ++b) total[b] += local[b];
  }
  for(std::size_t b = 0; b < BINS; ++b) BG_AT(out, b, BINS) = total[b];
}

}  // namespace bench

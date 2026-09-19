// The OpenMP baseline: four sections, one per part, in a team of four.
//
// There is no grain row here and no loop schedule to choose. The split is fixed at four parts by the
// kernel, so this arm compiles to the same program for every value of BENCH_GRAIN_ROW and the three
// rows of the table are one number repeated.
//
// The team is four and not the lane count, because the kernel spawns four threads whatever the pool
// is worth; a wider team would leave workers with no section to take.
//
// Every part is guarded on the calling thread before the region opens, which is where the emitter
// puts cr::part too. The boundaries are plain.cpp's, in the same places.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

static void fill_part(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept {
  BG_VIEW(out, n);
  for(std::size_t i = 0; i < n; ++i) BG_AT(out, i, n) = (seed + BG_CONVERT(std::uint64_t, i)) * 2654435761ULL;
}

void arm_run(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept {
  BG_VIEW(out, n);
  const std::size_t q1 = BG_DIVIDE(std::size_t, n, 4);
  const std::size_t q2 = BG_ADD(std::size_t, q1, q1);
  const std::size_t q3 = BG_ADD(std::size_t, q2, q1);
  const std::size_t rest = BG_SUB(std::size_t, n, q3);
  std::uint64_t* const pa = BG_PART(out, 0, q1, n, q1);
  std::uint64_t* const pb = BG_PART(out, q1, q2, n, q1);
  std::uint64_t* const pc = BG_PART(out, q2, q3, n, q1);
  std::uint64_t* const pd = BG_PART(out, q3, n, n, rest);
  const std::uint64_t sb = seed + BG_CONVERT(std::uint64_t, q1);
  const std::uint64_t sc = seed + BG_CONVERT(std::uint64_t, q2);
  const std::uint64_t sd = seed + BG_CONVERT(std::uint64_t, q3);
#pragma omp parallel sections num_threads(4)
  {
#pragma omp section
    fill_part(q1, pa, seed);
#pragma omp section
    fill_part(q1, pb, sb);
#pragma omp section
    fill_part(q1, pc, sc);
#pragma omp section
    fill_part(rest, pd, sd);
  }
}

}  // namespace bench

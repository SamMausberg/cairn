// The std::thread baseline: four threads over the same four ranges, joined in order.
//
// This is the C++ shape of what the kernel says. cairn_parallel.hpp's Task is itself a std::thread
// and never a lane of the region pool, so the two arms start and join the same objects and the gap
// between them is the ticket and the lease, not the mechanism.
//
// -fno-exceptions is in force, so a std::thread that cannot start terminates the process instead of
// throwing. Task keeps that same contract: its spawn is noexcept.
//
// The boundaries are plain.cpp's, in the same places.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "threads"; }

// Nothing to size: this arm makes exactly four threads, as the kernel does.
void arm_setup(std::size_t) {}

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
  std::thread a(fill_part, q1, BG_PART(out, 0, q1, n, q1), seed);
  std::thread b(fill_part, q1, BG_PART(out, q1, q2, n, q1), seed + BG_CONVERT(std::uint64_t, q1));
  std::thread c(fill_part, q1, BG_PART(out, q2, q3, n, q1), seed + BG_CONVERT(std::uint64_t, q2));
  std::thread d(fill_part, rest, BG_PART(out, q3, n, n, rest), seed + BG_CONVERT(std::uint64_t, q3));
  a.join();
  b.join();
  c.join();
  d.join();
}

}  // namespace bench

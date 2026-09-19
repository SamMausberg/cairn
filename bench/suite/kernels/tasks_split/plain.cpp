// The sequential C++ baseline. One thread walks the four ranges in turn with the four seeds the
// kernel uses, so the array it leaves behind is the one the parallel arms write.
//
// Its boundaries are the ones the receipt records for kernel.cairn: two view entries, five bounds
// sites (one element access in the fill, plus the four parts, which the emitter guards with
// cr::part), two checked additions and one checked subtraction, one checked division, and four
// checked conversions. Every other arm of this kernel carries the same ones in the same places.
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

// cf_fill_part as the emitter writes it: one view entry, one bounds site, one conversion. The two
// wrapping operations are unchecked in CAIRN too, and unsigned arithmetic wraps here by definition.
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
  fill_part(q1, BG_PART(out, 0, q1, n, q1), seed);
  fill_part(q1, BG_PART(out, q1, q2, n, q1), seed + BG_CONVERT(std::uint64_t, q1));
  fill_part(q1, BG_PART(out, q2, q3, n, q1), seed + BG_CONVERT(std::uint64_t, q2));
  fill_part(rest, BG_PART(out, q3, n, n, rest), seed + BG_CONVERT(std::uint64_t, q3));
}

}  // namespace bench

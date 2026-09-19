// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file. This arm allocates nothing, starts no lanes and needs
// no scan, which is what the parallel arms are being compared against.
#include "case.hpp"

extern "C" std::size_t cf_compact_even(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
// A host compact starts no lanes at all.
void arm_setup(std::size_t) {}

std::size_t arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept {
  return cf_compact_even(n, out, x);
}

}  // namespace bench

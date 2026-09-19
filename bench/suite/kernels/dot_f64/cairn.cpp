// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file. On the host this entry is the in-order fold itself, so
// it is the one arm of this kernel that computes the function the source names.
#include "case.hpp"

extern "C" double cf_dot_f64(std::size_t n, const double* x, const double* y) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
// A host reduce starts no lanes at all, which is the point of the comparison.
void arm_setup(std::size_t) {}

double arm_run(std::size_t n, const double* x, const double* y) noexcept { return cf_dot_f64(n, x, y); }

}  // namespace bench

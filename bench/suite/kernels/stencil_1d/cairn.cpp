// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
//
// This arm is parallel, and that is half the finding: the lane rule constrains what lanes write, so
// an out of place stencil reading a ro view anywhere it likes is accepted. The other half is
// in_place_rejected.cairn, which the same compiler refuses.
#include "case.hpp"

extern "C" void cf_stencil_1d(std::size_t n, float* out, const float* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, float* out, const float* x) noexcept { cf_stencil_1d(n, out, x); }

}  // namespace bench

// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
//
// This arm is sequential, and that is the finding. The parallel histogram is not a shape this
// language accepts: a lane may touch a written array only at its own index, so out[b] from every
// lane is rejected with E-PARALLEL-RACE, and there is no array of atomics to fall back on. The row
// to read beside this one is plain, which is sequential too.
#include "case.hpp"

extern "C" void cf_histogram_u32(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
// This kernel has no parallel region for it to size, which is the point.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept { cf_histogram_u32(n, out, x); }

}  // namespace bench

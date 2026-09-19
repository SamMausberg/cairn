// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
#include "case.hpp"

extern "C" void cf_mixed_u64(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept { cf_mixed_u64(n, out, x); }

}  // namespace bench

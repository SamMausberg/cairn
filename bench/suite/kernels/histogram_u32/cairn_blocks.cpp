// The second CAIRN arm, which the guards addendum to the preregistration adds: every lane counts one
// block of 65536 inputs into its own 256 bins, then one thread merges the bins in block order. The
// emitted function allocates and zeroes that scratch itself, so its cost is in this column.
#include "case.hpp"

extern "C" void cf_histogram_u32_blocks(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn_blocks"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept {
  cf_histogram_u32_blocks(n, out, x);
}

}  // namespace bench

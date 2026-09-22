// The third CAIRN arm: `reduce add_wrap parallel`, which the 1.4 addendum to the preregistration adds.
// The emitter writes cr::par::reduce: blocks of consecutive indices that the count alone fixes, each
// folded in order on some lane of the pool, then the block totals folded in order on this thread.
#include "case.hpp"

extern "C" std::uint64_t cf_sum_u64_pool(std::size_t n, const std::uint64_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn_pool"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept { *total = cf_sum_u64_pool(n, x); }

}  // namespace bench

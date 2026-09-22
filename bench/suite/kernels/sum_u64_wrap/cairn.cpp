// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
//
// This arm runs on one thread, as the source says: a host `reduce ... for` is an in-order fold
// (codegen.py, Emitter.s_reduce, and docs/concurrency.md under "reduce and compact"). So this column
// is the cost of the sequential form, to be read against the plain column first. The parallel host
// reductions are cairn_atomic.cpp, one relaxed Atomic, and cairn_pool.cpp, `reduce ... parallel`.
#include "case.hpp"

extern "C" std::uint64_t cf_sum_u64_wrap(std::size_t n, const std::uint64_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept { *total = cf_sum_u64_wrap(n, x); }

}  // namespace bench

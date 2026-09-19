// The second CAIRN arm: the only parallel host reduction the language offers. A parallel region
// whose lanes each add their element into one relaxed Atomic, which is what the emitter writes for
// `total.fetch_add(x[i], Order.relaxed)` inside `parallel`.
//
// It is here so the suite prices that choice rather than implying CAIRN has no parallel reduction.
// It is not a tree fold and does not pretend to be one: every element touches the same cache line,
// so the contention is the point of the measurement and not a flaw in it.
//
// The emitted entry has C++ linkage, not C: the cr::par::Atomic reference in its signature is not a
// C type, so codegen.py declares it as `void cf_sum_u64_atomic(...)` in the global namespace with no
// extern "C". The declaration below is the emitter's own, copied from the generated kernel.cpp.
#include "case.hpp"
#include "cairn_parallel.hpp"

void cf_sum_u64_atomic(std::size_t n, const std::uint64_t* x, cr::par::Atomic<std::uint64_t>& total) noexcept;

namespace bench {

const char* arm_name() { return "cairn_atomic"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

// The Atomic is built here and not held between calls: it is not copyable, it has to start at zero
// on every call for the result to be the same one every time, and it is a bare std::atomic inside,
// so building it costs a store and no allocation.
void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept {
  cr::par::Atomic<std::uint64_t> tally(0);
  cf_sum_u64_atomic(n, x, tally);
  *total = tally.load(cr::par::Order::relaxed);
}

}  // namespace bench

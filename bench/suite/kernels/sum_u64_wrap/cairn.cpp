// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
//
// This arm runs on one thread, and that is the language and not an oversight. On the host a CAIRN
// reduce is an ordinary in order fold: src/cairn/compiler/codegen.py s_reduce takes its else branch,
// commented "On the host a reduction is an ordinary in-order fold: no threads, no hidden cost", and
// docs/guide/language.md line 895 says the same. So this column is the cost of CAIRN's reduce and
// not the cost of a CAIRN parallel reduction, and it is to be read against the plain column first.
// The parallel host reduction the language does offer is the atomic form, which is cairn_atomic.cpp.
#include "case.hpp"

extern "C" std::uint64_t cf_sum_u64_wrap(std::size_t n, const std::uint64_t* x) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// cairn_parallel.hpp reads CAIRN_LANES itself, which is the same variable bench::lanes() reports.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, const std::uint64_t* x, std::uint64_t* total) noexcept { *total = cf_sum_u64_wrap(n, x); }

}  // namespace bench

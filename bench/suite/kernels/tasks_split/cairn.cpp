// The CAIRN arm: the function the emitter wrote, called as it was emitted. Its boundaries are not
// selected by guards.hpp; they are in the generated code, and harness.py takes their count from the
// build receipt rather than from this file.
#include "case.hpp"

extern "C" void cf_tasks_split(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept;

namespace bench {

const char* arm_name() { return "cairn"; }

// A task is a thread of its own and never a lane of the region pool (cairn_parallel.hpp), so there
// is no pool to size here: the kernel spawns four threads and waits for all four.
void arm_setup(std::size_t) {}

void arm_run(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept { cf_tasks_split(n, out, seed); }

}  // namespace bench

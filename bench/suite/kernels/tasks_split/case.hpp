// tasks_split: four visibly disjoint parts of one array, each filled by its own thread.
//
// The four seeds are chosen so the split cannot be seen in the result. Part p carries seed + p, and
// element i of that part adds i again, so element j of the whole array is (seed + j) * 2654435761
// wherever the boundaries fall. That is why the expected result here is one loop while every arm
// really does write four separate ranges: the loop and the four parts are the same function, and a
// split that went wrong shows up as a mismatch rather than as a different but plausible number.
//
// It is exact for every n the suite uses, including n < 4, where n / 4 is zero, the first three
// parts are empty and the fourth carries the whole array with seed + 0. kernels/tasks_split/oracle.py
// does the four part seeding instead, so the two forms check each other.
//
// apply() writes every element from its inputs and reads none of them, so the back to back row may
// call it a thousand times and still check the result.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters of
// tasks_split into (n, out, seed) in that order.
void arm_run(std::size_t n, std::uint64_t* out, std::uint64_t seed) noexcept;

struct TasksSplit {
  static constexpr std::uint64_t SEED = 0x9e3779b97f4a7c15ULL;
  static constexpr std::uint64_t MIX = 2654435761ULL;
  std::size_t n;
  Aligned<std::uint64_t> out, want;

  explicit TasksSplit(std::size_t size) : n(size), out(size), want(size) {
    // The sequential result, computed in this process. Unsigned arithmetic wraps, which is what
    // add_wrap and mul_wrap mean.
    for(std::size_t i = 0; i < n; ++i) want.p[i] = (SEED + std::uint64_t(i)) * MIX;
  }

  void apply() { arm_run(n, out.p, SEED); }

  bool agrees() const { return std::equal(want.p, want.p + n, out.p); }

  void dump() const {
    std::printf("[");
    for(std::size_t i = 0; i < n; ++i)
      std::printf("%s%llu", i ? ", " : "", static_cast<unsigned long long>(out.p[i]));
    std::printf("]");
  }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::TasksSplit>(argc, argv, "tasks_split"); }

// The oneTBB baseline: the same privatized bins, held by enumerable_thread_specific, and the same
// combine.
//
// Like the OpenMP arm this row records a capability rather than a scheduler. CAIRN rejects the
// shared bin parallel shape with E-PARALLEL-RACE, so there is no CAIRN function this arm is the
// parallel twin of; the like for like row of this kernel is cairn against plain.
//
// The store lives for the process, as the arena does, because building and tearing down a
// thread_specific per region would measure the teardown. Every call zeroes every slot it read, so a
// slot a later call never touches contributes nothing.
//
// The boundary matches the receipt for histogram_u32 exactly: view_entry 2 as the two entry views,
// the one writer pair as the one disjointness test, bounds 4 as four checked element accesses (the
// input read, the read and the write of the counted bin, and the store of the combined bin),
// conversion 1 as the one checked narrowing. The macro names are left out of this paragraph on
// purpose, because the harness counts the boundary by reading this file.
#include <tbb/enumerable_thread_specific.h>
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {
namespace {

struct Bins {
  std::uint64_t count[BINS];
};

// The exemplar is the zero bin array, so a thread that arrives mid region starts from zero.
inline tbb::enumerable_thread_specific<Bins>& privates() {
  static tbb::enumerable_thread_specific<Bins> held(Bins{});
  return held;
}

}  // namespace

void arm_run(std::size_t n, std::uint64_t* out, const std::uint32_t* x) noexcept {
  BG_VIEW(out, BINS);
  BG_VIEW(x, n);
  BG_DISJOINT(out, BINS, x, n);
  tbb::enumerable_thread_specific<Bins>& held = privates();
  tbb_for(n, [&](const tbb::blocked_range<std::size_t>& r) {
    Bins& local = held.local();  // one lookup per claimed range, not one per element
    for(std::size_t i = r.begin(); i < r.end(); ++i) {
      const std::size_t b = BG_CONVERT(std::size_t, std::uint32_t(BG_AT(x, i, n) & 255u));
      BG_AT(local.count, b, BINS) = BG_AT(local.count, b, BINS) + std::uint64_t(1);
    }
  });
  std::uint64_t total[BINS] = {};
  for(Bins& one : held) {
    for(std::size_t b = 0; b < BINS; ++b) total[b] += one.count[b];
    std::fill(one.count, one.count + BINS, std::uint64_t(0));  // ready for the next call
  }
  for(std::size_t b = 0; b < BINS; ++b) BG_AT(out, b, BINS) = total[b];
}

}  // namespace bench

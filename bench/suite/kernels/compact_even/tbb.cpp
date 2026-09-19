// The oneTBB arm: the same two-pass compaction over the same parts, in an arena as wide as the lane
// pool. Count each part, scan the counts in order, then scatter each part's selected run at its
// scanned offset. Stable and byte identical to the collector, for the reason the OpenMP arm gives.
//
// tbb_arm.hpp's tbb_for carries the grain row for a loop over elements; here the parallel loop is
// over parts, whose size the grain row already fixed, so the same rule is written out over parts: a
// plain range and auto_partitioner for the default row, a range of grain one with
// simple_partitioner for the chunk row, which makes each part its own task.
//
// The boundary is the emitter's: two cr::view, one cr::disjoint, two cr::at, one for each of the
// emitter's two reads of x[i], and the store into out unguarded exactly as the emitter leaves it.
#include "case.hpp"
#include "../../tbb_arm.hpp"

namespace bench {

namespace {

// Held across calls for the reason the OpenMP arm gives: the scan is what a parallel compaction
// costs, and the CAIRN collector pays none of it.
std::vector<std::size_t> offsets;

template<class Body> void over_parts(std::size_t parts, bool exact, Body body) {
  if(!exact) {
    tbb::parallel_for(tbb::blocked_range<std::size_t>(0, parts), body);
    return;
  }
  tbb::parallel_for(tbb::blocked_range<std::size_t>(0, parts, 1), body, tbb::simple_partitioner());
}

}  // namespace

std::size_t arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  const std::size_t claim = grain(n);
  const std::size_t crew = lanes();
  const std::size_t parts = claim != 0 ? (n + claim - 1) / claim : (crew != 0 ? crew : 1);
  const std::size_t span = (n + parts - 1) / parts;
  if(offsets.size() < parts + 1) offsets.resize(parts + 1);

  over_parts(parts, claim != 0, [x, n, span](const tbb::blocked_range<std::size_t>& r) {
    for(std::size_t p = r.begin(); p < r.end(); ++p) {
      const std::size_t lo = p * span;
      const std::size_t hi = lo + span < n ? lo + span : n;
      std::size_t kept = 0;
      for(std::size_t i = lo; i < hi; ++i) kept += (BG_AT(x, i, n) & 1ull) == 0ull ? 1u : 0u;
      offsets[p + 1] = kept;
    }
  });

  offsets[0] = 0;
  for(std::size_t p = 0; p < parts; ++p) offsets[p + 1] += offsets[p];

  over_parts(parts, claim != 0, [out, x, n, span](const tbb::blocked_range<std::size_t>& r) {
    for(std::size_t p = r.begin(); p < r.end(); ++p) {
      const std::size_t lo = p * span;
      const std::size_t hi = lo + span < n ? lo + span : n;
      std::size_t at = offsets[p];
      for(std::size_t i = lo; i < hi; ++i) {
        const std::uint64_t value = BG_AT(x, i, n);
        if((value & 1ull) == 0ull) {
          out[at] = value;
          ++at;
        }
      }
    }
  });

  return offsets[parts];
}

}  // namespace bench

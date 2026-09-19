// The OpenMP arm: a genuine two-pass parallel compaction, stable, byte identical to the collector.
//
// Pass one counts how many elements each part selects. A sequential exclusive scan over the parts
// turns those counts into the offset each part's run starts at. Pass two walks each part again and
// writes its selected elements, in source order, from that offset. Stability is not an extra care
// taken here; it falls out of the scan, because a part's run begins exactly where every earlier
// part's run ended. There is no critical section and no serial loop wearing a pragma: both passes
// are real parallel work and the only serial step is the scan over the parts, which is as many
// additions as there are parts.
//
// The parts are the grain row's chunks. bench::grain(n) is the claim size CAIRN would hand one
// lane, so a part is one claim and the OpenMP chunk size is one part; the default row, where
// grain(n) is zero and the library decides, gets one part per lane. That makes schedule(static, 1)
// "one claim assigned in advance" and schedule(dynamic, 1) "one claim taken on demand", which is
// what the two chunk rows mean everywhere else in this suite.
//
// The boundary is the emitter's: two cr::view, one cr::disjoint, two cr::at, one for each of the
// emitter's two reads of x[i], and the store into out unguarded exactly as the emitter leaves it.
#include "case.hpp"
#include "../../omp_arm.hpp"

namespace bench {

namespace {

// The exclusive scan of the per part counts, held across calls. A parallel compaction cannot avoid
// this array: no part can know where its run belongs until every earlier part has been counted. It
// grows once and is reused, so a timed pass allocates nothing. The CAIRN collector needs no such
// array at all, which is part of what this kernel reports rather than something it hides.
std::vector<std::size_t> offsets;

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
  const std::size_t bench_chunk = 1;  // one part is one claim; grain(n) already sized the part

  BENCH_OMP_FOR
  for(std::size_t p = 0; p < parts; ++p) {
    const std::size_t lo = p * span;
    const std::size_t hi = lo + span < n ? lo + span : n;
    std::size_t kept = 0;
    for(std::size_t i = lo; i < hi; ++i) kept += (BG_AT(x, i, n) & 1ull) == 0ull ? 1u : 0u;
    offsets[p + 1] = kept;
  }

  offsets[0] = 0;
  for(std::size_t p = 0; p < parts; ++p) offsets[p + 1] += offsets[p];

  BENCH_OMP_FOR
  for(std::size_t p = 0; p < parts; ++p) {
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

  return offsets[parts];
}

}  // namespace bench

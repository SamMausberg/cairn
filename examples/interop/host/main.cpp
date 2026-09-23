// A C++ program that owns its data and calls a CAIRN library through the header `cairn build --header`
// wrote. Nothing here comes from CAIRN's tooling: build it with your own compiler, as docs/tools.md shows,
// and it exits 0 when every answer is what the library promises.
#include <cstdint>
#include <cstdio>
#include <vector>

#include "stats.h"

int main() {
  std::vector<std::int64_t> samples{4, 8, 15, 16, 23, 42};
  const ct_Summary s = cf_summarize(samples.size(), samples.data());
  std::printf("count %llu, min %lld, max %lld, total %lld\n", static_cast<unsigned long long>(s.count),
              static_cast<long long>(s.min), static_cast<long long>(s.max), static_cast<long long>(s.total));

  std::vector<std::int64_t> sums(samples.size());
  cf_window_sums(samples.size(), samples.data(), 3, sums.data());
  std::printf("windows of 3:");
  for (std::int64_t v : sums) std::printf(" %lld", static_cast<long long>(v));
  std::printf("\n");

  const ct_Trend t = cf_trend(samples.size(), samples.data());
  const char* way = t.tag == ct_Trend_Up ? "up" : t.tag == ct_Trend_Down ? "down" : "flat";
  std::printf("trend %s by %llu, spread %lld\n", way, static_cast<unsigned long long>(t.payload.Up),
              static_cast<long long>(cf_spread(samples.size(), samples.data())));

  cf_scale(samples.size(), samples.data(), 3, 2);
  std::printf("scaled by 3/2: %lld .. %lld\n", static_cast<long long>(samples.front()),
              static_cast<long long>(samples.back()));

  const bool right = s.count == 6 && s.min == 4 && s.max == 42 && s.total == 108 && sums[2] == 27 &&
                     sums[5] == 81 && t.tag == ct_Trend_Up && t.payload.Up == 38 && samples.back() == 63;
  return right ? 0 : 1;
}

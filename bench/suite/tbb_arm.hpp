// What every oneTBB arm shares: the arena width, and the two partitioner rows.
//
// max_allowed_parallelism counts the calling thread, as CAIRN_LANES does, so the arena and the lane
// pool are the same width. The control object lives for the process: a task arena that is torn down
// and rebuilt between regions would measure the teardown.
#pragma once
#include <tbb/blocked_range.h>
#include <tbb/global_control.h>
#include <tbb/parallel_for.h>
#include <tbb/parallel_invoke.h>
#include <tbb/parallel_reduce.h>
#include <tbb/parallel_scan.h>
#include "bench.hpp"

namespace bench {

inline const char* arm_name() { return "tbb"; }

inline void arm_setup(std::size_t crew) {
  static tbb::global_control held(tbb::global_control::max_allowed_parallelism, crew);
  (void)&held;
}

// A chunk row hands TBB CAIRN's claim size with simple_partitioner, which honours it exactly. The
// default row hands it a plain range and auto_partitioner, which is what TBB chooses alone. TBB
// claims on demand and steals in both rows, so it has one chunk row and not two.
template<class Body> inline void tbb_for(std::size_t n, Body body) {
  const std::size_t chunk = grain(n);
  if(chunk == 0) {
    tbb::parallel_for(tbb::blocked_range<std::size_t>(0, n), body);
    return;
  }
  tbb::parallel_for(tbb::blocked_range<std::size_t>(0, n, chunk), body, tbb::simple_partitioner());
}

}  // namespace bench

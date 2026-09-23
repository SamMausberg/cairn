// How often does an element run on the same thread as it did in the region before? Runs one region of n
// elements R times over the same array and records, for every 1024th element, the thread that ran it.
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <thread>
#include <vector>
#include "cairn_parallel.hpp"

int main(int argc, char** argv) {
  const std::size_t n = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 1000000;
  const int rounds = 200;
  const std::size_t stride = 1024, marks = (n + stride - 1) / stride;
  std::vector<std::size_t> before(marks, 0), now(marks, 0);
  std::vector<float> data(n, 1.0f);
  float* d = data.data();
  std::size_t* mark = now.data();
  long same = 0, total = 0;
  for(int r = 0; r < rounds; ++r) {
    cr::par::run(n, [=](std::size_t i) noexcept {
      d[i] = d[i] * 1.0001f + 0.5f;
      if(i % stride == 0) mark[i / stride] = std::hash<std::thread::id>{}(std::this_thread::get_id());
    });
    if(r > 0)
      for(std::size_t m = 0; m < marks; ++m) {
        same += now[m] == before[m];
        ++total;
      }
    before = now;
  }
  std::printf("n=%zu same_thread_as_last_region=%.3f\n", n, double(same) / double(total));
  return data[n / 2] > 0 ? 0 : 1;
}

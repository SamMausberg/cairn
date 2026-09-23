// Region microbenchmark: saxpy and a dearer body through cr::par::run, or OpenMP static when OMP is defined.
// Prints one line per (body, n): median microseconds per region over BLOCKS timed blocks.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#ifdef OMP
#include <omp.h>
#else
#include "cairn_parallel.hpp"
#endif

static float* aligned(std::size_t n) {
  void* p = nullptr;
  if(posix_memalign(&p, 64, n * sizeof(float) + 64) != 0) std::abort();
  return static_cast<float*>(p);
}

template<class F> static void region(std::size_t n, F&& body) {
#ifdef OMP
  const long long m = (long long)n;
#pragma omp parallel for schedule(static)
  for(long long i = 0; i < m; ++i) body(std::size_t(i));
#else
  cr::par::run(n, body);
#endif
}

int main(int argc, char** argv) {
  const std::size_t sizes[] = {20000, 100000, 300000, 1000000, 3000000, 10000000};
  const int BLOCKS = argc > 1 ? std::atoi(argv[1]) : 15;
  const char* only = argc > 2 ? argv[2] : "";
  float sink = 0;
  for(int kind = 0; kind < 2; ++kind) {
    const char* name = kind == 0 ? "saxpy" : "dear";
    if(*only && std::strcmp(only, name) != 0) continue;
    for(std::size_t n : sizes) {
      float* out = aligned(n);
      float* x = aligned(n);
      float* y = aligned(n);
      for(std::size_t i = 0; i < n; ++i) {
        x[i] = float(i % 97) * 0.5f;
        y[i] = float(i % 13);
        out[i] = 0;
      }
      const float a = 1.5f;
      auto saxpy = [=](std::size_t i) noexcept { out[i] = a * x[i] + y[i]; };
      auto dear = [=](std::size_t i) noexcept {
        float v = x[i];
        for(int k = 0; k < 16; ++k) v = v * 0.999f + y[i] * 0.001f;
        out[i] = v;
      };
      std::size_t reps = std::max<std::size_t>(1, std::min<std::size_t>(4000, 32000000 / n));
      std::vector<double> per;
      for(int b = 0; b < BLOCKS + 1; ++b) {
        const auto s = std::chrono::steady_clock::now();
        for(std::size_t r = 0; r < reps; ++r) {
          if(kind == 0)
            region(n, saxpy);
          else
            region(n, dear);
        }
        const auto e = std::chrono::steady_clock::now();
        if(b) per.push_back(std::chrono::duration<double, std::micro>(e - s).count() / double(reps));
      }
      std::sort(per.begin(), per.end());
      sink += out[n / 2];
      std::printf("%s %zu median_us=%.3f min_us=%.3f max_us=%.3f\n", name, n, per[per.size() / 2], per.front(), per.back());
      std::free(out);
      std::free(x);
      std::free(y);
    }
  }
  std::fprintf(stderr, "sink %f\n", double(sink));
  return 0;
}

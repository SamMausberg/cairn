// Microbenchmark for the CAIRN host parallel region: cr::par::run against the sequential loop the
// same source would otherwise write. Three questions: how a region's cost scales with its size,
// where a region starts to beat the loop, and what one small region costs when it is repeated.
// Two lane bodies bracket what an element can cost: saxpy (f32, two flops over twelve bytes, so a
// large region is bound by memory bandwidth) and a dependent integer mix (registers only, so a
// large region is bound by the cores). Every case is checked against the sequential result before
// it is reported. Prints one JSON object; bench/host_regions/host_regions.py compiles it under the project's
// flags, runs it and records the environment. Nothing here is CUDA: the device half of the
// runtime is measured by bench/gpu/parallel_gpu.cu.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <thread>
#include <vector>
#include "cairn_parallel.hpp"

static std::size_t lanes() {
  const unsigned hw = std::thread::hardware_concurrency();
  return hw ? std::size_t(hw) : std::size_t(1);
}

// Medians, because one slow run on a shared machine should not become the number. `inner` is how
// many times the case runs inside one timed block: a small region takes less time than the clock
// can resolve, so it is repeated until the block is long enough and the total is divided back.
template<class F> static double median_ms(int reps, std::size_t inner, F f) {
  std::vector<double> seen;
  f();  // warm up: first touch of the pages, and whatever the runtime builds on first use
  for(int r = 0; r < reps; ++r) {
    const auto start = std::chrono::steady_clock::now();
    for(std::size_t k = 0; k < inner; ++k) {
      f();
      __asm__ __volatile__("" ::: "memory");  // or the compiler keeps one pass and drops the rest
    }
    const std::chrono::duration<double, std::milli> took = std::chrono::steady_clock::now() - start;
    seen.push_back(took.count() / double(inner));
  }
  std::sort(seen.begin(), seen.end());
  return seen[seen.size() / 2];
}

static int repetitions(std::size_t n) { return n <= 1000000 ? 9 : n <= 10000000 ? 5 : 3; }
// Enough passes that one timed block covers sixteen million elements, so neither the clock nor a
// single scheduling hiccup is what the small sizes measure. The cap keeps a runtime whose regions
// cost a millisecond each from turning the smallest sizes into an afternoon.
static std::size_t inner_passes(std::size_t n) {
  const std::size_t want = n >= 16000000 ? 1 : 16000000 / n;
  return want > 1000 ? 1000 : want;
}

// A buffer whose first element starts on a cache line. Plain vectors of an awkward length start at
// whatever offset the allocator hands out, and a sequential case that happened to be aligned was
// measured a quarter faster than the parallel case it is compared against, which is a fact about
// the allocator and not about either loop.
template<class T> struct Aligned {
  std::vector<T> held;
  T* p;
  explicit Aligned(std::size_t n) : held(n + 64 / sizeof(T), T{}) {
    const std::uintptr_t at = reinterpret_cast<std::uintptr_t>(held.data());
    p = reinterpret_cast<T*>((at + 63) & ~std::uintptr_t(63));
  }
};

static std::uint64_t mixed(std::uint64_t v) {
  for(int k = 0; k < 8; ++k) {
    v ^= v >> 29;
    v *= 0xbf58476d1ce4e5b9ull;
  }
  return v;
}

struct Row {
  std::size_t n;
  double seq;
  double par;
  bool ok;
};

static Row saxpy_row(std::size_t n) {
  const int reps = repetitions(n);
  const std::size_t inner = inner_passes(n);
  const float a = 2.5f;
  Aligned<float> x(n), y(n), want(n), got(n);
  for(std::size_t i = 0; i < n; ++i) {
    x.p[i] = float(i % 1024) * 0.5f;
    y.p[i] = float(i % 77);
  }
  const float* xp = x.p;
  const float* yp = y.p;
  float* wp = want.p;
  float* gp = got.p;
  const double seq = median_ms(reps, inner, [=] { for(std::size_t i = 0; i < n; ++i) wp[i] = a * xp[i] + yp[i]; });
  const double par =
      median_ms(reps, inner, [=] { cr::par::run(n, [=](std::size_t i) noexcept { gp[i] = a * xp[i] + yp[i]; }); });
  return Row{n, seq, par, std::equal(wp, wp + n, gp)};
}

static Row mixed_row(std::size_t n) {
  const int reps = repetitions(n);
  const std::size_t inner = inner_passes(n);
  Aligned<std::uint64_t> in(n), want(n), got(n);
  for(std::size_t i = 0; i < n; ++i) in.p[i] = std::uint64_t(i) * 2654435761ull + 1;
  const std::uint64_t* ip = in.p;
  std::uint64_t* wp = want.p;
  std::uint64_t* gp = got.p;
  const double seq = median_ms(reps, inner, [=] { for(std::size_t i = 0; i < n; ++i) wp[i] = mixed(ip[i]); });
  const double par =
      median_ms(reps, inner, [=] { cr::par::run(n, [=](std::size_t i) noexcept { gp[i] = mixed(ip[i]); }); });
  return Row{n, seq, par, std::equal(wp, wp + n, gp)};
}

// Many small regions one after another, as a loop of `parallel` statements writes them. The work
// inside is deliberately trivial, so what is timed is almost entirely what a region itself costs.
struct Tiny {
  std::size_t n;
  std::size_t regions;
  double seq;
  double par;
  bool ok;
};

static Tiny tiny_row(std::size_t n, std::size_t regions) {
  Aligned<std::uint64_t> want(n), got(n);
  std::uint64_t* wp = want.p;
  std::uint64_t* gp = got.p;
  // The barrier in both loops is what makes them comparable: without it the sequential version is
  // one nest the compiler may optimize across rounds, while each region really is a separate step.
  const double seq = median_ms(3, 1, [=] {
    for(std::size_t r = 0; r < regions; ++r) {
      for(std::size_t i = 0; i < n; ++i) wp[i] = wp[i] + i + r;
      __asm__ __volatile__("" ::: "memory");
    }
  });
  const double par = median_ms(3, 1, [=] {
    for(std::size_t r = 0; r < regions; ++r) {
      cr::par::run(n, [=](std::size_t i) noexcept { gp[i] = gp[i] + i + r; });
      __asm__ __volatile__("" ::: "memory");
    }
  });
  return Tiny{n, regions, seq, par, std::equal(wp, wp + n, gp)};
}

static void print_row(const char* name, const Row& r, bool last) {
  std::printf(
      "    {\"case\": \"%s\", \"n\": %zu, \"sequential_ms\": %.6f, \"parallel_ms\": %.6f,\n"
      "     \"speedup_par_over_seq\": %.3f, \"results_agree\": %s}%s\n",
      name, r.n, r.seq, r.par, r.seq / r.par, r.ok ? "true" : "false", last ? "" : ",");
}

// The smallest ladder size from which the region beat the loop by a quarter and kept beating it at
// every larger size. A bare `first n where parallel < sequential` reports a crossover below the
// runtime's serial cutoff, where the two are the same code and differ only by the machine's mood.
static std::size_t crossover(const std::vector<Row>& rows) {
  std::size_t found = 0;
  for(std::size_t k = rows.size(); k-- > 0;) {
    if(rows[k].seq < rows[k].par * 1.25) break;
    found = rows[k].n;
  }
  return found;
}

int main() {
  static const std::size_t ladder[] = {1000,    3000,    10000,    30000,    100000,   300000,
                                       1000000, 3000000, 10000000, 30000000, 100000000};
  std::printf("{\n  \"lanes_reported_by_hardware_concurrency\": %zu,\n", lanes());
  std::printf(
      "  \"statistic\": \"median of 9 timed runs (5 above 1e6, 3 above 1e7) after one warm up; a sweep "
      "block repeats the case until it covers sixteen million elements (at most a thousand times) and "
      "the total is divided back, so a small region is not measured against the clock's floor\",\n");
  std::printf("  \"sweep\": [\n");
  std::vector<Row> saxpy, mix;
  bool agree = true;
  for(std::size_t n : ladder) {
    saxpy.push_back(saxpy_row(n));
    mix.push_back(mixed_row(n));
  }
  for(std::size_t k = 0; k < saxpy.size(); ++k) {
    agree = agree && saxpy[k].ok && mix[k].ok;
    print_row("saxpy_f32", saxpy[k], false);
    print_row("mixed_u64", mix[k], k + 1 == saxpy.size());
  }
  std::printf("  ],\n  \"crossover\": {\n");
  std::printf(
      "    \"note\": \"smallest ladder size from which the region beat the sequential loop by a quarter "
      "and kept beating it at every larger size; 0 means none did\",\n");
  std::printf("    \"saxpy_f32\": %zu, \"mixed_u64\": %zu\n  },\n", crossover(saxpy), crossover(mix));
  std::printf("  \"tiny_regions\": [\n");
  static const std::size_t widths[] = {64, 1024, 16384};
  for(std::size_t k = 0; k < std::size(widths); ++k) {
    const Tiny t = tiny_row(widths[k], 1000);
    agree = agree && t.ok;
    std::printf(
        "    {\"n\": %zu, \"regions\": %zu, \"sequential_total_ms\": %.4f, \"parallel_total_ms\": %.4f,\n"
        "     \"parallel_per_region_us\": %.3f, \"sequential_per_region_us\": %.3f, \"results_agree\": %s}%s\n",
        t.n, t.regions, t.seq, t.par, t.par * 1000.0 / double(t.regions), t.seq * 1000.0 / double(t.regions),
        t.ok ? "true" : "false", k + 1 == std::size(widths) ? "" : ",");
  }
  std::printf("  ],\n  \"every_case_agreed_with_the_sequential_result\": %s\n}\n", agree ? "true" : "false");
  return agree ? 0 : 1;
}

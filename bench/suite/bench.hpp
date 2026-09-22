// The measurement protocol every arm of every kernel runs, written once.
//
// The protocol is bench/host_regions/host_regions.cpp's, kept deliberately: a median of nine timed
// blocks after one warm up, each block repeated until it covers sixteen million elements and the
// total divided back, every case checked against a sequential result computed in this process, and
// nothing reported when a case disagrees. A kernel supplies a Case type; run_all supplies the
// ladder, the clock, the checking and the JSON. An arm supplies arm_setup and the kernel entry the
// Case calls.
//
// Timing lives here so that no kernel can measure itself differently from another, and so that the
// only thing that varies between two rows of a table is the arm.
#pragma once
#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

// The three grain rows, named here so a build line, a table column and a pragma cannot drift apart.
// DEFAULT lets a library choose alone. CHUNK hands it CAIRN's claim size assigned in advance, which
// is OpenMP's schedule(static, g) and TBB's simple_partitioner over a range of grain g. CLAIM hands
// it the same size taken on demand, which is OpenMP's schedule(dynamic, g) and is what cr::par::run
// itself does; TBB claims and steals in both of its rows, so it has no separate CLAIM row.
#define BENCH_GRAIN_DEFAULT 0
#define BENCH_GRAIN_CHUNK 1
#define BENCH_GRAIN_CLAIM 2
#ifndef BENCH_GRAIN_ROW
#define BENCH_GRAIN_ROW BENCH_GRAIN_DEFAULT
#endif
#ifndef BENCH_GUARDED
#error "every arm is built twice: pass -DBENCH_GUARDED=1 or -DBENCH_GUARDED=0"
#endif

namespace bench {

// A buffer whose first element starts on a cache line. host_regions.cpp records why: a sequential
// case that happened to be aligned measured a quarter faster than the parallel case it was
// compared against, which is a fact about the allocator and not about either loop.
template<class T> struct Aligned {
  std::vector<T> held;
  T* p;
  explicit Aligned(std::size_t n) : held(n + 64 / sizeof(T) + 1, T{}) {
    const std::uintptr_t at = reinterpret_cast<std::uintptr_t>(held.data());
    p = reinterpret_cast<T*>((at + 63) & ~std::uintptr_t(63));
  }
};

// The worker count every arm is given. One number for all of them, from the environment, so the
// CAIRN pool, the OpenMP team and the TBB arena are the same width by construction and not by
// coincidence. CAIRN_LANES is what the CAIRN runtime itself reads (cairn_parallel.hpp).
inline std::size_t lanes() noexcept {
  if(const char* text = std::getenv("CAIRN_LANES")) {
    const long want = std::atol(text);
    if(want > 0) return std::size_t(want);
  }
  const unsigned found = std::thread::hardware_concurrency();
  return found != 0 ? std::size_t(found) : std::size_t(1);
}

// The claim size cr::par::run hands one lane, derived from src/cairn/runtime/cairn_parallel.hpp
// (GRAIN, SPLIT, CUTOFF and run_wide). It is not a constant: 8192 is the floor, and a large region
// gives each lane two claims of n / (2 * used lanes). The matched grain row hands a library this
// same number, so a scheduling difference between two rows is the library's and not the size's.
inline constexpr std::size_t CAIRN_GRAIN = 8192;
inline constexpr std::size_t CAIRN_SPLIT = 2;
inline constexpr std::size_t CAIRN_CUTOFF = 2 * CAIRN_GRAIN;

inline std::size_t cairn_claim(std::size_t n) noexcept {
  if(n < CAIRN_CUTOFF) return n;  // below the cutoff a region is exactly the loop it replaces
  std::size_t use = n / CAIRN_GRAIN;
  const std::size_t crew = lanes();
  if(use > crew) use = crew;
  const std::size_t grain = n / (use * CAIRN_SPLIT);
  return grain < CAIRN_GRAIN ? CAIRN_GRAIN : grain;
}

// BENCH_GRAIN_ROW selects the grain row. library_default hands a library nothing and lets it
// choose; the two chunk rows hand it CAIRN's claim size, once assigned in advance and once claimed
// on demand, because CAIRN claims on demand and a library's default rarely does. grain() returns
// zero for the default row, meaning "the library decides".
inline std::size_t grain(std::size_t n) noexcept {
#if BENCH_GRAIN_ROW == BENCH_GRAIN_DEFAULT
  (void)n;
  return 0;
#else
  return cairn_claim(n);
#endif
}

inline const char* grain_row() noexcept {
#if BENCH_GRAIN_ROW == BENCH_GRAIN_CHUNK
  return "cairn_claim_assigned";
#elif BENCH_GRAIN_ROW == BENCH_GRAIN_CLAIM
  return "cairn_claim_on_demand";
#else
  return "library_default";
#endif
}

inline bool guarded() noexcept { return BENCH_GUARDED == 1; }  // 2 is the matched build: see guards.hpp

}  // namespace bench

// The one OpenMP loop directive, so no kernel can schedule its loop differently from another. The
// chunk rows name CAIRN's claim size, which the arm holds in a local called bench_chunk; the
// default row names no schedule at all and lets the runtime choose.
// BENCH_OMP_FOR_INNER is the same schedule as a worksharing loop inside a region the arm opened
// itself, which is what an arm needs when each thread keeps something of its own across the loop: a
// privatized histogram is written that way, and the alternative costs a thread-local lookup at every
// element, which would price the arm's bookkeeping rather than its work.
#if BENCH_GRAIN_ROW == BENCH_GRAIN_CHUNK
#define BENCH_OMP_FOR _Pragma("omp parallel for schedule(static, bench_chunk)")
#define BENCH_OMP_FOR_INNER _Pragma("omp for schedule(static, bench_chunk)")
#elif BENCH_GRAIN_ROW == BENCH_GRAIN_CLAIM
#define BENCH_OMP_FOR _Pragma("omp parallel for schedule(dynamic, bench_chunk)")
#define BENCH_OMP_FOR_INNER _Pragma("omp for schedule(dynamic, bench_chunk)")
#else
#define BENCH_OMP_FOR _Pragma("omp parallel for")
#define BENCH_OMP_FOR_INNER _Pragma("omp for")
#endif

namespace bench {

// Medians, because one slow block on a shared machine should not become the number.
template<class F> double median_ms(int reps, std::size_t inner, F f) {
  std::vector<double> seen;
  f();  // warm up: first touch of the pages, and whatever a runtime builds on first use
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

struct Options {
  std::vector<std::size_t> sizes = {1000, 10000, 100000, 1000000, 10000000, 100000000};
  std::vector<std::size_t> widths = {64, 1024, 16384};
  std::size_t regions = 1000;
  std::size_t cover = 16000000;
  std::size_t cap = 1000;
  int reps = 9;
  std::size_t dump = 0;  // nonzero: print this size's result for oracle.py and measure nothing
};

inline std::vector<std::size_t> numbers(const char* text) {
  std::vector<std::size_t> out;
  std::size_t value = 0;
  bool digits = false;
  for(const char* p = text;; ++p) {
    if(*p >= '0' && *p <= '9') {
      value = value * 10 + std::size_t(*p - '0');
      digits = true;
    } else {
      if(digits) out.push_back(value);
      value = 0;
      digits = false;
      if(*p == '\0') break;
    }
  }
  return out;
}

inline Options options(int argc, char** argv) {
  Options opt;
  for(int i = 1; i + 1 < argc; i += 2) {
    const std::string flag = argv[i];
    if(flag == "--sizes") opt.sizes = numbers(argv[i + 1]);
    else if(flag == "--region-widths") opt.widths = numbers(argv[i + 1]);
    else if(flag == "--regions") opt.regions = numbers(argv[i + 1]).at(0);
    else if(flag == "--cover") opt.cover = numbers(argv[i + 1]).at(0);
    else if(flag == "--cap") opt.cap = numbers(argv[i + 1]).at(0);
    else if(flag == "--reps") opt.reps = int(numbers(argv[i + 1]).at(0));
    else if(flag == "--dump") opt.dump = numbers(argv[i + 1]).at(0);
    else std::exit(2);  // an unknown option is a harness mistake, never a silent default
  }
  return opt;
}

// Enough passes that one timed block covers `cover` elements, so neither the clock nor a single
// scheduling hiccup is what a small size measures. The cap keeps a slow arm from taking an
// afternoon over the smallest sizes.
inline std::size_t inner_passes(const Options& opt, std::size_t n) {
  if(n == 0) return 1;  // a size of zero is not on the ladder, and dividing by it is not a result
  const std::size_t want = n >= opt.cover ? 1 : opt.cover / n;
  return want > opt.cap ? opt.cap : want;
}

// Every arm supplies these two. arm_setup gives the arm's runtime the one worker count.
const char* arm_name();
void arm_setup(std::size_t lanes);

// The whole protocol for one kernel. Case must offer:
//   explicit Case(std::size_t n)  allocate, fill the inputs, and compute the expected result with
//                                 an ordinary sequential loop in this process
//   void apply()                  run the arm once over the whole input
//   bool agrees() const           the arm's output equals the expected result
//   void dump() const             print the arm's output as JSON for oracle.py
// Every kernel here is idempotent under repeated apply(), which is what lets the back-to-back
// region row call it a thousand times and still check the result.
template<class Case> int run_all(int argc, char** argv, const char* kernel) {
  const Options opt = options(argc, argv);
  const std::size_t crew = lanes();
  arm_setup(crew);
  if(opt.dump != 0) {
    Case one(opt.dump);
    one.apply();
    std::printf("{\"kernel\": \"%s\", \"arm\": \"%s\", \"n\": %zu, \"result\": ", kernel, arm_name(), opt.dump);
    one.dump();
    std::printf(", \"agrees_with_in_process_sequential\": %s}\n", one.agrees() ? "true" : "false");
    return one.agrees() ? 0 : 1;
  }
  bool agree = true;
  std::printf("{\n  \"kernel\": \"%s\",\n  \"arm\": \"%s\",\n", kernel, arm_name());
  // An arm with no scheduler to set has no grain row, and "library_default" there would read as if
  // a library had chosen. Every CAIRN arm picks its own claim size inside cr::par::run, the plain
  // arm is one thread, and the threads arm splits into a fixed number of parts.
  const char* const arm = arm_name();
  const bool schedules =
      std::strncmp(arm, "cairn", 5) != 0 && std::strcmp(arm, "plain") != 0 && std::strcmp(arm, "threads") != 0;
  std::printf("  \"guarded\": %s,\n  \"grain_row\": \"%s\",\n", guarded() ? "true" : "false",
              schedules ? grain_row() : "not_applicable");
  std::printf("  \"lanes\": %zu,\n  \"reps\": %d,\n  \"sweep\": [\n", crew, opt.reps);
  for(std::size_t k = 0; k < opt.sizes.size(); ++k) {
    const std::size_t n = opt.sizes[k];
    const std::size_t inner = inner_passes(opt, n);
    Case one(n);
    Case* held = &one;
    const double ms = median_ms(opt.reps, inner, [held] { held->apply(); });
    const bool ok = one.agrees();
    agree = agree && ok;
    std::printf("    {\"n\": %zu, \"inner\": %zu, \"median_ms\": %.9f, \"agrees\": %s}%s\n", n, inner, ms,
                ok ? "true" : "false", k + 1 == opt.sizes.size() ? "" : ",");
  }
  std::printf("  ],\n  \"back_to_back\": [\n");
  for(std::size_t k = 0; k < opt.widths.size(); ++k) {
    const std::size_t n = opt.widths[k];
    Case one(n);
    Case* held = &one;
    const std::size_t rounds = opt.regions;
    const double ms = median_ms(3, 1, [held, rounds] {
      for(std::size_t r = 0; r < rounds; ++r) {
        held->apply();
        __asm__ __volatile__("" ::: "memory");  // each round really is a separate step
      }
    });
    const bool ok = one.agrees();
    agree = agree && ok;
    std::printf("    {\"n\": %zu, \"regions\": %zu, \"total_ms\": %.6f, \"per_region_us\": %.4f, \"agrees\": %s}%s\n", n,
                rounds, ms, ms * 1000.0 / double(rounds), ok ? "true" : "false",
                k + 1 == opt.widths.size() ? "" : ",");
  }
  std::printf("  ],\n  \"every_case_agreed_with_the_sequential_result\": %s\n}\n", agree ? "true" : "false");
  return agree ? 0 : 1;
}

}  // namespace bench

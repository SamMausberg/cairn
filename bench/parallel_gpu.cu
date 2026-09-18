// Microbenchmark for the CAIRN parallel and device runtimes: saxpy (f32), a dot product reduce
// (f32) and an even number compaction (u64), each on a sequential host loop, on cr::par::run and
// on the device. Device numbers are reported twice: kernel only, and end to end with transfers.
// Every case is checked against the sequential result before it is timed. Prints one JSON object;
// bench/parallel_gpu.py compiles it, runs it and adds the environment. Build line: see
// cairn_gpu.hpp (this file needs no other flags).
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <vector>
#include "cairn_gpu.hpp"
#include "cairn_parallel.hpp"

using cr::gpu::Dir;
static const int REPS = 5;

template<class F> static double median_ms(F f) {
  std::vector<double> seen;
  f();  // warm up: first touch, JIT and any lazy context creation stay out of the numbers
  for(int r = 0; r < REPS; ++r) {
    const auto start = std::chrono::steady_clock::now();
    f();
    const std::chrono::duration<double, std::milli> took = std::chrono::steady_clock::now() - start;
    seen.push_back(took.count());
  }
  std::sort(seen.begin(), seen.end());
  return seen[seen.size() / 2];
}
static std::size_t chunks(std::size_t n) {
  const std::size_t t = std::thread::hardware_concurrency() ? std::thread::hardware_concurrency() : 1;
  return t < n ? t : n;
}
static void report(const char* name, const char* type, std::size_t n, double seq, double par,
                   double kernel, double e2e, bool ok) {
  std::printf(
      "    {\"case\": \"%s\", \"type\": \"%s\", \"n\": %zu, \"host_sequential_ms\": %.3f,\n"
      "     \"host_parallel_ms\": %.3f, \"device_kernel_ms\": %.3f, \"device_end_to_end_ms\": %.3f,\n"
      "     \"speedup_par_over_seq\": %.2f, \"speedup_kernel_over_seq\": %.2f,\n"
      "     \"speedup_end_to_end_over_seq\": %.2f, \"agrees_with_sequential\": %s}",
      name, type, n, seq, par, kernel, e2e, seq / par, seq / kernel, seq / e2e, ok ? "true" : "false");
}

static void saxpy(std::size_t n, bool last) {
  const float a = 2.5f;
  std::vector<float> x(n), y(n), want(n), got(n);
  for(std::size_t i = 0; i < n; ++i) {
    x[i] = float(i % 1024) * 0.5f;
    y[i] = float(i % 77);
  }
  const float* xp = x.data();
  const float* yp = y.data();
  float* wp = want.data();
  const double seq = median_ms([=] { for(std::size_t i = 0; i < n; ++i) wp[i] = a * xp[i] + yp[i]; });
  float* gp = got.data();
  const double par = median_ms([=] { cr::par::run(n, [=](std::size_t i) { gp[i] = a * xp[i] + yp[i]; }); });
  bool ok = got == want;
  cr::gpu::Buffer<float> dx(n), dy(n), dout(n);
  float* dxp = dx.data();
  float* dyp = dy.data();
  float* dop = dout.data();
  cr::gpu::copy(dxp, xp, n, Dir::h2d);
  cr::gpu::copy(dyp, yp, n, Dir::h2d);
  const auto lane = [=] CR_DEVICE(std::size_t i) { dop[i] = a * dxp[i] + dyp[i]; };
  const double kernel = median_ms([=] { cr::gpu::launch(n, lane); });
  const double e2e = median_ms([=] {
    cr::gpu::copy(dxp, xp, n, Dir::h2d);
    cr::gpu::copy(dyp, yp, n, Dir::h2d);
    cr::gpu::launch(n, lane);
    cr::gpu::copy(gp, dop, n, Dir::d2h);
  });
  ok = ok && got == want;
  report("saxpy", "f32", n, seq, par, kernel, e2e, ok);
  std::printf(last ? "\n" : ",\n");
}

static void dot(std::size_t n, bool last) {
  std::vector<float> x(n), y(n);
  for(std::size_t i = 0; i < n; ++i) {
    x[i] = float(i % 1000) * 0.001f;
    y[i] = float(i % 13) * 0.25f;
  }
  const float* xp = x.data();
  const float* yp = y.data();
  float want = 0;
  const double seq = median_ms([&] {
    float acc = 0;
    for(std::size_t i = 0; i < n; ++i) acc += xp[i] * yp[i];
    want = acc;
  });
  // cr::par::run over chunk ids: each lane folds one contiguous block, the caller folds the blocks.
  const std::size_t parts = chunks(n);
  std::vector<float> partial(parts, 0);
  float* pp = partial.data();
  float got = 0;
  const double par = median_ms([&] {
    cr::par::run(parts, [=](std::size_t c) {
      const std::size_t lo = c * (n / parts) + (c < n % parts ? c : n % parts);
      const std::size_t hi = lo + n / parts + (c < n % parts ? 1 : 0);
      float acc = 0;
      for(std::size_t i = lo; i < hi; ++i) acc += xp[i] * yp[i];
      pp[c] = acc;
    });
    float acc = 0;
    for(float v : partial) acc += v;
    got = acc;
  });
  bool ok = std::abs(double(got) - double(want)) <= std::abs(double(want)) * 1e-4;
  cr::gpu::Buffer<float> dx(n), dy(n);
  float* dxp = dx.data();
  float* dyp = dy.data();
  cr::gpu::copy(dxp, xp, n, Dir::h2d);
  cr::gpu::copy(dyp, yp, n, Dir::h2d);
  const auto plus = [] CR_DEVICE(float u, float v) { return u + v; };
  const auto term = [=] CR_DEVICE(std::size_t i) { return dxp[i] * dyp[i]; };
  float device = 0;
  const double kernel = median_ms([&] { device = cr::gpu::reduce<float>(n, 0.0f, plus, term); });
  const double e2e = median_ms([&] {
    cr::gpu::copy(dxp, xp, n, Dir::h2d);
    cr::gpu::copy(dyp, yp, n, Dir::h2d);
    device = cr::gpu::reduce<float>(n, 0.0f, plus, term);
  });
  // Association order differs between a sequential f32 fold and a tree, so compare with tolerance.
  ok = ok && std::abs(double(device) - double(want)) <= std::abs(double(want)) * 1e-4;
  report("dot_reduce", "f32", n, seq, par, kernel, e2e, ok);
  std::printf(last ? "\n" : ",\n");
}

static void compact(std::size_t n, bool last) {
  std::vector<std::uint64_t> in(n), want(n, 0), got(n, 0);
  for(std::size_t i = 0; i < n; ++i) in[i] = i * 2654435761ull;
  const std::uint64_t* ip = in.data();
  std::uint64_t* wp = want.data();
  std::size_t kept = 0;
  const double seq = median_ms([&] {
    std::size_t at = 0;
    for(std::size_t i = 0; i < n; ++i)
      if(ip[i] % 2 == 0) wp[at++] = ip[i];
    kept = at;
  });
  // Stable in parallel: count each chunk, scan the counts, then write. Two passes over the input.
  const std::size_t parts = chunks(n);
  std::vector<std::size_t> counts(parts, 0), base(parts, 0);
  std::size_t* cp = counts.data();
  std::size_t* bp = base.data();
  std::uint64_t* gp = got.data();
  std::size_t total = 0;
  const double par = median_ms([&] {
    const auto span = [=](std::size_t c, std::size_t& lo, std::size_t& hi) {
      lo = c * (n / parts) + (c < n % parts ? c : n % parts);
      hi = lo + n / parts + (c < n % parts ? 1 : 0);
    };
    cr::par::run(parts, [=](std::size_t c) {
      std::size_t lo = 0, hi = 0, seen = 0;
      span(c, lo, hi);
      for(std::size_t i = lo; i < hi; ++i) seen += ip[i] % 2 == 0;
      cp[c] = seen;
    });
    std::size_t running = 0;
    for(std::size_t c = 0; c < parts; ++c) {
      bp[c] = running;
      running += cp[c];
    }
    total = running;
    cr::par::run(parts, [=](std::size_t c) {
      std::size_t lo = 0, hi = 0, at = bp[c];
      span(c, lo, hi);
      for(std::size_t i = lo; i < hi; ++i)
        if(ip[i] % 2 == 0) gp[at++] = ip[i];
    });
  });
  bool ok = total == kept && std::equal(got.begin(), got.begin() + std::ptrdiff_t(kept), want.begin());
  cr::gpu::Buffer<std::uint64_t> din(n), dout(n);
  std::uint64_t* dip = din.data();
  std::uint64_t* dop = dout.data();
  cr::gpu::copy(dip, ip, n, Dir::h2d);
  const auto even = [=] CR_DEVICE(std::size_t i) { return dip[i] % 2 == 0; };
  const auto value = [=] CR_DEVICE(std::size_t i) { return dip[i]; };
  std::size_t device = 0;
  const double kernel = median_ms([&] { device = cr::gpu::compact(dop, n, even, value); });
  const double e2e = median_ms([&] {
    cr::gpu::copy(dip, ip, n, Dir::h2d);
    device = cr::gpu::compact(dop, n, even, value);
    cr::gpu::copy(gp, dop, device, Dir::d2h);
  });
  ok = ok && device == kept && std::equal(got.begin(), got.begin() + std::ptrdiff_t(kept), want.begin());
  report("compact_even", "u64", n, seq, par, kernel, e2e, ok);
  std::printf(last ? "\n" : ",\n");
}

int main() {
  cudaDeviceProp prop{};
  cr::gpu::check(cudaGetDeviceProperties(&prop, 0));
  int runtime = 0, driver = 0;
  cr::gpu::check(cudaRuntimeGetVersion(&runtime));
  cr::gpu::check(cudaDriverGetVersion(&driver));
  std::printf("{\n  \"gpu\": \"%s\", \"compute_capability\": \"%d.%d\", \"cuda_runtime\": %d,\n",
              prop.name, prop.major, prop.minor, runtime);
  std::printf("  \"cuda_driver\": %d, \"host_threads\": %u, \"repetitions\": %d,\n",
              driver, std::thread::hardware_concurrency(), REPS);
  std::printf("  \"statistic\": \"median of %d timed runs after one warm up\",\n", REPS);
  std::printf("  \"measurements\": [\n");
  for(std::size_t n : {std::size_t(1000000), std::size_t(100000000)}) {
    const bool last = n == 100000000;
    saxpy(n, false);
    dot(n, false);
    compact(n, last);
  }
  std::printf("  ]\n}\n");
  return 0;
}

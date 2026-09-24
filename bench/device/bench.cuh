// What every device pair's main shares: a CUDA error aborts, and a call is timed on the caller's stream two ways.
//
// gpu_us is the stream's own time between an event recorded before the call and one recorded after it returns,
// as a leaderboard harness measures a kernel: work the call leaves queued counts, and so does any time the stream
// sits idle while the call waits on the host before it returns. host_us is the host's time from the call until the
// stream has finished everything the call queued. return_us is the host's time until the call returned. burst_us
// is the stream's time for BURST calls made back to back, divided by BURST: where the host queues work faster than
// the device runs it, that is the device's time per call, and a call that waits on the host adds its round trip.
#pragma once
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <string>
#include <vector>
#include <cuda_runtime.h>

inline void ok(cudaError_t e) {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "bench: cuda: %s\n", cudaGetErrorString(e));
  std::abort();
}

inline double now_us() {
  return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

constexpr int BURST = 10;

struct Sample {
  double gpu_us, host_us, return_us;
};

class Clock {
  cudaEvent_t start_{}, stop_{};

public:
  Clock() {
    ok(cudaEventCreate(&start_));
    ok(cudaEventCreate(&stop_));
  }
  ~Clock() {
    cudaEventDestroy(start_);
    cudaEventDestroy(stop_);
  }
  template<class F> Sample time(cudaStream_t s, F&& call) {
    ok(cudaStreamSynchronize(s));
    const double t0 = now_us();
    ok(cudaEventRecord(start_, s));
    call();
    const double t1 = now_us();
    ok(cudaEventRecord(stop_, s));
    ok(cudaStreamSynchronize(s));
    const double t2 = now_us();
    float ms = 0.0f;
    ok(cudaEventElapsedTime(&ms, start_, stop_));
    return {double(ms) * 1000.0, t2 - t0, t1 - t0};
  }
  template<class F> double burst(cudaStream_t s, F&& call) {
    ok(cudaStreamSynchronize(s));
    ok(cudaEventRecord(start_, s));
    for(int k = 0; k < BURST; ++k) call();
    ok(cudaEventRecord(stop_, s));
    ok(cudaStreamSynchronize(s));
    float ms = 0.0f;
    ok(cudaEventElapsedTime(&ms, start_, stop_));
    return double(ms) * 1000.0 / BURST;
  }
};

inline double least(const std::vector<double>& v) { return v.empty() ? 0.0 : *std::min_element(v.begin(), v.end()); }

inline double median(std::vector<double> v) {
  if(v.empty()) return 0.0;
  std::sort(v.begin(), v.end());
  const std::size_t h = v.size() / 2;
  return v.size() % 2 ? v[h] : (v[h - 1] + v[h]) / 2.0;
}

// One variant's samples at one size, printed as a JSON row.
struct Series {
  std::string variant, side;  // side: "cairn" or "cuda"
  std::size_t n = 0;
  std::vector<double> gpu, host, back, bursts;
  double error = 0.0;  // the largest relative error of any timed call's result
  void add(const Sample& s) {
    gpu.push_back(s.gpu_us);
    host.push_back(s.host_us);
    back.push_back(s.return_us);
  }
  void print(bool last) const {
    std::printf("    {\"variant\": \"%s\", \"side\": \"%s\", \"n\": %zu, \"reps\": %zu, \"gpu_us\": %.2f, "
                "\"host_us\": %.2f, \"return_us\": %.2f, \"gpu_us_min\": %.2f, \"host_us_min\": %.2f, "
                "\"burst_us\": %.2f, \"relative_error\": %.3g}%s\n",
                variant.c_str(), side.c_str(), n, gpu.size(), median(gpu), median(host), median(back), least(gpu),
                least(host), median(bursts), error, last ? "" : ",");
  }
};

// One way to make the pair's call: the CUDA side's or a CAIRN entry.
struct Variant {
  std::string name, side;
  std::function<void()> call;
};

// Every variant at one size: a warm-up call each, then `reps` rounds of one timed call of each in turn, then bursts
// in the same order, so the two sides meet the same clocks and the same state of the device.
// A variant whose "side/name" holds this text is left out: `prog REPS TEXT`.
inline std::string skipped;

inline void measure(cudaStream_t s, std::size_t n, std::vector<Variant> variants, int reps, std::vector<Series>& all) {
  if(!skipped.empty())
    std::erase_if(variants, [](const Variant& v) { return (v.side + "/" + v.name).find(skipped) != std::string::npos; });
  Clock clock;
  const std::size_t first = all.size();
  for(const Variant& v : variants) {
    std::fprintf(stderr, "warm-up %s/%s n=%zu\n", v.side.c_str(), v.name.c_str(), n);
    all.push_back(Series{v.name, v.side, n});
    v.call();
    ok(cudaGetLastError());
    ok(cudaStreamSynchronize(s));
  }
  ok(cudaStreamSynchronize(s));
  for(int r = 0; r < reps; ++r)
    for(std::size_t k = 0; k < variants.size(); ++k) all[first + k].add(clock.time(s, variants[k].call));
  for(int r = 0; r < reps / 4 + 1; ++r)
    for(std::size_t k = 0; k < variants.size(); ++k) all[first + k].bursts.push_back(clock.burst(s, variants[k].call));
}

inline void header(const char* pair, const std::string& more = "") {
  cudaDeviceProp p{};
  int device = 0, driver = 0, runtime = 0;
  ok(cudaGetDevice(&device));
  ok(cudaGetDeviceProperties(&p, device));
  ok(cudaDriverGetVersion(&driver));
  ok(cudaRuntimeGetVersion(&runtime));
  std::printf("{\n  \"pair\": \"%s\", \"device\": \"%s\", \"sm\": \"%d.%d\", \"sms\": %d, \"driver_api\": %d, "
              "\"runtime_api\": %d,%s\n  \"rows\": [\n",
              pair, p.name, p.major, p.minor, p.multiProcessorCount, driver, runtime, more.c_str());
}

inline void footer(const std::vector<Series>& all) {
  for(std::size_t k = 0; k < all.size(); ++k) all[k].print(k + 1 == all.size());
  std::printf("  ]\n}\n");
}

// Reps from the command line, so a batch can stay short, and what to leave out: `prog [reps] [text]`.
inline int reps_from(int argc, char** argv, int fallback) {
  if(argc > 2) skipped = argv[2];
  return argc > 1 ? std::max(1, std::atoi(argv[1])) : fallback;
}

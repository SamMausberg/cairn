// saxpy_f32: out[i] = a * x[i] + y[i] over f32.
//
// The fill is bench/host/host_regions.cpp's, so a number here sits beside the ones already
// recorded in evidence/v1_2/host_regions. Every value is exactly representable in f32 and every
// product and sum is exact, so kernels/saxpy_f32/oracle.py can check the result in Python doubles
// without a tolerance.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, out, x, y, a) in that order.
void arm_run(std::size_t n, float* out, const float* x, const float* y, float a) noexcept;

struct Saxpy {
  static constexpr float A = 2.5f;
  std::size_t n;
  Aligned<float> x, y, out, want;

  explicit Saxpy(std::size_t size) : n(size), x(size), y(size), out(size), want(size) {
    for(std::size_t i = 0; i < n; ++i) {
      x.p[i] = float(i % 1024) * 0.5f;
      y.p[i] = float(i % 77);
      want.p[i] = A * x.p[i] + y.p[i];  // the sequential result, computed in this process
    }
  }

  void apply() { arm_run(n, out.p, x.p, y.p, A); }

  bool agrees() const { return std::equal(want.p, want.p + n, out.p); }

  void dump() const {
    std::printf("[");
    for(std::size_t i = 0; i < n; ++i) std::printf("%s%.9g", i ? ", " : "", double(out.p[i]));
    std::printf("]");
  }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::Saxpy>(argc, argv, "saxpy_f32"); }

// stencil_1d: out[i] = 0.25 * x[i-1] + 0.5 * x[i] + 0.25 * x[i+1], with the two ends copied.
//
// The fill is a small integer times a power of two: x[i] = (i % 64) * 0.5f, so every value, every
// product and every partial sum is exact in f32 and kernels/stencil_1d/oracle.py checks the result
// in Python doubles without a tolerance. The largest blend is 31.5, which is 252 quarters of a
// quarter, so nothing here comes near the 24 bits an f32 significand holds.
//
// blend3 is written with the parenthesisation the emitter writes, ((0.25f * l) + (0.5f * c)) +
// (0.25f * r), and the build line pins it there with -ffp-contract=off -fno-fast-math. Every
// baseline arm calls this one function, so no arm can drift into a different association from the
// CAIRN arm's cf_blend3.
//
// apply() overwrites every element of out, so it is idempotent and the back-to-back row may call it
// a thousand times and still check the result.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, out, x) in that order.
void arm_run(std::size_t n, float* out, const float* x) noexcept;

// The emitted cf_blend3 is `return (((0.25f * v_l) + (0.5f * v_c)) + (0.25f * v_r));`.
inline float blend3(float l, float c, float r) { return ((0.25f * l) + (0.5f * c)) + (0.25f * r); }

struct Stencil {
  std::size_t n;
  Aligned<float> x, out, want;

  explicit Stencil(std::size_t size) : n(size), x(size), out(size), want(size) {
    for(std::size_t i = 0; i < n; ++i) x.p[i] = float(i % 64) * 0.5f;
    for(std::size_t i = 0; i < n; ++i) {  // the sequential result, computed in this process
      want.p[i] = i > 0 && i + 1 < n ? blend3(x.p[i - 1], x.p[i], x.p[i + 1]) : x.p[i];
    }
  }

  void apply() { arm_run(n, out.p, x.p); }

  bool agrees() const { return std::equal(want.p, want.p + n, out.p); }

  void dump() const {
    std::printf("[");
    for(std::size_t i = 0; i < n; ++i) std::printf("%s%.9g", i ? ", " : "", double(out.p[i]));
    std::printf("]");
  }
};

using TheCase = Stencil;

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::TheCase>(argc, argv, "stencil_1d"); }

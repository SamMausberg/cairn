// mixed_u64: out[i] = mix(x[i]), where mix is eight dependent rounds of xor shift and wrapping
// multiply. Each round needs the previous round's value, so the arithmetic cannot be overlapped
// within one element and a large region is bound by the cores rather than by memory bandwidth.
//
// The body and the fill are bench/host/host_regions.cpp's, so a number here sits beside the
// ones already recorded in evidence/v1_2/host_regions. Every operation is exact in u64: the shift
// and the xor are bit operations and the multiply wraps, so kernels/mixed_u64/oracle.py reproduces
// the result in Python integers masked to 64 bits and the comparison carries no tolerance.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, out, x) in that order.
void arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept;

struct Mixed {
  std::size_t n;
  Aligned<std::uint64_t> x, out, want;

  explicit Mixed(std::size_t size) : n(size), x(size), out(size), want(size) {
    for(std::size_t i = 0; i < n; ++i) {
      x.p[i] = std::uint64_t(i) * 2654435761ull + 1;
      std::uint64_t v = x.p[i];  // the sequential result, computed in this process
      for(int k = 0; k < 8; ++k) {
        v ^= v >> 29;
        v *= 0xbf58476d1ce4e5b9ull;
      }
      want.p[i] = v;
    }
  }

  void apply() { arm_run(n, out.p, x.p); }

  bool agrees() const { return std::equal(want.p, want.p + n, out.p); }

  // %llu and not a double: a u64 mix fills all sixty four bits, and a double would round it.
  void dump() const {
    std::printf("[");
    for(std::size_t i = 0; i < n; ++i) std::printf("%s%llu", i ? ", " : "", static_cast<unsigned long long>(out.p[i]));
    std::printf("]");
  }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::Mixed>(argc, argv, "mixed_u64"); }

// compact_even: the stable prefix of the even elements of x, written into out, with the count.
//
// CAIRN's `compact` writes the selected elements in source order into storage of capacity exactly
// n, leaves everything beyond the returned count unchanged, allocates nothing on the host and runs
// on the calling thread. Its one store into the output is deliberately unguarded in the emitted C++
// (`v_out[v_used] = ...`), and that store is what the seventeen affine certificates of
// docs/internals/verification.md justify: the running index is proved to stay inside the declared
// capacity, so no bounds test is emitted for it and none is needed. Every baseline arm here leaves
// its own store unguarded for the same reason, so the two sides carry the same boundary and the
// guarded-against-unguarded pair prices that boundary and nothing else.
//
// The fill is x[i] = i * 2654435761 + 1 in wrapping u64. 2654435761 is odd, so x[i] comes out even
// exactly when i is odd and exactly half of the elements survive; the predicate still has to look
// at the value to learn that, so no arm can hoist the selection out of the loop. A parallel arm
// cannot know where its own run belongs until every earlier element has been counted, which is why
// the omp and tbb arms are two-pass.
//
// THE TAIL AND REPEATED apply(). agrees() compares the whole buffer, not just the prefix, because
// leaving the tail alone is part of what the collector promises. out is filled with TAIL at
// construction, and apply() refills it with TAIL before calling the arm. TAIL is odd, so it can
// never be one of the even values an arm selects, and a written element can never be mistaken for
// an untouched one. The refill is in apply() and not in any arm, so every arm pays exactly the same
// fill and the arms stay comparable; it is part of every number this kernel reports. The refill is
// not what makes repeated apply() idempotent, because these arms already are: each writes the same
// prefix from the same x and none of them touches the tail. It is there to pin the buffer's state
// before every pass, so a tail that is wrong after a thousand rounds is the arm's doing.
#pragma once
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, out, x) in that order and returns the count as std::size_t.
std::size_t arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept;

struct Compact {
  static constexpr std::uint64_t STRIDE = 2654435761ull;
  static constexpr std::uint64_t TAIL = 0xffffffffffffffffull;  // odd, so never a selected value

  std::size_t n;
  Aligned<std::uint64_t> x, out, want;
  std::size_t want_used;
  std::size_t used;

  explicit Compact(std::size_t size) : n(size), x(size), out(size), want(size), want_used(0), used(0) {
    for(std::size_t i = 0; i < n; ++i) x.p[i] = std::uint64_t(i) * STRIDE + 1ull;
    for(std::size_t i = 0; i < n; ++i) out.p[i] = TAIL;
    // The sequential result, computed in this process: the stable selected prefix, then the tail
    // the collector leaves alone.
    for(std::size_t i = 0; i < n; ++i) want.p[i] = TAIL;
    for(std::size_t i = 0; i < n; ++i) {
      if((x.p[i] & 1ull) == 0ull) {
        want.p[want_used] = x.p[i];
        ++want_used;
      }
    }
  }

  // The refill is part of every arm's apply, equally. See the header.
  void apply() {
    std::fill(out.p, out.p + n, TAIL);
    used = arm_run(n, out.p, x.p);
  }

  bool agrees() const { return used == want_used && std::equal(want.p, want.p + n, out.p); }

  void dump() const {
    std::printf("{\"used\": %llu, \"out\": [", static_cast<unsigned long long>(used));
    for(std::size_t i = 0; i < n; ++i) std::printf("%s%llu", i ? ", " : "", static_cast<unsigned long long>(out.p[i]));
    std::printf("]}");
  }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::Compact>(argc, argv, "compact_even"); }

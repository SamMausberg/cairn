// dot_f64: total = sum over i of x[i] * y[i] in f64, folded strictly left to right.
//
// This kernel is preregistered as a semantic difference report, not a speed comparison. CAIRN's
// host `reduce +` is a strict in-order fold: s_reduce in src/cairn/compiler/region_lowering.py,
// whose host branch is commented "Without `parallel`, a host reduction is an in-order fold", and
// docs/concurrency.md, reduce and compact. It is built with -ffp-contract=off -fno-fast-math, so
// it is exactly the sum the source writes and nothing else. An OpenMP `reduction(+:)` and a TBB
// parallel_reduce reassociate, and floating point addition is not associative, so those arms
// compute a DIFFERENT function of the same inputs. Dividing their times by CAIRN's would be
// dividing the cost of one function by the cost of another, so the suite does not do it; it records
// what each arm computed instead.
//
// The fill makes reassociation visible. x[i] = 1 / (i + 1) and y[i] = 1 for even i, i + 1 for odd
// i, so every odd term is about 1 and every even term is about 1 / (i + 1): the running total grows
// like n / 2 while the small terms shrink like 1 / n, and an in-order fold loses a different amount
// of them than a chunked one does. Whether it does so at a given size is what a run reports, and
// not something this file states in advance.
//
// WHAT agrees() CLAIMS, AND WHAT IT DOES NOT. run_all treats a false agrees() as a failed case and
// stops the suite, so agrees() here cannot mean "bit for bit equal to the fold": the parallel arms
// legitimately are not, and saying so here would abort a run over the difference this kernel exists
// to report. agrees() therefore means only "finite, and within 1e-9 relative of the in-order fold",
// which is a sanity bound on this arm's own answer and not a claim of identity. The bound separates
// two effects of very different size. Reassociating this sum moves it by the rounding that two
// orderings of the same terms disagree by, which grows like the square root of n times the machine
// epsilon and is far below 1e-9 across this sweep; dropping or duplicating one of the n / 2 unit
// terms moves the result by 2 / n relative, which is 2e-8 at the largest size and larger at every
// smaller one. The bound is fixed here before any run is recorded, and a reassociation difference
// that ever exceeded it would stop the run, which would itself be a result worth reporting rather
// than a threshold to widen. A reader must not mistake the bound for the claim.
//
// THE CLAIM IS THE BITS. dump() prints the result twice, as a %.17g decimal and as the exact u64
// bit pattern of the same double, and kernels/dot_f64/oracle.py computes the in-order fold in
// Python and reports whether those bits are the fold's. That is how the harness tells a
// reassociation difference, which is expected and is the finding, from a bug, which is not.
#pragma once
#include <cmath>
#include "../../bench.hpp"
#include "../../guards.hpp"

namespace bench {

// The arm supplies this. The signature is the emitted one: codegen.py turns the CAIRN parameters
// into (n, x, y) in that order and returns the double.
double arm_run(std::size_t n, const double* x, const double* y) noexcept;

struct Dot {
  // The stated tolerance of agrees(), and nothing more than that. See the header above.
  static constexpr double TOLERANCE = 1e-9;

  std::size_t n;
  Aligned<double> x, y;
  double want;  // the strict in-order fold, computed in this process
  double got;

  explicit Dot(std::size_t size) : n(size), x(size), y(size), want(0.0), got(0.0) {
    for(std::size_t i = 0; i < n; ++i) {
      x.p[i] = 1.0 / double(i + 1);
      y.p[i] = (i % 2 == 0) ? 1.0 : double(i + 1);
    }
    // Left to right, one term at a time, under the same strict flags the emitted fold is built
    // with: this loop is the function the CAIRN source names.
    for(std::size_t i = 0; i < n; ++i) want = want + x.p[i] * y.p[i];
  }

  void apply() { got = arm_run(n, x.p, y.p); }

  bool agrees() const {
    if(!std::isfinite(got)) return false;
    const double scale = std::fabs(want);
    return std::fabs(got - want) <= TOLERANCE * (scale > 0.0 ? scale : 1.0);
  }

  // Both forms of the one double: the decimal for a reader, and the bit pattern for the comparison
  // this kernel is preregistered to make.
  void dump() const {
    std::uint64_t pattern = 0;
    std::memcpy(&pattern, &got, sizeof pattern);
    std::printf("{\"value\": %.17g, \"bits\": %llu}", got, static_cast<unsigned long long>(pattern));
  }
};

}  // namespace bench

int main(int argc, char** argv) { return bench::run_all<bench::Dot>(argc, argv, "dot_f64"); }

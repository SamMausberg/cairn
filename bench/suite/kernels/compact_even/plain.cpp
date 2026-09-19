// The sequential C++ baseline: std::copy_if into out, returning the count.
//
// The emitter reads x[i] twice per element, once to test the predicate and once to yield the value,
// and both reads cross cr::at; the store into out crosses nothing. std::copy_if performs the same
// two reads and the same store, but it hands a predicate and an output iterator a value rather than
// an index, so each of them recovers the index from the element's address and reads it back through
// the boundary. That keeps the two guarded reads and the one unguarded store of the emitted code,
// which is the whole of what an equal boundary means here: two cr::view, one cr::disjoint, two
// cr::at, and a store the affine certificates already bound.
#include <iterator>
#include "case.hpp"

namespace bench {

const char* arm_name() { return "plain"; }

void arm_setup(std::size_t) {}

namespace {

// The cursor std::copy_if writes through. It stores at the running count, which is the one store
// codegen.py leaves unchecked because the certificates prove it cannot leave the capacity.
struct Collect {
  using iterator_category = std::output_iterator_tag;
  using value_type = void;
  using difference_type = std::ptrdiff_t;
  using pointer = void;
  using reference = void;

  std::uint64_t* out;
  const std::uint64_t* x;
  std::size_t n;
  std::size_t used;

  Collect& operator*() noexcept { return *this; }
  Collect& operator++() noexcept { return *this; }
  Collect& operator++(int) noexcept { return *this; }

  Collect& operator=(const std::uint64_t& element) noexcept {
    out[used] = BG_AT(x, static_cast<std::size_t>(&element - x), n);
    ++used;
    return *this;
  }
};

}  // namespace

std::size_t arm_run(std::size_t n, std::uint64_t* out, const std::uint64_t* x) noexcept {
  BG_VIEW(out, n);
  BG_VIEW(x, n);
  BG_DISJOINT(out, n, x, n);
  const Collect cursor{out, x, n, 0};
  const auto even = [x, n](const std::uint64_t& element) noexcept {
    return (BG_AT(x, static_cast<std::size_t>(&element - x), n) & 1ull) == 0ull;
  };
  return std::copy_if(x, x + n, cursor, even).used;
}

}  // namespace bench

// CAIRN correctly rounded sums. A parallel `reduce +` over f32 or f64 is the exact sum of its terms, rounded once to
// nearest with ties to even (docs/numerics.md, "Parallel float sums"). The exact sum does not depend on how the
// terms are ordered or grouped, so the lane count, the blocks, a plan and the device never change a bit of it.
//
// A block of terms keeps CHAINS expansions, each a pair (e0, e1) of doubles, and an accumulator. Knuth's TwoSum adds
// a term into e0 and puts the rounding error, exactly, into e1, and the error of that second sum, almost always zero,
// goes to the accumulator. So at every step the block's terms add up exactly to its expansions plus its accumulator.
// The accumulator is a two's complement integer counting the least quantum a term of the format can carry, wide
// enough for any sum of 2^64 terms: its additions are integer additions, exact, associative and commutative. Blocks
// merge their accumulators by adding them, and the total is rounded once.
//
// TwoSum is exact only while nothing overflows and nothing contracts a subtraction into a fused multiply-add: every
// CAIRN build compiles with -ffp-contract=off -fno-fast-math (projects/toolchain.py). An overflow, an infinity or a
// NaN anywhere in a chunk leaves a non-finite value in an expansion or an error, and nothing else does, so a chunk
// whose results are all finite was added exactly; a chunk that was not is added again, term by term, into the
// accumulator, and its infinities and NaNs are kept as flags. Pairs with cairn_parallel.hpp, whose lane pool runs the
// blocks.
#pragma once
#if defined(__FAST_MATH__)
#error "cairn_sum.hpp needs IEEE arithmetic: TwoSum is exact only without -ffast-math."
#endif
#include <atomic>
#include <cfloat>
#include <cstdint>
#include "cairn_parallel.hpp"
namespace cr::sum {
static_assert(FLT_EVAL_METHOD == 0, "TwoSum needs each double operation rounded to double, not to a wider format");

// A result format: its precision in bits, its least normal and largest exponents, the exponent of its least
// subnormal, which is the least quantum any term or exact sum of its terms can carry, and the 64-bit limbs of an
// accumulator. An f64 accumulator holds magnitudes below 2^1024 * 2^64 above 2^-1074, and a sign: 2163 bits in 34
// limbs. An f32 one holds magnitudes below 2^128 * 2^64 above 2^-149, and a sign: 342 bits in 6 limbs. The f32 terms
// are summed as doubles, and every double such a sum passes through is a multiple of 2^-149 below 2^192.
template<class T> struct Format;
template<> struct Format<double> {
  using Bits = std::uint64_t;
  static constexpr int P = 53, EMIN = -1022, EMAX = 1023, LEAST = -1074, LIMBS = 34;
};
template<> struct Format<float> {
  using Bits = std::uint32_t;
  static constexpr int P = 24, EMIN = -126, EMAX = 127, LEAST = -149, LIMBS = 6;
};

// What a block saw besides finite terms. OTHER marks a term that is not -0.0: an exact zero sum is -0.0 only when
// there was at least one term and every term was -0.0, as IEEE addition gives.
enum : unsigned { NAN_SEEN = 1, PLUS_INFINITY = 2, MINUS_INFINITY = 4, OTHER = 8 };

CR_HD inline std::uint64_t bits(double x) noexcept { return cr::to_bits<std::uint64_t>(x); }
template<class T> CR_HD inline T from_bits(typename Format<T>::Bits u) noexcept {
#if defined(__CUDA_ARCH__)
  if constexpr(sizeof(T) == 4) return __uint_as_float(u);
  else return __longlong_as_double(static_cast<long long>(u));
#else
  return __builtin_bit_cast(T, u);
#endif
}
CR_HD inline int leading(std::uint64_t x) noexcept {  // the index of the highest set bit; x is not zero
#if defined(__CUDA_ARCH__)
  return 63 - __clzll(static_cast<long long>(x));
#else
  return 63 - __builtin_clzll(x);
#endif
}

// The exact accumulator of format T: limb[0] is least significant, and the value is the integer times 2^LEAST.
template<class T> struct Fixed {
  static constexpr int LEAST = Format<T>::LEAST, LIMBS = Format<T>::LIMBS;
  std::uint64_t limb[LIMBS] = {};

  // Add a finite double. Every double a sum of format T adds is a multiple of 2^LEAST well inside the range, so a
  // double with a bit below 2^LEAST or beyond the limbs means the runtime broke its own invariant: it traps rather
  // than lose the bit.
  CR_HD void add(double v) noexcept {
    const std::uint64_t u = bits(v);
    const int biased = int(u >> 52) & 0x7ff;
    std::uint64_t m = u & 0xfffffffffffffull;
    if(biased) m |= 1ull << 52;
    if(!m) return;
    int at = (biased ? biased : 1) - 1075 - LEAST;  // v = m * 2^(at + LEAST)
    if(at < 0) {
      if(at <= -53 || (m & ((1ull << -at) - 1))) trap();
      m >>= -at;
      at = 0;
    }
    const int k = at >> 6, s = at & 63;
    if(k >= LIMBS - 1) trap();
    const std::uint64_t lo = m << s, hi = s ? m >> (64 - s) : 0;
    if(u >> 63) take(k, lo, hi);
    else put(k, lo, hi);
  }
  CR_HD void put(int k, std::uint64_t lo, std::uint64_t hi) noexcept {
    std::uint64_t t = limb[k] + lo;
    std::uint64_t c = t < lo;
    limb[k] = t;
    const std::uint64_t h = hi + c;  // hi is below 2^63, so this cannot wrap
    t = limb[k + 1] + h;
    c = t < h;
    limb[k + 1] = t;
    for(int j = k + 2; c && j < LIMBS; ++j) c = ++limb[j] == 0;
  }
  CR_HD void take(int k, std::uint64_t lo, std::uint64_t hi) noexcept {
    std::uint64_t t = limb[k];
    std::uint64_t b = t < lo;
    limb[k] = t - lo;
    const std::uint64_t h = hi + b;
    t = limb[k + 1];
    b = t < h;
    limb[k + 1] = t - h;
    for(int j = k + 2; b && j < LIMBS; ++j) b = limb[j]-- == 0;
  }
  CR_HD bool negative() const noexcept { return limb[LIMBS - 1] >> 63; }
  // The magnitude, in place: the two's complement negation of a negative value.
  CR_HD void negate() noexcept {
    std::uint64_t c = 1;
    for(int j = 0; j < LIMBS; ++j) {
      limb[j] = ~limb[j] + c;
      c = c && limb[j] == 0;
    }
  }
  // Add this accumulator into `total`, which other threads add into at the same time: each limb goes in with one
  // atomic addition, or subtraction for a negative value's magnitude, and a carry or borrow out of a limb goes into
  // the next one the same way. Additions modulo 2^(64 * LIMBS) commute, so the total ends exact whatever the
  // interleaving, and no limb that is zero here is touched.
  void into(std::uint64_t* total) const noexcept {
    Fixed m = *this;
    const bool minus = m.negative();
    if(minus) m.negate();
    for(int j = 0; j < LIMBS; ++j)
      if(m.limb[j]) carry(total, j, m.limb[j], minus);
  }
  static void carry(std::uint64_t* total, int j, std::uint64_t v, bool minus) noexcept {
    for(; j < LIMBS; ++j, v = 1) {
      std::atomic_ref<std::uint64_t> at(total[j]);
      if(minus) {
        if(at.fetch_sub(v, std::memory_order_relaxed) >= v) return;
      } else if(at.fetch_add(v, std::memory_order_relaxed) <= ~v) {
        return;
      }
    }
  }
};

// The exact value of `f`, rounded once to nearest with ties to even in format T. `zero` is the sign an exact zero
// takes. The bit indices below count from the accumulator's least bit, whose weight 2^LEAST is also the format's
// least subnormal.
template<class T> CR_HD T rounded(Fixed<T> f, T zero) noexcept {
  using F = Format<T>;
  using Bits = typename F::Bits;
  constexpr int LIMBS = F::LIMBS;
  const bool minus = f.negative();
  if(minus) f.negate();
  int top = LIMBS - 1;
  while(top >= 0 && !f.limb[top]) --top;
  if(top < 0) return zero;
  const int lead = 64 * top + leading(f.limb[top]);  // the index of the leading one
  // The quantum of the result, the index of its last bit: P bits below a normal result's leading one, and index 0,
  // the least subnormal, for a result below the least normal.
  int q = lead - (F::P - 1) > 0 ? lead - (F::P - 1) : 0;
  const auto field = [&f](int at) noexcept {  // the 64 bits from index `at` up, zeros past the top
    const int k = at >> 6, s = at & 63;
    std::uint64_t v = f.limb[k] >> s;
    if(s && k + 1 < LIMBS) v |= f.limb[k + 1] << (64 - s);
    return v;
  };
  std::uint64_t m = field(q) & ((1ull << (lead - q + 1)) - 1);  // at most P bits
  if(q > 0) {
    const int half = q - 1;
    const bool up = (f.limb[half >> 6] >> (half & 63)) & 1;
    bool rest = (f.limb[half >> 6] & ((1ull << (half & 63)) - 1)) != 0;
    for(int j = 0; !rest && j < (half >> 6); ++j) rest = f.limb[j] != 0;
    if(up && (rest || (m & 1))) ++m;
    if(m >> F::P) {  // rounded up to the next power of two
      m >>= 1;
      ++q;
    }
  }
  const Bits sign = Bits(minus) << (8 * sizeof(T) - 1);
  constexpr Bits fraction = (Bits(1) << (F::P - 1)) - 1;
  if(!(m >> (F::P - 1))) return from_bits<T>(sign | Bits(m));  // subnormal: q is 0, and the pattern is m
  const int exponent = q + F::LEAST + F::P - 1;
  if(exponent > F::EMAX) return from_bits<T>(sign | (Bits(2 * F::EMAX + 1) << (F::P - 1)));  // infinity
  return from_bits<T>(sign | (Bits(exponent + F::EMAX) << (F::P - 1)) | (Bits(m) & fraction));
}

// The sum from what every block added into `total` and the flags they saw, for `n` terms.
template<class T> CR_HD T result(const Fixed<T>& total, unsigned seen, std::size_t n) noexcept {
  using Bits = typename Format<T>::Bits;
  constexpr int P = Format<T>::P, EMAX = Format<T>::EMAX;
  constexpr Bits sign = Bits(1) << (8 * sizeof(T) - 1), infinity = Bits(2 * EMAX + 1) << (P - 1);
  if((seen & NAN_SEEN) || ((seen & PLUS_INFINITY) && (seen & MINUS_INFINITY)))
    return from_bits<T>(infinity | (Bits(1) << (P - 2)));  // the quiet NaN with no payload, the same bits everywhere
  if(seen & PLUS_INFINITY) return from_bits<T>(infinity);
  if(seen & MINUS_INFINITY) return from_bits<T>(sign | infinity);
  return rounded<T>(total, from_bits<T>(n && !(seen & OTHER) ? sign : 0));
}


// The fast path adds eight terms at once, one into each of eight expansions held in four 16-byte vectors: GCC and
// Clang vector extensions, which both compile to the SIMD registers of x86-64 and AArch64 at any -march. GCC left a
// loop over plain arrays scalar, and kept a single 64-byte vector in memory.
using Pair = double __attribute__((vector_size(16)));
using Bits2 = std::uint64_t __attribute__((vector_size(16)));
inline constexpr int VECTORS = 4, CHAINS = 2 * VECTORS;
inline constexpr std::size_t CHUNK = 256;  // terms evaluated into a buffer before they are added, a multiple of CHAINS
inline constexpr std::uint64_t NEGATIVE_ZERO = 0x8000000000000000ull;

// One block's sum: CHAINS expansions (e0, e1), the accumulator and the flags. Every e0 starts at -0.0, the identity
// of IEEE addition, and stays -0.0 exactly while every term added to it is -0.0: that is how a block learns whether it
// saw a term other than -0.0 without looking at each one.
template<class T> struct Block {
  Pair e0[VECTORS], e1[VECTORS];
  Fixed<T> fixed;
  unsigned seen = 0;
  Block() noexcept { restart(); }
  void restart() noexcept {
    for(int v = 0; v < VECTORS; ++v) e0[v] = e1[v] = -Pair{};
  }

  // Add x[0..n), n a multiple of CHAINS. TwoSum twice for each term: e0 + x = s + r and e1 + r = t + r2 exactly,
  // unless something overflowed, and r2 is kept to go to the accumulator. An infinity, a NaN or an overflow anywhere
  // leaves an infinity or a NaN in e0, e1 or r2, and nothing else does, so a chunk whose results are all finite was
  // added exactly. Otherwise the chunk is added again from the expansions it started with, term by term.
  void chunk(const double* x, std::size_t n) noexcept {
    Pair h[VECTORS], l[VECTORS];
    Bits2 left[VECTORS];
    for(int v = 0; v < VECTORS; ++v) {
      h[v] = e0[v];
      l[v] = e1[v];
      left[v] = Bits2{};
    }
    alignas(16) double r2[CHUNK];
    for(std::size_t k = 0; k < n; k += CHAINS)
      for(int v = 0; v < VECTORS; ++v) {
        Pair b;
        __builtin_memcpy(&b, x + k + 2 * v, sizeof b);
        const Pair s = h[v] + b, bb = s - h[v], r = (h[v] - (s - bb)) + (b - bb);
        const Pair t = l[v] + r, tt = t - l[v], e = (l[v] - (t - tt)) + (r - tt);
        h[v] = s;
        l[v] = t;
        __builtin_memcpy(r2 + k + 2 * v, &e, sizeof e);
        Bits2 w;
        __builtin_memcpy(&w, &e, sizeof w);
        left[v] |= w;  // -0.0 counts as a zero: its sign bit alone is set
      }
    bool fine = true, kept = false;
    for(int v = 0; v < VECTORS; ++v) {
      const Pair z = (h[v] - h[v]) + (l[v] - l[v]);  // zero exactly where both are finite
      Bits2 odd;
      __builtin_memcpy(&odd, &z, sizeof odd);
      for(int j = 0; j < 2; ++j) {
        fine &= (odd[j] & ~NEGATIVE_ZERO) == 0;
        kept |= (left[v][j] & ~NEGATIVE_ZERO) != 0;
      }
    }
    if(fine && kept)
      for(std::size_t k = 0; k < n; ++k) fine &= r2[k] - r2[k] == 0;
    if(!fine) {
      settle();
      return slow(x, n);
    }
    for(int v = 0; v < VECTORS; ++v) {
      e0[v] = h[v];
      e1[v] = l[v];
    }
    if(kept)
      for(std::size_t k = 0; k < n; ++k)
        if(r2[k] != 0) fixed.add(r2[k]);
  }
  // Each finite term straight to the accumulator; the others as flags.
  void slow(const double* x, std::size_t n) noexcept {
    for(std::size_t k = 0; k < n; ++k) {
      const double v = x[k];
      if(bits(v) != NEGATIVE_ZERO) seen |= OTHER;
      if(v != v) seen |= NAN_SEEN;
      else if(v - v != 0) seen |= v > 0 ? PLUS_INFINITY : MINUS_INFINITY;
      else fixed.add(v);
    }
  }
  // Move every expansion into the accumulator, exactly, and start them again at -0.0.
  void settle() noexcept {
    for(int v = 0; v < VECTORS; ++v)
      for(int j = 0; j < 2; ++j) {
        if(bits(e0[v][j]) != NEGATIVE_ZERO) seen |= OTHER;
        fixed.add(e0[v][j]);
        fixed.add(e1[v][j]);
      }
    restart();
  }
  // Add the terms value(lo..hi), CHUNK at a time, each evaluated once. A last chunk that is not a multiple of CHAINS
  // is padded with -0.0, which adds nothing and leaves an e0 of -0.0 as it was.
  template<class F> void add(std::size_t lo, std::size_t hi, F& value) noexcept {
    alignas(16) double x[CHUNK];
    while(lo < hi) {
      const std::size_t n = hi - lo < CHUNK ? hi - lo : CHUNK;
      for(std::size_t k = 0; k < n; ++k) x[k] = static_cast<double>(value(lo + k));
      std::size_t padded = n;
      for(; padded % CHAINS; ++padded) x[padded] = -0.0;
      chunk(x, padded);
      lo += n;
    }
    settle();
  }
};
}  // namespace cr::sum

namespace cr::par {
// reduce + parallel i in n yield value(i), over f32 or f64: the exact sum of the n terms rounded once. The blocks are
// reduce's, fixed by the count, though nothing here depends on them: each block adds its accumulator into the total
// as it finishes, in whatever order the lanes finish, and the total is the same integer whatever that order was.
// Each term is evaluated once. The total and the flags live in this frame.
template<class T, class F> T sum(std::size_t n, F value) noexcept {
  static_assert(std::is_same_v<T, float> || std::is_same_v<T, double>);
  const std::size_t blocks = n < lanes::CUTOFF ? 1 : std::min(BLOCKS, n / lanes::GRAIN);
  if(blocks == 1) {
    cr::sum::Block<T> one;
    one.add(0, n, value);
    return cr::sum::result<T>(one.fixed, one.seen, n);
  }
  cr::sum::Fixed<T> total;
  unsigned seen = 0;
  auto fold = [&](std::size_t b) noexcept {
    const std::size_t lo = n / blocks * b + std::min(b, n % blocks);
    const std::size_t hi = lo + n / blocks + (b < n % blocks);
    cr::sum::Block<T> mine;
    mine.add(lo, hi, value);
    mine.fixed.into(total.limb);
    if(mine.seen) std::atomic_ref<unsigned>(seen).fetch_or(mine.seen, std::memory_order_relaxed);
  };
  run(blocks, fold, n / blocks);
  return cr::sum::result<T>(total, seen, n);
}
}  // namespace cr::par

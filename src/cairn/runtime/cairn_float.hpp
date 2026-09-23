// Storage floats: formats that hold a value and convert, and never compute. One routine rounds on the host
// and inside a device lane alike, integer arithmetic on the bit pattern, so the host's exhaustive tests
// cover the lane too. docs/numerics.md#storage-floats states the rules this header implements.
#pragma once
#include "cairn_runtime.hpp"

namespace cr {
namespace fp {
// E exponent bits and M fraction bits. With INF the top exponent holds infinity and NaN, as in IEEE 754;
// without it (OCP f8e4m3) the top exponent holds finite values and only an all-ones fraction is NaN.
template<int E, int M, bool INF> struct Float {
  using Bits = std::conditional_t<(1 + E + M > 8), std::uint16_t, std::uint8_t>;
  static constexpr int bias = (1 << (E - 1)) - 1;
  static constexpr unsigned top = (1u << E) - 1, ones = (1u << M) - 1;
  static constexpr Bits sign = Bits(1u << (E + M));
  static constexpr Bits nan = Bits(INF ? (top << M) | (1u << (M - 1)) : (top << M) | ones);
  static constexpr Bits largest = Bits(INF ? ((top - 1) << M) | ones : (top << M) | (ones - 1));
  static constexpr Bits infinity = Bits(top << M);  // meaningful only with INF
  static constexpr bool infinite = INF;
  static constexpr int exponent = E, fraction = M;
  Bits bits;
  CR_HD explicit operator double() const noexcept;  // exact: every value of these formats is a double
  CR_HD explicit operator float() const noexcept { return static_cast<float>(static_cast<double>(*this)); }
};

CR_HD inline std::uint64_t pattern(double x) noexcept { return cr::to_bits<std::uint64_t>(x); }
CR_HD inline double value(std::uint64_t u) noexcept {
#if defined(__CUDA_ARCH__)
  return __longlong_as_double(static_cast<long long>(u));
#else
  return __builtin_bit_cast(double, u);
#endif
}

template<int E, int M, bool INF> CR_HD inline Float<E, M, INF>::operator double() const noexcept {
  const std::uint64_t s = std::uint64_t(bits >> (E + M)) << 63;
  const unsigned e = (bits >> M) & top;
  const std::uint64_t f = bits & ones;
  if (e == top && (INF || f == ones))  // infinity, or NaN with the fraction kept as its payload's top bits
    return value(s | 0x7ff0000000000000ULL | (f ? 0x0008000000000000ULL | (f << (52 - M)) : 0));
  if (e == 0) {  // zero, or a subnormal f * 2^(1 - bias - M), which is a normal double
    if (f == 0) return value(s);
    int p = M - 1;
    while (!(f >> p)) --p;
    return value(s | (std::uint64_t(p + 1 - bias - M + 1023) << 52) | ((f ^ (std::uint64_t(1) << p)) << (52 - p)));
  }
  return value(s | (std::uint64_t(int(e) - bias + 1023) << 52) | (f << (52 - M)));
}

// |x| rounded to nearest, ties to even, with the exponent unbounded above, as a pattern of T without its sign.
// `over` says the result lies beyond T's largest finite value, which is where each conversion differs. Given
// `noise`, it rounds away from zero instead when the dropped fraction, to 32 bits, exceeds the noise: stochastic
// rounding, which moves up with the probability the fraction is and is exact where |x| is representable.
template<class T> CR_HD inline typename T::Bits nearest(double x, bool& over, const std::uint32_t* noise = nullptr) noexcept {
  constexpr int M = T::fraction, emin = 1 - T::bias;
  const std::uint64_t u = pattern(x);
  const int stored = int((u >> 52) & 0x7ff);
  std::uint64_t m = u & ((std::uint64_t(1) << 52) - 1);
  over = stored == 0x7ff;  // infinity; NaN never reaches here
  if (over) return 0;
  const int q = stored ? stored - 1075 : -1074;  // |x| = m * 2^q
  if (stored) m |= std::uint64_t(1) << 52;
  if (m == 0) return 0;
  int lead = 52;
  while (!(m >> lead)) --lead;
  const int binade = lead + q < emin ? emin : lead + q;  // the exponent of the result's spacing, 2^(binade - M)
  const int shift = q - (binade - M);                     // n = m * 2^shift units of that spacing
  std::uint64_t n;
  if (shift >= 0) {
    n = m << shift;  // exact: |x| has no bits below the spacing
  } else {
    const int r = -shift;
    const std::uint64_t rest = r >= 64 ? m : m & ((std::uint64_t(1) << r) - 1);
    n = r >= 64 ? 0 : m >> r;
    if (noise) {
      const std::uint64_t top = r <= 32 ? rest << (32 - r) : r - 32 >= 64 ? 0 : rest >> (r - 32);
      n += top > *noise;
    } else if (r <= 60) {  // with more dropped, the rest is below half a unit, since m < 2^53
      const std::uint64_t half = std::uint64_t(1) << (r - 1);
      n += rest > half || (rest == half && (n & 1));
    }
  }
  int field = binade + T::bias;
  if (n >> (M + 1)) {  // rounding carried into the next binade
    n >>= 1;
    ++field;
  }
  if (n < (std::uint64_t(1) << M)) return typename T::Bits(n);  // subnormal: the exponent field is 0
  const std::uint64_t frac = n - (std::uint64_t(1) << M);
  over = field > int(T::top) || (field == int(T::top) && (T::infinite || frac == T::ones));
  return over ? 0 : typename T::Bits((unsigned(field) << M) | frac);
}

template<class T> CR_HD inline typename T::Bits signed_of(double x) noexcept {
  return (pattern(x) >> 63) ? T::sign : typename T::Bits(0);
}

// f16(x): IEEE 754's conversion, which overflows to infinity. A format without infinity holds no answer
// there, so f8e4m3(x) traps when x or its rounding is beyond 448. A NaN becomes the format's quiet NaN.
template<class T> CR_HD inline T narrow(double x) noexcept {
  const auto s = signed_of<T>(x);
  if (x != x) return T{typename T::Bits(s | T::nan)};
  bool over = false;
  const auto b = nearest<T>(x, over);
  if (over) {
    if constexpr (!T::infinite) trap();
    return T{typename T::Bits(s | T::infinity)};
  }
  return T{typename T::Bits(s | b)};
}

// quantize[T](x, scale): x / scale rounded once to nearest, ties to even, and clamped to T's finite range.
// The quotient of two floats is exact in double to well within half a unit of every format here, so rounding
// it again is the correctly rounded result. A scale that is not positive and finite traps, and so does a NaN
// quantized to an integer, which has no NaN.
template<class T> CR_HD inline T quantized(float x, float scale, const std::uint32_t* noise) noexcept {
  if (!(scale > 0.0f) || !(scale <= 3.4028234663852886e38f)) trap();
  const double q = static_cast<double>(x) / static_cast<double>(scale);
  if constexpr (std::is_integral_v<T>) {
    if (q != q) trap();
    constexpr double lo = double(std::numeric_limits<T>::min()), hi = double(std::numeric_limits<T>::max());
    if (q <= lo) return std::numeric_limits<T>::min();
    if (q >= hi) return std::numeric_limits<T>::max();
    const double magnitude = q < 0 ? -q : q;
    auto whole = static_cast<std::int64_t>(magnitude);  // |q| < 2^16 here, so the rest is exact
    const double rest = magnitude - static_cast<double>(whole);
    if (noise) whole += static_cast<std::uint64_t>(rest * 4294967296.0) > *noise;
    else whole += rest > 0.5 || (rest == 0.5 && (whole & 1));
    return static_cast<T>(q < 0 ? -whole : whole);
  } else {
    const auto s = signed_of<T>(q);
    if (q != q) return T{typename T::Bits(s | T::nan)};
    bool over = false;
    const auto b = nearest<T>(q, over, noise);
    return T{typename T::Bits(s | (over ? T::largest : b))};
  }
}

template<class T> CR_HD inline T quantize(float x, float scale) noexcept { return quantized<T>(x, scale, nullptr); }

// quantize_stochastic[T](x, scale, noise): the same, rounding away from zero with the probability the fraction
// of the double quotient beyond T's precision is, to 32 bits, when noise is uniform: unbiased on average, and the
// same bits for the same noise on every machine.
template<class T> CR_HD inline T quantize_stochastic(float x, float scale, std::uint32_t noise) noexcept {
  return quantized<T>(x, scale, &noise);
}

template<class T, class U> CR_HD inline T from_bits(U u) noexcept {
  static_assert(sizeof(T) == sizeof(U));
  if constexpr (std::is_same_v<T, double>) return value(u);
#if defined(__CUDA_ARCH__)
  else if constexpr (std::is_same_v<T, float>) return __uint_as_float(u);
#else
  else if constexpr (std::is_same_v<T, float>) return __builtin_bit_cast(float, u);
#endif
  else return T{u};
}
} // namespace fp

using f16 = fp::Float<5, 10, true>;
using bf16 = fp::Float<8, 7, true>;
using f8e4m3 = fp::Float<4, 3, false>;
using f8e5m2 = fp::Float<5, 2, true>;
// Each is its pattern and nothing else, laid out as CUDA's __half, __nv_bfloat16 and __nv_fp8 types are; asking
// the size also completes the type before a C-linkage function returns one.
static_assert(sizeof(f16) == 2 && sizeof(bf16) == 2 && sizeof(f8e4m3) == 1 && sizeof(f8e5m2) == 1);
static_assert(std::is_trivially_copyable_v<f16> && std::is_standard_layout_v<f8e4m3>);

// The pattern of a storage float, as the unsigned integer of its width (the float and double forms are in
// cairn_runtime.hpp); the more specialized template wins overload resolution.
template<class U, int E, int M, bool INF> CR_HD inline U to_bits(fp::Float<E, M, INF> x) noexcept {
  static_assert(sizeof(U) == sizeof(x));
  return x.bits;
}
} // namespace cr

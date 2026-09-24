// The device lane's checked arithmetic, run on the host: cairn_runtime.hpp's __CUDA_ARCH__ branches of cr::ovf,
// compiled here with the CUDA intrinsics they call defined as their documented results, and held to the host
// compiler's __builtin_*_overflow on every pair of boundary values of every integer type. Exit 0 is a pass. The
// same comparison runs on a device in tests/runtime/gpu_arithmetic.cu, only under `make gpu`.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <math.h>  // the C names a lane's sqrt, floor, ceil, trunc and fabs call
#include <utility>
#include <vector>

// What the device pass has and a host pass does not. __umul64hi and __mul64hi are the high 64 bits of the 128-bit
// product; the rest are never reached by what is tested here.
#define __CUDA_ARCH__ 1200
[[maybe_unused]] static unsigned long long __umul64hi(unsigned long long a, unsigned long long b) {
  return static_cast<unsigned long long>((static_cast<unsigned __int128>(a) * b) >> 64);
}
[[maybe_unused]] static long long __mul64hi(long long a, long long b) {
  return static_cast<long long>((static_cast<__int128>(a) * b) >> 64);
}
[[noreturn]] static void __trap() { std::abort(); }
[[maybe_unused]] static unsigned __float_as_uint(float x) {
  unsigned u;
  std::memcpy(&u, &x, sizeof u);
  return u;
}
[[maybe_unused]] static long long __double_as_longlong(double x) {
  long long u;
  std::memcpy(&u, &x, sizeof u);
  return u;
}
#include "cairn_runtime.hpp"
#undef __CUDA_ARCH__

static long failures = 0, checked = 0;

// Every value within 3 of 0, 1, the type's extremes, their halves and the square root of its range, both signs.
template<class T> static std::vector<T> boundary() {
  using L = std::numeric_limits<T>;
  std::vector<T> out;
  const long double pivots[] = {0.0L, 1.0L, (long double)L::max(), (long double)L::min(), (long double)L::max() / 2,
                                (long double)L::min() / 2, (long double)(1ULL << (L::digits / 2)),
                                -(long double)(1ULL << (L::digits / 2)), 3.0L, -3.0L};
  for(long double p : pivots)
    for(int d = -3; d <= 3; ++d) {
      const long double v = p + d;
      if(v < (long double)L::min() || v > (long double)L::max()) continue;
      out.push_back(static_cast<T>(v));
    }
  return out;
}

// Random operands too: whole-width patterns, and magnitudes near the square root of the range, where products cross it.
template<class T> static std::vector<std::pair<T, T>> pairs() {
  const std::vector<T> values = boundary<T>();
  std::vector<std::pair<T, T>> out;
  for(T a : values)
    for(T b : values) out.emplace_back(a, b);
  std::uint64_t state = 0x9e3779b97f4a7c15ull;
  auto next = [&state] {
    std::uint64_t z = (state += 0x9e3779b97f4a7c15ull);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
  };
  const int half = int(sizeof(T) * 4) + 1;
  for(int k = 0; k < 20000; ++k) {
    const std::uint64_t x = next(), y = next();
    out.emplace_back(static_cast<T>(x), static_cast<T>(y));
    const std::uint64_t mask = (std::uint64_t(1) << half) - 1;
    const T a = static_cast<T>(x & mask), b = static_cast<T>(y & mask);
    out.emplace_back(a, (k & 1) && std::numeric_limits<T>::is_signed ? static_cast<T>(-b) : b);
  }
  return out;
}

template<class T> static void compare(const char* type) {
  for(const auto& [a, b] : pairs<T>()) {
    T want = 0, got = 0;
    const bool over = __builtin_mul_overflow(a, b, &want), seen = cr::ovf::mul(a, b, &got);
    ++checked;
    if(over != seen || (!over && got != want)) {
      ++failures;
      std::fprintf(stderr, "%s mul %lld %lld: builtin %d, lane %d\n", type, (long long)a, (long long)b, over, seen);
    }
    const bool add = __builtin_add_overflow(a, b, &want), add_seen = cr::ovf::add(a, b, &got);
    ++checked;
    if(add != add_seen || (!add && got != want)) ++failures;
    const bool sub = __builtin_sub_overflow(a, b, &want), sub_seen = cr::ovf::sub(a, b, &got);
    ++checked;
    if(sub != sub_seen || (!sub && got != want)) ++failures;
  }
}

int main() {
  compare<std::uint8_t>("u8");
  compare<std::int8_t>("i8");
  compare<std::uint16_t>("u16");
  compare<std::int16_t>("i16");
  compare<std::uint32_t>("u32");
  compare<std::int32_t>("i32");
  compare<std::uint64_t>("u64");
  compare<std::int64_t>("i64");
  compare<std::size_t>("usize");
  std::printf("device arithmetic: %s after %ld checks\n", failures ? "FAILED" : "ok", checked);
  return failures ? 1 : 0;
}

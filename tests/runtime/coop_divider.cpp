// A cooperative region's block split by a divider made once per launch (cairn_coop.hpp, cr::coop::Divider), held on
// the host to the host compiler's / and %: every pair of boundary dividends and divisors, pseudo-random pairs of every
// width, and every dividend and divisor below 3000. Built as is, it runs the branch a host region and an emulated
// device region run; built with DEVICE defined, the __CUDA_ARCH__ branch a device runs, with the intrinsic it calls
// defined as its documented result. The argument is the number of random pairs. Exit 0 is a pass.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <math.h>  // the C names a lane's sqrt, floor, ceil, trunc and fabs call

#if defined(DEVICE)
// What the device pass has and a host pass does not. __umul64hi is the high 64 bits of the 128-bit product, which is
// all the divider calls; the rest are what the headers' device branches name, never reached here.
#define __CUDA_ARCH__ 900
[[maybe_unused]] static unsigned long long __umul64hi(unsigned long long a, unsigned long long b) {
  return static_cast<unsigned long long>((static_cast<unsigned __int128>(a) * b) >> 64);
}
[[maybe_unused]] static long long __mul64hi(long long a, long long b) {
  return static_cast<long long>((static_cast<__int128>(a) * b) >> 64);
}
[[maybe_unused, noreturn]] static void __trap() { std::abort(); }
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
[[maybe_unused, noreturn]] static unsigned long long atomicCAS(unsigned long long*, unsigned long long, unsigned long long) {
  std::abort();
}
[[maybe_unused, noreturn]] static unsigned long long atomicAdd(unsigned long long*, unsigned long long) { std::abort(); }
[[maybe_unused, noreturn]] static unsigned long long atomicExch(unsigned long long*, unsigned long long) { std::abort(); }
#endif
#include "cairn_coop.hpp"
#if defined(DEVICE)
#undef __CUDA_ARCH__
#endif

static long long checked = 0, failures = 0;

static void check(std::uint64_t n, std::uint64_t d) {
  if(d == 0) return;
  const cr::coop::Divider by(d);
  const std::uint64_t q = by.div(n);
  ++checked;
  if(q != n / d || n - q * d != n % d) {
    if(++failures <= 10)
      std::printf("n=%llu d=%llu: %llu, not %llu\n", (unsigned long long)n, (unsigned long long)d,
                  (unsigned long long)q, (unsigned long long)(n / d));
  }
}

static std::uint64_t next(std::uint64_t& state) {  // splitmix64
  std::uint64_t z = (state += 0x9e3779b97f4a7c15ull);
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
  return z ^ (z >> 31);
}

int main(int argc, char** argv) {
  const long long random = argc > 1 ? std::atoll(argv[1]) : 2000000;
  std::uint64_t edges[200];
  int e = 0;
  for(int k = 0; k < 64; ++k) {  // every power of two and its neighbours, where the shifts and the multiplier change
    const std::uint64_t p = std::uint64_t(1) << k;
    edges[e++] = p, edges[e++] = p - 1, edges[e++] = p + 1;
  }
  edges[e++] = ~std::uint64_t(0), edges[e++] = ~std::uint64_t(0) - 1, edges[e++] = 3, edges[e++] = 7;
  for(int a = 0; a < e; ++a)
    for(int b = 0; b < e; ++b) check(edges[a], edges[b]);
  std::uint64_t state = 20260928;
  for(long long k = 0; k < random; ++k) {  // each operand of a random width, so small and large divisors both occur
    const std::uint64_t d = next(state) >> (next(state) % 64), n = next(state) >> (next(state) % 64);
    check(n, d);
  }
  for(std::uint64_t d = 1; d < 3000; ++d)
    for(std::uint64_t n = 0; n < 3000; ++n) check(n, d);
  std::printf("%s: %lld cases, %lld mismatches\n", failures ? "divider differs" : "divider ok", checked, failures);
  return failures != 0;
}

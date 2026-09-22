// CAIRN Native runtime: guarded values/views and explicit scoped buffers; no scheduler.
#pragma once
#include <algorithm>
#include <array>
#include <new>
#include <cstddef>
#include <cstdint>
#if defined(CAIRN_FREESTANDING)
// A freestanding image links no C library and no C++ runtime, so the target's start-up code owns the
// only way out: cr_exit(status) stops the machine with that status (src/cairn/targets/<name>/start.S).
extern "C" [[noreturn]] void cr_exit(int status) noexcept;
#else
#include <cstdlib>
#endif
#include <limits>
#include <type_traits>
#include <utility>
// CR_HD marks a guard that must also hold inside a device lane; CR_DEVICE marks a lambda the
// emitter hands to cr::gpu::launch. Both are empty without nvcc, so host code is unchanged.
#if defined(__CUDACC__)
#define CR_HD __host__ __device__
#define CR_DEVICE __device__
#else
#define CR_HD
#define CR_DEVICE
#endif
namespace cr {
// A failed guard aborts the process. In a device lane __trap() ends the kernel and poisons the
// context, so the next cr::gpu wait reports the failure and aborts the host process. assert()
// (deleted by NDEBUG) and __builtin_trap() (silently ignored by nvcc) were measured to break
// that promise; see the mechanism note at the top of cairn_gpu.hpp.
[[noreturn]] CR_HD inline void trap() noexcept {
#if defined(__CUDA_ARCH__)
  __trap();
  __builtin_unreachable();
#elif defined(CAIRN_FREESTANDING)
  cr_exit(134);  // The abort status a hosted shell reports for std::abort; no fn main() -> i32 ends this way.
#else
  std::abort();
#endif
}
// nvcc's device pass does not implement __builtin_*_overflow: it reports no overflow and writes
// no result, so a device lane checks two's complement by hand. The host path is untouched.
namespace ovf {
template<class T> CR_HD inline bool add(T a,T b,T* r) noexcept {
#if defined(__CUDA_ARCH__)
  *r=static_cast<T>(std::uint64_t(a)+std::uint64_t(b));
  if constexpr(std::is_signed_v<T>) return ((a^*r)&(b^*r))<0; else return *r<a;
#else
  return __builtin_add_overflow(a,b,r);
#endif
}
template<class T> CR_HD inline bool sub(T a,T b,T* r) noexcept {
#if defined(__CUDA_ARCH__)
  *r=static_cast<T>(std::uint64_t(a)-std::uint64_t(b));
  if constexpr(std::is_signed_v<T>) return ((a^b)&(a^*r))<0; else return b>a;
#else
  return __builtin_sub_overflow(a,b,r);
#endif
}
template<class T> CR_HD inline bool mul(T a,T b,T* r) noexcept {
#if defined(__CUDA_ARCH__)
  *r=static_cast<T>(std::uint64_t(a)*std::uint64_t(b));
  if(a==T(0)) return false;
  if constexpr(std::is_signed_v<T>) if(a==T(-1)) return b==std::numeric_limits<T>::min();
  return *r/a!=b;
#else
  return __builtin_mul_overflow(a,b,r);
#endif
}
} // namespace ovf
template<class T> CR_HD inline T add(T a,T b) noexcept { T r; if(ovf::add(a,b,&r)) trap(); return r; }
template<class T> CR_HD inline T sub(T a,T b) noexcept { T r; if(ovf::sub(a,b,&r)) trap(); return r; }
template<class T> CR_HD inline T mul(T a,T b) noexcept { T r; if(ovf::mul(a,b,&r)) trap(); return r; }
template<class T> CR_HD inline T divide(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a/b;
}
template<class T> CR_HD inline T remainder(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a%b;
}
// IEEE 754 makes sqrt correctly rounded and floor, ceil and trunc exact, so they agree on every compiler,
// on the host and in a device lane; the libm functions whose last bit varies (exp, log, sin) are not here.
namespace math {
#if defined(__CUDA_ARCH__)
#define CR_MATH(name, f, d) template<class T> CR_HD inline T name(T x) noexcept { \
  if constexpr(std::is_same_v<T,float>) return ::f(x); else return ::d(x); }
#else
#define CR_MATH(name, f, d) template<class T> CR_HD inline T name(T x) noexcept { \
  if constexpr(std::is_same_v<T,float>) return __builtin_##f(x); else return __builtin_##d(x); }
#endif
CR_MATH(sqrt, sqrtf, sqrt)
CR_MATH(floor, floorf, floor)
CR_MATH(ceil, ceilf, ceil)
CR_MATH(trunc, truncf, trunc)
CR_MATH(fabs, fabsf, fabs)
#undef CR_MATH
} // namespace math
// The magnitude: exact for a float, and a trap for the signed minimum, whose magnitude its type cannot hold.
template<class T> CR_HD inline T abs(T x) noexcept {
  if constexpr(std::is_floating_point_v<T>) return math::fabs(x);
  else { if(x==std::numeric_limits<T>::min()) trap(); return x<0 ? static_cast<T>(-x) : x; }
}
// The IEEE bit pattern of a float, as the unsigned integer of its width.
template<class U, class F> CR_HD inline U to_bits(F x) noexcept {
  static_assert(sizeof(U)==sizeof(F));
#if defined(__CUDA_ARCH__)
  if constexpr(std::is_same_v<F,float>) return __float_as_uint(x); else return static_cast<U>(__double_as_longlong(x));
#else
  return __builtin_bit_cast(U, x);
#endif
}
// An unsigned sum that remembers whether it ever overflowed: associative and commutative, so a device
// reduction may combine it in any order and the host still traps exactly when the true total does not fit.
template<class T> struct Sum {
  T v; bool over;
  CR_HD Sum(T x = 0, bool o = false) noexcept : v(x), over(o) {}
  CR_HD friend Sum operator+(Sum a, Sum b) noexcept {
    const T s = static_cast<T>(a.v + b.v);
    return Sum(s, a.over || b.over || s < a.v);
  }
  T checked() const noexcept { if(over) trap(); return v; }
};
template<class T> CR_HD inline T add_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)+std::uint64_t(b)); }
template<class T> CR_HD inline T sub_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)-std::uint64_t(b)); }
template<class T> CR_HD inline T mul_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)*std::uint64_t(b)); }
template<class T> CR_HD inline T shl_wrap(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)<<n);
}
template<class T> CR_HD inline T shr(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)>>n);
}
template<class T,class S> CR_HD inline T convert(S x) noexcept {
  if(!std::in_range<T>(x)) trap();
  return static_cast<T>(x);
}
// Float to integer truncates toward zero and traps on NaN or a value outside T. 2^digits is exact
// in every float format, and where low-1 is not (64-bit), comparing with low itself is.
template<class T,class F> CR_HD inline T truncate(F x) noexcept {
  constexpr F limit = static_cast<F>(std::uint64_t(1) << (std::numeric_limits<T>::digits - 1)) * F(2);
  constexpr F low = std::is_signed_v<T> ? -limit : F(0);
  if(!((x > low - F(1) || x >= low) && x < limit)) trap();
  return static_cast<T>(x);
}
template<class T> CR_HD inline T& at(T* p,std::size_t i,std::size_t n) noexcept {
  if(i>=n) trap();
  return p[i];
}
template<class T> CR_HD inline void view(T* p,std::size_t n) noexcept {
  if(n==0) return;
  if(p==nullptr || reinterpret_cast<std::uintptr_t>(p)%alignof(T)) trap();
  if(n>std::numeric_limits<std::size_t>::max()/sizeof(T)) trap();
  const auto a=reinterpret_cast<std::uintptr_t>(p);
  if(a>std::numeric_limits<std::uintptr_t>::max()-n*sizeof(T)) trap();
}
template<class A,class B> CR_HD inline void disjoint(A* a,std::size_t na,B* b,std::size_t nb) noexcept {
  if(na==0 || nb==0) return;
  const auto x=reinterpret_cast<std::uintptr_t>(a), y=reinterpret_cast<std::uintptr_t>(b);
  if(x<y+nb*sizeof(B) && y<x+na*sizeof(A)) trap();
}
// A lexical owner cannot be copied, moved or returned by the source profile.
// Zero initialization and scope release are part of the buffer constructor contract.
// OOM and object-size violations trap; process abort does not promise cleanup.
template<class T> class Buffer final {
  T* p_ = nullptr;
public:
  explicit Buffer(std::size_t n) noexcept {
    static_assert(std::is_arithmetic_v<T>);
    if(n > static_cast<std::size_t>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T)) trap();
    if(n) { p_ = new(std::nothrow) T[n](); if(!p_) trap(); }
  }
  ~Buffer() noexcept { delete[] p_; }
  Buffer(const Buffer&) = delete;
  Buffer& operator=(const Buffer&) = delete;
  Buffer(Buffer&&) = delete;
  Buffer& operator=(Buffer&&) = delete;
  T* data() const noexcept { return p_; }
};
} // namespace cr

// Wide loads and stores (compiler/primitives/wide.py), K adjacent elements of an array moved by one access, and atomic updates of
// one element (compiler/primitives/atomics.py), on the host and in a device lane or cooperative thread alike.
//
// K * sizeof(T) is a power of two up to 16 bytes, the widest a thread moves at once (LDG.E.128, STG.E.128, LDS.128).
// An access has two guards, and either failing traps: the K elements lie inside the array's n, and the first sits on
// the access's width. PTX leaves a misaligned vector access undefined, so the device needs the second; the host needs
// neither, and checks both, so a host or emulated run traps where a device run would.
//
// On the device an access to device memory is one instruction with the cache operator its hint names: all levels
// (.ca, .wb), L2 only (.cg), streaming (.cs), last use (.lu) or the read-only path (.nc, only for an ro view, which
// nothing writes while it is lent). An access to a block's shared memory is one instruction with no hint. On the host
// it is K ordinary loads or stores, one memcpy, and the hint says nothing.
#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include "cairn_runtime.hpp"

namespace cr::wide {
enum class Cache : unsigned { all, l2, streaming, last_use, read_only };

// The first of K elements of p[0 .. n), after the two guards.
template<std::size_t K, class T> CR_HD inline T* first(T* p, std::size_t i, std::size_t n) noexcept {
  static_assert(K > 0 && (K * sizeof(T) & (K * sizeof(T) - 1)) == 0 && K * sizeof(T) <= 16, "one access of 16 bytes");
  if(i > n || n - i < K) trap();
  if(reinterpret_cast<std::uintptr_t>(p + i) % (K * sizeof(T))) trap();
  return p + i;
}

#if defined(__CUDA_ARCH__)
// The unsigned vector one access of B bytes moves.
template<std::size_t B> struct Bits;
template<> struct Bits<1> { using type = unsigned char; };
template<> struct Bits<2> { using type = unsigned short; };
template<> struct Bits<4> { using type = unsigned int; };
template<> struct Bits<8> { using type = uint2; };
template<> struct Bits<16> { using type = uint4; };

template<Cache C, class B> __device__ inline B fetch(const B* p) {
  if constexpr(C == Cache::l2) return __ldcg(p);
  else if constexpr(C == Cache::streaming) return __ldcs(p);
  else if constexpr(C == Cache::last_use) return __ldlu(p);
  else if constexpr(C == Cache::read_only) return __ldg(p);
  else return *p;
}
template<Cache C, class B> __device__ inline void put(B* p, B v) {
  if constexpr(C == Cache::l2) __stcg(p, v);
  else if constexpr(C == Cache::streaming) __stcs(p, v);
  else *p = v;
}
#endif

// `load_wide[K](x, i, hint)`: x[i .. i + K) as one value. SHARED is an array of the block's shared memory.
template<std::size_t K, Cache C, bool SHARED, class T>
CR_HD inline std::array<T, K> load(const T* p, std::size_t i, std::size_t n) noexcept {
  const T* at = first<K>(p, i, n);
  std::array<T, K> v;
#if defined(__CUDA_ARCH__)
  using B = typename Bits<K * sizeof(T)>::type;
  const B bits = fetch<SHARED ? Cache::all : C>(reinterpret_cast<const B*>(at));
  __builtin_memcpy(&v, &bits, sizeof v);
#else
  __builtin_memcpy(&v, at, sizeof v);
#endif
  return v;
}

// `store_wide(x, i, v, hint)`: v into x[i .. i + K) at once.
template<Cache C, bool SHARED, class T, std::size_t K>
CR_HD inline void store(T* p, std::size_t i, std::size_t n, const std::array<T, K>& v) noexcept {
  T* at = first<K>(p, i, n);
#if defined(__CUDA_ARCH__)
  using B = typename Bits<K * sizeof(T)>::type;
  B bits;
  __builtin_memcpy(&bits, &v, sizeof bits);
  put<SHARED ? Cache::all : C>(reinterpret_cast<B*>(at), bits);
#else
  __builtin_memcpy(at, &v, sizeof v);
#endif
}
}  // namespace cr::wide

// Atomic updates of one element (compiler/primitives/atomics.py): the index's guard, then one indivisible read-modify-write that
// returns the element's old value. Each is relaxed, so it orders nothing but the element's own updates. On the device
// it is one atomic instruction (RED where the result goes unused); on the host a std::atomic_ref operation, so the
// thread sanitizer sees an atomic where there is one. An integer add wraps, min and max compare as the element's type
// does, and a float add rounds once, in the order the threads arrive.
namespace cr::atomic {
template<class T> CR_HD inline T& element(T* p, std::size_t i, std::size_t n) noexcept {
  if(i >= n) trap();
  return p[i];
}

#if defined(__CUDA_ARCH__)
// The pointer CUDA's atomics take for an element: its own type for int, unsigned, float and double, and the 64-bit
// integer CUDA names for a u64, a usize or an i64. `bits` is the unsigned word of the same width, for CAS and the
// bit operations, which compare and combine bits alike whatever the sign.
template<class T> __device__ inline auto word(T& x) noexcept {
  if constexpr(std::is_floating_point_v<T>) return &x;
  else if constexpr(sizeof(T) == 8 && std::is_signed_v<T>) return reinterpret_cast<long long*>(&x);
  else if constexpr(sizeof(T) == 8) return reinterpret_cast<unsigned long long*>(&x);
  else if constexpr(std::is_signed_v<T>) return reinterpret_cast<int*>(&x);
  else return reinterpret_cast<unsigned*>(&x);
}
template<class T> __device__ inline auto bits(T& x) noexcept {
  if constexpr(sizeof(T) == 8) return reinterpret_cast<unsigned long long*>(&x);
  else return reinterpret_cast<unsigned*>(&x);
}
template<class P, class T> __device__ inline auto as(P, T v) noexcept { return static_cast<std::remove_pointer_t<P>>(v); }
#else
constexpr std::memory_order RELAXED = std::memory_order_relaxed;
// min and max as a compare-and-swap loop: std::atomic_ref has no fetch_min before C++26.
template<class T, class Better> inline T exchange_if(T& x, T v, Better better) noexcept {
  std::atomic_ref<T> held(x);
  T old = held.load(RELAXED);
  while(better(v, old) && !held.compare_exchange_weak(old, v, RELAXED, RELAXED)) {}
  return old;
}
#endif

template<class T> CR_HD inline T add_wrap(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicAdd(bits(x), as(bits(x), v)));
#else
  return std::atomic_ref<T>(x).fetch_add(v, RELAXED);
#endif
}
template<class T> CR_HD inline T add_unordered(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return atomicAdd(&x, v);
#else
  return std::atomic_ref<T>(x).fetch_add(v, RELAXED);
#endif
}
template<class T> CR_HD inline T min(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicMin(word(x), as(word(x), v)));
#else
  return exchange_if(x, v, [](T a, T b) { return a < b; });
#endif
}
template<class T> CR_HD inline T max(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicMax(word(x), as(word(x), v)));
#else
  return exchange_if(x, v, [](T a, T b) { return a > b; });
#endif
}
template<class T> CR_HD inline T and_(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicAnd(bits(x), as(bits(x), v)));
#else
  return std::atomic_ref<T>(x).fetch_and(v, RELAXED);
#endif
}
template<class T> CR_HD inline T or_(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicOr(bits(x), as(bits(x), v)));
#else
  return std::atomic_ref<T>(x).fetch_or(v, RELAXED);
#endif
}
template<class T> CR_HD inline T xor_(T* p, std::size_t i, std::size_t n, T v) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicXor(bits(x), as(bits(x), v)));
#else
  return std::atomic_ref<T>(x).fetch_xor(v, RELAXED);
#endif
}
template<class T> CR_HD inline T cas(T* p, std::size_t i, std::size_t n, T expected, T desired) noexcept {
  T& x = element(p, i, n);
#if defined(__CUDA_ARCH__)
  return static_cast<T>(atomicCAS(bits(x), as(bits(x), expected), as(bits(x), desired)));
#else
  std::atomic_ref<T>(x).compare_exchange_strong(expected, desired, RELAXED, RELAXED);
  return expected;  // the old value: desired went in exactly where it equalled expected
#endif
}
}  // namespace cr::atomic

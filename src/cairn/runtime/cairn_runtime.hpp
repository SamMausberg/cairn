// CAIRN Native runtime: guarded values and views, no allocator or scheduler.
#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <type_traits>
#include <utility>
namespace cr {
[[noreturn]] inline void trap() noexcept { std::abort(); }
template<class T> inline T add(T a,T b) noexcept { T r; if(__builtin_add_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T sub(T a,T b) noexcept { T r; if(__builtin_sub_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T mul(T a,T b) noexcept { T r; if(__builtin_mul_overflow(a,b,&r)) trap(); return r; }
template<class T> inline T divide(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a/b;
}
template<class T> inline T remainder(T a,T b) noexcept {
  if(b==0) trap();
  if constexpr(std::is_signed_v<T>) if(a==std::numeric_limits<T>::min() && b==T(-1)) trap();
  return a%b;
}
template<class T> inline T add_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)+std::uint64_t(b)); }
template<class T> inline T sub_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)-std::uint64_t(b)); }
template<class T> inline T mul_wrap(T a,T b) noexcept { return static_cast<T>(std::uint64_t(a)*std::uint64_t(b)); }
template<class T> inline T shl_wrap(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)<<n);
}
template<class T> inline T shr(T a,std::size_t n) noexcept {
  if(n>=sizeof(T)*8) trap();
  return static_cast<T>(std::uint64_t(a)>>n);
}
template<class T,class S> inline T convert(S x) noexcept {
  if(!std::in_range<T>(x)) trap();
  return static_cast<T>(x);
}
template<class T> inline T& at(T* p,std::size_t i,std::size_t n) noexcept {
  if(i>=n) trap();
  return p[i];
}
template<class T> inline void view(T* p,std::size_t n) noexcept {
  if(n==0) return;
  if(p==nullptr || reinterpret_cast<std::uintptr_t>(p)%alignof(T)) trap();
  if(n>std::numeric_limits<std::size_t>::max()/sizeof(T)) trap();
  const auto a=reinterpret_cast<std::uintptr_t>(p);
  if(a>std::numeric_limits<std::uintptr_t>::max()-n*sizeof(T)) trap();
}
template<class A,class B> inline void disjoint(A* a,std::size_t na,B* b,std::size_t nb) noexcept {
  if(na==0 || nb==0) return;
  const auto x=reinterpret_cast<std::uintptr_t>(a), y=reinterpret_cast<std::uintptr_t>(b);
  if(x<y+nb*sizeof(B) && y<x+na*sizeof(A)) trap();
}
} // namespace cr

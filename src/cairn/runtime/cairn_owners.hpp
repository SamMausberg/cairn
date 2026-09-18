// CAIRN owners: a movable zeroed heap array, visible scope cleanup and checked array parts.
#pragma once
#include "cairn_runtime.hpp"
namespace cr {
// Affine in the source language: moved or released exactly once. A moved-from Buf is empty,
// so the implicit release at scope exit is a no-op and every type keeps an all-zero value.
template<class T> class Buf final {
  T* p_ = nullptr;
  std::size_t n_ = 0;
public:
  Buf() noexcept = default;
  explicit Buf(std::size_t n) noexcept : n_(n) {
    if(n > static_cast<std::size_t>(std::numeric_limits<std::ptrdiff_t>::max()) / sizeof(T)) trap();
    if(n) { p_ = new(std::nothrow) T[n](); if(!p_) trap(); }
  }
  ~Buf() noexcept { delete[] p_; }
  Buf(const Buf&) = delete;
  Buf& operator=(const Buf&) = delete;
  Buf(Buf&& o) noexcept : p_(std::exchange(o.p_, nullptr)), n_(std::exchange(o.n_, 0)) {}
  Buf& operator=(Buf&& o) noexcept {
    if(this != &o) { delete[] p_; p_ = std::exchange(o.p_, nullptr); n_ = std::exchange(o.n_, 0); }
    return *this;
  }
  T* data() const noexcept { return p_; }
  std::size_t size() const noexcept { return n_; }
};
// `defer call;` runs at every normal exit of its block. Aborts do not promise cleanup.
template<class F> struct Defer final {
  F run;
  ~Defer() noexcept { run(); }
};
template<class F> Defer(F) -> Defer<F>;
// x[lo..hi] passed where the callee expects `want` elements: one guard, then a plain pointer.
template<class T> inline T* part(T* p,std::size_t lo,std::size_t hi,std::size_t n,std::size_t want) noexcept {
  if(lo>hi || hi>n || hi-lo!=want) trap();
  return p+lo;
}
} // namespace cr

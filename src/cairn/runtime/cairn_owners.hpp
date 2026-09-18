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
// ro<fn(A) -> R>: a borrowed callable. It points at a closure or function that outlives the call
// it is passed to, which the source language guarantees by never letting it be stored or returned.
template<class S> using FnPtr = S*;  // fn(A) -> R: a plain code pointer, freely copied.
template<class S> inline S* callable(S* f) noexcept { if(!f) trap(); return f; }  // Zero storage holds no code.
template<class S> class Fn;
template<class R,class... A> class Fn<R(A...)> final {
  void* env_;
  R (*call_)(void*,A...);
public:
  template<class F> Fn(F&& f) noexcept {
    using D = std::decay_t<F>;
    if constexpr(std::is_pointer_v<D>) {  // A declared function: the code pointer is the environment.
      env_ = reinterpret_cast<void*>(static_cast<D>(f));
      call_ = [](void* e,A... a) -> R { return reinterpret_cast<D>(e)(std::forward<A>(a)...); };
    } else {
      env_ = const_cast<void*>(static_cast<const void*>(&f));
      call_ = [](void* e,A... a) -> R { return (*static_cast<std::remove_reference_t<F>*>(e))(std::forward<A>(a)...); };
    }
  }
  R operator()(A... a) const { return call_(env_,std::forward<A>(a)...); }
};
// Dyn[Trait]: an owned value of some implementing type. R is the generated two-word reference
// {object, table}; the third word is how to release the object. A moved-from or zeroed Dyn is
// empty, and lending an empty one is a guard failure, not a null call.
template<class R> class Dyn final {
  R ref_{};
  void (*drop_)(void*) noexcept = nullptr;
public:
  Dyn() noexcept = default;
  template<class T,class V> static Dyn make(T value,const V* table) noexcept {
    Dyn d;
    T* object = new(std::nothrow) T(std::move(value));
    if(!object) trap();
    d.ref_ = R{object, table};
    d.drop_ = [](void* p) noexcept { delete static_cast<T*>(p); };
    return d;
  }
  ~Dyn() noexcept { if(drop_) drop_(ref_.self); }
  Dyn(const Dyn&) = delete;
  Dyn& operator=(const Dyn&) = delete;
  Dyn(Dyn&& o) noexcept : ref_(std::exchange(o.ref_, R{})), drop_(std::exchange(o.drop_, nullptr)) {}
  Dyn& operator=(Dyn&& o) noexcept {
    if(this != &o) { if(drop_) drop_(ref_.self); ref_ = std::exchange(o.ref_, R{}); drop_ = std::exchange(o.drop_, nullptr); }
    return *this;
  }
  R view() const noexcept { if(!ref_.self) trap(); return ref_; }
};
// x[lo..hi] passed where the callee expects `want` elements: one guard, then a plain pointer.
template<class T> inline T* part(T* p,std::size_t lo,std::size_t hi,std::size_t n,std::size_t want) noexcept {
  if(lo>hi || hi>n || hi-lo!=want) trap();
  return p+lo;
}
} // namespace cr

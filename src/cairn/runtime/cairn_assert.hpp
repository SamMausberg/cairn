// CAIRN assert and assert_eq: guards the program writes. A false condition prints where it was written on standard
// error, assert_eq with the two values it compared, then aborts as every failed guard does. A device lane prints
// through the device's printf, which the host reads when the kernel ends; a freestanding image only stops.
#pragma once
#include "cairn_runtime.hpp"
#if !defined(CAIRN_FREESTANDING)
#include <cstdio>
#endif
#include <type_traits>
namespace cr {
CR_HD inline void check(bool holds, const char* where) noexcept {
  if (holds) return;
#if defined(__CUDA_ARCH__)
  printf("%s\n", where);
#elif !defined(CAIRN_FREESTANDING)
  std::fputs(where, stderr);
  std::fputc('\n', stderr);
#else
  (void)where;
#endif
  trap();
}

// One scalar as the message shows it: bools as words, floats to the digits that round-trip, integers exactly.
template <class T> CR_HD inline void shown(const char* label, T value) noexcept {
#if !defined(CAIRN_FREESTANDING)
#if defined(__CUDA_ARCH__)
#define CR_SHOW(...) printf(__VA_ARGS__)
#else
#define CR_SHOW(...) std::fprintf(stderr, __VA_ARGS__)
#endif
  if constexpr (std::is_same_v<T, bool>) {
    CR_SHOW("%s %s", label, value ? "true" : "false");
  } else if constexpr (std::is_same_v<T, float>) {
    CR_SHOW("%s %.9g", label, static_cast<double>(value));
  } else if constexpr (std::is_floating_point_v<T>) {
    CR_SHOW("%s %.17g", label, static_cast<double>(value));
  } else if constexpr (std::is_signed_v<T>) {
    CR_SHOW("%s %lld", label, static_cast<long long>(value));
  } else {
    CR_SHOW("%s %llu", label, static_cast<unsigned long long>(value));
  }
#undef CR_SHOW
#else
  (void)label, (void)value;
#endif
}

template <class T> CR_HD inline void check_eq(T left, T right, const char* where) noexcept {
  if (left == right) return;
#if defined(__CUDA_ARCH__)
  printf("%s:", where);
#elif !defined(CAIRN_FREESTANDING)
  std::fputs(where, stderr);
  std::fputc(':', stderr);
#endif
  shown(" left", left);
  shown(", right", right);
#if defined(__CUDA_ARCH__)
  printf("\n");
#elif !defined(CAIRN_FREESTANDING)
  std::fputc('\n', stderr);
#endif
  trap();
}
} // namespace cr

// CAIRN assert: a guard the program writes. A false condition prints where it was written on standard error,
// then aborts as every failed guard does. A device lane prints through the device's printf, which the host
// reads when the kernel ends; a freestanding image has no stream to print on and only stops.
#pragma once
#include "cairn_runtime.hpp"
#if !defined(CAIRN_FREESTANDING)
#include <cstdio>
#endif
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
} // namespace cr

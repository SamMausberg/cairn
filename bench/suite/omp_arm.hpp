// What every OpenMP arm shares: the worker count, and the three runtime entry points.
//
// <omp.h> is not included. The header that ships with a compiler is not always the one whose
// runtime is linked, and on this machine one of the two pairs does not compile under the project's
// own -Werror. Three extern "C" declarations are the whole interface an arm needs, they are the
// same under both compilers, and bench/suite/harness.py proves at build time that the runtime
// behind them really runs on more than one thread before any OpenMP column is reported.
#pragma once
#include "bench.hpp"

extern "C" int omp_get_max_threads(void);
extern "C" int omp_get_thread_num(void);
extern "C" void omp_set_num_threads(int);

namespace bench {

inline const char* arm_name() { return "omp"; }

// One worker count for every arm: the team is exactly as wide as the CAIRN lane pool.
inline void arm_setup(std::size_t crew) { omp_set_num_threads(int(crew)); }

}  // namespace bench

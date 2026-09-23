// The plate's relaxation as a C++ programmer writes it, for demos/numeric/run.py to time beside the CAIRN build.
//
// The same cells, the same edge test and the same order of additions, so both programs compute the same bits and
// print the same fingerprint. Built with -DGUARDS it also makes the checks CAIRN's lowering keeps in the sweep, one
// for one (`cairn explain demos/numeric --symbol sweep`): the entry checks of the two arrays and their disjointness
// once per sweep, and per cell the overflow check of r + 1 in the edge test, the two subtractions i - SIDE and
// i - 1, and the bounds of t[i + SIDE] and t[i + 1]. Built with -fopenmp the sweep is an OpenMP parallel for over
// as many threads as the CAIRN lane pool, which run.py sets through OMP_NUM_THREADS and CAIRN_LANES alike.
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#ifdef _OPENMP
extern "C" int omp_get_max_threads();  // declared here: a clang without its own omp.h still links a system libomp
#endif

namespace {

constexpr std::size_t SIDE = 1024;
constexpr std::size_t CELLS = SIDE * SIDE;
constexpr std::size_t STEPS = 200;
constexpr float HOT = 100.0f;

#ifdef GUARDS
[[noreturn]] void trap() { std::abort(); }

inline std::size_t add(std::size_t a, std::size_t b) {
  std::size_t r;
  if (__builtin_add_overflow(a, b, &r)) trap();
  return r;
}
inline std::size_t sub(std::size_t a, std::size_t b) {
  if (a < b) trap();
  return a - b;
}
inline float at(const float* t, std::size_t i) {
  if (i >= CELLS) trap();
  return t[i];
}
inline void entry(const float* out, const float* t) {
  if (out == nullptr || t == nullptr) trap();
  if (out < t + CELLS && t < out + CELLS) trap();
}
#else
inline std::size_t add(std::size_t a, std::size_t b) { return a + b; }
inline std::size_t sub(std::size_t a, std::size_t b) { return a - b; }
inline float at(const float* t, std::size_t i) { return t[i]; }
inline void entry(const float*, const float*) {}
#endif

inline float mean4(float n, float s, float w, float e) { return 0.25f * ((n + s) + (w + e)); }

inline bool edge(std::size_t i) {
  const std::size_t r = i / SIDE, c = i % SIDE;
  return r == 0 || c == 0 || add(r, 1) == SIDE || c + 1 == SIDE;
}

void sweep(float* out, const float* t) {
  entry(out, t);
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
  for (std::size_t i = 0; i < CELLS; ++i) {
    if (edge(i)) {
      out[i] = t[i];
    } else {
      out[i] = mean4(t[sub(i, SIDE)], at(t, i + SIDE), t[sub(i, 1)], at(t, i + 1));
    }
  }
}

}  // namespace

int main() {
  std::vector<float> a(CELLS, 0.0f), b(CELLS, 0.0f);
  for (std::size_t i = 0; i < SIDE; ++i) a[i] = b[i] = HOT;
  const auto began = std::chrono::steady_clock::now();
  for (std::size_t k = 0; k < STEPS / 2; ++k) {
    sweep(b.data(), a.data());
    sweep(a.data(), b.data());
  }
  const auto took = std::chrono::steady_clock::now() - began;
  std::uint64_t h = 1469598103934665603ULL;
  for (float x : a) {
    std::uint32_t bits;
    std::memcpy(&bits, &x, sizeof bits);
    h = (h ^ bits) * 1099511628211ULL;
  }
  const long long us = std::chrono::duration_cast<std::chrono::microseconds>(took).count();
  std::printf("plate: %zu x %zu cells, %zu sweeps in %lld us\n", SIDE, SIDE, STEPS, us);
  std::printf("plate: fingerprint of every cell's bits %llu\n", static_cast<unsigned long long>(h));
#ifdef _OPENMP
  std::printf("plate: openmp threads %d\n", omp_get_max_threads());
#endif
  return 0;
}

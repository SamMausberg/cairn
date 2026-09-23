// The tensor-core multiply's tile, run on the host: every thread of a block phase by phase, as the barriers of
// cr::tensor::product order them, with a model of the tensor-core operations that adds each fragment's products in
// increasing k. The model then adds exactly what the reference adds, in its order, so every output must equal the
// reference's: whatever the tile's indexing, its tails, its zero fill or its two stages get wrong shows as a
// difference. The operations themselves, and the order the hardware adds in, are what `make gpu` checks.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <vector>
#include "cairn_tensor.hpp"

namespace {
using namespace cr::tensor;

template<class A> struct Model {  // what Device<A> does, with float standing for the staged format
  using Stage = float;
  Stage zero() const { return 0.0f; }
  Stage widen(A x) const { return static_cast<float>(x); }
  void chunk(Stage* to, const A* from, bool inside) const {
    for(int e = 0; e < 8; ++e) to[e] = inside ? static_cast<float>(from[e]) : 0.0f;
  }
};

// One block's work for every tile, with each phase run by every thread before the next phase starts.
template<class A> void simulate(std::size_t m, std::size_t n, std::size_t k, float* c, const A* a, const A* b,
                                bool chunks) {
  using T = Tile<Model<A>>;
  auto tile = std::make_unique<T>();
  Model<A> ops;
  static float acc[WARPS][WM][WN][F][F];
  const std::size_t across = (n + BN - 1) / BN, last = steps(k);
  auto stage = [&](std::size_t row, std::size_t col, std::size_t step) {
    for(std::size_t t = 0; t < THREADS; ++t)
      if(chunks) tile->load_chunks(ops, m, n, k, a, b, row, col, step * BK, unsigned(step & 1), t);
      else tile->load_ab(ops, m, n, k, a, b, row, col, step * BK, unsigned(step & 1), t);
  };
  for(std::size_t at = 0; at < tiles(m, n); ++at) {
    const std::size_t row = at / across * BM, col = at % across * BN;
    for(std::size_t t = 0; t < THREADS; ++t) tile->load_c(m, n, c, row, col, t);
    if(last) stage(row, col, 0);
    for(std::size_t w = 0; w < WARPS; ++w)
      for(std::size_t i = 0; i < WM; ++i)
        for(std::size_t j = 0; j < WN; ++j)
          for(std::size_t r = 0; r < F; ++r)
            for(std::size_t q = 0; q < F; ++q) acc[w][i][j][r][q] = tile->cs[T::rows(w) + i * F + r][T::cols(w) + j * F + q];
    for(std::size_t step = 0; step < last; ++step) {
      const unsigned s = unsigned(step & 1);
      if(step + 1 < last) stage(row, col, step + 1);
      for(std::size_t w = 0; w < WARPS; ++w)
        for(std::size_t q0 = 0; q0 < BK; q0 += F)
          for(std::size_t i = 0; i < WM; ++i)
            for(std::size_t j = 0; j < WN; ++j)
              for(std::size_t r = 0; r < F; ++r)
                for(std::size_t q = 0; q < F; ++q)
                  for(std::size_t p = 0; p < F; ++p)
                    acc[w][i][j][r][q] = acc[w][i][j][r][q] +
                        tile->as[s][T::rows(w) + i * F + r][q0 + p] * tile->bs[s][q0 + p][T::cols(w) + j * F + q];
    }
    for(std::size_t w = 0; w < WARPS; ++w)
      for(std::size_t i = 0; i < WM; ++i)
        for(std::size_t j = 0; j < WN; ++j)
          for(std::size_t r = 0; r < F; ++r)
            for(std::size_t q = 0; q < F; ++q) tile->cs[T::rows(w) + i * F + r][T::cols(w) + j * F + q] = acc[w][i][j][r][q];
    for(std::size_t t = 0; t < THREADS; ++t) tile->store_c(m, n, c, row, col, t);
  }
}

std::uint64_t mix(std::uint64_t x) {
  x ^= x >> 31;
  x *= 0x7fb5d329728ea185ULL;
  x ^= x >> 27;
  return x;
}

template<class A> std::vector<A> matrix(std::size_t count, std::uint64_t seed) {
  std::vector<A> out(count);
  for(std::size_t e = 0; e < count; ++e) out[e] = cr::fp::narrow<A>(double(std::int64_t(mix(seed + e) % 61) - 30) / 16.0);
  return out;
}

int failures = 0;

template<class A> void check(const char* format, std::size_t m, std::size_t n, std::size_t k, bool chunks) {
  const auto a = matrix<A>(m * k, 1 + m), b = matrix<A>(k * n, 7 + n);
  std::vector<float> want(m * n), got(m * n);
  for(std::size_t e = 0; e < m * n; ++e) want[e] = got[e] = float(std::int64_t(mix(99 + e) % 9) - 4) / 4.0f;
  multiply(m, n, k, want.data(), want.size(), a.data(), a.size(), b.data(), b.size());
  simulate(m, n, k, got.data(), a.data(), b.data(), chunks);
  for(std::size_t e = 0; e < m * n; ++e)
    if(!(got[e] == want[e])) {  // equal values; a zero may differ in its sign where padding added +0 to -0
      std::printf("%s %zux%zux%zu chunks=%d: output %zu is %.9g, the reference %.9g\n", format, m, n, k, int(chunks),
                  e, double(got[e]), double(want[e]));
      ++failures;
      return;
    }
}

template<class A> void shapes(const char* format, bool two_bytes) {
  const std::size_t odd[][3] = {{1, 1, 1}, {64, 64, 32}, {65, 63, 33}, {130, 70, 100}, {17, 200, 64}, {0, 5, 5},
                                {5, 5, 0}, {3, 129, 31}};  // tails in every direction, and the empty cases
  for(auto& s : odd) check<A>(format, s[0], s[1], s[2], false);
  if(two_bytes) {  // chunks only where k and n are multiples of 8
    const std::size_t even[][3] = {{64, 64, 32}, {72, 80, 96}, {1, 8, 8}, {100, 64, 40}, {129, 136, 8}};
    for(auto& s : even) {
      check<A>(format, s[0], s[1], s[2], false);
      check<A>(format, s[0], s[1], s[2], true);
    }
  }
}
}  // namespace

int main() {
  shapes<cr::f16>("f16", true);
  shapes<cr::bf16>("bf16", true);
  shapes<cr::f8e4m3>("f8e4m3", false);
  shapes<cr::f8e5m2>("f8e5m2", false);
  if(failures) return 1;
  std::printf("ok: every tile equals the reference\n");
  return 0;
}

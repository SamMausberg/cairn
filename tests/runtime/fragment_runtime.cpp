// Tensor-core fragments on the host (runtime/cairn_fragment.hpp): one warp of 32 threads, each holding every
// fragment whole, loads operands from shared tiles through their layouts, multiplies and accumulates, and stores
// the accumulator into a shared tile, each lane only the elements the device gives it. Every output must equal the
// reference loop's, bit for bit, since both add in increasing k; every element must be written exactly once; and
// under the thread sanitizer no two lanes may write one element. Row-major, padded and swizzled tiles all serve.
#include <cmath>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>
#include "cairn_fragment.hpp"

namespace {
using namespace cr::frag;

std::uint64_t mix(std::uint64_t x) {
  x ^= x >> 31;
  x *= 0x7fb5d329728ea185ULL;
  x ^= x >> 27;
  return x;
}

template<class A> A value(std::uint64_t seed) {
  return cr::fp::narrow<A>(double(std::int64_t(mix(seed) % 61) - 30) / 16.0);
}

int failures = 0;

// One layout of an R x C tile: an offset for each element.
struct Tile {
  std::size_t rows, cols, pitch;
  unsigned swizzle;  // 0: none; else CuTe's Swizzle<swizzle, 3, 3> on the offset
  std::size_t operator()(std::size_t r, std::size_t c) const {
    const std::size_t o = r * pitch + c;
    return swizzle ? o ^ ((o >> 3) & (((std::size_t(1) << swizzle) - 1) << 3)) : o;
  }
  std::size_t cosize() const {
    std::size_t most = 0;
    for(std::size_t r = 0; r < rows; ++r)
      for(std::size_t c = 0; c < cols; ++c) most = std::max(most, (*this)(r, c));
    return most + 1;
  }
};

// C += A * B for one M x N x K fragment shape, through the given tiles, on 32 lanes at once.
template<class T, int M, int N, int K> void warp(const char* what, Tile ta, Tile tb, Tile tc) {
  std::vector<T> as(ta.cosize()), bs(tb.cosize());
  std::vector<float> cs(tc.cosize(), NAN), want(M * N);
  for(int r = 0; r < M; ++r)
    for(int p = 0; p < K; ++p) as[ta(r, p)] = value<T>(1 + r * K + p);
  for(int p = 0; p < K; ++p)
    for(int c = 0; c < N; ++c) bs[tb(p, c)] = value<T>(9001 + p * N + c);
  for(int r = 0; r < M; ++r)
    for(int c = 0; c < N; ++c) {
      float acc = float(std::int64_t(mix(77 + r * N + c) % 9) - 4) / 4.0f;
      cs[tc(r, c)] = acc;
      for(int p = 0; p < K; ++p) acc = acc + static_cast<float>(as[ta(r, p)]) * static_cast<float>(bs[tb(p, c)]);
      want[r * N + c] = acc;
    }
  std::vector<float> out(cs.size(), NAN);  // the lanes store here, so an element no lane writes stays NaN
  auto at = [](const Tile& t) { return [t](std::size_t r, std::size_t c) { return t(r, c); }; };
  std::vector<std::thread> lanes;
  for(unsigned lane = 0; lane < 32; ++lane)
    lanes.emplace_back([&, lane] {
      Whole<Role::a, T, M, N, K> a;
      Whole<Role::b, T, M, N, K> b;
      Whole<Role::acc, float, M, N, K> c;
      load(a, as.data(), at(ta));
      load(b, bs.data(), at(tb));
      load(c, cs.data(), at(tc));
      mma(c, a, b);
      store(c, out.data(), at(tc), lane);
    });
  for(auto& l : lanes) l.join();
  for(int r = 0; r < M; ++r)
    for(int c = 0; c < N; ++c) {
      if(!(out[tc(r, c)] == want[r * N + c])) {
        std::printf("%s: element (%d, %d) is %.9g, the reference %.9g\n", what, r, c, double(out[tc(r, c)]),
                    double(want[r * N + c]));
        ++failures;
        return;
      }
    }
  for(unsigned lane = 0; lane < 32; ++lane) {  // each lane holds M * N / 32 elements, and each element one lane
    int held = 0;
    for(int r = 0; r < M; ++r)
      for(int c = 0; c < N; ++c) held += holder(r, c) == lane;
    if(held != M * N / 32) {
      std::printf("%s: lane %u holds %d elements\n", what, lane, held);
      ++failures;
    }
  }
}
}  // namespace

int main() {
  const Tile rows16{16, 16, 16, 0}, pad16{16, 16, 24, 0}, swz16{16, 16, 16, 1};
  const Tile rows8{16, 8, 8, 0}, pad8{16, 8, 16, 0}, acc16{16, 16, 16, 0}, acc8{16, 8, 8, 0}, accpad{16, 16, 20, 0};
  warp<cr::f16, 16, 16, 16>("wmma f16 16x16x16 row-major", rows16, rows16, acc16);
  warp<cr::bf16, 16, 16, 16>("wmma bf16 16x16x16 padded", pad16, pad16, accpad);
  warp<cr::f16, 16, 8, 16>("mma f16 16x8x16 swizzled A", swz16, rows8, acc8);
  warp<cr::bf16, 16, 8, 16>("mma bf16 16x8x16 padded", pad16, pad8, acc8);
  warp<cr::f16, 32, 8, 16>("wmma f16 32x8x16", Tile{32, 16, 16, 0}, rows8, Tile{32, 8, 8, 0});
  warp<cr::f16, 8, 32, 16>("wmma f16 8x32x16", Tile{8, 16, 16, 0}, Tile{16, 32, 40, 0}, Tile{8, 32, 32, 0});
  if(failures) return 1;
  std::printf("ok: every lane's fragments equal the reference\n");
  return 0;
}

// The device lane's checked arithmetic on a device: cr::ovf's multiply, add and subtract in a kernel over boundary
// and random operands of every integer type, each flag and result held to the host compiler's __builtin_*_overflow.
// Nothing traps: the kernel records what each check found. Exit 0 is a pass. Only `make gpu` runs it; the same
// comparison of the device branches runs on the host in tests/runtime/device_arithmetic.cpp.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <vector>
#include <cuda_runtime.h>
#include "cairn_runtime.hpp"

static void ok(cudaError_t e) {
  if(e == cudaSuccess) return;
  std::fprintf(stderr, "gpu_arithmetic: %s\n", cudaGetErrorString(e));
  std::exit(2);
}

template<class T> struct Found {
  T mul, add, sub;
  bool mul_over, add_over, sub_over;
};

template<class T> __global__ void check(std::size_t n, const T* a, const T* b, Found<T>* out) {
  const std::size_t i = blockIdx.x * std::size_t(blockDim.x) + threadIdx.x;
  if(i >= n) return;
  Found<T> f{};
  f.mul_over = cr::ovf::mul(a[i], b[i], &f.mul);
  f.add_over = cr::ovf::add(a[i], b[i], &f.add);
  f.sub_over = cr::ovf::sub(a[i], b[i], &f.sub);
  out[i] = f;
}

static std::uint64_t next(std::uint64_t& state) {
  std::uint64_t z = (state += 0x9e3779b97f4a7c15ull);
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
  return z ^ (z >> 31);
}

template<class T> static long compare(const char* type) {
  using L = std::numeric_limits<T>;
  std::vector<T> edge;
  const T pivots[] = {T(0), T(1), L::max(), L::min(), T(L::max() / 2), T(L::min() / 2), T(T(1) << (L::digits / 2))};
  for(T p : pivots)
    for(int d = -3; d <= 3; ++d) edge.push_back(static_cast<T>(p + T(d)));
  std::vector<T> a, b;
  for(T x : edge)
    for(T y : edge) a.push_back(x), b.push_back(y);
  std::uint64_t state = 1;
  const std::uint64_t mask = (std::uint64_t(1) << (sizeof(T) * 4 + 1)) - 1;
  for(int k = 0; k < 100000; ++k) {
    const std::uint64_t x = next(state), y = next(state);
    a.push_back(static_cast<T>(x)), b.push_back(static_cast<T>(y));
    a.push_back(static_cast<T>(x & mask)), b.push_back(static_cast<T>(k & 1 ? -(y & mask) : (y & mask)));
  }
  const std::size_t n = a.size();
  T *da = nullptr, *db = nullptr;
  Found<T>* dout = nullptr;
  ok(cudaMalloc(&da, n * sizeof(T)));
  ok(cudaMalloc(&db, n * sizeof(T)));
  ok(cudaMalloc(&dout, n * sizeof(Found<T>)));
  ok(cudaMemcpy(da, a.data(), n * sizeof(T), cudaMemcpyHostToDevice));
  ok(cudaMemcpy(db, b.data(), n * sizeof(T), cudaMemcpyHostToDevice));
  check<<<unsigned((n + 255) / 256), 256>>>(n, da, db, dout);
  ok(cudaGetLastError());
  std::vector<Found<T>> got(n);
  ok(cudaMemcpy(got.data(), dout, n * sizeof(Found<T>), cudaMemcpyDeviceToHost));
  long wrong = 0;
  for(std::size_t i = 0; i < n; ++i) {
    T m = 0, s = 0, d = 0;
    const bool mo = __builtin_mul_overflow(a[i], b[i], &m), so = __builtin_add_overflow(a[i], b[i], &s),
               dor = __builtin_sub_overflow(a[i], b[i], &d);
    const Found<T>& f = got[i];
    wrong += f.mul_over != mo || (!mo && f.mul != m);
    wrong += f.add_over != so || (!so && f.add != s);
    wrong += f.sub_over != dor || (!dor && f.sub != d);
  }
  ok(cudaFree(da));
  ok(cudaFree(db));
  ok(cudaFree(dout));
  std::printf("%s: %zu pairs, %ld wrong\n", type, n, wrong);
  return wrong;
}

int main() {
  long wrong = 0;
  wrong += compare<std::uint8_t>("u8") + compare<std::int8_t>("i8");
  wrong += compare<std::uint16_t>("u16") + compare<std::int16_t>("i16");
  wrong += compare<std::uint32_t>("u32") + compare<std::int32_t>("i32");
  wrong += compare<std::uint64_t>("u64") + compare<std::int64_t>("i64");
  std::printf("gpu arithmetic: %s\n", wrong ? "FAILED" : "ok");
  return wrong ? 1 : 0;
}

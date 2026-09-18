// Self-checking test for cr::gpu: exit 0 is a pass. With an argument it runs one death case,
// which must abort the host process; tests/test_native_runtime.py drives those as subprocesses.
// The build line is documented at the top of cairn_gpu.hpp.
#include <cstdio>
#include <cstring>
#include <vector>
#include "cairn_gpu.hpp"
#include "cairn_parallel.hpp"

using cr::gpu::Dir;
static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

static CR_HD std::uint64_t mix(std::uint64_t x) noexcept {
  x = cr::add_wrap<std::uint64_t>(x, 0x9e3779b97f4a7c15ull);
  x = cr::mul_wrap<std::uint64_t>(x ^ (x >> 30), 0xbf58476d1ce4e5b9ull);
  return x ^ (x >> 31);
}

// The same guarded arithmetic must produce the same values in a lane as on the host. Operands
// stay small so nothing legitimately overflows; a misfiring device check would trap instead.
template<class T> static CR_HD std::uint64_t probe(unsigned k) noexcept {
  T a = T(k % 11), b = T((k / 11) % 11);
  if constexpr(std::is_signed_v<T>) { a = T(a - T(5)); b = T(b - T(5)); }
  std::uint64_t h = 1469598103934665603ull;
  const T seen[6] = {cr::add<T>(a, b),      cr::sub<T>(cr::add<T>(a, b), b),
                     cr::mul<T>(a, b),      cr::add_wrap<T>(a, b),
                     cr::mul_wrap<T>(a, T(k)), cr::shr<T>(a, k % (sizeof(T) * 8))};
  for(T s : seen) h = (h ^ std::uint64_t(s)) * 1099511628211ull;
  if(b != T(0)) {
    h = (h ^ std::uint64_t(cr::divide<T>(a, b))) * 1099511628211ull;
    h = (h ^ std::uint64_t(cr::remainder<T>(a, b))) * 1099511628211ull;
  }
  return (h ^ std::uint64_t(cr::convert<T>(std::int32_t(k % 7)))) * 1099511628211ull;
}
template<class T> static void test_arith() {
  const std::size_t n = 121;
  cr::gpu::Unified<std::uint64_t> got(n);
  std::uint64_t* g = got.data();
  cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) { g[i] = probe<T>(unsigned(i)); });
  for(std::size_t i = 0; i < n; ++i) CHECK(g[i] == probe<T>(unsigned(i)));
}

static void test_owners() {
  cr::gpu::Buffer<float> empty(0);
  CHECK(empty.data() == nullptr);
  cr::gpu::Pinned<double> host(1024);
  cr::gpu::Unified<std::uint64_t> shared(1024);
  cr::gpu::Buffer<std::uint64_t> device(1024);
  CHECK(host.data() != nullptr && shared.data() != nullptr && device.data() != nullptr);
  for(std::size_t i = 0; i < 1024; ++i) CHECK(host.data()[i] == 0.0 && shared.data()[i] == 0);
  std::vector<std::uint64_t> back(1024, 7);
  cr::gpu::copy(back.data(), device.data(), std::size_t(1024), Dir::d2h);
  for(std::uint64_t v : back) CHECK(v == 0);  // cudaMalloc + cudaMemset really zeroed it
  for(std::size_t i = 0; i < 1024; ++i) back[i] = i;
  cr::gpu::copy(device.data(), back.data(), std::size_t(1024), Dir::h2d);
  cr::gpu::copy(shared.data(), device.data(), std::size_t(1024), Dir::d2d);
  cr::gpu::copy(back.data(), back.data() + 512, std::size_t(0), Dir::h2h);  // n == 0 is a no-op
  std::vector<std::uint64_t> other(512, 0);
  cr::gpu::copy(other.data(), back.data(), std::size_t(512), Dir::h2h);
  for(std::size_t i = 0; i < 512; ++i) CHECK(other[i] == i);
  for(std::size_t i = 0; i < 1024; ++i) CHECK(shared.data()[i] == i);
}

static void test_launch() {
  cr::gpu::Unified<std::uint64_t> marks(8);
  std::uint64_t* m = marks.data();
  cr::gpu::launch(0, [=] CR_DEVICE(std::size_t i) { m[0] = 1; });  // n == 0 launches nothing
  CHECK(m[0] == 0);
  cr::gpu::launch(1, [=] CR_DEVICE(std::size_t i) { m[1] = cr::add<std::uint64_t>(i, 9); });
  CHECK(m[1] == 9);
  const std::size_t n = 50u * 1000u * 1000u;  // 50M elements, guarded reads and writes
  cr::gpu::Buffer<float> x(n), y(n), out(n);
  cr::gpu::Pinned<float> host(n);
  for(std::size_t i = 0; i < n; ++i) host.data()[i] = float(i % 1024);
  cr::gpu::copy(x.data(), host.data(), n, Dir::h2d);
  for(std::size_t i = 0; i < n; ++i) host.data()[i] = float(i % 7);
  cr::gpu::copy(y.data(), host.data(), n, Dir::h2d);
  const float* xp = x.data();
  const float* yp = y.data();
  float* op = out.data();
  cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) {
    cr::at(op, i, n) = 2.5f * cr::at(xp, i, n) + cr::at(yp, i, n);
  });
  cr::gpu::copy(host.data(), out.data(), n, Dir::d2h);
  for(std::size_t i = 0; i < n; i += 499)
    CHECK(host.data()[i] == 2.5f * float(i % 1024) + float(i % 7));
  CHECK(host.data()[n - 1] == 2.5f * float((n - 1) % 1024) + float((n - 1) % 7));
  // More lanes than a 32 bit index can hold: the grid strides, the index stays 64 bit.
  const std::size_t huge = (std::size_t(1) << 32) + 3;
  cr::gpu::launch(huge, [=] CR_DEVICE(std::size_t i) {
    if(i == huge - 1) m[2] = i;
    if(i == (std::size_t(1) << 32)) m[3] = i;
  });
  CHECK(m[2] == huge - 1);
  CHECK(m[3] == (std::size_t(1) << 32));
}

static void test_reduce() {
  const std::size_t n = 50u * 1000u * 1000u;
  const auto wrap_add = [] CR_DEVICE(std::uint64_t a, std::uint64_t b) {
    return cr::add_wrap<std::uint64_t>(a, b);
  };
  const auto hashed = [] CR_DEVICE(std::size_t i) { return mix(i); };
  CHECK(cr::gpu::reduce<std::uint64_t>(0, 11, wrap_add, hashed) == 11);  // empty is the identity
  CHECK(cr::gpu::reduce<std::uint64_t>(1, 11, wrap_add, hashed) == cr::add_wrap<std::uint64_t>(11, mix(0)));
  std::uint64_t want = 0;
  for(std::size_t i = 0; i < n; ++i) want = cr::add_wrap<std::uint64_t>(want, mix(i));
  CHECK(cr::gpu::reduce<std::uint64_t>(n, 0, wrap_add, hashed) == want);  // associative: exact
  std::uint64_t lo = ~std::uint64_t(0), hi = 0;
  for(std::size_t i = 0; i < n; ++i) {
    lo = mix(i) < lo ? mix(i) : lo;
    hi = mix(i) > hi ? mix(i) : hi;
  }
  CHECK(cr::gpu::reduce<std::uint64_t>(
            n, ~std::uint64_t(0), [] CR_DEVICE(std::uint64_t a, std::uint64_t b) { return a < b ? a : b; },
            hashed) == lo);
  CHECK(cr::gpu::reduce<std::uint64_t>(
            n, 0, [] CR_DEVICE(std::uint64_t a, std::uint64_t b) { return a > b ? a : b; }, hashed) == hi);
  // f32 sum: association order is unspecified, so compare against a double oracle with tolerance.
  double exact = 0;
  for(std::size_t i = 0; i < n; ++i) exact += double(float(i % 1000)) * 0.001;
  const float got = cr::gpu::reduce<float>(
      n, 0.0f, [] CR_DEVICE(float a, float b) { return a + b; },
      [] CR_DEVICE(std::size_t i) { return float(i % 1000) * 0.001f; });
  CHECK(double(got) > exact * (1 - 1e-5) && double(got) < exact * (1 + 1e-5));
}

static void test_compact() {
  const std::size_t n = 1000003;
  cr::gpu::Buffer<std::uint64_t> out(n);
  cr::gpu::Pinned<std::uint64_t> back(n);
  cr::gpu::Unified<unsigned> pred_hits(n), value_hits(n);
  std::uint64_t* o = out.data();
  unsigned* ph = pred_hits.data();
  unsigned* vh = value_hits.data();
  cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) { o[i] = 0xdeadbeefull; });  // sentinel tail
  const std::size_t count = cr::gpu::compact(
      o, n, [=] CR_DEVICE(std::size_t i) { atomicAdd(&ph[i], 1u); return mix(i) % 5 == 0; },
      [=] CR_DEVICE(std::size_t i) { atomicAdd(&vh[i], 1u); return mix(i); });
  std::vector<std::uint64_t> want;
  for(std::size_t i = 0; i < n; ++i)
    if(mix(i) % 5 == 0) want.push_back(mix(i));
  CHECK(count == want.size());
  cr::gpu::copy(back.data(), out.data(), n, Dir::d2h);
  for(std::size_t i = 0; i < count; ++i) CHECK(back.data()[i] == want[i]);  // stable, in order
  for(std::size_t i = count; i < n; ++i) CHECK(back.data()[i] == 0xdeadbeefull);  // tail untouched
  for(std::size_t i = 0; i < n; ++i) {
    CHECK(ph[i] == 1);                                 // pred exactly once per index
    CHECK(vh[i] == (mix(i) % 5 == 0 ? 1u : 0u));       // value only for the selected
  }
  const auto all = [] CR_DEVICE(std::size_t i) { return true; };
  const auto none = [] CR_DEVICE(std::size_t i) { return false; };
  const auto ident = [] CR_DEVICE(std::size_t i) { return std::uint64_t(i); };
  CHECK(cr::gpu::compact(o, std::size_t(0), all, ident) == 0);
  CHECK(cr::gpu::compact(o, std::size_t(1), all, ident) == 1);
  CHECK(cr::gpu::compact(o, std::size_t(1), none, ident) == 0);
  CHECK(cr::gpu::compact(o, n, none, ident) == 0);
  CHECK(cr::gpu::compact(o, n, all, ident) == n);
  cr::gpu::copy(back.data(), out.data(), n, Dir::d2h);
  for(std::size_t i = 0; i < n; i += 997) CHECK(back.data()[i] == i);
}

// launch_after must order on the device: no host wait stands between the two launches below.
static void test_tickets() {
  const std::size_t n = 2u * 1000u * 1000u;
  cr::gpu::Buffer<std::uint64_t> buf(n);
  cr::gpu::Pinned<std::uint64_t> back(n);
  std::uint64_t* b = buf.data();
  auto slow = cr::gpu::launch_async(n, [=] CR_DEVICE(std::size_t i) {
    std::uint64_t x = i;
    for(int k = 0; k < 400; ++k) x = mix(x);
    b[i] = x == 0 ? 0 : 1;  // always 1, but only after a lot of work
  });
  auto chain = cr::gpu::launch_after(slow, n, [=] CR_DEVICE(std::size_t i) {
    b[i] = cr::add_wrap<std::uint64_t>(cr::mul_wrap<std::uint64_t>(b[i], 3), 1);
  });
  auto again = cr::gpu::launch_after(chain, n, [=] CR_DEVICE(std::size_t i) {
    b[i] = cr::add_wrap<std::uint64_t>(cr::mul_wrap<std::uint64_t>(b[i], 3), 1);
  });
  auto fetch = cr::gpu::copy_after(again, back.data(), buf.data(), n, Dir::d2h);
  cr::gpu::wait(std::move(slow));
  cr::gpu::wait(std::move(chain));
  cr::gpu::wait(std::move(again));
  cr::gpu::wait(std::move(fetch));
  for(std::size_t i = 0; i < n; i += 1021) CHECK(back.data()[i] == 13);  // ((1*3+1)*3+1)
  auto idle = cr::gpu::launch_async(0, [=] CR_DEVICE(std::size_t i) { b[0] = 99; });
  cr::gpu::wait(std::move(idle));
  CHECK(back.data()[0] == 13);
  std::vector<std::uint64_t> src(n, 5);
  auto up = cr::gpu::copy_async(buf.data(), src.data(), n, Dir::h2d);
  auto bump = cr::gpu::launch_after(up, n, [=] CR_DEVICE(std::size_t i) { b[i] = cr::add<std::uint64_t>(b[i], 1); });
  auto down = cr::gpu::copy_after(bump, back.data(), buf.data(), n, Dir::d2h);
  cr::gpu::wait(std::move(up));
  cr::gpu::wait(std::move(bump));
  cr::gpu::wait(std::move(down));
  for(std::size_t i = 0; i < n; i += 1021) CHECK(back.data()[i] == 6);
}

static int death(const char* name) {
  const std::size_t n = 4096;
  cr::gpu::Buffer<std::uint64_t> buf(n);
  std::uint64_t* b = buf.data();
  if(!std::strcmp(name, "device_bounds")) {
    cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) { cr::at(b, i + n, n) = 1; });
  } else if(!std::strcmp(name, "device_overflow")) {
    cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) {
      b[i] = cr::add<std::uint64_t>(~std::uint64_t(0), i + 1);
    });
  } else if(!std::strcmp(name, "device_divide_zero")) {
    cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) { b[i] = cr::divide<std::uint64_t>(i, i % 2); });
  } else if(!std::strcmp(name, "device_narrow")) {
    cr::gpu::launch(n, [=] CR_DEVICE(std::size_t i) {
      b[i] = cr::convert<std::uint8_t>(cr::add<std::uint64_t>(i, 256));
    });
  } else if(!std::strcmp(name, "reduce_overflow")) {
    (void)cr::gpu::reduce<std::uint64_t>(
        n, 0, [] CR_DEVICE(std::uint64_t x, std::uint64_t y) { return cr::add<std::uint64_t>(x, y); },
        [] CR_DEVICE(std::size_t i) { return ~std::uint64_t(0); });
  } else if(!std::strcmp(name, "async_bounds")) {
    auto t = cr::gpu::launch_async(n, [=] CR_DEVICE(std::size_t i) { cr::at(b, i + n, n) = 1; });
    cr::gpu::wait(std::move(t));  // the guard fired on a stream: wait must report it
  } else if(!std::strcmp(name, "ticket_dropped")) {
    auto t = cr::gpu::launch_async(n, [=] CR_DEVICE(std::size_t i) { b[i] = i; });
    (void)t;  // never waited: ~Ticket must trap
  } else if(!std::strcmp(name, "allocation_failed")) {
    cr::gpu::Buffer<double> more(std::size_t(1) << 40);  // 8 TiB
    (void)more.data();
  } else {
    std::fprintf(stderr, "unknown death case %s\n", name);
    return 2;
  }
  cr::gpu::launch(1, [=] CR_DEVICE(std::size_t i) { b[0] = 1; });  // force a synchronization
  std::fprintf(stderr, "death case %s did not abort\n", name);
  return 3;
}

int main(int argc, char** argv) {
  static const char* cases[] = {"device_bounds",  "device_overflow",   "device_divide_zero",
                                "device_narrow",  "reduce_overflow",   "async_bounds",
                                "ticket_dropped", "allocation_failed"};
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    for(const char* c : cases) std::printf("%s\n", c);
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_arith<std::int8_t>();
  test_arith<std::int16_t>();
  test_arith<std::int32_t>();
  test_arith<std::int64_t>();
  test_arith<std::uint8_t>();
  test_arith<std::uint16_t>();
  test_arith<std::uint32_t>();
  test_arith<std::uint64_t>();
  test_owners();
  test_launch();
  test_reduce();
  test_compact();
  test_tickets();
  std::printf("gpu_runtime: %s after %ld checks\n", failures ? "FAILED" : "ok", checked);
  return failures ? 1 : 0;
}

// Device test for cr::gpu::Context: exit 0 is a pass; with an argument it runs one death case, which must abort.
// It runs only under `make gpu`. The default suite compiles it for sm_120 and never runs it, because device runs
// on the reference machine share the GPU with the display. What it checks is what tests/runtime/reuse_runtime.cpp
// checks against a mock, now on the device: reductions and compactions that allocate nothing and start no stream
// after the first, a reduction whose result stays on the device for the next kernel, a budget that answers
// over_budget, and stream-ordered growth.
#include <cstdio>
#include <cstring>
#include <vector>
#include "cairn_gpu.hpp"

static int failures = 0;
static long checked = 0;
#define CHECK(c) \
  do { \
    ++checked; \
    if(!(c)) { std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } \
  } while(0)

using cr::gpu::Allocation;
using cr::gpu::Budget;
using cr::gpu::Context;
using cr::gpu::Scratch;

struct Plus {
  CR_DEVICE std::uint64_t operator()(std::uint64_t a, std::uint64_t b) const { return a + b; }
};
struct Index {
  CR_HD std::uint64_t operator()(std::size_t i) const { return std::uint64_t(i); }
};

static void test_repeated_reductions_reuse_everything() {
  Context ctx(Budget{1 << 20, 1 << 20});
  const std::size_t n = 1000003;
  for(int k = 0; k < 100; ++k) {
    std::uint64_t total = 0;
    CHECK(cr::gpu::reduce(ctx, total, n, std::uint64_t(0), Plus{}, Index{}) == Scratch::ok);
    CHECK(total == std::uint64_t(n) * (n - 1) / 2);
  }
  CHECK(ctx.streams_made() == 1 && ctx.grown() == 0 && ctx.lent() == 0);
}

// The reduction writes a device cell and the next kernel reads it, ordered by the ticket, with no host copy between.
static void test_a_result_stays_on_the_device() {
  Context ctx(Budget{1 << 20, 1 << 20});
  cr::gpu::Buffer<std::uint64_t> cell(1), twice(1);
  std::uint64_t* c = cell.data();
  std::uint64_t* t = twice.data();
  Scratch answer = Scratch::over_budget;
  auto sum = cr::gpu::reduce_to(ctx, c, 4096, std::uint64_t(0), Plus{}, Index{}, &answer);
  CHECK(answer == Scratch::ok);
  auto use = cr::gpu::launch_on(ctx, 1, [=] CR_DEVICE(std::size_t) { *t = *c * 2; }, sum);
  std::move(sum).wait();
  std::move(use).wait();
  std::uint64_t host = 0;
  cr::gpu::copy(&host, t, std::size_t(1), cr::gpu::Dir::d2h);
  CHECK(host == std::uint64_t(4096) * 4095);
}

static void test_compaction_matches_the_host() {
  Context ctx(Budget{1 << 22, 1 << 22});
  const std::size_t n = 100000;
  cr::gpu::Buffer<std::uint32_t> out(n);
  std::uint32_t* o = out.data();
  std::size_t used = 0;
  CHECK(cr::gpu::compact(ctx, &used, o, n, [] CR_DEVICE(std::size_t i) { return i % 3 == 0; },
                         [] CR_DEVICE(std::size_t i) { return std::uint32_t(i); }) == Scratch::ok);
  CHECK(used == (n + 2) / 3);
  std::vector<std::uint32_t> host(used);
  cr::gpu::copy(host.data(), o, used, cr::gpu::Dir::d2h);
  bool right = true;
  for(std::size_t j = 0; j < used; ++j) right = right && host[j] == std::uint32_t(3 * j);
  CHECK(right);
}

static void test_a_budget_answers() {
  Context small(Budget{16, 16});
  std::uint64_t total = 7;
  CHECK(cr::gpu::reduce(small, total, 1000003, std::uint64_t(0), Plus{}, Index{}) == Scratch::over_budget);
  CHECK(total == 7);  // nothing was written
  Context ordered(Budget{0, 1 << 22}, Allocation::stream_ordered);
  for(int k = 0; k < 10; ++k) {
    CHECK(cr::gpu::reduce(ordered, total, 1000003, std::uint64_t(0), Plus{}, Index{}) == Scratch::ok);
    CHECK(total == std::uint64_t(1000003) * 1000002 / 2);
  }
  CHECK(ordered.grown() == 1);
}

static int death(const char* name) {
  if(!std::strcmp(name, "lent_dropped")) {
    Context ctx(Budget{256, 256});
    auto t = cr::gpu::launch_on(ctx, 1, [] CR_DEVICE(std::size_t) {});
    (void)t;  // never waited: ~Lent must trap
  } else {
    std::fprintf(stderr, "unknown death case %s\n", name);
    return 2;
  }
  std::fprintf(stderr, "death case %s did not abort\n", name);
  return 3;
}

int main(int argc, char** argv) {
  static const char* cases[] = {"lent_dropped"};
  if(argc > 1 && !std::strcmp(argv[1], "--list")) {
    for(const char* c : cases) std::printf("%s\n", c);
    return 0;
  }
  if(argc > 1) return death(argv[1]);
  test_repeated_reductions_reuse_everything();
  test_a_result_stays_on_the_device();
  test_compaction_matches_the_host();
  test_a_budget_answers();
  std::printf("gpu_reuse: %s after %ld checks\n", failures ? "FAILED" : "ok", checked);
  return failures ? 1 : 0;
}

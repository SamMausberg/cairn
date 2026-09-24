#include <cstdio>
#include <cstdint>
#include <cstring>
#include <functional>
#include <vector>
#include "counting_coop.hpp"
#include "ops.h"
struct Snap { std::size_t v[13]; };
static Snap snap() {
  const auto& c = cr::gpu::counted;
  return {{c.launches.load(), c.library_calls.load(), c.copies.load(), c.memsets.load(), c.records.load(), c.stream_event_waits.load(),
           c.stream_waits.load(), c.event_waits.load(), c.streams.load(), c.events.load(), c.allocations.load() + c.scratch_allocations.load(), c.frees.load(), 0}};
}
int main() {
  const std::size_t n = 4096, g = 3;
  std::vector<float> x(n, 1.0f), h(n, 2.0f), d(n);
  std::vector<std::uint32_t> u(n, 5u), out(n), bits(g);
  static cr::gpu::HostStream mine{0};
  struct Op { const char* name; std::function<void()> call; };
  std::vector<Op> ops = {
    {"region", [&] { cf_region(n, x.data()); }},
    {"two_regions", [&] { cf_two_regions(n, x.data()); }},
    {"cooperative_region", [&] { cf_coop(g, bits.data()); }},
    {"transfer_h2d", [&] { cf_to_device(n, d.data(), h.data()); }},
    {"transfer_d2h", [&] { cf_to_host(n, h.data(), d.data()); }},
    {"transfer_d2d", [&] { cf_device_copy(n, d.data(), x.data()); }},
    {"reduce", [&] { (void)cf_total(n, u.data()); }},
    {"scan", [&] { (void)cf_prefix(n, out.data(), u.data()); }},
    {"compact", [&] { (void)cf_keep(n, out.data(), u.data()); }},
    {"buffer_and_two_regions", [&] { cf_scratch(n, x.data()); }},
    {"spawn_and_wait", [&] { cf_queued(n, x.data()); }},
#ifdef ENQUEUED
    {"region_enqueued", [&] { cq_region(&mine, n, x.data()); }},
    {"two_regions_enqueued", [&] { cq_two_regions(&mine, n, x.data()); }},
    {"cooperative_region_enqueued", [&] { cq_coop(&mine, g, bits.data()); }},
    {"transfer_d2d_enqueued", [&] { cq_device_copy(&mine, n, d.data(), x.data()); }},
#endif
  };
  const char* names[] = {"launches", "library_calls", "copies", "memsets", "event_records", "stream_waits_event", "stream_syncs", "event_syncs", "streams_made", "events_made", "allocations", "frees"};
  std::printf("[\n");
  for(std::size_t k = 0; k < ops.size(); ++k) {
    ops[k].call();  // the first call makes the context, its lane and its arena
    const Snap a = snap();
    ops[k].call();
    const Snap b = snap();
    std::printf("  {\"op\": \"%s\"", ops[k].name);
    for(int f = 0; f < 12; ++f) std::printf(", \"%s\": %zu", names[f], b.v[f] - a.v[f]);
    std::printf("}%s\n", k + 1 < ops.size() ? "," : "");
  }
  std::printf("]\n");
  return 0;
}

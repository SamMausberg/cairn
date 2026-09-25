// A host stand-in for the device half of cairn_coop.hpp, so the lambda the compiler writes for a device cooperative
// region runs on the host: the same body, given the host's block context in place of the device's, each block's
// threads real threads at a std::barrier. It replaces cairn_gpu.hpp in a test program and brings in gpu_host.hpp,
// the host machine the rest of a device program runs on; the region runs on the execution context's synchronous lane
// as the device launch does. The device launch itself is `make gpu`'s.
#pragma once
#include "gpu_host.hpp"
#include "cairn_coop.hpp"

namespace cr::coop {
using Device = Host;
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, class F>
inline void launch(gpu::Context& ctx, std::size_t grid, F body) noexcept {
  reuse::synchronous(ctx, [&](typename gpu::Machine::Stream s) {
    gpu::Host::queued(s);  // queued on the lane, for the machine's record of what is waited for
    run<THREADS, BYTES, ZERO>(grid, body);
  });
}
// A region with a finish claims its word as the device launch does (Finishes), and counts its blocks in the stand-in's
// own table as the device's blocks would, one after another: the last to arrive, and only it, runs the finish, then
// puts the word back. `finished` is the last claim, for a test to read.
inline Claim finished{};
inline unsigned long long words[SLOTS] = {};
template<unsigned THREADS, std::size_t BYTES, std::size_t ZERO = BYTES, std::size_t FINISH = BYTES, class F, class G>
inline void launch_then(gpu::Context& ctx, std::size_t grid, F body, G finish) noexcept {
  const unsigned g = grid < 1 ? 1u : grid < reuse::MAX_GRID ? unsigned(grid) : reuse::MAX_GRID;
  queue_then(ctx, [&](typename gpu::Machine::Stream s, Claim held) {
    gpu::Host::queued(s);
    finished = held;
    run<THREADS, BYTES, ZERO>(grid, body);
    for(unsigned b = 0; b < g; ++b)
      if(arrive(words + held.slot, held.tag, g) != (b == g - 1)) trap();
    run<THREADS, BYTES, FINISH>(1, finish);
    depart(words + held.slot);
  });
}
}  // namespace cr::coop

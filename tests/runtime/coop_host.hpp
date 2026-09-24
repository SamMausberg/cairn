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
template<unsigned THREADS, std::size_t BYTES, class F>
inline void launch(gpu::Context& ctx, std::size_t grid, F body) noexcept {
  reuse::synchronous(ctx, [&](typename gpu::Machine::Stream) { run<THREADS, BYTES>(grid, body); });
}
template<unsigned THREADS, std::size_t BYTES, class F, class G>
inline void launch_then(gpu::Context& ctx, std::size_t grid, F body, G finish) noexcept {
  reuse::synchronous(ctx, [&](typename gpu::Machine::Stream) { run_then<THREADS, BYTES>(grid, body, finish); });
}
}  // namespace cr::coop

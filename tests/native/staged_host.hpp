// A host stand-in for the two launches of cairn_gpu.hpp a staged region's test program uses, so the lambdas the
// compiler writes for `plan f { stage R; }` run on the host: every block's tiles in turn, each tile's loads by every
// thread before any thread's body, as the kernel's barriers order them. The tile lives in its own exactly sized
// allocation, filled with a pattern before each load, so a body that reads an element its block did not load reads
// the pattern, and one that reads past the tile is an AddressSanitizer error. The device's own launch is `make gpu`'s.
#pragma once
#include <cstring>
#include <vector>
#include "cairn_runtime.hpp"

namespace cr::gpu {
constexpr unsigned BLOCK = 256;

template<class T> constexpr std::size_t tile_bytes(std::size_t width) noexcept { return (width * sizeof(T) + 15) / 16 * 16; }

template<unsigned U = 1, class F>
inline void launch(std::size_t n, F body, unsigned = BLOCK, std::size_t = 1) noexcept {
  for(std::size_t i = 0; i < n; ++i) body(i);
}

template<std::size_t R, unsigned U = 1, class L, class F, class S>
inline void launch_staged(std::size_t n, L load, F body, S bytes, unsigned block = BLOCK, std::size_t = 1) noexcept {
  const std::size_t w = block + 2 * R;
  for(std::size_t base = 0; base < n; base += block) {
    std::vector<unsigned char> tile(bytes(w));
    std::memset(tile.data(), 0x7f, tile.size());  // a float of about 3.4e38: nothing the stencils compute
    for(std::size_t t = 0; t < block; ++t) load(base, w, t, std::size_t(block), tile.data());
    for(std::size_t t = 0; t < block; ++t)
      if(base + t < n) body(base + t, base, w, tile.data());
  }
}
}  // namespace cr::gpu

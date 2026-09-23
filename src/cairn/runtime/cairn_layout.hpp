// Layouts in code (compiler/layouts.py). `L.at(r, c)`, `D.row(t, v)` and their siblings lower to the arithmetic a
// layout's modes spell, after each coordinate is checked against its extent here: a coordinate outside its layout
// traps, as an index outside a view does, on the host and in a device lane alike.
#pragma once
#include <cstddef>
#include "cairn_runtime.hpp"

namespace cr::layout {

CR_HD inline std::size_t within(std::size_t x, std::size_t n) noexcept {
  if(x >= n) trap();
  return x;
}

// CuTe's Swizzle<B, M, S>: bits M + S to M + S + B of an offset flip bits M to M + B. With S at least 1 each bit
// that flips is decided by a higher one that does not, so no two offsets meet; with S = 0 they do, and
// compiler/layouts.py refuses a layout under which two elements share an offset before any code is written.
template<unsigned B, unsigned M, unsigned S> CR_HD constexpr std::size_t swizzle(std::size_t o) noexcept {
  return o ^ ((o >> S) & (((std::size_t(1) << B) - 1) << M));
}

} // namespace cr::layout

"""An independent Python result for histogram_u32, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a sequential C++ loop that shares its
compiler and its flags. This file shares neither, so a mistake common to both C++ paths still fails
here. Every bin is a count, so the comparison is exact and carries no tolerance.

The result an arm dumps is the 256 bins, whatever n was, so `expected` returns 256 numbers for every
size.
"""

BINS = 256
KNUTH = 2654435761
MASK64 = (1 << 64) - 1
MASK32 = (1 << 32) - 1


def sample(i: int) -> int:
    """x[i] as case.hpp fills it: a 64-bit product, shifted down by seven, truncated to u32."""
    return (((i * KNUTH) & MASK64) >> 7) & MASK32


def expected(n: int) -> list[int]:
    bins = [0] * BINS
    for i in range(n):
        bins[sample(i) & 255] += 1
    return bins

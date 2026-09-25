"""An independent Python result for sum_u64_wrap, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a sequential C++ loop that shares its
compiler and its flags. This file shares neither, so a mistake common to both C++ paths still fails
here. Python integers are unbounded, so the fill and the total are masked back to sixty four bits by
hand; wrapping addition is exact, so the comparison carries no tolerance and no association order of
its own is implied.
"""

MASK = (1 << 64) - 1


def expected(n: int) -> int:
    return sum((i * 2654435761 + 1) & MASK for i in range(n)) & MASK

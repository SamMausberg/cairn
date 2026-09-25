"""An independent Python result for stencil_1d, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a sequential C++ loop that shares its
compiler and its flags. This file shares neither, so a mistake common to both C++ paths still fails
here. The fill is a small integer times a power of two and every blend is a multiple of 0.125 below
32, so every value is exact in f32 and the comparison carries no tolerance.

The blend is associated the way the emitter writes it, ((0.25 * l) + (0.5 * c)) + (0.25 * r), which
costs nothing here because the result is exact either way and says plainly which function this is.
"""


def sample(i: int) -> float:
    """x[i] as case.hpp fills it."""
    return (i % 64) * 0.5


def blend3(left: float, centre: float, right: float) -> float:
    return ((0.25 * left) + (0.5 * centre)) + (0.25 * right)


def expected(n: int) -> list[float]:
    x = [sample(i) for i in range(n)]
    return [blend3(x[i - 1], x[i], x[i + 1]) if 0 < i < n - 1 else x[i] for i in range(n)]

"""An independent Python result for saxpy_f32, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a sequential C++ loop that shares its
compiler and its flags. This file shares neither, so a mistake common to both C++ paths still fails
here. Every value is exact in f32, so the comparison is exact and carries no tolerance.
"""

A = 2.5


def expected(n: int) -> list[float]:
    return [A * (float(i % 1024) * 0.5) + float(i % 77) for i in range(n)]


def agrees(n: int, result) -> bool:
    want = expected(n)
    return len(result) == len(want) and all(a == b for a, b in zip(result, want, strict=True))

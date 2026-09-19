"""An independent Python result for mixed_u64, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a sequential C++ loop that shares its
compiler and its flags. This file shares neither, so a mistake common to both C++ paths still fails
here. Python integers are unbounded, so every step is masked back to sixty four bits by hand and the
comparison is exact and carries no tolerance.
"""

MASK = (1 << 64) - 1
MULTIPLIER = 0xBF58476D1CE4E5B9


def mix(v: int) -> int:
    for _ in range(8):
        v ^= v >> 29
        v = (v * MULTIPLIER) & MASK
    return v


def expected(n: int) -> list[int]:
    return [mix((i * 2654435761 + 1) & MASK) for i in range(n)]


def agrees(n: int, result) -> bool:
    want = expected(n)
    return len(result) == len(want) and all(a == b for a, b in zip(result, want, strict=True))

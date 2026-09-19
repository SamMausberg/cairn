"""An independent Python result for tasks_split, used at the smoke sizes only.

The in-process check inside case.hpp compares an arm against a single sequential loop over the whole
array, which is right only because the four seeds hide the split. This file does not take that on
trust. It walks the four parts the kernel walks, with the four seeds the kernel hands them, so a part
that started in the wrong place or carried the wrong seed fails here even when both C++ paths agree,
and it shares neither the compiler nor the flags of the in-process loop. Every value is a 64 bit
unsigned integer and every operation wraps, so the comparison is exact and carries no tolerance.
"""

SEED = 0x9E3779B97F4A7C15
MIX = 2654435761
MASK = (1 << 64) - 1


def expected(n: int) -> list[int]:
    out = [0] * n
    q1 = n // 4
    q2 = q1 + q1
    q3 = q2 + q1
    parts = [(0, q1, SEED), (q1, q2, SEED + q1), (q2, q3, SEED + q2), (q3, n, SEED + q3)]
    for lo, hi, seed in parts:
        for i in range(hi - lo):
            out[lo + i] = (((seed & MASK) + i) & MASK) * MIX & MASK
    return out


def agrees(n: int, result) -> bool:
    want = expected(n)
    return len(result) == len(want) and all(a == b for a, b in zip(result, want, strict=True))

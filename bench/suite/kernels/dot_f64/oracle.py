"""An independent Python reference for dot_f64, and the bitwise report the kernel is preregistered to make.

The in-process check inside case.hpp compares an arm against a sequential C++ fold that shares its compiler and its
flags. This file shares neither, so a mistake common to both C++ paths still fails here. Python floats are IEEE
doubles and the arms are built with -ffp-contract=off -fno-fast-math, so the fold below is the same sequence of
roundings the emitted host reduce performs, term by term.

Two questions are asked separately, because they have different answers and only one of them is a defect. agrees
answers "is this arm's own result right", which is the question run_all stops the suite over, and it allows the
reassociation an OpenMP reduction or a TBB parallel_reduce performs: a finite result within TOLERANCE of the
in-order fold passes. folds_in_order answers "is this bit for bit the sum the CAIRN source names", which is the
finding this kernel exists to record and which the parallel arms are expected to answer no to at large n.
"""

import struct

# The bound of agrees, argued in kernels/dot_f64/case.hpp and repeated here so the two checks are one rule. It sits
# well above the rounding two orderings of these terms disagree by and well below the 2 / n relative move a dropped
# or duplicated unit term makes, which is 2e-8 at the largest size of the sweep.
TOLERANCE = 1e-9


def terms(n: int):
    """The products the kernel sums, in the order it sums them."""
    for i in range(n):
        yield (1.0 / (i + 1)) * (1.0 if i % 2 == 0 else float(i + 1))


def expected(n: int) -> float:
    """The strict in-order fold: left to right, one term at a time, no reassociation."""
    total = 0.0
    for term in terms(n):
        total = total + term
    return total


def bits(value: float) -> int:
    """The exact u64 bit pattern of a double, which is what dump prints and what this file compares."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def agrees(n: int, result) -> bool:
    """True when the arm's result is finite, self consistent and within TOLERANCE of the in-order fold."""
    value = float(result["value"])
    if bits(value) != int(result["bits"]):
        return False
    if value != value or value in (float("inf"), float("-inf")):
        return False
    want = expected(n)
    scale = abs(want)
    return abs(value - want) <= TOLERANCE * (scale if scale > 0.0 else 1.0)


def folds_in_order(n: int, result) -> bool:
    """True when the arm returned the in-order fold bit for bit, which is the report, not the pass or fail."""
    return int(result["bits"]) == bits(expected(n))

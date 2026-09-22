"""An independent model of the storage floats: every value a format holds, as an exact rational, and rounding as
a search for the nearest of them. Nothing here shifts bits the way runtime/cairn_float.hpp does; the two meet
only in the definitions of the formats, which are IEEE 754's binary16, bfloat16, and OCP's E4M3 and E5M2.
"""

from __future__ import annotations

import bisect
import math
import struct
from fractions import Fraction

FORMATS = {"f16": (5, 10, True), "bf16": (8, 7, True), "f8e4m3": (4, 3, False), "f8e5m2": (5, 2, True)}
TRAP = "trap"


class Format:
    def __init__(self, name: str):
        self.name = name
        self.e, self.m, self.infinite = FORMATS[name]
        self.width = 1 + self.e + self.m
        self.sign = 1 << (self.e + self.m)
        self.bias = 2 ** (self.e - 1) - 1
        finite = sorted((v, p) for p in range(self.sign) if isinstance(v := self.magnitude(p), Fraction))
        self.values, self.patterns = [v for v, _ in finite], [p for _, p in finite]
        # One step past the largest value at its own spacing, and the pattern it would have: rounding there
        # overflows. Its fraction is even only where that pattern is infinity.
        top = self.values[-1]
        self.values.append(top + (top - self.values[-2]))
        self.patterns.append(self.patterns[-1] + 1)

    def magnitude(self, p: int):
        """The value of a pattern without its sign: a Fraction, math.inf or math.nan."""
        exponent, fraction = p >> self.m, p % (1 << self.m)
        if exponent == 2**self.e - 1 and (self.infinite or fraction == 2**self.m - 1):
            return math.nan if fraction else math.inf
        if exponent == 0:
            return Fraction(fraction, 2**self.m) * Fraction(2) ** (1 - self.bias)
        return (1 + Fraction(fraction, 2**self.m)) * Fraction(2) ** (exponent - self.bias)

    def value(self, pattern: int) -> float:
        """A pattern as the double it denotes; every value of these formats is one."""
        v = self.magnitude(pattern % self.sign)
        v = float(v) if isinstance(v, Fraction) else v
        return -v if pattern & self.sign else v

    def is_nan(self, pattern: int) -> bool:
        return math.isnan(self.value(pattern))

    def nearest(self, x: Fraction):
        """|x| to nearest with ties to even among the values, and the step past them; None means past them."""
        i = bisect.bisect_left(self.values, x)
        if i < len(self.values) and self.values[i] == x:
            chosen = i
        elif i == len(self.values):
            chosen = len(self.values) - 1
        else:
            below, above = self.values[i - 1], self.values[i]
            if x - below != above - x:
                chosen = i - 1 if x - below < above - x else i
            else:
                chosen = i - 1 if self.patterns[i - 1] % 2 == 0 else i
        return None if chosen == len(self.values) - 1 else self.patterns[chosen]

    def narrow(self, x: float):
        """f16(x) and the rest: IEEE's conversion, which overflows to infinity; where the format has none, a trap."""
        sign = self.sign if math.copysign(1.0, x) < 0 else 0
        if math.isnan(x):
            return ("nan", sign)
        if math.isinf(x):
            return self.overflow(sign)
        found = self.nearest(abs(Fraction(x)))
        return self.overflow(sign) if found is None else sign | found

    def overflow(self, sign: int):
        return sign | self.patterns[-1] if self.infinite else TRAP

    def quantize(self, x: float, scale: float):
        """x / scale rounded once to nearest even, clamped to the largest finite value; a NaN stays one."""
        if not (scale > 0 and math.isfinite(scale)):
            return TRAP
        sign = self.sign if math.copysign(1.0, x) * math.copysign(1.0, scale) < 0 else 0
        if math.isnan(x):
            return ("nan", sign)
        if math.isinf(x):
            return sign | self.patterns[-2]
        found = self.nearest(abs(Fraction(x) / Fraction(scale)))
        return sign | (self.patterns[-2] if found is None else found)


def quantize_integer(x: float, scale: float, low: int, high: int):
    """quantize[i8] and the rest: x / scale to the nearest integer, ties to even, clamped to [low, high]."""
    if not (scale > 0 and math.isfinite(scale)) or math.isnan(x):
        return TRAP
    if math.isinf(x):
        return high if x > 0 else low
    q = Fraction(x) / Fraction(scale)
    whole = math.floor(q)
    rest = q - whole
    rounded = whole + (rest > Fraction(1, 2) or (rest == Fraction(1, 2) and whole % 2 == 1))
    return max(low, min(high, rounded))


def f16_by_struct(x: float):
    """Python's own binary16 packing, a second reference for f16 alone: it rounds to nearest even and raises on
    overflow, where the conversion gives infinity."""
    try:
        return struct.unpack("<H", struct.pack("<e", x))[0]
    except OverflowError:
        return 0xFC00 if x < 0 else 0x7C00


def double_bits(x: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def from_double_bits(u: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", u))[0]

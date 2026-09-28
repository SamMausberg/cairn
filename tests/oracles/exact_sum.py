"""An independent model of the correctly rounded sum a float `reduce + parallel` computes (docs/numerics.md): the
special values first, then the exact sum of the terms as an integer count of the format's least quantum, rounded once
to nearest with ties to even. Each rounding is then checked by a second argument that shares nothing with the first:
no value of the format lies nearer the exact sum, and a tie went to the even neighbour. An f32 sum is rounded from
the exact rational directly, never through a double, which could round twice.
"""

from __future__ import annotations

import math
import struct
from fractions import Fraction

# precision, least normal exponent, largest exponent, struct codes of the value and of its pattern
FORMATS = {"f64": (53, -1022, 1023, "<d", "<Q"), "f32": (24, -126, 127, "<f", "<I")}


def width(fmt: str) -> int:
    return 64 if fmt == "f64" else 32


def pattern(x: float, fmt: str) -> int:
    _, _, _, value_code, pattern_code = FORMATS[fmt]
    return struct.unpack(pattern_code, struct.pack(value_code, x))[0]


def value(bits: int, fmt: str) -> float:
    _, _, _, value_code, pattern_code = FORMATS[fmt]
    return struct.unpack(value_code, struct.pack(pattern_code, bits))[0]


def f32(x: float) -> float:
    """`x` rounded once to f32, as Python's struct rounds, to nearest with ties to even."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def infinity(fmt: str) -> int:
    p, _, emax, _, _ = FORMATS[fmt]
    return (2 * emax + 1) << (p - 1)


def exact(terms: list[float], fmt: str) -> Fraction:
    """The exact sum of finite terms: each is an integer multiple of the format's least subnormal, so the sum is too."""
    p, emin, _, _, _ = FORMATS[fmt]
    least = p - 1 - emin  # 1074 or 149: the least subnormal is 2^-least
    scaled = 0
    for t in terms:
        num, den = t.as_integer_ratio()
        assert (1 << least) % den == 0, f"{t!r} is not a value of {fmt}"
        scaled += num * ((1 << least) // den)
    return Fraction(scaled, 1 << least)


def rounded(total: Fraction, fmt: str) -> int:
    """The pattern of a nonzero exact value rounded once to nearest with ties to even, or the infinity of its sign."""
    p, emin, emax, _, _ = FORMATS[fmt]
    sign = 1 << (width(fmt) - 1) if total < 0 else 0
    magnitude = abs(total)
    e = magnitude.numerator.bit_length() - magnitude.denominator.bit_length()
    while Fraction(2) ** e > magnitude:
        e -= 1
    while Fraction(2) ** (e + 1) <= magnitude:
        e += 1
    quantum = max(e, emin) - (p - 1)  # the exponent of the result's last bit
    scaled = magnitude / Fraction(2) ** quantum
    m, rest = divmod(scaled.numerator, scaled.denominator)
    if 2 * rest > scaled.denominator or (2 * rest == scaled.denominator and m % 2 == 1):
        m += 1
    if m == 1 << p:
        m >>= 1
        quantum += 1
    if m < 1 << (p - 1):  # subnormal, at the least quantum
        return sign | m
    exponent = quantum + p - 1
    if exponent > emax:
        return sign | infinity(fmt)
    return sign | ((exponent + emax) << (p - 1)) | (m & ((1 << (p - 1)) - 1))


def correct(terms: list[float], fmt: str) -> int:
    """The pattern of the correctly rounded sum of `terms`, each a value of `fmt`: NaN if any term is NaN or both
    infinities occur, else the infinity that occurs, else the exact sum rounded once. An exact zero is -0.0 when there
    is at least one term and every term is -0.0, and +0.0 otherwise, as IEEE addition gives."""
    p = FORMATS[fmt][0]
    top = infinity(fmt)
    if any(t != t for t in terms) or (math.inf in terms and -math.inf in terms):
        return top | (1 << (p - 2))  # the quiet NaN with no payload
    if math.inf in terms:
        return top
    if -math.inf in terms:
        return top | (1 << (width(fmt) - 1))
    total = exact(terms, fmt)
    if total == 0:
        every_negative_zero = bool(terms) and all(math.copysign(1.0, t) < 0 for t in terms)
        return (1 << (width(fmt) - 1)) if every_negative_zero else 0
    bits = rounded(total, fmt)
    nearest(total, bits, fmt)
    return bits


def nearest(total: Fraction, bits: int, fmt: str) -> None:
    """Fail unless `bits` is a correct rounding of `total`: it has the sign of the sum, no value of the format is
    nearer, and a tie went to the neighbour with an even last bit. Past the largest finite value the next power of two
    stands for infinity, as IEEE's rounding to nearest takes it."""
    _, _, emax, _, _ = FORMATS[fmt]
    magnitude = bits & ((1 << (width(fmt) - 1)) - 1)
    top = infinity(fmt)
    beyond = Fraction(2) ** (emax + 1)
    assert (bits >> (width(fmt) - 1)) == (total < 0), "the sign differs from the exact sum's"
    a = abs(total)
    if magnitude == top:
        largest = Fraction(value(top - 1, fmt))
        assert a - largest >= beyond - a, "rounded to infinity below the threshold"
        return
    r = Fraction(value(magnitude, fmt))
    d = abs(a - r)
    neighbours = [Fraction(value(magnitude - 1, fmt))] if magnitude else []
    neighbours.append(Fraction(value(magnitude + 1, fmt)) if magnitude + 1 < top else beyond)
    for other in neighbours:
        e = abs(a - other)
        assert d <= e, "a nearer value of the format exists"
        if d == e:
            assert magnitude % 2 == 0, "a tie went to the odd neighbour"

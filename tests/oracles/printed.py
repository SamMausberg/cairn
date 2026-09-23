"""What print writes for a float, computed with exact rationals and nothing from C++.

The shortest decimal that reads back to the same f32 or f64 is found by trying one significant digit, then two,
each time the two decimals of that length that bracket the value, keeping those that round back to it and the
nearer of them (an even last digit on a tie). They are laid out as ECMAScript's Number::toString lays out a number
(ECMA-262, Number::toString, radix 10): with the value 0.d1...dk times ten to the n, plain digits and zeros while
k <= n <= 21, a point inside the digits while 0 < n <= 21, 0.000d1...dk while -6 < n <= 0, and an exponent
otherwise, restated from the standard's words rather than from any library's code.
"""

import math
import struct
from fractions import Fraction


def nearest(q: Fraction, fraction_bits: int, lowest: int) -> Fraction:
    """q rounded to the binary float of that precision, ties to even; subnormals below 2**lowest."""
    if q == 0:
        return q
    a = abs(q)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    e += 1 if Fraction(2) ** (e + 1) <= a else -1 if Fraction(2) ** e > a else 0
    ulp = Fraction(2) ** (max(e, lowest) - fraction_bits)
    n = a / ulp
    whole, rest = divmod(n.numerator, n.denominator)
    if 2 * rest > n.denominator or (2 * rest == n.denominator and whole % 2):
        whole += 1
    return (1 if q > 0 else -1) * whole * ulp


def reads_back(x: float, bits: int):
    target, (fraction_bits, lowest) = Fraction(x), (52, -1022) if bits == 64 else (23, -126)
    return lambda c: nearest(c, fraction_bits, lowest) == target


def shortest(x: float, bits: int) -> tuple[str, int]:
    """The digits and the power of ten of the last one, for x > 0."""
    q, back = Fraction(x), reads_back(x, bits)
    k = math.floor(math.log10(x))
    k += 1 if Fraction(10) ** (k + 1) <= q else -1 if Fraction(10) ** k > q else 0
    for count in range(1, 20):
        scale = Fraction(10) ** (k - count + 1)
        low = math.floor(q / scale)
        kept = [(abs(n * scale - q), n % 2, n) for n in (low, low + 1) if back(n * scale)]
        if kept:
            digits, power = str(min(kept)[2]), k - count + 1
            while len(digits) > 1 and digits.endswith("0"):
                digits, power = digits[:-1], power + 1
            return digits, power
    raise AssertionError(f"no decimal reads back to {x!r}")


def printed(x: float, bits: int = 64) -> str:
    if bits == 32:
        x = struct.unpack("<f", struct.pack("<f", x))[0]
    sign = "-" if math.copysign(1.0, x) < 0 else ""
    if math.isnan(x):
        return sign + "nan"
    if math.isinf(x):
        return sign + "inf"
    if x == 0:
        return sign + "0"
    digits, power = shortest(abs(x), bits)
    k, n = len(digits), len(digits) + power
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * -n + digits
    return sign + digits[0] + ("." + digits[1:] if k > 1 else "") + f"e{'-' if n - 1 < 0 else '+'}{abs(n - 1)}"

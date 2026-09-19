"""An independent Python reference for compact_even, checking the whole buffer and not only the selected prefix.

The in-process check inside case.hpp compares an arm against a sequential C++ collector that shares its compiler
and its flags. This file shares neither, so a mistake common to both C++ paths still fails here. Python integers
are unbounded, so the wrapping multiply the fill performs is written out with an explicit mask rather than left to
the language.

The tail matters as much as the prefix. CAIRN's compact leaves everything beyond the returned count unchanged, so
expected returns the whole buffer the harness should see: the stable selected prefix followed by the TAIL value
case.hpp fills out with before every pass. TAIL is odd and every selected element is even, so no written element
can be mistaken for an untouched one.
"""

MASK = (1 << 64) - 1
STRIDE = 2654435761
TAIL = (1 << 64) - 1


def source(n: int) -> list[int]:
    """The fill, in wrapping u64: x[i] = i * 2654435761 + 1."""
    return [(i * STRIDE + 1) & MASK for i in range(n)]


def expected(n: int) -> dict:
    """The count and the whole output buffer: the stable even elements, then the untouched tail."""
    kept = [v for v in source(n) if v % 2 == 0]
    return {"used": len(kept), "out": kept + [TAIL] * (n - len(kept))}


def agrees(n: int, result) -> bool:
    """True when the count and every element of the buffer, tail included, are the collector's."""
    want = expected(n)
    return int(result["used"]) == want["used"] and [int(v) for v in result["out"]] == want["out"]

"""The numerical policy: when a candidate's float result agrees with the reference's.

Host validation (verify/validation.py), the device tests it generates (verify/device_validation.py), `cairn test`'s
replay of kept regressions and the implementation session's admission compare float results here and nowhere else.
The rules are written once, in `RULES`, as CAIRN expressions over the reference's value `r` and the candidate's `c`.
The host evaluates that text as Python, and the device generator writes it into its tests as a CAIRN function, which
the compiler lowers to C++. `tests/verification/test_agreement.py` drives both over one table of pairs under clang++
and g++ and requires the same verdict from each.

Both values are first widened exactly to f64: an f32 widens exactly, and a storage float (f16, bf16, f8e4m3, f8e5m2)
widens exactly to f32 and then to f64. Every rule computes in f64, one rounding per operation, whatever the width
of the values compared. The first rule whose condition holds decides:

1. A NaN agrees with any NaN, whatever its sign and payload, and with nothing else.
2. Two equal values agree when their bits are the same; -0.0 and 0.0 agree only when a tolerance is given.
3. An infinity agrees only with itself, which rule 2 decided.
4. Otherwise `|r - c| <= absolute + relative * |r|`. The relative part scales the reference's magnitude, never the
   candidate's, so a wrong result cannot widen its own bound.

`VERSION` names this policy and `DIGEST` pins its rules, so a validation made under another policy says so.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any

from ..compiler.syntax.tree import FLOAT, STORAGE

VERSION = "cairn.agreement/1"
# Each rule: its condition, then its verdict, in CAIRN over r and c widened to f64, the tolerance's `absolute` and
# `relative` parts, and `given`, whether either is above zero.
RULES = (
    ("r != r || c != c", "r != r && c != c"),
    ("r == c", "to_bits(r) == to_bits(c) || given"),
    ("r - r != r - r || c - c != c - c", "false"),
    ("true", "abs(r - c) <= absolute + relative * abs(r)"),
)
DIGEST = hashlib.sha256(json.dumps([VERSION, RULES, "widened exactly to f64"]).encode()).hexdigest()
COMPARED = FLOAT | set(STORAGE)  # the types these rules compare; every other value agrees only with itself


def python(text: str) -> Any:
    """A rule's CAIRN text as Python, compiled once: the operators it uses mean the same over Python floats."""
    spelled = text.replace("||", " or ").replace("&&", " and ").replace("true", "True").replace("false", "False")
    return compile(spelled, "<agreement>", "eval")


COMPILED = tuple((python(condition), python(verdict)) for condition, verdict in RULES)


def bits(x: float) -> int:
    """`to_bits` of an f64."""
    return struct.unpack("<Q", struct.pack("<d", x))[0]


def given(tolerance: dict[str, float]) -> bool:
    return any(v > 0 for v in tolerance.values())


def agrees(r: float, c: float, tolerance: dict[str, float]) -> bool:
    """The host's verdict on the reference's value `r` and the candidate's `c`, each already widened to f64."""
    names = {"r": r, "c": c, "absolute": float(tolerance.get("absolute", 0.0)),
             "relative": float(tolerance.get("relative", 0.0)), "given": given(tolerance), "to_bits": bits, "abs": abs}  # fmt: skip
    for condition, verdict in COMPILED:
        if eval(condition, {"__builtins__": {}}, names):
            return bool(eval(verdict, {"__builtins__": {}}, names))
    raise AssertionError("the last rule's condition is true")


def same(ty: str, reference: Any, candidate: Any, tolerance: dict[str, float]) -> bool:
    """Whether two results of type `ty` agree, as verify/isolated_calls.py encodes them: a float as the hexadecimal
    text of its value widened to f64, anything else as itself."""
    if ty not in COMPARED or reference == candidate:  # identical text is identical bits, or two NaNs
        return reference == candidate
    return agrees(float.fromhex(reference), float.fromhex(candidate), tolerance)


def single(value: float) -> int:
    """The f32 pattern `value` rounds to, as a C++ conversion rounds it: past f32's range, an infinity."""
    try:
        return struct.unpack("<I", struct.pack("<f", value))[0]
    except OverflowError:
        return 0xFF800000 if value < 0 else 0x7F800000


def exact(ty: str, value: float) -> str:
    """A CAIRN expression of float type `ty` that is exactly `value` rounded to `ty` as a C++ conversion rounds it:
    its shortest decimal digits where they say it, its bits for a NaN, an infinity and -0.0, which they cannot."""
    rounded = value if ty == "f64" else struct.unpack("<f", struct.pack("<I", single(value)))[0]
    if rounded == rounded and abs(rounded) != float("inf") and not (value == 0 and math.copysign(1.0, value) < 0):
        return f"{ty}({value!r})"
    return f"from_bits[f64](0x{bits(value):016x})" if ty == "f64" else f"from_bits[f32](0x{single(value):08x})"


def widened(expr: str, ty: str) -> str:
    """`expr`, of float type `ty`, as the f64 the rules compare: exact for every float CAIRN has."""
    return expr if ty == "f64" else f"f64({expr})" if ty == "f32" else f"f64(f32({expr}))"


def helper(name: str, tolerance: dict[str, float]) -> str:
    """The rules as a CAIRN function `name(r:f64, c:f64) -> bool` under `tolerance`, its parts written as their bits
    so the generated code compares against exactly the host's numbers."""
    parts = {k: float(tolerance.get(k, 0.0)) for k in ("absolute", "relative")}
    lines = [f"fn {name}(r:f64, c:f64) -> bool {{  // {VERSION}, verify/agreement.py"]
    lines += [f"  let {k}:f64 = from_bits[f64](0x{bits(v):016x});  // {v!r}" for k, v in parts.items()]
    lines.append(f"  let given:bool = {'true' if given(tolerance) else 'false'};")
    for condition, verdict in RULES:
        lines.append(f"  return {verdict};" if condition == "true" else f"  if {condition} {{ return {verdict}; }}")
    return "\n".join([*lines, "}"]) + "\n"


def stated(tolerance: dict[str, float]) -> dict[str, Any]:
    """The policy a record was made under: its version and digest, the tolerance, and what the relative part
    scales."""
    return {"version": VERSION, "sha256": DIGEST, "tolerance": dict(tolerance), "relative_to": "the reference",
            "computes_in": "f64"}  # fmt: skip

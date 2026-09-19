"""Constants fold to one literal of their type: exact arithmetic over literals and other constants."""

from __future__ import annotations

import math
import operator
import struct
from typing import TYPE_CHECKING, Any

from .syntax import BITS, FLOAT, INT, NUMERIC, SCALAR, SIGNED, Expr, Type, fail

if TYPE_CHECKING:
    from .checking import Checker


def quotient(a: Any, b: Any) -> Any:
    """Division as the language defines it: exact for floats, toward zero for integers."""
    return (
        a / b if isinstance(a, float) or isinstance(b, float) else abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
    )


FOLD = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": quotient, "&": operator.and_, "|": operator.or_,
        "^": operator.xor, "==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt,
        ">=": operator.ge, "&&": lambda a, b: a and b, "||": lambda a, b: a or b,
        "%": lambda a, b: math.fmod(a, b) if isinstance(a, float) or isinstance(b, float) else a - b * quotient(a, b)}  # fmt: skip


def constant(c: Checker, name: str, pending: list[str]) -> Any:
    """A constant folds to one literal of its type: exact arithmetic over literals and other constants."""
    declared, written = c.p.consts[name]
    if name not in c.folded:
        if name in pending:
            fail("E-CONST", f"{name} is defined in terms of itself.", written)
        with c.within(c.p.modules.get(name, "")):
            ty = c.resolve(declared, written)
            if ty.name not in SCALAR or ty.mode != "value":
                fail("E-CONST", "A constant is a scalar.", written)
            value = fold(c, written, ty, [*pending, name])
            if isinstance(value, float) and not math.isfinite(value):
                fail("E-CONST", "A constant is finite.", written)
            text = str(value).lower() if isinstance(value, bool) else repr(abs(value))
            tag = "bool" if isinstance(value, bool) else "float" if isinstance(value, float) else "int"
            literal = Expr(tag, text, [], written.line, written.col)
            if not isinstance(value, bool) and (value < 0 or (value == 0 and math.copysign(1, value) < 0)):
                literal = Expr("unary", "-", [literal], written.line, written.col)
            c.expr(literal, ty)  # The literal's own range check says whether the result fits.
        c.p.consts[name], c.folded[name] = (declared, literal), value
    return c.folded[name]


def single(value: Any, narrow: bool = True) -> Any:
    """What an f32 holds: the machine rounds every literal, conversion and operation once, and so does folding."""
    return struct.unpack("f", struct.pack("f", value))[0] if narrow and isinstance(value, float) else value


def fold(c: Checker, e: Expr, ty: Type, pending: list[str]) -> Any:
    f32 = ty.name == "f32"
    if e.tag in {"int", "float", "bool"}:
        if e.tag == "bool":
            return e.val == "true"
        return single(float(e.val), f32) if e.tag == "float" or ty.name in FLOAT else int(e.val)
    if e.tag == "name":
        const = c.qualify(e.val, c.p.consts, node=e)
        if const is None:
            fail("E-CONST", "A constant is made of literals and other constants.", e)
        return single(constant(c, const, pending), f32)
    inner = Type("i64") if e.tag == "call" and e.val in NUMERIC else ty  # A conversion's operand has its own type:
    args = [fold(c, a, inner, pending) for a in e.args]  # nothing is expected of it, so its floats are f64.
    numbers = all(not isinstance(a, bool) for a in args)
    if e.tag == "call" and e.val in NUMERIC and len(args) == 1 and numbers:  # u32(x), f64(n): checked like any literal.
        low, high = (
            (-(2 ** (BITS[e.val] - 1)), 2 ** (BITS[e.val] - 1) - 1)
            if e.val in SIGNED
            else (0, 2 ** BITS.get(e.val, 0) - 1)
        )
        if e.val in INT and not low <= int(args[0]) <= high:
            fail("E-CONST", f"{args[0]} does not fit {e.val}.", e)
        return single(float(args[0]), e.val == "f32") if e.val in FLOAT else int(args[0])
    if e.tag == "unary" and ((e.val == "-" and numbers) or (e.val == "!" and not numbers)):
        return -args[0] if e.val == "-" else not args[0]
    logical = e.tag == "binary" and e.val in {"&&", "||"} and not numbers
    if e.tag != "binary" or e.val not in FOLD or not (numbers or logical or e.val in {"==", "!="}):
        fail("E-CONST", "A constant is made of literals, other constants, arithmetic, comparison and conversion.", e)
    if e.val in {"/", "%"} and args[1] == 0:
        fail("E-CONST", "A constant does not divide by zero.", e)
    if e.val in {"&", "|", "^"} and not all(isinstance(a, int) for a in args):
        fail("E-CONST", "Bit operations fold integers.", e)
    return single(FOLD[e.val](*args), f32)

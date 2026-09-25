"""The concrete evaluator: an input replayed with exact-width arithmetic, independently of the solver.

A counterexample is exposed only when this replay separates the two functions the way the solver
said it would, and the replay traps exactly where the emitted guards would.
"""

from __future__ import annotations

import math
import operator
from typing import Any

from ..compiler.cairnc import SIGNED, WIDTH, Expr, Function, Type
from ..compiler.syntax.tree import FLOAT, NUMERIC, VOID, is_view
from .scalar_values import (
    MAX_REPLAY,
    MAX_UNROLL,
    ConcreteTrap,
    Propagate,
    Source,
    Unsupported,
    admissible,
    bounds,
    integral,
    owned,
    rounded,
)

COMPARED = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
BITS = {"&": operator.and_, "|": operator.or_, "^": operator.xor}
ARITHMETIC = {"+": operator.add, "-": operator.sub, "*": operator.mul}
WRAPPED = {"add_wrap": operator.add, "sub_wrap": operator.sub, "mul_wrap": operator.mul}


class Zeroed:
    """Zero-initialized storage of any length, held only where it was written: `Buf[T](n)` for a symbolic n."""

    def __init__(self, count: int, zero):
        self.count, self.zero = count, zero
        self.written: dict[int, Any] = {}

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, i: int):
        return self.written[i] if i in self.written else self.zero()

    def __setitem__(self, i: int, value):
        self.written[i] = value


class Region:
    """Concrete storage a view denotes: a window into one store, so a write is seen through every view of it."""

    data: list | Zeroed
    offset: int
    extent: int

    def __init__(self, data: list | Zeroed | Region, offset: int = 0, extent: int | None = None):
        self.data = data.data if isinstance(data, Region) else data
        self.offset = offset + (data.offset if isinstance(data, Region) else 0)
        self.extent = size(self.data) - self.offset if extent is None else extent

    def part(self, lo: int, extent: int) -> Region:
        return Region(self, lo, extent)

    def contents(self) -> list:
        return [self.data[self.offset + i] for i in range(self.extent)]

    def __getitem__(self, i: int):
        return self.data[self.offset + i]

    def __setitem__(self, i: int, value):
        self.data[self.offset + i] = value


def size(x) -> int:
    """How many elements storage or an inline array holds; a symbolic length may exceed what `len` can answer."""
    return x.extent if isinstance(x, Region) else x.count if isinstance(x, Zeroed) else len(x)


class Concrete:
    """Independent operational semantics, used to replay SMT counterexamples.

    Uses Python values and explicit representability tests, not the SMT
    formulas. Shares the parser and type annotations with the compiler.
    """

    def __init__(self, source: Source):
        self.source = source
        self.functions = source.functions

    def checked(self, value, ty):
        lo, hi = bounds(ty)
        if not lo <= value <= hi:
            raise ConcreteTrap("integer-overflow-or-conversion")
        return value

    def zeros(self, ty: Type):
        array = self.source.elements(ty)
        layout = self.source.layout(ty)
        if array is not None:
            return [self.zeros(array[0]) for _ in range(array[1])]
        if isinstance(layout, list):
            return {n: self.zeros(t) for n, t in layout}
        if isinstance(layout, dict):
            name, payload = next(iter(layout.items()))
            return {"variant": name, **({"value": self.zeros(payload)} if payload is not None else {})}
        return False if ty.name == "bool" else 0.0 if ty.name in FLOAT else 0

    def divided(self, a: float, b: float, ty: str) -> float:
        if b == 0.0:
            if a != a or a == 0.0:
                return math.nan
            return math.copysign(math.inf, math.copysign(1.0, a) * math.copysign(1.0, b))
        return rounded(a / b, ty)

    def convert(self, value, origin: str, target: str):
        if target in FLOAT:
            return rounded(value, target) if origin in FLOAT else integral(value, target)
        if origin not in FLOAT:
            return self.checked(value, target)
        width, signed = WIDTH[target], target in SIGNED
        limit = float(1 << (width - signed))
        low = -limit if signed else 0.0
        if not ((value > rounded(low - 1.0, origin) or value >= low) and value < limit):
            raise ConcreteTrap("invalid-float-conversion")
        return int(value)

    def expr(self, e, env, stack):
        if e.tag == "name" and isinstance(e.ref, int | Expr):
            return self.expr(Expr("int", str(e.ref), ty=e.ty) if isinstance(e.ref, int) else e.ref, env, stack)
        if e.tag == "name":
            return env[e.val]
        if e.tag == "int":
            return integral(int(e.val), e.ty.name) if e.ty.name in FLOAT else int(e.val)
        if e.tag == "float":
            return rounded(float(e.val), e.ty.name)
        if e.tag == "bool":
            return e.val == "true"
        ty = e.ty
        if e.tag == "field":
            if isinstance(e.ref, tuple):
                return {"variant": e.val.rsplit(".", 1)[-1]}
            return self.expr(e.args[0], env, stack)[e.val]
        if e.tag == "index":
            base = self.expr(e.args[0], env, stack)
            i = self.expr(e.args[1], env, stack)
            if not 0 <= i < size(base):
                raise ConcreteTrap("out-of-bounds")
            return base[i]
        if e.tag == "slice":
            base, lo, hi = (self.expr(a, env, stack) for a in e.args)
            want = self.expr(e.ref, env, stack) if isinstance(e.ref, Expr) else e.ref
            if lo > hi or hi > size(base) or (str(want).isdigit() and hi - lo != int(want)):
                raise ConcreteTrap("invalid-part")
            return Region(base).part(lo, hi - lo)
        if e.tag == "try":
            return self.attempt(e, env, stack)
        if e.tag == "unary":
            a = self.expr(e.args[0], env, stack)
            if e.val == "!":
                return not a
            if e.val == "~":
                return (~a) & ((1 << WIDTH[ty.name]) - 1)
            return -a if ty.name in FLOAT else self.checked(-a, ty.name)
        if e.tag == "binary":
            return self.operate(e, env, stack)
        if e.tag == "call":
            return self.dispatch(e, env, stack)
        raise Unsupported("Concrete replay encountered an unsupported expression.")

    def operate(self, e, env, stack):
        a, op, ty = self.expr(e.args[0], env, stack), e.val, e.ty
        if op == "&&":
            return a and self.expr(e.args[1], env, stack)
        if op == "||":
            return a or self.expr(e.args[1], env, stack)
        b = self.expr(e.args[1], env, stack)
        if op in {"==", "!="}:
            if isinstance(a, dict):  # A tag-only enum compares as the emitted discriminant does, tag against tag.
                equal = (a.get("variant"), a.get("tag")) == (b.get("variant"), b.get("tag"))
            else:
                equal = a == b
            return equal if op == "==" else not equal
        if op in COMPARED:
            return COMPARED[op](a, b)
        if op in BITS:
            return BITS[op](a, b)
        if op in ARITHMETIC:
            value = ARITHMETIC[op](a, b)
            return rounded(value, ty.name) if ty.name in FLOAT else self.checked(value, ty.name)
        if ty.name in FLOAT:
            return self.divided(a, b, ty.name)
        if b == 0 or (ty.name in SIGNED and a == bounds(ty.name)[0] and b == -1):
            raise ConcreteTrap("invalid-division")
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        return q if op == "/" else a - q * b

    def dispatch(self, e, env, stack):
        n = e.val
        if isinstance(e.ref, tuple) and e.ref[0] == "variant":
            name = n.rsplit(".", 1)[-1]
            return {"variant": name, **({"value": self.expr(e.args[0], env, stack)} if e.args else {})}
        xs = [self.expr(a, env, stack) for a in e.args]
        if isinstance(e.ref, tuple) and e.ref[0] == "record":
            return {f: x for (f, _), x in zip(self.source.layout(e.ty), xs, strict=True)}
        if n in NUMERIC:
            return self.convert(xs[0], e.args[0].ty.name, n)
        if n in {"min", "max"}:
            return (min if n == "min" else max)(*xs)
        if n in WRAPPED:
            return WRAPPED[n](*xs) % (1 << WIDTH[e.ty.name])
        if n in {"shl_wrap", "shr"}:
            a, b = xs
            if not 0 <= b < WIDTH[e.ty.name]:
                raise ConcreteTrap("invalid-shift")
            return ((a << b) % (1 << WIDTH[e.ty.name])) if n == "shl_wrap" else (a >> b)
        if n == "Array":
            return self.zeros(e.ty)
        if n == "len":
            return size(xs[0])
        if n == "Buf":  # Zeroed storage of any length; a failed allocation is outside this model.
            return Region(Zeroed(xs[0], lambda: self.zeros(e.ty.args[0])))
        if n == "take":  # The storage moves out; what is left is empty, as a moved-from cr::Buf is.
            held = xs[0]
            self.store(e.args[0], env, stack, Region([]) if isinstance(held, Region) else self.zeros(e.ty))
            return held
        if n == "swap":
            self.store(e.args[0], env, stack, xs[1])
            self.store(e.args[1], env, stack, xs[0])
            return None
        n = e.ref.name if isinstance(e.ref, Function) else n
        if n in self.functions:
            lent = [(x, t) for x, (_, t) in zip(xs, self.functions[n].params, strict=True) if isinstance(x, Region)]
            for i, (a, ta) in enumerate(lent):  # `cr::disjoint` at the callee's entry, on windows into one storage.
                for b, tb in lent[i + 1 :]:
                    shared = a.data is b.data and "rw" in (ta.mode, tb.mode) and a.extent and b.extent
                    if shared and a.offset < b.offset + b.extent and b.offset < a.offset + a.extent:
                        raise ConcreteTrap("overlapping-views")
            value, written = self.invoke(n, xs, stack)
            lent = [(a, t) for a, (_, t) in zip(e.args, self.functions[n].params, strict=True) if t.mode == "rw"]
            for (a, t), final in zip(lent, written, strict=True):
                if not is_view(t):  # A view wrote through its storage; a borrowed value is written back.
                    self.store(a, env, stack, final)
            return value
        raise Unsupported("Concrete replay encountered an unsupported call.")

    def attempt(self, e, env, stack):
        value = self.expr(e.args[0], env, stack)
        ok, _, _, carrier = e.ref
        if value.get("variant") == ok:
            return value.get("value")
        if "variant" not in value:  # The emitted `try` would carry payload bits this replay does not hold.
            raise Unsupported("A try over a tag outside the declared variants is not replayed.")
        raise Propagate({"variant": carrier, **({"value": value["value"]} if "value" in value else {})})

    def invoke(self, name, args, stack=()):
        """(returned value, what each rw parameter holds afterwards)."""
        if name in stack:
            raise Unsupported("Concrete replay does not recurse.")
        f = self.functions[name]
        env = {n: a for (n, _), a in zip(f.params, args, strict=True)}
        try:
            signal, value = self.block(f.body, env, (*stack, name))
        except Propagate as p:
            signal, value = "return", p.value
        if signal != "return" and f.ret != VOID:
            raise Unsupported("Concrete function did not return.")
        return value, [env[n] for n, t in f.params if t.mode == "rw"]

    def store(self, target, env, stack, value):
        if target.tag == "name":
            env[target.val] = value
            return
        base = self.expr(target.args[0], env, stack)
        if target.tag == "slice":
            return  # A part is a window: what was written through it is already in that storage.
        if target.tag == "field":
            self.store(target.args[0], env, stack, {**base, target.val: value})
            return
        i = self.expr(target.args[1], env, stack)
        if not 0 <= i < size(base):
            raise ConcreteTrap("out-of-bounds")
        if isinstance(base, Region):  # Shared storage: the write is seen through every view of it.
            base[i] = value
        else:
            self.store(target.args[0], env, stack, [value if k == i else x for k, x in enumerate(base)])

    def block(self, body, env, stack):
        """("return", value), ("break"|"continue", None) or (None, None) when control falls through."""
        old = set(env)
        for s in body:
            signal = None
            if s.tag in {"let", "reg"}:
                env[s.name] = self.expr(s.exprs[0], env, stack)
            elif s.tag == "assign":
                self.store(s.exprs[0], env, stack, self.expr(s.exprs[1], env, stack))
            elif s.tag in {"stack", "buffer"}:
                env[s.name] = Region(Zeroed(self.expr(s.exprs[0], env, stack), lambda item=s.ty: self.zeros(item)))
            elif s.tag in {"compact", "reduce"}:
                self.fold(s, env, stack)
            elif s.tag == "expr":
                self.expr(s.exprs[0], env, stack)
            elif s.tag == "return":
                return "return", (self.expr(s.exprs[0], env, stack) if s.exprs else None)
            elif s.tag in {"break", "continue"}:
                return s.tag, None
            elif s.tag == "block":
                signal, value = self.block(s.body, env, stack)
            elif s.tag == "if":
                branch = s.body if self.expr(s.exprs[0], env, stack) else s.other
                signal, value = self.block(branch, env, stack)
            elif s.tag == "match":
                subject = self.expr(s.exprs[0], env, stack)
                arm = next((a for a, v in zip(s.arms, s.ref, strict=True) if v == subject.get("variant")), None)
                if arm is None:  # The emitted switch sends a tag no case names to `default: cr::trap()`.
                    raise ConcreteTrap("unmatched-tag")
                if arm.binder:
                    env[arm.binder] = subject["value"]
                signal, value = self.block(arm.body, env, stack)
            elif s.tag in {"while", "for"}:
                signal, value = self.iterate(s, env, stack)
            else:
                raise Unsupported("Concrete replay does not execute this statement.")
            if signal == "return":
                return signal, value
            if signal in {"break", "continue"}:
                return signal, None
        for k in set(env) - old:
            del env[k]
        return None, None

    def iterate(self, s, env, stack):
        steps, limit = 0, 0
        if s.tag == "for":
            env[s.name] = self.expr(s.exprs[0], env, stack)
            limit = self.expr(s.exprs[1], env, stack)
        while steps < MAX_UNROLL:
            if not (env[s.name] < limit if s.tag == "for" else self.expr(s.exprs[0], env, stack)):
                break
            signal, value = self.block(s.body, env, stack)
            if signal == "return":
                return signal, value
            if signal == "break":
                break
            if s.tag == "for":
                env[s.name] += 1
            steps += 1
        else:
            raise Unsupported("Concrete replay exceeded the loop unrolling budget.")
        if s.tag == "for":
            del env[s.name]
        return None, None

    def fold(self, s, env, stack):
        """`reduce` and `compact` as the host emits them: one in-order pass, within the same budget."""
        collect = s.tag == "compact"
        count = self.expr(s.exprs[1] if collect else s.exprs[0], env, stack)
        if count > MAX_UNROLL:
            raise Unsupported("Concrete replay exceeded the loop unrolling budget.")
        out = self.expr(s.exprs[0], env, stack) if collect else None
        lo, hi = bounds(s.ty.name)
        used = 0 if collect else {"mul_wrap": 1, "&": hi, "min": hi, "max": lo}.get(s.op, 0)
        for i in range(count):
            env[s.binder] = i
            if collect:
                if self.expr(s.exprs[2], env, stack):
                    out[used] = self.expr(s.exprs[3], env, stack)
                    used += 1
            else:
                v = self.expr(s.exprs[1], env, stack)
                if s.op == "+":
                    used = self.checked(used + v, s.ty.name)
                elif s.op in WRAPPED:
                    used = WRAPPED[s.op](used, v) % (1 << WIDTH[s.ty.name])
                elif s.op in BITS:
                    used = BITS[s.op](used, v)
                else:
                    used = (min if s.op == "min" else max)(used, v)
        env.pop(s.binder, None)  # An empty pass never binds it.
        env[s.name] = used

    def outcome(self, name, args: dict[str, Any]):
        f = self.functions[name]
        if set(args) != {n for n, _ in f.params}:
            raise ValueError("Wrong argument names.")
        for n, t in f.params:
            if not admissible(self.source, t, args[n]):
                raise ValueError(f"Input {n} is not a value of {t.display()}.")
            if is_view(t) and len(args[n]) != (int(t.extent) if t.extent.isdigit() else args.get(t.extent)):
                raise ValueError(f"Input {n} does not hold the {t.extent} elements its view lends.")
        held = {n: Region(list(args[n])) if is_view(t) or owned(t) else args[n] for n, t in f.params}
        try:
            value, written = self.invoke(name, [held[n] for n, _ in f.params])
        except ConcreteTrap as e:
            return {"defined": False, "trap": str(e)}
        rw = [n for n, t in f.params if t.mode == "rw"]
        final = {n: x.contents() if isinstance(x, Region) else x for n, x in zip(rw, written, strict=True)}
        if isinstance(value, Region) and value.extent > MAX_REPLAY:
            raise Unsupported(f"A returned owner past {MAX_REPLAY} elements cannot be replayed.")
        result = value.contents() if isinstance(value, Region) else value  # An owner comes back as its elements.
        return {"defined": True, "return": result, "written": final}

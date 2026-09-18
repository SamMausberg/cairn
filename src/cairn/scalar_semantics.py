"""Exact-width value semantics and solver-backed equivalence for a source fragment.

Supported: bool, fixed integers, f32/f64, records, tag-only enums and payload
sums with match and try, fixed local arrays (`stack x:T[N]`, `Array[T, N]`),
locals, assignment to a name/field/element, if/else, bounded for/while with
break/continue, early returns, acyclic calls of value parameters (a `ro` single
borrow reads as its value), checked/wrapping arithmetic and checked conversions.
Excluded: heap owners, views as parameters, `rw` borrows, recursion, tasks,
lanes, closures, dyn, atomics, FFI and void results.

A value is flattened into its scalar components: a record is its fields, a sum
is the emitted u32 tag beside every variant payload, an array is its elements.
Only a sum's active payload is compared, so inactive storage is not observed,
and inputs are quantified over well-formed values -- every tag names a declared
variant, which is what the emitted entry guard admits.

Every guard aborts and every abort is one observation, so "both trap" is equal
behaviour. Floats use Z3's FloatingPoint theory: one round-to-nearest-even per
operation, matching the `-ffp-contract=off -fno-fast-math` contract, IEEE
comparison predicates, and `cr::truncate` modeled as the header writes it. A
result is compared as an IEEE datum, so +0.0 and -0.0 differ; but the theory has
a single NaN, so a reachable NaN result is reported unknown rather than equal,
and a caller who does not care excludes NaN with a precondition.

Loops are unrolled to MAX_UNROLL iterations; whatever would still be running is
a residual obligation the solver must refute, so exceeding the budget is
unknown, never success.

This translator and Z3 are trusted. No result is a Lean-kernel proof or a
verification of the C++ backend. Unsupported syntax returns unknown.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cairnc import SIGNED, WIDTH, Diagnostic, Expr, Function, Parser, Stmt, Type, compile_program
from .smt_bridge import Solver, SolverUnavailable
from .syntax import BOOL, FLOAT, NUMERIC, VOID

PROFILE = "cairn-value-bv-fp/2"
MAX_PATHS = 256
MAX_TERMS = 12000
MAX_SOURCE_BYTES = 64000
MAX_UNROLL = 16  # Iterations of one loop; a path that would need more is a residual obligation.
MAX_LEAVES = 64  # Scalar components of one value.
TAG = Type("u32")  # The emitted discriminant of an enum or sum.
FORMATS = {"f32": (8, 24, "<f", "<I"), "f64": (11, 53, "<d", "<Q")}


class Unsupported(Exception):
    pass


class ConcreteTrap(Exception):
    pass


class Propagate(Exception):
    """A concrete `try` leaving its function with the failure it found."""

    def __init__(self, value: Any):
        super().__init__("try")
        self.value = value


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def bounds(ty: str) -> tuple[int, int]:
    w = WIDTH[ty]
    return (-(1 << (w - 1)), (1 << (w - 1)) - 1) if ty in SIGNED else (0, (1 << w) - 1)


def rounded(value: float, ty: str) -> float:
    """What the named format holds: round to nearest even, overflowing to infinity."""
    if ty == "f64":
        return value
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def integral(value: int, ty: str) -> float:
    """An exact integer converted once into the target format, round to nearest even.

    Python would round twice through binary64, which for f32 is not always the value C++ produces.
    """
    digits = FORMATS[ty][1]
    m, e = abs(value), abs(value).bit_length()
    if e > digits:  # Keep `digits` significant bits and round what is below them to nearest even.
        shift = e - digits
        q, rest, half = m >> shift, m & ((1 << shift) - 1), 1 << (shift - 1)
        m = (q + (rest > half or (rest == half and q & 1))) << shift
    return math.copysign(float(m), value)


def encoded(value: float, ty: str) -> int:
    _, _, fmt, raw = FORMATS[ty]
    return struct.unpack(raw, struct.pack(fmt, rounded(value, ty)))[0]


def decoded(pattern: int, ty: str) -> float:
    _, _, fmt, raw = FORMATS[ty]
    return struct.unpack(fmt, struct.pack(raw, pattern))[0]


def constant(value: int, ty: str) -> str:
    return f"(_ bv{value % (1 << WIDTH[ty])} {WIDTH[ty]})"


def real(value: float, ty: str) -> str:
    """A float literal as the exact bit pattern the target format holds."""
    eb, sb, _, _ = FORMATS[ty]
    return f"((_ to_fp {eb} {sb}) #b{encoded(value, ty):0{eb + sb}b})"


def sort(ty: Type) -> str:
    if ty.name == "bool":
        return "Bool"
    if ty.name in FLOAT:
        return "Float" + str(sum(FORMATS[ty.name][:2]))
    return f"(_ BitVec {WIDTH[ty.name]})"


def declared(ty: Type) -> str:
    """The sort an input is declared with: a float arrives as the bit pattern its model reports."""
    return f"(_ BitVec {sum(FORMATS[ty.name][:2])})" if ty.name in FLOAT else sort(ty)


def conj(*parts: str) -> str:
    if "false" in parts:
        return "false"
    xs = list(dict.fromkeys(x for x in parts if x != "true"))
    return "true" if not xs else xs[0] if len(xs) == 1 else "(and " + " ".join(xs) + ")"


def disj(*parts: str) -> str:
    if "true" in parts:
        return "true"
    xs = list(dict.fromkeys(x for x in parts if x != "false"))
    return "false" if not xs else xs[0] if len(xs) == 1 else "(or " + " ".join(xs) + ")"


def neg(p: str) -> str:
    return {"true": "false", "false": "true"}.get(p, f"(not {p})")


def same(a: str, b: str) -> str:
    return "true" if a == b else f"(= {a} {b})"


def ite(c: str, a: str, b: str) -> str:
    if a == b:
        return a
    if c == "true":
        return a
    if c == "false":
        return b
    return f"(ite {c} {a} {b})"


def extend(value: str, source_width: int, dest_width: int, signed: bool) -> str:
    if source_width == dest_width:
        return value
    if source_width > dest_width:
        return f"((_ extract {dest_width - 1} 0) {value})"
    return f"((_ {'sign_extend' if signed else 'zero_extend'} {dest_width - source_width}) {value})"


@dataclass(frozen=True)
class Term:
    """A value: one SMT term per scalar component, and whether evaluation continued."""

    ty: Type
    parts: tuple[str, ...]
    defined: str = "true"

    @property
    def value(self) -> str:
        return self.parts[0]


@dataclass(frozen=True)
class Frame:
    """What an expression needs besides its environment: its call stack, where a `try` returns, its path."""

    stack: tuple[str, ...]
    returns: list
    path: str = "true"
    top: bool = False  # A `try` is modeled only as the whole right-hand side of a statement.

    def at(self, path: str, top: bool = False) -> Frame:
        return Frame(self.stack, self.returns, path, top)


class Loop:
    def __init__(self):
        self.breaks: list = []
        self.continues: list = []


class Source:
    """One compiled program: its functions, and the scalar layout of every value type."""

    def __init__(self, text: str):
        if len(text.encode()) > MAX_SOURCE_BYTES:
            raise Unsupported("Scalar source limit exceeded.")
        program, checker, _ = compile_program(text)  # Linked, monomorphized and typed.
        self.functions = {f.name: f for f in program.functions}
        self.types = checker.layouts
        self.enums = set(program.enums)
        self.cache: dict[Type, tuple[Type, ...]] = {}

    def layout(self, ty: Type) -> Any:
        """The declared fields (a list) or variants (a dict) of a type, or None."""
        return self.types.get(ty.value if ty.mode != "value" else ty)

    def elements(self, ty: Type) -> tuple[Type, int] | None:
        """(element type, count) of an inline array: a fixed local view or an Array value."""
        if ty.mode != "value" and ty.extent.isdigit():
            return (ty.value, int(ty.extent)) if ty.place == "host" else None
        if ty.mode == "value" and ty.name == "Array":
            return ty.args[0], ty.args[1]
        return None

    def leaves(self, ty: Type) -> tuple[Type, ...]:
        """The scalar components of a value, in the order the model stores them."""
        if ty in self.cache:
            return self.cache[ty]
        if ty == VOID:
            return ()
        array = self.elements(ty)
        layout = self.layout(ty)
        if array is not None:
            inner = self.leaves(array[0])
            if len(inner) * array[1] > MAX_LEAVES:
                raise Unsupported("Value layout exceeds the modeled component budget.")
            found = inner * array[1]
        elif ty.mode != "value" and ty.extent:
            raise Unsupported("Array views other than fixed local storage are not modeled.")
        elif ty.name in NUMERIC | {"bool"}:
            found = (ty.value,)
        elif isinstance(layout, list):
            found = tuple(x for _, t in layout for x in self.leaves(t))
        elif isinstance(layout, dict):
            found = (TAG, *(x for t in layout.values() if t for x in self.leaves(t)))
        else:
            raise Unsupported(f"{ty.display()} is outside the modeled value fragment.")
        if len(found) > MAX_LEAVES:
            raise Unsupported("Value layout exceeds the modeled component budget.")
        self.cache[ty] = found
        return found

    def span(self, ty: Type, member: str) -> tuple[int, int]:
        """Where a record field or a sum's payload sits among the components of its value."""
        layout = self.layout(ty)
        if isinstance(layout, list):
            at = 0
            for name, t in layout:
                if name == member:
                    return at, at + len(self.leaves(t))
                at += len(self.leaves(t))
        else:
            at = 1
            for name, t in layout.items():
                width = len(self.leaves(t)) if t else 0
                if name == member:
                    return at, at + width
                at += width
        raise Unsupported(f"Unknown member {member}.")

    def shape(self, ty: Type) -> str:
        """A structural name for a type, so a reference and a candidate cannot disagree about it."""
        array = self.elements(ty)
        layout = self.layout(ty)
        if array is not None:
            return f"[{self.shape(array[0])}; {array[1]}]"
        if isinstance(layout, list):
            return "{" + ", ".join(f"{n}: {self.shape(t)}" for n, t in layout) + "}"
        if isinstance(layout, dict):
            kind = "enum" if ty.value.name in self.enums else "sum"
            return kind + "(" + ", ".join(f"{n}: {self.shape(t) if t else '-'}" for n, t in layout.items()) + ")"
        return ty.value.display()


def observed(src: Source, ty: Type, a: tuple[str, ...], b: tuple[str, ...]) -> str:
    """Do two values look the same to a caller? A sum shows its tag and its active payload only."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        width = len(src.leaves(array[0]))
        return conj(*(observed(src, array[0], a[i : i + width], b[i : i + width])
                      for i in range(0, width * array[1], width)))  # fmt: skip
    if isinstance(layout, list):
        return conj(*(observed(src, t, *(x[slice(*src.span(ty, n))] for x in (a, b))) for n, t in layout))
    if isinstance(layout, dict):
        equal = same(a[0], b[0])
        for i, (n, t) in reversed(list(enumerate(layout.items()))):
            if t is not None:
                arms = (x[slice(*src.span(ty, n))] for x in (a, b))
                equal = ite(same(a[0], constant(i, TAG.name)), conj(equal, observed(src, t, *arms)), equal)
        return equal
    return conj(*(same(x, y) for x, y in zip(a, b, strict=True)))


def undecided(src: Source, ty: Type, parts: tuple[str, ...]) -> str:
    """Can this value hold a NaN, whose payload bits the model does not track?"""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        width = len(src.leaves(array[0]))
        return disj(*(undecided(src, array[0], parts[i : i + width])
                      for i in range(0, width * array[1], width)))  # fmt: skip
    if isinstance(layout, list):
        return disj(*(undecided(src, t, parts[slice(*src.span(ty, n))]) for n, t in layout))
    if isinstance(layout, dict):
        return disj(*(conj(same(parts[0], constant(i, TAG.name)), undecided(src, t, parts[slice(*src.span(ty, n))]))
                      for i, (n, t) in enumerate(layout.items()) if t is not None))  # fmt: skip
    return disj(*(f"(fp.isNaN {p})" for p, t in zip(parts, src.leaves(ty), strict=True) if t.name in FLOAT))


def wellformed(src: Source, ty: Type, parts: tuple[str, ...]) -> str:
    """Every tag inside an input names a declared variant; the entry guard traps on anything else."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        width = len(src.leaves(array[0]))
        return conj(*(wellformed(src, array[0], parts[i : i + width])
                      for i in range(0, width * array[1], width)))  # fmt: skip
    if isinstance(layout, list):
        return conj(*(wellformed(src, t, parts[slice(*src.span(ty, n))]) for n, t in layout))
    if isinstance(layout, dict):
        limit = f"(bvult {parts[0]} {constant(len(layout), TAG.name)})"
        return conj(limit, *(wellformed(src, t, parts[slice(*src.span(ty, n))])
                             for n, t in layout.items() if t is not None))  # fmt: skip
    return "true"


def rebuild(src: Source, ty: Type, values: list) -> Any:
    """A model assignment read back as a CAIRN value; `values` is consumed in component order."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        return [rebuild(src, array[0], values) for _ in range(array[1])]
    if isinstance(layout, list):
        return {n: rebuild(src, t, values) for n, t in layout}
    if isinstance(layout, dict):
        tag = values.pop(0)
        payloads = {n: rebuild(src, t, values) for n, t in layout.items() if t is not None}
        name = list(layout)[tag]
        return {"variant": name, **({"value": payloads[name]} if layout[name] is not None else {})}
    raw = values.pop(0)
    return decoded(raw, ty.name) if ty.name in FLOAT else raw


def admissible(src: Source, ty: Type, value: Any) -> bool:
    """Is a caller-supplied input a value of this type? Malformed inputs are refused, not guessed."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        return isinstance(value, list) and len(value) == array[1] and all(admissible(src, array[0], v) for v in value)
    if isinstance(layout, list):
        names = [n for n, _ in layout]
        return isinstance(value, dict) and sorted(value) == sorted(names) and all(
            admissible(src, t, value[n]) for n, t in layout)  # fmt: skip
    if isinstance(layout, dict):
        if not isinstance(value, dict) or value.get("variant") not in layout:
            return False
        payload = layout[value["variant"]]
        expected = {"variant"} | ({"value"} if payload is not None else set())
        return set(value) == expected and (payload is None or admissible(src, payload, value["value"]))
    if ty.name == "bool":
        return type(value) is bool
    if ty.name in FLOAT:
        return type(value) is float and (value != value or rounded(value, ty.name) == value)
    return type(value) is int and bounds(ty.name)[0] <= value <= bounds(ty.name)[1]


def identical(src: Source, ty: Type, a: Any, b: Any) -> bool:
    """Concrete observation equality; floats compare by bit pattern, so +0 and -0 differ and NaNs do not tie."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        return all(identical(src, array[0], x, y) for x, y in zip(a, b, strict=True))
    if isinstance(layout, list):
        return all(identical(src, t, a[n], b[n]) for n, t in layout)
    if isinstance(layout, dict):
        if a["variant"] != b["variant"]:
            return False
        payload = layout[a["variant"]]
        return payload is None or identical(src, payload, a["value"], b["value"])
    if ty.name in FLOAT:
        return encoded(a, ty.name) == encoded(b, ty.name)
    return a == b


class Formula:
    def __init__(self, params: list[tuple[str, Type]], source: Source):
        self.source = source
        self.declarations: list[str] = []
        self.definitions: list[str] = []
        self.cache: dict[tuple[str, str], str] = {}
        self.inputs: dict[str, Term] = {}
        self.variables: dict[str, str] = {}  # The solver reads a float input as its bit pattern.
        self.floating = False
        for i, (name, ty) in enumerate(params):
            if ty.mode != "value":
                raise Unsupported("Only by-value parameters are modeled; borrows and views are not.")
            parts = []
            for j, leaf in enumerate(source.leaves(ty)):
                n = f"arg_{i}_{j}"
                self.declarations.append(f"(declare-const {n} {declared(leaf)})")
                self.variables[n] = leaf.name
                eb, sb = FORMATS[leaf.name][:2] if leaf.name in FLOAT else (0, 0)
                parts.append(self.bind(f"((_ to_fp {eb} {sb}) {n})", leaf) if leaf.name in FLOAT else n)
            self.track(ty)
            self.inputs[name] = Term(ty, tuple(parts))

    def track(self, ty: Type):
        """Remember that a float exists, so the query names a logic whose theory has one."""
        self.floating = self.floating or any(leaf.name in FLOAT for leaf in self.source.leaves(ty))

    def bind(self, expression: str, ty: Type) -> str:
        if expression in {"true", "false"}:
            return expression
        key = (sort(ty), expression)
        if key in self.cache:
            return self.cache[key]
        if len(self.definitions) >= MAX_TERMS:
            raise Unsupported("Symbolic term budget exceeded.")
        n = f"term_{len(self.definitions)}"
        self.definitions.append(f"(define-fun {n} () {sort(ty)} {expression})")
        self.cache[key] = n
        return n

    def term(self, ty: Type, value: str, defined: str = "true") -> Term:
        self.track(ty)
        return Term(ty, (self.bind(value, ty),), self.bind(defined, BOOL))

    def make(self, ty: Type, parts: tuple[str, ...], defined: str = "true") -> Term:
        self.track(ty)
        return Term(ty, parts, self.bind(defined, BOOL))

    def text(self, assertion: str) -> str:
        logic = "ALL" if self.floating else "QF_BV"
        head = [f"(set-logic {logic})", *self.declarations, *self.definitions]
        return "\n".join([*head, f"(assert {assertion})"]) + "\n"


class Symbolic:
    def __init__(self, formula: Formula, source: Source):
        self.q = formula
        self.source = source
        self.visits = 0
        self.residual = "false"  # Paths that would still need another loop iteration.

    def tick(self):
        self.visits += 1
        if self.visits > MAX_TERMS:
            raise Unsupported("Symbolic evaluation budget exceeded.")

    def zeros(self, ty: Type) -> tuple[str, ...]:
        """Zeroed storage: false, 0, +0.0, and tag 0 with zeroed payloads."""
        self.q.track(ty)
        return tuple(
            "false" if t.name == "bool" else real(0.0, t.name) if t.name in FLOAT else constant(0, t.name)
            for t in self.source.leaves(ty)
        )

    def expr(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        self.tick()
        if e.ty is None:
            raise Unsupported("An untyped expression cannot be modeled.")
        ty = e.ty
        self.source.leaves(ty)  # Refuse an unmodeled type before anything is built from it.
        inner = frame.at(frame.path)  # A `try` is only itself, never an operand of something larger.
        if e.tag == "name" and isinstance(e.ref, int | Expr):  # A static natural or a module constant.
            return self.expr(Expr("int", str(e.ref), ty=ty) if isinstance(e.ref, int) else e.ref, env, inner)
        if e.tag == "name":
            return env[e.val]
        if e.tag == "int":  # An integer literal takes a float type from its peer or its expected type.
            whole = int(e.val)
            return self.q.make(ty, (real(integral(whole, ty.name), ty.name) if ty.name in FLOAT else constant(whole, ty.name),))  # fmt: skip
        if e.tag == "float":
            return self.q.make(ty, (real(float(e.val), ty.name),))
        if e.tag == "bool":
            return self.q.make(ty, (e.val,))
        if e.tag == "field":
            if isinstance(e.ref, tuple):  # A nullary variant written Enum.Name.
                return self.compose(ty, e.ref[1], None)
            base = self.expr(e.args[0], env, inner)
            return self.q.make(ty, base.parts[slice(*self.source.span(base.ty, e.val))], base.defined)
        if e.tag == "index":
            return self.element(e, env, inner)
        if e.tag == "try":
            if not frame.top:
                raise Unsupported("A try inside a larger expression is not modeled; bind it first.")
            return self.propagate(e, env, frame)
        if e.tag == "unary":
            return self.unary(e, env, inner)
        if e.tag == "binary":
            return self.binary(e, env, inner)
        if e.tag == "call":
            return self.call(e, env, inner)
        raise Unsupported(f"Expression form {e.tag!r} is not modeled.")

    def unary(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        a = self.expr(e.args[0], env, frame)
        ty = e.ty
        if e.val == "!":
            return self.q.term(ty, neg(a.value), a.defined)
        if e.val == "~":
            return self.q.term(ty, f"(bvnot {a.value})", a.defined)
        if e.val == "-" and ty.name in FLOAT:
            return self.q.term(ty, f"(fp.neg {a.value})", a.defined)
        if e.val == "-" and ty.name in SIGNED:
            ok = neg(same(a.value, constant(bounds(ty.name)[0], ty.name)))
            return self.q.term(ty, f"(bvneg {a.value})", conj(a.defined, ok))
        raise Unsupported("Unsupported unary operator.")

    def binary(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        op, ty = e.val, e.ty
        a = self.expr(e.args[0], env, frame)
        if op in {"&&", "||"}:
            taken = a.value if op == "&&" else neg(a.value)
            b = self.expr(e.args[1], env, frame.at(conj(frame.path, a.defined, taken)))
            value = conj(a.value, b.value) if op == "&&" else disj(a.value, b.value)
            return self.q.term(ty, value, conj(a.defined, ite(taken, b.defined, "true")))
        b = self.expr(e.args[1], env, frame)
        both = conj(a.defined, b.defined)
        name = a.ty.name
        if op in {"==", "!="}:
            if len(a.parts) != 1:  # The checker allows equality on scalars and tag-only enums only.
                raise Unsupported("Equality on this type is not modeled.")
            v = f"(fp.eq {a.value} {b.value})" if name in FLOAT else same(a.value, b.value)
            return self.q.term(ty, v if op == "==" else neg(v), both)
        if op in {"<", "<=", ">", ">="}:
            if name in FLOAT:
                v = f"(fp.{ {'<': 'lt', '<=': 'leq', '>': 'gt', '>=': 'geq'}[op] } {a.value} {b.value})"
            elif name == "bool":
                ordered = {"<": conj(neg(a.value), b.value), ">": conj(a.value, neg(b.value))}
                v = ordered[op] if op in ordered else neg(ordered[">" if op == "<=" else "<"])
            else:
                suffix = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge"}[op]
                v = f"(bv{'s' if name in SIGNED else 'u'}{suffix} {a.value} {b.value})"
            return self.q.term(ty, v, both)
        if name in FLOAT:
            fn = {"+": "add", "-": "sub", "*": "mul", "/": "div"}[op]
            return self.q.term(ty, f"(fp.{fn} RNE {a.value} {b.value})", both)
        if op in {"&", "|", "^"}:
            return self.q.term(ty, f"({ {'&': 'bvand', '|': 'bvor', '^': 'bvxor'}[op] } {a.value} {b.value})", both)
        if op in {"+", "-", "*"}:
            return self.arithmetic(ty, op, a, b, checked=True)
        if op in {"/", "%"}:
            n = ty.name
            ok = neg(same(b.value, constant(0, n)))
            if n in SIGNED:
                ok = conj(ok, neg(conj(same(a.value, constant(bounds(n)[0], n)), same(b.value, constant(-1, n)))))
            fn = ("bvsdiv" if n in SIGNED else "bvudiv") if op == "/" else ("bvsrem" if n in SIGNED else "bvurem")
            return self.q.term(ty, f"({fn} {a.value} {b.value})", conj(both, ok))
        raise Unsupported("Unsupported binary operator.")

    def arithmetic(self, ty: Type, op: str, a: Term, b: Term, checked: bool) -> Term:
        fn = {"+": "bvadd", "-": "bvsub", "*": "bvmul"}[op]
        value = f"({fn} {a.value} {b.value})"
        ok = conj(a.defined, b.defined)
        if checked:
            w = WIDTH[ty.name]
            signed = ty.name in SIGNED
            wide = f"({fn} {extend(a.value, w, 2 * w, signed)} {extend(b.value, w, 2 * w, signed)})"
            ok = conj(ok, same(wide, extend(value, w, 2 * w, signed)))
        return self.q.term(ty, value, ok)

    def convert(self, ty: Type, a: Term, ok: str) -> Term:
        """An explicit scalar conversion: float targets round, integer targets are range checked."""
        target, origin = ty.name, a.ty.name
        if target in FLOAT:
            eb, sb, _, _ = FORMATS[target]
            if origin in FLOAT:
                return self.q.term(ty, f"((_ to_fp {eb} {sb}) RNE {a.value})", ok)
            kind = "to_fp" if origin in SIGNED else "to_fp_unsigned"
            return self.q.term(ty, f"((_ {kind} {eb} {sb}) RNE {a.value})", ok)
        if origin in FLOAT:  # cr::truncate: toward zero, trapping on NaN or a value the target cannot hold.
            width, signed = WIDTH[target], target in SIGNED
            limit = float(1 << (width - signed))
            low = -limit if signed else 0.0
            inside = conj(
                disj(f"(fp.gt {a.value} {real(low - 1.0, origin)})", f"(fp.geq {a.value} {real(low, origin)})"),
                f"(fp.lt {a.value} {real(limit, origin)})",
            )
            fn = f"(_ fp.to_{'s' if signed else 'u'}bv {width})"
            return self.q.term(ty, f"({fn} RTZ {a.value})", conj(ok, inside))
        ws, wt = WIDTH[origin], WIDTH[target]
        common = max(ws, wt) + 1
        v = extend(a.value, ws, wt, origin in SIGNED)
        before = extend(a.value, ws, common, origin in SIGNED)
        after = extend(v, wt, common, target in SIGNED)
        return self.q.term(ty, v, conj(ok, same(before, after)))

    def call(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        n = e.val
        if isinstance(e.ref, tuple) and e.ref[0] == "variant":
            payload = self.expr(e.args[0], env, frame) if e.args else None
            return self.compose(e.ty, e.ref[1], payload)
        args = [self.expr(a, env, frame) for a in e.args]
        ok = conj(*(a.defined for a in args))
        if isinstance(e.ref, tuple) and e.ref[0] == "record":
            return self.q.make(e.ty, tuple(x for a in args for x in a.parts), ok)
        if n in NUMERIC:
            return self.convert(e.ty, args[0], ok)
        if n in {"add_wrap", "sub_wrap", "mul_wrap"}:
            wrap = {"add_wrap": "+", "sub_wrap": "-", "mul_wrap": "*"}[n]
            return self.arithmetic(e.ty, wrap, args[0], args[1], checked=False)
        if n in {"shl_wrap", "shr"}:
            a, b = args
            width = WIDTH[e.ty.name]
            limit = f"(bvult {b.value} {constant(width, b.ty.name)})"
            shift = extend(b.value, WIDTH[b.ty.name], width, False)
            return self.q.term(e.ty, f"({'bvshl' if n == 'shl_wrap' else 'bvlshr'} {a.value} {shift})", conj(ok, limit))
        if n in {"min", "max"}:
            a, b = args
            cmp = f"(bv{'s' if e.ty.name in SIGNED else 'u'}le {a.value} {b.value})"
            return self.q.term(e.ty, ite(cmp, a.value, b.value) if n == "min" else ite(cmp, b.value, a.value), ok)
        if n == "Array":  # Inline zeroed storage; the heap owners Buf and Dyn are not modeled.
            return self.q.make(e.ty, self.zeros(e.ty))
        n = e.ref.name if isinstance(e.ref, Function) else n  # The callee the checker resolved.
        if n in self.source.functions:
            value = self.invoke(n, [Term(a.ty, a.parts) for a in args], frame.stack)
            return self.q.make(value.ty, value.parts, conj(ok, value.defined))
        raise Unsupported("Call is outside the supported value fragment.")

    def compose(self, ty: Type, index: int, payload: Term | None) -> Term:
        """A sum value: the tag beside every payload, the inactive ones zeroed as the emitter zeroes them."""
        layout = self.source.layout(ty)
        parts = [constant(index, TAG.name)]
        for i, t in enumerate(layout.values()):
            if t is not None:
                parts += list(payload.parts if i == index and payload else self.zeros(t))
        return self.q.make(ty, tuple(parts), payload.defined if payload else "true")

    def element(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        """A guarded read of one element of fixed local storage."""
        base = self.expr(e.args[0], env, frame)
        array = self.source.elements(base.ty)
        if array is None or len(e.args) != 2:
            raise Unsupported("Only fixed local arrays are indexed in this model.")
        item, count = array
        index = self.expr(e.args[1], env, frame)
        leaves = self.source.leaves(item)
        inside = f"(bvult {index.value} {constant(count, index.ty.name)})" if count else "false"
        parts = []
        for k, leaf in enumerate(leaves):
            chosen = base.parts[max(count - 1, 0) * len(leaves) + k] if count else self.zeros(item)[k]
            for i in reversed(range(count - 1)):
                chosen = ite(same(index.value, constant(i, index.ty.name)), base.parts[i * len(leaves) + k], chosen)
            parts.append(self.q.bind(chosen, leaf))
        return self.q.make(item, tuple(parts), conj(base.defined, index.defined, inside))

    def propagate(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        """`try x`: the success payload, or a return of the failure the enclosing result carries."""
        inner = self.expr(e.args[0], env, frame.at(frame.path))
        ok, err, target, carrier = e.ref
        layout = self.source.layout(inner.ty)
        failed = neg(same(inner.parts[0], constant(0, TAG.name)))
        payload = layout[err] and self.q.make(layout[err], inner.parts[slice(*self.source.span(inner.ty, err))])
        index = list(self.source.layout(target)).index(carrier)
        frame.returns.append((conj(frame.path, inner.defined, failed), self.compose(target, index, payload)))
        good = inner.parts[slice(*self.source.span(inner.ty, ok))] if layout[ok] else ()
        return self.q.make(e.ty, good, conj(inner.defined, neg(failed)))

    def invoke(self, name: str, args: list[Term], stack: tuple[str, ...] = ()) -> Term:
        if name in stack:
            raise Unsupported("Recursive functions are not modeled.")
        if len(stack) > 24:
            raise Unsupported("Call-depth limit exceeded.")
        f = self.source.functions[name]
        if f.static or f.ret == VOID:
            raise Unsupported("Static parameters and void results are outside the modeled fragment.")
        self.source.leaves(f.ret)
        if any(t.mode == "rw" or t.extent for _, t in f.params):
            raise Unsupported("Callee has a nonscalar signature.")  # A read-only borrow reads as its value.
        env = {n: Term(t.value, a.parts) for (n, t), a in zip(f.params, args, strict=True)}
        frame = Frame((*stack, name), [])
        if self.block(f.body, [("true", env)], frame, None):
            raise Unsupported("A function fell through without returning.")
        value = self.zeros(f.ret)
        for path, v in reversed(frame.returns):
            value = tuple(ite(path, x, y) for x, y in zip(v.parts, value, strict=True))
        leaves = self.source.leaves(f.ret)
        parts = tuple(self.q.bind(x, t) for x, t in zip(value, leaves, strict=True))
        return self.q.make(f.ret, parts, self.q.bind(disj(*(path for path, _ in frame.returns)), BOOL))

    def store(self, target: Expr, env: dict[str, Term], frame: Frame, value: Term) -> str:
        """Assign into a local place; returns what the assignment needs in order to happen."""
        if target.tag == "name":
            env[target.val] = Term(env[target.val].ty, value.parts)
            return "true"
        base = self.expr(target.args[0], env, frame)
        if target.tag == "field":
            lo, hi = self.source.span(base.ty, target.val)
            whole = Term(base.ty, base.parts[:lo] + value.parts + base.parts[hi:])
            return conj(base.defined, self.store(target.args[0], env, frame, whole))
        if target.tag != "index":
            raise Unsupported("Only names, fields and fixed array elements are assigned in this model.")
        array = self.source.elements(base.ty)
        if array is None:
            raise Unsupported("Only fixed local arrays are assigned element-wise in this model.")
        item, count = array
        index = self.expr(target.args[1], env, frame)
        leaves = self.source.leaves(item)
        parts = list(base.parts)
        for i in range(count):
            hit = same(index.value, constant(i, index.ty.name))
            for k, leaf in enumerate(leaves):
                at = i * len(leaves) + k
                parts[at] = self.q.bind(ite(hit, value.parts[k], base.parts[at]), leaf)
        inside = conj(base.defined, index.defined, f"(bvult {index.value} {constant(count, index.ty.name)})")
        return conj(inside, self.store(target.args[0], env, frame, Term(base.ty, tuple(parts))))

    def block(self, body: list[Stmt], states: list, frame: Frame, loop: Loop | None) -> list:
        initial = set(states[0][1]) if states else set()
        for s in body:
            self.tick()
            if len(states) + len(frame.returns) > MAX_PATHS:
                raise Unsupported("Path budget exceeded.")
            next_states: list = []
            for path, env in states:
                env = dict(env)
                here = frame.at(path, top=True)
                if s.tag in {"let", "reg"}:
                    v = self.expr(s.exprs[0], env, here)
                    env[s.name] = Term(v.ty, v.parts)
                    next_states.append((conj(path, v.defined), env))
                elif s.tag == "assign":
                    v = self.expr(s.exprs[1], env, here)
                    guard = self.store(s.exprs[0], env, frame.at(path), v)
                    next_states.append((conj(path, v.defined, guard), env))
                elif s.tag == "stack" and s.ref == "host" and s.exprs[0].tag == "int":
                    view = Type(s.ty.name, "rw", s.exprs[0].val, s.ty.args, "host")
                    env[s.name] = Term(view, self.zeros(view))
                    next_states.append((path, env))
                elif s.tag == "return" and len(s.exprs) == 1:
                    v = self.expr(s.exprs[0], env, here)
                    frame.returns.append((conj(path, v.defined), v))
                elif s.tag == "expr":
                    next_states.append((conj(path, self.expr(s.exprs[0], env, here).defined), env))
                elif s.tag == "block":
                    next_states.extend(self.block(s.body, [(path, env)], frame, loop))
                elif s.tag == "if":
                    c = self.expr(s.exprs[0], env, frame.at(path))
                    p = conj(path, c.defined)
                    next_states.extend(self.block(s.body, [(conj(p, c.value), dict(env))], frame, loop))
                    next_states.extend(self.block(s.other, [(conj(p, neg(c.value)), dict(env))], frame, loop))
                elif s.tag == "match":
                    next_states.extend(self.arms(s, path, env, frame, loop))
                elif s.tag in {"break", "continue"}:
                    if loop is None:
                        raise Unsupported("break/continue outside a modeled loop.")
                    getattr(loop, s.tag + "s").append((path, env))
                elif s.tag in {"while", "for"}:
                    next_states.extend(self.repeat(s, path, env, frame))
                else:
                    raise Unsupported(f"Statement {s.tag!r} is not modeled.")
            states = next_states
        return [(p, {n: v for n, v in env.items() if n in initial}) for p, env in states]

    def arms(self, s: Stmt, path: str, env: dict[str, Term], frame: Frame, loop: Loop | None) -> list:
        subject = self.expr(s.exprs[0], env, frame.at(path))
        layout = self.source.layout(subject.ty)
        p, out = conj(path, subject.defined), []
        for arm, name in zip(s.arms, s.ref, strict=True):
            chosen = dict(env)
            if arm.binder:
                lo, hi = self.source.span(subject.ty, name)
                chosen[arm.binder] = Term(layout[name], subject.parts[lo:hi])
            taken = same(subject.parts[0], constant(list(layout).index(name), TAG.name))
            out.extend(self.block(arm.body, [(conj(p, taken), chosen)], frame, loop))
        return out

    def merge(self, states: list, keep: set[str]) -> list:
        """Join mutually exclusive paths into one, so a branching loop costs iterations, not powers of two."""
        states = [(p, {n: v for n, v in inside.items() if n in keep}) for p, inside in states if p != "false"]
        if len(states) < 2:
            return states
        env = {}
        for n in states[0][1]:
            ty = states[0][1][n].ty
            parts = list(states[-1][1][n].parts)
            for p, inside in reversed(states[:-1]):
                parts = [ite(p, a, b) for a, b in zip(inside[n].parts, parts, strict=True)]
            leaves = self.source.leaves(ty)
            env[n] = Term(ty, tuple(self.q.bind(x, t) for x, t in zip(parts, leaves, strict=True)))
        return [(self.q.bind(disj(*(p for p, _ in states)), BOOL), env)]

    def repeat(self, s: Stmt, path: str, env: dict[str, Term], frame: Frame) -> list:
        """Unroll a loop; a path that would iterate past the budget becomes a residual obligation."""
        binder, counter = s.name if s.tag == "for" else "", None
        states = [(path, dict(env))]
        if s.tag == "for":
            start = self.expr(s.exprs[0], env, frame.at(path))
            counter = self.expr(s.exprs[1], env, frame.at(path))
            states = [(conj(path, start.defined, counter.defined), {**env, binder: Term(start.ty, start.parts)})]
        keep, survivors = set(states[0][1]), []
        for step in range(MAX_UNROLL + 1):
            entering = []
            for p, inside in states:
                if s.tag == "for":
                    c = self.q.term(BOOL, f"(bvult {inside[binder].value} {counter.value})")
                else:
                    c = self.expr(s.exprs[0], inside, frame.at(p))
                held = conj(p, c.defined)  # Bound, or an unrolled path doubles in size every iteration.
                survivors.append((self.q.bind(conj(held, neg(c.value)), BOOL), inside))
                entering.append((self.q.bind(conj(held, c.value), BOOL), dict(inside)))
            if step == MAX_UNROLL:
                self.residual = disj(self.residual, *(p for p, _ in entering))
                break
            loop = Loop()
            states = self.merge(self.block(s.body, entering, frame, loop) + loop.continues, keep)
            survivors += loop.breaks
            if s.tag == "for":
                for _, inside in states:
                    i = inside[binder]
                    inside[binder] = Term(i.ty, (self.q.bind(f"(bvadd {i.value} {constant(1, i.ty.name)})", i.ty),))
        return self.merge(survivors, keep - {binder})


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
            if not 0 <= i < len(base):
                raise ConcreteTrap("out-of-bounds")
            return base[i]
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
            equal = a["variant"] == b["variant"] if isinstance(a, dict) else a == b
            return equal if op == "==" else not equal
        if op in {"<", "<=", ">", ">="}:
            return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        if op in {"&", "|", "^"}:
            return {"&": a & b, "|": a | b, "^": a ^ b}[op]
        if ty.name in FLOAT:
            if op == "/":
                return self.divided(a, b, ty.name)
            return rounded({"+": a + b, "-": a - b, "*": a * b}[op], ty.name)
        if op in {"+", "-", "*"}:
            return self.checked({"+": a + b, "-": a - b, "*": a * b}[op], ty.name)
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
        if n in {"add_wrap", "sub_wrap", "mul_wrap"}:
            a, b = xs
            v = a + b if n == "add_wrap" else a - b if n == "sub_wrap" else a * b
            return v % (1 << WIDTH[e.ty.name])
        if n in {"shl_wrap", "shr"}:
            a, b = xs
            if not 0 <= b < WIDTH[e.ty.name]:
                raise ConcreteTrap("invalid-shift")
            return ((a << b) % (1 << WIDTH[e.ty.name])) if n == "shl_wrap" else (a >> b)
        if n == "Array":
            return self.zeros(e.ty)
        n = e.ref.name if isinstance(e.ref, Function) else n
        if n in self.functions:
            return self.invoke(n, xs, stack)
        raise Unsupported("Concrete replay encountered an unsupported call.")

    def attempt(self, e, env, stack):
        value = self.expr(e.args[0], env, stack)
        ok, _, _, carrier = e.ref
        if value["variant"] == ok:
            return value.get("value")
        raise Propagate({"variant": carrier, **({"value": value["value"]} if "value" in value else {})})

    def invoke(self, name, args, stack=()):
        if name in stack:
            raise Unsupported("Concrete replay does not recurse.")
        f = self.functions[name]
        env = {n: a for (n, _), a in zip(f.params, args, strict=True)}
        try:
            signal, value = self.block(f.body, env, (*stack, name))
        except Propagate as p:
            return p.value
        if signal != "return":
            raise Unsupported("Concrete function did not return.")
        return value

    def store(self, target, env, stack, value):
        if target.tag == "name":
            env[target.val] = value
            return
        base = self.expr(target.args[0], env, stack)
        if target.tag == "field":
            self.store(target.args[0], env, stack, {**base, target.val: value})
            return
        i = self.expr(target.args[1], env, stack)
        if not 0 <= i < len(base):
            raise ConcreteTrap("out-of-bounds")
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
            elif s.tag == "stack":
                env[s.name] = [self.zeros(s.ty) for _ in range(int(s.exprs[0].val))]
            elif s.tag == "expr":
                self.expr(s.exprs[0], env, stack)
            elif s.tag == "return":
                return "return", self.expr(s.exprs[0], env, stack)
            elif s.tag in {"break", "continue"}:
                return s.tag, None
            elif s.tag == "block":
                signal, value = self.block(s.body, env, stack)
            elif s.tag == "if":
                branch = s.body if self.expr(s.exprs[0], env, stack) else s.other
                signal, value = self.block(branch, env, stack)
            elif s.tag == "match":
                subject = self.expr(s.exprs[0], env, stack)
                arm = next(a for a, v in zip(s.arms, s.ref, strict=True) if v == subject["variant"])
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

    def outcome(self, name, args: dict[str, Any]):
        f = self.functions[name]
        if set(args) != {n for n, _ in f.params}:
            raise ValueError("Wrong argument names.")
        for n, t in f.params:
            if not admissible(self.source, t, args[n]):
                raise ValueError(f"Input {n} is not a value of {t.display()}.")
        try:
            return {"defined": True, "return": self.invoke(name, [args[n] for n, _ in f.params])}
        except ConcreteTrap as e:
            return {"defined": False, "trap": str(e)}


def prepared(source: str) -> Source:
    return Source(source)


def outcome_key(outcome):
    """A scalar outcome as one comparable key; composite results use `identical` instead."""
    return (outcome["defined"], outcome.get("return") if outcome["defined"] else None)


def implementation_hash():
    parent = Path(__file__).parent
    return hashlib.sha256(
        b"".join(
            (parent / n).read_bytes()
            for n in [
                "scalar_semantics.py",
                "smt_bridge.py",
                "cairnc.py",
                "syntax.py",
                "checking.py",
                "expansion.py",
                "codegen.py",
                "modules.py",
                "version.py",
            ]
        )
    ).hexdigest()


def equivalent(
    reference: str,
    candidate: str,
    symbol: str,
    *,
    assume: str = "true",
    allow_reference_traps: bool = False,
    timeout_ms: int = 3000,
    query_log: list | None = None,
) -> dict[str, Any]:
    """Check a fixed reference contract, not a model-editable expected result.

    The default requires reference totality over a nonempty, total domain.
    Explicit allow_reference_traps compares return versus abort observations.
    Counterexamples are independently replayed before being exposed.
    """
    if not all(isinstance(x, str) for x in (reference, candidate, symbol, assume)):
        return {
            "protocol": "cairn.semantic/1",
            "status": "invalid-contract",
            "reason": "Sources, symbol and precondition must be strings.",
            "lean_verified": False,
            "native_verified": False,
        }
    common = {
        "protocol": "cairn.semantic/1",
        "profile": PROFILE,
        "reference_sha256": sha(reference),
        "candidate_sha256": sha(candidate),
        "symbol": symbol,
        "assume": assume,
        "allow_reference_traps": allow_reference_traps,
        "implementation_sha256": implementation_hash(),
        "trust": ["CAIRN parser/typechecker", "value SMT translation", "Z3 solver"],
        "lean_verified": False,
        "native_verified": False,
    }
    if type(allow_reference_traps) is not bool:
        return {**common, "status": "invalid-contract", "reason": "Trap policy must be Boolean."}
    try:
        precondition_parser = Parser(assume)
        precondition_parser.expr()
        precondition_parser.need("<eof>")
        refs = prepared(reference)
        cands = prepared(candidate)
        if symbol not in refs.functions or symbol not in cands.functions:
            raise Unsupported("Selected symbol not found.")
        rf = refs.functions[symbol]
        cf = cands.functions[symbol]
        if rf.params != cf.params or rf.ret != cf.ret:
            return {**common, "status": "invalid-contract", "reason": "Candidate signature differs from reference."}
        types = [t for _, t in rf.params] + [rf.ret]
        if [refs.shape(t) for t in types] != [cands.shape(t) for t in types]:
            return {
                **common,
                "status": "invalid-contract",
                "reason": "A type named in the signature has a different definition in the candidate.",
            }
        q = Formula(rf.params, refs)
        left = Symbolic(q, refs)
        right = Symbolic(q, cands)
        lv = left.invoke(symbol, list(q.inputs.values()))
        rv = right.invoke(symbol, list(q.inputs.values()))
        formed = conj(*(wellformed(refs, t, q.inputs[n].parts) for n, t in rf.params))
        domain = Term(BOOL, ("true",))
        if assume != "true":
            dn = "cairn_domain"
            while dn in refs.functions:
                dn += "x"
            sig = ", ".join(n + ":" + t.display() for n, t in rf.params)
            ds = reference + f"\nfn {dn}({sig})->bool {{return ({assume});}}"
            domains = prepared(ds)
            domain = Symbolic(q, domains).invoke(dn, list(q.inputs.values()))
        admitted = conj(formed, domain.value)
        query_summaries = []
        with Solver(timeout_ms) as solver:

            def run(stage, assertion):
                text = q.text(assertion)
                result = solver.check(text, q.variables)
                query_summaries.append({"stage": stage, **result})
                if query_log is not None:
                    query_log.append({"stage": stage, "smt2": text + "(check-sat)\n", "result": result})
                return result

            def finish(status, **fields):
                return {
                    **common,
                    "status": status,
                    "solver_version": solver.version,
                    "queries": query_summaries,
                    **fields,
                }

            def inputs(result):
                out = {}
                for i, (n, t) in enumerate(rf.params):
                    values = [result["values"][f"arg_{i}_{j}"] for j in range(len(refs.leaves(t)))]
                    out[n] = rebuild(refs, t, values)
                return out

            if domain.defined != "true":
                r = run("domain-totality", conj(formed, neg(domain.defined)))
                if r["status"] == "sat":
                    return finish("invalid-domain", reason="Precondition may trap.", counterexample=inputs(r))
                if r["status"] != "unsat":
                    return finish("unknown", reason="Domain totality was not established.")
            if admitted != "true":
                r = run("domain-nonempty", admitted)
                if r["status"] == "unsat":
                    return finish(
                        "invalid-domain", reason="Precondition admits no inputs; vacuous acceptance rejected."
                    )
                if r["status"] != "sat":
                    return finish("unknown", reason="Nonempty domain was not established.")
            residual = disj(left.residual, right.residual)
            if residual != "false":
                r = run("loop-unrolling", conj(admitted, residual))
                if r["status"] != "unsat":
                    return finish("unknown", reason=f"A loop may run past the {MAX_UNROLL}-iteration unrolling budget.")
            if not allow_reference_traps:
                r = run("reference-totality", conj(admitted, neg(lv.defined)))
                if r["status"] == "sat":
                    return finish(
                        "invalid-reference", reason="Reference traps on an admitted input.", counterexample=inputs(r)
                    )
                if r["status"] != "unsat":
                    return finish("unknown", reason="Reference totality was not established.")
            mismatch = disj(
                neg(same(lv.defined, rv.defined)),
                conj(lv.defined, rv.defined, neg(observed(refs, rf.ret, lv.parts, rv.parts))),
            )
            r = run("equivalence", conj(admitted, mismatch))
            if r["status"] == "unsat":
                nan = conj(admitted, lv.defined, undecided(refs, rf.ret, lv.parts))
                if nan != "false":
                    n = run("nan-observation", nan)
                    if n["status"] != "unsat":
                        return finish(
                            "unknown",
                            reason="A returned float may be NaN, whose payload bits this model does not track.",
                            **({"counterexample": inputs(n)} if n["status"] == "sat" else {}),
                        )
                return finish(
                    "smt-equivalent",
                    quantification="All well-formed values of the declared parameter types satisfying the host "
                    "precondition; every tag names a declared variant.",
                    observation="Return value, or one undifferentiated abort outcome; a sum shows its tag and "
                    "active payload only; no memory/timing observation.",
                )
            if r["status"] != "sat":
                return finish("unknown", reason="Equivalence solver did not decide the obligation.")
            args = inputs(r)
            expected = Concrete(refs).outcome(symbol, args)
            actual = Concrete(cands).outcome(symbol, args)
            if assume != "true":
                observation = Concrete(domains).outcome(dn, args)
                if not observation["defined"] or not observation["return"]:
                    return finish("unknown", reason="Solver/concrete precondition disagreement.", counterexample=args)
            agree = expected["defined"] == actual["defined"] and (
                not expected["defined"] or identical(refs, rf.ret, expected["return"], actual["return"])
            )
            if agree:
                return finish(
                    "unknown",
                    reason="Solver/concrete replay disagreed; no semantic rejection is certified.",
                    counterexample=args,
                )
            return finish(
                "counterexample",
                counterexample=args,
                expected=expected,
                actual=actual,
                concrete_replay=True,
                native_replay="not-run",
            )
    except Diagnostic as e:
        return {**common, "status": "rejected", "diagnostic": e.data}
    except Unsupported as e:
        return {**common, "status": "unknown", "reason": str(e), "unsupported_profile": True}
    except (SolverUnavailable, OSError, ValueError, KeyError, IndexError, RecursionError) as e:
        return {**common, "status": "unknown", "reason": str(e)}

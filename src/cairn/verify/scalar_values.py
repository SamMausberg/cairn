"""Values as scalar components, and the SMT vocabulary they are written in.

A value is flattened into its leaves (a record is its fields, a sum is the emitted u32 tag beside
every payload, an array is its elements); `Source` answers what a type is made of. `wellformed` says
which inputs the emitted entry guard admits, `observed` what a caller can tell apart, and `rebuild`
and `admissible` read a solver model back into a value the concrete evaluator can replay.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Any

from ..compiler.cairnc import SIGNED, WIDTH, Type, compile_program
from ..compiler.tree import FLOAT, NUMERIC, USIZE, VOID, is_view

PROFILE = "cairn-value-bv-fp-array/3"
MAX_PATHS = 256
MAX_TERMS = 12000
MAX_SOURCE_BYTES = 64000
MAX_UNROLL = 16  # Iterations of one loop; a path that would need more is a residual obligation.
MAX_LEAVES = 64  # Scalar components of one value.
MAX_REPLAY = 16  # Elements of one view a counterexample may carry into the concrete replay.
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


def arrayed(ty: Type) -> str:
    """The sort of the storage one component of a view's element lives in."""
    return f"(Array {sort(USIZE)} {sort(ty)})"


def declared(ty: Type) -> str:
    """The sort an input is declared with: a float arrives as the bit pattern its model reports."""
    return f"(_ BitVec {sum(FORMATS[ty.name][:2])})" if ty.name in FLOAT else sort(ty)


def lifted(name: str, ty: Type) -> str:
    """A declared constant read as its value: a float is the datum its bit pattern encodes."""
    eb, sb = FORMATS[ty.name][:2] if ty.name in FLOAT else (0, 0)
    return f"((_ to_fp {eb} {sb}) {name})" if ty.name in FLOAT else name


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


ZERO = constant(0, "usize")


def numeral(term: str) -> int | None:
    """What `(_ bvN w)` denotes, or None: a literal index keeps a read of storage a lookup."""
    digits = term[5:-1].split(" ")[0] if term.startswith("(_ bv") and term.endswith(")") else ""
    return int(digits) if digits.isdigit() else None


def shifted(offset: str, index: str) -> str:
    a, b = numeral(offset), numeral(index)
    if a is not None and b is not None:
        return constant(a + b, "usize")
    return index if offset == ZERO else f"(bvadd {offset} {index})"


@dataclass(frozen=True)
class Window:
    """Where a view's elements live: element i is `offset + i` of the arrays the term carries.

    `root` names the storage itself, so two views of one array are recognised
    however they were narrowed.
    """

    offset: str
    extent: str
    root: int


@dataclass(frozen=True)
class Term:
    """A value: one SMT term per scalar component, and whether evaluation continued.

    A view carries one SMT array per component of its element instead, read
    through its window.
    """

    ty: Type
    parts: tuple[str, ...]
    defined: str = "true"
    window: Window | None = None

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
    outs: tuple[str, ...] = ()  # The rw parameters whose final value leaves the function.

    def at(self, path: str, top: bool = False) -> Frame:
        return Frame(self.stack, self.returns, path, top, self.outs)


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
        """(element type, count) of an inline array value."""
        return (ty.args[0], ty.args[1]) if ty.mode == "value" and ty.name == "Array" else None

    def item(self, ty: Type) -> Type:
        """The element type of a view, of fixed local storage or of an owned array."""
        if ty.mode == "value" and ty.name in {"Buf", "Array"}:
            return ty.args[0]
        if is_view(ty):
            return ty.value
        raise Unsupported(f"{ty.display()} holds no elements.")

    def leaves(self, ty: Type) -> tuple[Type, ...]:
        """The scalar components of a value, in the order the model stores them.

        A view has the components of its element, each held in storage of its own.
        """
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
        elif is_view(ty):
            if ty.place != "host":
                raise Unsupported(f"{ty.place} memory is not modeled.")
            found = self.leaves(ty.value)
        elif ty.name in NUMERIC | {"bool"}:
            found = (ty.value,)
        elif isinstance(layout, list):
            found = tuple(x for _, t in layout for x in self.leaves(t))
        elif isinstance(layout, dict):
            found = (TAG, *(x for t in layout.values() if t for x in self.leaves(t)))
        elif owned(ty):  # An owner is storage: it lives in a local, a parameter or a result, nowhere inside a value.
            raise Unsupported("An owner inside a record, a sum or an array is not modeled.")
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


def owned(ty: Type) -> bool:
    """A `Buf[T]` held by value: storage with a length of its own, moved rather than copied."""
    return ty.mode == "value" and ty.name == "Buf"


def components(src: Source, t: Term) -> tuple[Type, ...]:
    """The scalar components of a term: a view's are its element's, one array each."""
    return src.leaves(src.item(t.ty) if t.window else t.ty)


def kinds(src: Source, t: Term) -> tuple[str, ...]:
    """The SMT sort each component of a term is bound at."""
    return tuple(arrayed(x) if t.window else sort(x) for x in components(src, t))


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
    """What the emitted entry guard admits of one parameter, and nothing more.

    The guard reads the top-level tag of an enum or sum passed by value and traps unless it names a
    declared variant. It reads nothing else: a tag nested in a record, an array or a payload, or reached
    through a borrow, is admitted as storage is, any value, on which a `match` aborts.
    """
    layout = src.layout(ty)
    if ty.mode == "value" and isinstance(layout, dict):
        return f"(bvult {parts[0]} {constant(len(layout), TAG.name)})"
    return "true"


def rebuild(src: Source, ty: Type, values: list) -> Any:
    """A model assignment read back as a CAIRN value; `values` is consumed in component order.

    A tag outside the declared variants is read back as `{"tag": n}`, since no
    variant names it and a `match` over it aborts.
    """
    array = src.elements(ty)
    layout = src.layout(ty)
    if array is not None:
        return [rebuild(src, array[0], values) for _ in range(array[1])]
    if isinstance(layout, list):
        return {n: rebuild(src, t, values) for n, t in layout}
    if isinstance(layout, dict):
        tag = values.pop(0)
        payloads = {n: rebuild(src, t, values) for n, t in layout.items() if t is not None}
        names = list(layout)
        if not 0 <= tag < len(names):  # Nothing the entry guard reads holds one, and nothing observes its payloads.
            return {"tag": tag}
        return {"variant": names[tag], **({"value": payloads[names[tag]]} if layout[names[tag]] is not None else {})}
    raw = values.pop(0)
    return decoded(raw, ty.name) if ty.name in FLOAT else raw


def admissible(src: Source, ty: Type, value: Any, guarded: bool = True) -> bool:
    """Is a caller-supplied input a value of this type? Malformed inputs are refused, not guessed.

    `guarded` marks the one position the emitted entry guard reads: the top-level tag of an enum or
    sum passed by value. Everywhere else, behind a view or a borrow, inside a record, an array or a
    payload, a tag may name no variant, as storage may hold one.
    """
    array = src.elements(ty)
    layout = src.layout(ty)
    if is_view(ty) or owned(ty):  # Storage arrives as its elements; a view is held to its extent by `outcome`.
        item = ty.args[0] if owned(ty) else ty.value
        return isinstance(value, list) and all(admissible(src, item, v, False) for v in value)
    guarded = guarded and ty.mode == "value"
    if array is not None:
        return isinstance(value, list) and len(value) == array[1] and all(
            admissible(src, array[0], v, False) for v in value)  # fmt: skip
    if isinstance(layout, list):
        names = [n for n, _ in layout]
        return isinstance(value, dict) and sorted(value) == sorted(names) and all(
            admissible(src, t, value[n], False) for n, t in layout)  # fmt: skip
    if isinstance(layout, dict):
        if not isinstance(value, dict):
            return False
        if (
            "variant" not in value
        ):  # A tag no variant names: what an unguarded position may hold, and what a match aborts on.
            shaped = set(value) == {"tag"} and type(value["tag"]) is int
            return not guarded and shaped and len(layout) <= value["tag"] < 1 << WIDTH[TAG.name]
        if value["variant"] not in layout:
            return False
        payload = layout[value["variant"]]
        expected = {"variant"} | ({"value"} if payload is not None else set())
        return set(value) == expected and (payload is None or admissible(src, payload, value["value"], False))
    if ty.name == "bool":
        return type(value) is bool
    if ty.name in FLOAT:
        return type(value) is float and (value != value or rounded(value, ty.name) == value)
    return type(value) is int and bounds(ty.name)[0] <= value <= bounds(ty.name)[1]


def identical(src: Source, ty: Type, a: Any, b: Any) -> bool:
    """Concrete observation equality; floats compare by bit pattern, so +0 and -0 differ and NaNs do not tie."""
    array = src.elements(ty)
    layout = src.layout(ty)
    if is_view(ty) or owned(ty):
        item = ty.args[0] if owned(ty) else ty.value
        return len(a) == len(b) and all(identical(src, item, x, y) for x, y in zip(a, b, strict=True))
    if array is not None:
        return all(identical(src, array[0], x, y) for x, y in zip(a, b, strict=True))
    if isinstance(layout, list):
        return all(identical(src, t, a[n], b[n]) for n, t in layout)
    if isinstance(layout, dict):
        order = list(layout)
        at = [order.index(x["variant"]) if "variant" in x else x["tag"] for x in (a, b)]
        if at[0] != at[1]:  # The tag is observed first, as the emitted discriminant is.
            return False
        payload = layout[order[at[0]]] if at[0] < len(order) else None  # No variant is active, so nothing else shows.
        return payload is None or identical(src, payload, a["value"], b["value"])
    if ty.name in FLOAT:
        return encoded(a, ty.name) == encoded(b, ty.name)
    return a == b


def extent_of(env: dict[str, Term], ty: Type) -> str:
    """The element count a view was declared with: a literal, or the usize name that stands for it."""
    if ty.extent.isdigit():
        return constant(int(ty.extent), "usize")
    bound = env.get(ty.extent)
    if bound is None or bound.ty != USIZE or bound.window is not None:
        raise Unsupported("A view extent must be a literal or a usize parameter of the same function.")
    return bound.value

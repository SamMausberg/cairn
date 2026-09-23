"""The syntax tree, the closed scalar vocabulary and the diagnostic that names a source position.

Every name the parser produces is resolved, typed and given effects by checking.py; nothing here
evaluates source. `Type` is frozen and compared by value, so a side table (`Program.field_extents`)
carries what a type does not say about itself.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from typing import Any, NoReturn, TypeVar

# Size limits: past one, a program is refused with a code instead of running out of time or memory. On the reference
# machine a program of 31,802 functions, 7 MB and 758,000 nodes checked in 33 s at 800 MiB (evidence/v1_0/scale);
# the source and node limits admit about twice that, which was not measured.
MAX_SOURCE = 16_000_000  # bytes of a program's combined source
MAX_FUNCTIONS = 32_768  # functions a program holds: its own, its library's, and every copy and instance made
MAX_NODES = 3_200_000  # syntax nodes of those functions before expansion
# What code may generate stays small whatever the program's size: expansion is cheap to write and dear to check.
MAX_FAMILY = 1024  # copies one family makes
MAX_EXPANSION = 2048  # functions one recipe generates, and all of a program's families together
Node = TypeVar("Node")


def clone(node: Node) -> Node:
    """A deep copy of plain tree data, as `copy.deepcopy` makes it, through pickle's C code in a third of the time."""
    return pickle.loads(pickle.dumps(node, pickle.HIGHEST_PROTOCOL))


class Diagnostic(Exception):
    def __init__(self, code: str, message: str, line: int = 0, column: int = 0, **details):
        super().__init__(message)
        self.data = {
            "protocol": "cairn.diagnostic/2",
            "status": "rejected",
            "code": code,
            "message": message,
            "line": line,
            "column": column,
            "trust": "prototype-not-verified",
            **details,
        }


def fail(code: str, message: str, node: Any = None, **details) -> NoReturn:
    raise Diagnostic(code, message, getattr(node, "line", 0), getattr(node, "col", 0), **details)


PLACES = ("host", "device", "pinned", "unified")
HOST_VISIBLE = {"host", "pinned", "unified"}
# A view may be lent as what its memory also is: page-locked memory is host memory, managed memory is both.
VISIBLE_AS = {("pinned", "host"), ("unified", "host"), ("unified", "device")}


@dataclass(frozen=True)
class Type:
    """A value type, or a second-class borrow of one (`mode` ro/rw, optional array extent)."""

    name: str
    mode: str = "value"
    extent: str = ""
    args: tuple = ()
    place: str = "host"

    @property
    def value(self) -> Type:
        return Type(self.name, args=self.args)

    def display(self) -> str:
        if self.name == "fn":
            inner = f"fn({', '.join(a.display() for a in self.args[:-1])})"
            inner += "" if self.args[-1].name == "void" else " -> " + self.args[-1].display()
        elif self.name == "dyn":
            inner = "dyn " + self.args[0].display()
        else:
            shown = (a.display() if isinstance(a, Type) else str(a) for a in self.args)
            inner = self.name + ("[" + ", ".join(shown) + "]" if self.args else "")
        if self.mode == "value":
            return inner
        return f"{self.mode}<{inner}>" + (f"[{self.extent}]@{self.place}" if self.extent else "")


BITS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64, "usize": 64, "i8": 8, "i16": 16, "i32": 32, "i64": 64}
CPP = {n: "std::size_t" if n == "usize" else f"std::{'u' * (n[0] == 'u')}int{w}_t" for n, w in BITS.items()}
CPP |= {"bool": "bool", "f32": "float", "f64": "double", "void": "void"}
# Storage floats hold a value and convert; they never compute (docs/numerics.md#storage-floats). Each is
# (exponent bits, fraction bits, whether the top exponent means infinity and NaN as in IEEE 754).
STORAGE = {"f16": (5, 10, True), "bf16": (8, 7, True), "f8e4m3": (4, 3, False), "f8e5m2": (5, 2, True)}
CPP |= {name: "cr::" + name for name in STORAGE}
UNSIGNED = {n for n in BITS if n[0] == "u"}
SIGNED = set(BITS) - UNSIGNED
INT = UNSIGNED | SIGNED
FLOAT = {"f32", "f64"}
NUMERIC = INT | FLOAT
SCALAR = NUMERIC | {"bool"}
WIDTH = BITS
# name -> number of type arguments
INTRINSIC_TYPES = {"Buf": 1, "Array": 2, "fn": None, "dyn": 1, "Dyn": 1, "Ticket": 1, "Atomic": 1, "Mutex": 1, "Group": 1,
                   "IoRing": 0}  # fmt: skip
# Tensor-core fragments (compiler/fragments.py): an element type and M, N, K.
INTRINSIC_TYPES |= dict.fromkeys(("WmmaA", "WmmaB", "WmmaAcc", "MmaA", "MmaB", "MmaAcc", "TmemAcc"), 4)
VOID, BOOL, USIZE = Type("void"), Type("bool"), Type("usize")


@dataclass
class Expr:
    tag: str
    val: str = ""
    args: list[Expr] = field(default_factory=list)
    line: int = 0
    col: int = 0
    ty: Type | None = None
    start: int = -1
    end: int = -1
    ref: Any = None  # The checker's resolution: a binding, callee, constant or lambda.
    established: bool = False  # The checker showed this site's guard cannot fail (facts.py); lowering omits it.
    proof: Any = None  # Why it cannot: ("facts", the facts used) or ("span", part), checked by verify/elision.py.
    span: Any = None  # The part `x[lo..hi]` of the same call whose bounds this node repeats (calls.spanned).
    char: bool = False  # An integer written as a character literal 'c', which print writes as its byte.


@dataclass
class Arm:
    variant: str
    binder: str
    body: list[Stmt]
    line: int = 0
    col: int = 0


@dataclass
class Stmt:
    tag: str
    name: str = ""
    ty: Type | None = None
    exprs: list[Expr] = field(default_factory=list)
    body: list[Stmt] = field(default_factory=list)
    other: list[Stmt] = field(default_factory=list)
    line: int = 0
    col: int = 0
    binder: str = ""
    arms: list[Arm] = field(default_factory=list)
    op: str = ""
    ref: Any = None
    other_names: list[Expr] = field(default_factory=list)  # `after a, b` on a spawned region; the names of an unpack.
    pooled: bool = False  # `reduce op parallel i in n`: the host fold runs on the lane pool.
    exclusive: bool = False  # `scan op exclusive out ...`: out[i] combines the yields before i, not up to it.
    block: int = 1  # The widest block of elements one index of a region owns (facts.window); it sizes claims.
    plan: tuple[int, int] = (0, 0)  # (grain, lanes) a `plan` fixes for a host region; 0 leaves the pool's choice.
    launch: tuple[int, int, int] = (0, 0, 0)  # (block, per_lane, unroll) a plan fixes for a device region.
    fuse: int = 0  # How many adjacent regions, this one first, a plan lets run as one traversal (compiler/fusion.py).
    vector: int = 0  # Adjacent indices each lane of a device region runs over W-element chunks (compiler/chunks.py).
    chunked: tuple = ()  # The arrays a vectored region moves W at a time: (name, element, loaded, stored, view).
    stage: int = 0  # How far either side of a device block's indices its tiles reach (compiler/staging.py).
    staged: tuple = ()  # The arrays a staged region reads from its block's tiles: (name, element, view).
    touched: tuple = ()  # A region's (outer name, block stride or None, written) accesses, as its lanes made them.
    assembly: Assembly | None = None  # What a typed `asm` statement declares (compiler/machine.py).


@dataclass
class Assembly:
    """`asm [volatile] target [capability] "template" (operands) effects(...);`, typed inline assembly. Operands are
    numbered as written, outputs first. An output `out name:T` binds a fresh local, and `out name:T = e` starts it at
    e; Stmt.exprs holds those starts, then the inputs."""

    target: str  # ptx, x86_64 or aarch64
    capability: str  # the device architecture PTX needs (sm_75, sm_90a); empty for host assembly
    template: str
    outputs: list[tuple[str, Type, bool, int, int]]  # (the local it binds, its type, whether it has a start, line, col)
    effects: tuple[str, ...]  # declared, and trusted as written
    volatile: bool = False
    clobbers: tuple[str, ...] = ()  # host registers the instructions write besides their outputs


@dataclass
class Implements:
    """`implements total when n % K == 0 tune K in [4, 8] needs(cp_async)`: the function is an alternative
    implementation of `total`, applicable where the condition holds and on the targets it names, one instance per
    value `tune` lists for each of its natural parameters (compiler/implementations.py)."""

    reference: str  # as written, resolved in the implementation's module
    when: Expr | None = None  # None: it applies to every input
    text: str = ""  # the condition as written, for a receipt and a packet
    needs: tuple[str, ...] = ()  # device features its code uses (projects/target.py FEATURES)
    identity: str = ""  # sha256 of the reference's tokens and its own, as the parser read them
    tune: tuple[tuple[str, tuple[int, ...]], ...] = ()  # `tune K in [2, 4, 8]`: each natural parameter's values


@dataclass
class Function:
    name: str
    params: list[tuple[str, Type]]
    ret: Type
    body: list[Stmt]
    generics: list[tuple[str, str]] = field(default_factory=list)  # (name, nat | type | Trait)
    bindings: dict[str, Any] = field(default_factory=dict)  # instantiated generics
    source_name: str = ""
    line: int = 0
    col: int = 0
    start: int = -1
    body_start: int = -1
    end: int = -1
    module: str = ""
    public: bool = False
    extern: bool = False
    effects: tuple[str, ...] | None = None  # A declared ceiling; None infers.
    owner: tuple[str, Type] | None = None  # (trait, Self) for an impl member.
    block: Any = None  # Which `impl { }` wrote this member: one Self type has one block, not a union of several.
    kernel: bool = False  # Runs on the device: callable only from device lanes and other kernels.
    symbol: str = ""  # The C symbol of an extern, when it differs from the CAIRN name.
    launch: tuple[str, int] | None = None  # (threads, block) of an extern that is a CUDA kernel (compiler/launches.py)
    test: bool = False  # `test name { }`: run by `cairn test` in a process of its own, emitted into no other build.
    captures: list[tuple[str, str]] = field(default_factory=list)  # A closure's (outer place, mode) accesses.
    row: tuple[set, set] = field(default_factory=lambda: (set(), set()))  # A closure's own (effects, callees).
    implements: Implements | None = None  # An alternative implementation of another function.

    @property
    def static(self) -> str | None:
        return self.generics[0][0] if self.generics and not self.bindings else None

    @property
    def binding(self) -> int | None:
        return next((v for v in self.bindings.values() if isinstance(v, int)), None)


@dataclass
class Each:
    """Static iteration in a recipe, over a record's fields or a natural range; `where` names static values."""

    binder: str
    seq: list[Expr]
    where: list[tuple[str, Expr]]
    items: list[Any]  # Declarations, statements or one expression, by where it is written.


@dataclass
class Shape:
    """A record that a recipe generates; its field list may iterate."""

    name: str
    fields: list[Any]  # (name, Type) or Each of them
    public: bool = False
    line: int = 0
    col: int = 0


@dataclass
class Impl:
    """A trait implementation that a recipe generates for the type it is derived for."""

    trait: str
    target: Type
    members: list[Function]
    block: int = 0  # Where its `impl` stands among the recipe's tokens.


@dataclass
class Recipe:
    """A library-defined generator: declarations with `$name` splices, expanded per `derive` before checking."""

    name: str
    statics: list[tuple[str, str]]  # Bracket parameters: a natural (`K:nat`) or the name of a function (`F:fn`).
    param: str
    where: list[tuple[str, Expr]]
    items: list[Any]
    module: str = ""
    public: bool = False
    line: int = 0
    col: int = 0
    start: int = -1
    end: int = -1
    digest: str = ""  # sha256 of its tokens: what a derived function's receipt pins.


@dataclass
class Program:
    records: dict[str, list[tuple[str, Type]]] = field(default_factory=dict)
    enums: dict[str, list[str]] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    families: list[tuple[str, str, int, int]] = field(default_factory=list)
    derivations: list[tuple] = field(default_factory=list)  # (module, recipe as written, naturals, target, token)
    plans: list[tuple] = field(default_factory=list)  # (module, function as written, {item: value}, token)
    selections: list[tuple] = field(default_factory=list)  # `plan f use g;`: (module, f, g as written, token)
    scopes: list[tuple[int, str]] = field(default_factory=list)  # (where a `module` line starts, the module it opens)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    sums: dict[str, list[tuple[str, Type | None]]] = field(default_factory=dict)
    generics: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # generic types
    attributes: dict[str, set[str]] = field(default_factory=dict)  # linear, packed, align(n)
    traits: dict[str, list[Function]] = field(default_factory=dict)
    consts: dict[str, tuple[Type, Expr]] = field(default_factory=dict)
    layouts: dict[str, Expr] = field(default_factory=dict)  # `layout NAME = ...;`, which compiler/layouts.py folds
    imports: list[tuple[str, str, str]] = field(default_factory=list)  # (importer, path, alias)
    public: set[str] = field(default_factory=set)
    uses: dict[tuple[str, str], str] = field(default_factory=dict)  # (importer, bare name) -> full name
    sources: dict[str, str] = field(default_factory=dict)  # linked library module -> its text
    modules: dict[str, str] = field(default_factory=dict)  # declared name -> owning module
    field_extents: dict[str, dict[str, str]] = field(default_factory=dict)  # record -> Buf field -> extent field
    lends: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # record -> (Buf field, lo, hi) it lends


def is_view(ty: Type) -> bool:
    return ty.mode != "value" and ty.extent != ""


def nested(s: Stmt) -> list[Stmt]:
    """The statements directly inside `s`: its body, its other branch and every arm's body."""
    return [*s.body, *s.other, *(x for arm in s.arms for x in arm.body)]


def root(e: Expr) -> Expr:
    while e.tag in {"field", "index", "slice"}:
        e = e.args[0]
    return e

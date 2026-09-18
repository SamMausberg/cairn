"""Types, places, ownership and conservative interprocedural effects.

Generic functions and types are instantiated on demand and every instance is
checked as ordinary monomorphic code. Borrows are second class (parameters and
call arguments only), so no lifetime annotations exist; owners are affine and
`linear` values must be consumed exactly once. Nothing here is mechanically proved.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from .effects import LANE_SAFE, PURE, audit, exposed, fixed_point
from .syntax import (
    BOOL,
    CPP,
    FLOAT,
    INT,
    MAX_FUNCTIONS,
    MAX_NODES,
    NUMERIC,
    SCALAR,
    SIGNED,
    UNSIGNED,
    USIZE,
    VOID,
    WIDTH,
    Expr,
    Function,
    Program,
    Stmt,
    Type,
    fail,
    root,
)  # fmt: skip

WRAPPING = {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr"}
SOFT = {
    "take",
    "swap",
    "transfer",
    "mmio_read",
    "mmio_write",
    "asm",
    "wait",
}  # New in 1.0: a program's own function wins.
BUILTINS = WRAPPING | SOFT | {"min", "max", "len"}
INTRINSIC_TYPES = {"Buf": 1, "Array": 2, "fn": None, "dyn": 1, "Dyn": 1, "Ticket": 1, "Atomic": 1, "Mutex": 1}
PINNED = {"Ticket", "Atomic", "Mutex"}  # Declared and borrowed in place; never stored, passed or returned.
ORDERS = ["relaxed", "acquire", "release", "acquire_release", "seq_cst"]
ATOMIC_OPS = {
    "load": 0,
    "store": 1,
    "swap": 1,
    "fetch_add": 1,
    "fetch_sub": 1,
    "fetch_and": 1,
    "fetch_or": 1,
    "fetch_xor": 1,
}
KINDS = ["copy", "affine", "linear"]
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}
HOST_VISIBLE = {"host", "pinned", "unified"}


@dataclass
class Scope:
    """Everything the checker knows about the function it is inside."""

    f: Function
    tenv: dict[str, Any] = field(default_factory=dict)
    env: dict[str, Binding] = field(default_factory=dict)
    effects: set[str] = field(default_factory=set)
    callset: set[str] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    moved: set[str] = field(default_factory=set)
    deferred: set[str] = field(default_factory=set)
    loop_depth: int = 0
    unsafe_depth: int = 0
    device_depth: int = 0
    module: str = ""
    lanes: Lanes | None = None
    closure: tuple[Type, set[str]] | None = None  # (return type, names bound outside the closure)
    leases: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # ticket -> [(place, mode)]
    spawning: str = ""  # The name a `let t = spawn ...` is about to bind.


@dataclass
class Lanes:
    """One data-parallel region: lanes may only touch element `binder` of what any lane writes."""

    binder: str
    outer: set[str]
    accesses: list[tuple[str, bool, bool, Expr]] = field(default_factory=list)  # root, at binder, write


@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None
    depth: int = 0  # Loop depth at declaration: an outer owner cannot be moved inside a loop.


SCOPED = frozenset(Scope.__dataclass_fields__)


def is_view(ty: Type) -> bool:
    return ty.mode != "value" and ty.extent != ""


def path(e: Expr, stable=lambda name: False) -> str:
    """A syntactic place identity for alias checks: a.b, a[], a[lo..hi].

    A part bound counts only when it cannot change: a literal, or a name `stable` vouches for.
    """
    if e.tag == "field":
        return path(e.args[0], stable) + "." + e.val
    if e.tag == "index":
        return path(e.args[0], stable) + "[]"
    if e.tag == "slice":
        bounds = (a.val if a.tag == "int" or (a.tag == "name" and stable(a.val)) else "?" for a in e.args[1:])
        return path(e.args[0], stable) + "[" + "..".join(bounds) + "]"
    return e.val


def overlaps(a: str, b: str) -> bool:
    """Two parts of one array are disjoint only when they visibly share a boundary."""
    (base_a, _, part_a), (base_b, _, part_b) = a.partition("["), b.partition("[")
    if base_a == base_b and ".." in part_a and ".." in part_b:
        (lo_a, hi_a), (lo_b, hi_b) = part_a[:-1].split(".."), part_b[:-1].split("..")
        return "?" in (lo_a, hi_a, lo_b, hi_b) or not (hi_a == lo_b or hi_b == lo_a)
    return base_a == base_b or base_a.startswith(base_b + ".") or base_b.startswith(base_a + ".")


class Checker:
    # Declared for readers and type checkers; the values live in the current Scope (see __getattr__).
    f: Function
    tenv: dict[str, Any]
    env: dict[str, Binding]
    effects: set[str]
    callset: set[str]
    counts: dict[str, int]
    moved: set[str]
    deferred: set[str]
    loop_depth: int
    unsafe_depth: int
    device_depth: int
    module: str
    lanes: Lanes | None
    closure: tuple[Type, set[str]] | None
    leases: dict[str, list[tuple[str, str]]]
    spawning: str

    def __init__(self, program: Program, capture_sites: bool = False):
        self.p = program
        self.capture_sites = capture_sites
        self.sites: list[dict[str, Any]] = []
        self.fs = {f.name: f for f in program.functions}
        program.enums.setdefault("Order", ORDERS)
        self.types = {**program.records, **program.enums, **program.sums}
        for name in [*self.fs, *self.types, *program.consts, *program.traits]:
            if name in CPP or name in BUILTINS - SOFT or name in INTRINSIC_TYPES:
                fail("E-BUILTIN-NAME", f"Cannot redefine builtin {name}.")
        self.aliases: dict[str, dict[str, str]] = {}
        for importer, target, alias in program.imports:
            self.aliases.setdefault(importer, {})[alias] = target
        self.layouts: dict[Type, Any] = {}  # Concrete records and sums, dependencies first.
        self.kinds: dict[Type, str] = {}
        self.early: dict[int, Expr] = {}  # Arguments typed ahead of their call for dispatch.
        self.s = Scope(Function("", [], VOID, []))
        self.signed: set[str] = set()
        self.local_effects: dict[str, set[str]] = {}
        self.calls: dict[str, set[str]] = {}
        self.checks: dict[str, dict[str, int]] = {}
        self.call_edges: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.resources: dict[str, list[dict[str, Any]]] = {}
        self.unchecked: list[str] = []
        self.lane_calls: list[tuple[str, bool, Expr]] = []
        self.borrowed: list[tuple[str, str]] = []
        self.device_functions: set[str] = set()
        self.address_taken: set[str] = set()
        self.nodes = self.unique = 0

    def __getattr__(self, name: str):  # Per-function state lives in the current Scope.
        if name in SCOPED:
            return getattr(self.s, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any):
        if name in SCOPED:
            setattr(self.s, name, value)
        else:
            object.__setattr__(self, name, value)

    # Names and types ---------------------------------------------------------------------------

    def qualify(self, name: str, *tables, node=None) -> str | None:
        """Resolve a possibly alias-qualified name: imports, current module, then the root."""
        head, _, rest = name.partition(".")
        candidates = [f"{self.module}.{name}" if self.module else name, name]
        if rest and head in self.aliases.get(self.module, {}):
            candidates.insert(0, f"{self.aliases[self.module][head]}.{rest}")
        if (self.module, head) in self.p.uses:
            candidates.insert(0, self.p.uses[self.module, head] + ("." + rest if rest else ""))
        for candidate in candidates:
            if any(candidate in table for table in tables):
                owner = self.p.modules.get(candidate, "")
                if owner not in ("", self.module) and candidate not in self.p.public:
                    fail("E-PRIVATE", f"{candidate} is private to module {owner}.", node)
                return candidate
        return None

    def within(self, module: str, tenv: dict[str, Any] | None = None):
        """Temporarily resolve names as another module (and generic instance) would."""
        checker = self

        class Scope:
            def __enter__(self):
                self.saved = checker.module, checker.tenv
                checker.module = module
                checker.tenv = checker.tenv if tenv is None else tenv

            def __exit__(self, *_):
                checker.module, checker.tenv = self.saved

        return Scope()

    def resolve(self, ty: Type, node=None) -> Type:
        """Substitute generic parameters, qualify names and instantiate generic layouts."""
        if ty.name in self.tenv and not ty.args:
            base = self.tenv[ty.name]
            if not isinstance(base, Type):
                fail("E-TYPE", f"{ty.name} is a static natural, not a type.", node)
        elif ty.name == "Dyn" and len(ty.args) == 1 and isinstance(ty.args[0], Type):
            trait = self.qualify(ty.args[0].name, self.p.traits, node=node)
            if trait is None:
                fail("E-DYN", f"Dyn[...] owns a value behind a trait; {ty.args[0].name} is not a trait.", node)
            return Type("Dyn", ty.mode, ty.extent, (Type(trait),), ty.place)
        elif ty.name == "dyn":
            trait = self.qualify(ty.args[0].name, self.p.traits, node=node)
            if trait is None or ty.mode == "value" or ty.extent:
                fail(
                    "E-DYN",
                    "Write ro<dyn Trait> or rw<dyn Trait>: a dynamic interface is a borrowed fat reference.",
                    node,
                )
            return Type("dyn", ty.mode, args=(Type(trait),))
        else:
            args = tuple(self.static(a, node) for a in ty.args)
            name: str | None = ty.name
            arity = INTRINSIC_TYPES.get(ty.name, 0)
            if ty.name not in CPP and ty.name not in INTRINSIC_TYPES:
                name = self.qualify(ty.name, self.types, node=node)
                if name is None:
                    fail("E-TYPE", f"Unknown type {ty.name}.", node)
                arity = len(self.p.generics.get(name, []))
            if arity is not None and len(args) != arity:
                fail("E-GENERIC-ARITY", f"{name} takes {arity} type arguments.", node)
            if name == "fn" and any(is_view(a) for a in args):
                fail("E-FN-TYPE", "A function type carries values and single borrows, not array views.", node)
            base = Type(name, args=args)
            self.define(base, node)
            if name in {"Buf", "Array"} and self.kind(args[0]) == "linear":
                fail(
                    "E-LINEAR-STORAGE", "Zeroed storage cannot hold linear values: a zero would be a forged one.", node
                )
        if ty.mode == "value":
            return base
        if base == VOID:
            fail("E-TYPE", "A slice cannot contain void.", node)
        return Type(base.name, ty.mode, ty.extent, base.args, ty.place)

    def static(self, argument: Any, node=None) -> Any:
        """A type argument: a natural (literal or bound static name) or a type."""
        if isinstance(argument, Type) and not argument.args and isinstance(self.tenv.get(argument.name), int):
            return self.tenv[argument.name]
        return argument if isinstance(argument, int) else self.resolve(argument, node)

    def define(self, ty: Type, node=None):
        """Validate a concrete record or sum once, after the layouts it contains by value."""
        if ty in self.layouts or ty.name not in self.types:
            return
        self.layouts[ty] = None
        generics = (n for n, _ in self.p.generics.get(ty.name, []))
        with self.within(self.p.modules.get(ty.name, ""), dict(zip(generics, ty.args, strict=True))):
            if ty.name in self.p.records:
                fields = self.p.records[ty.name]
                if not fields:
                    fail("E-RECORD", "Empty records are outside this ABI subset.", node)
                if len({x for x, _ in fields}) != len(fields):
                    fail("E-DUPLICATE", f"Duplicate field in {ty.name}.", node)
                layout: Any = [(n, self.member(t, "E-RECORD-TYPE", "Record fields")) for n, t in fields]
            else:
                variants = self.p.sums.get(ty.name) or [(v, None) for v in self.p.enums[ty.name]]
                if not variants or len({v for v, _ in variants}) != len(variants):
                    fail("E-ENUM", f"Invalid enum {ty.name}.", node)
                layout = {v: t and self.member(t, "E-SUM-PAYLOAD", "Payloads") for v, t in variants}
        del self.layouts[ty]
        self.layouts[ty] = layout

    def member(self, declared: Type, code: str, what: str) -> Type:
        if declared.mode != "value" or declared == VOID:
            fail(code, f"{what} must be value types, never borrows or void.")
        ty = self.resolve(declared)
        if ty.name in PINNED:
            fail("E-PINNED", f"{ty.display()} lives where it is declared; share it by ro borrow.")
        if ty in self.layouts and self.layouts[ty] is None:
            fail(code, f"{what} cannot contain their own type by value; reach it through a Buf.")
        return ty

    def kind(self, ty: Type) -> str:
        """copy < affine (moves, implicit release) < linear (must be consumed exactly once)."""
        if ty.mode != "value" or ty.name in CPP or ty.name == "fn" or ty.name in self.p.enums:
            return "copy"
        if ty not in self.kinds:
            self.kinds[ty] = "affine"  # A recursive owner is only reachable through a Buf.
            layout = self.layouts.get(ty) or {}
            parts = [t for _, t in layout] if isinstance(layout, list) else [t for t in layout.values() if t]
            inline = parts or (ty.args[:1] if ty.name == "Array" else [])
            ranks = [KINDS.index(self.kind(t)) for t in inline]
            linear = "linear" in self.p.attributes.get(ty.name, ()) or ty.name == "Ticket"
            own = 2 if linear else int(ty.name in {"Buf", "dyn", "Dyn", "Atomic", "Mutex"})
            self.kinds[ty] = KINDS[max([own, *ranks])]
        return self.kinds[ty]

    def sizeof(self, ty: Type) -> int:
        """A conservative byte size (8-byte alignment) for the explicit stack budget."""
        if ty.name in CPP:
            return 1 if ty.name == "bool" else WIDTH.get(ty.name, 32 if ty.name == "f32" else 64) // 8
        if ty.name == "Array":
            return self.sizeof(ty.args[0]) * ty.args[1]
        layout = self.layouts.get(ty)
        parts = [t for _, t in layout] if isinstance(layout, list) else [t for t in (layout or {}).values() if t]
        return 8 * isinstance(layout, dict) + sum(-(-self.sizeof(t) // 8) * 8 for t in parts) or 16

    # Whole program -----------------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        for name in self.types:
            if not self.p.generics.get(name) and (name != "Order" or "Order" in self.p.modules):
                with self.within(self.p.modules.get(name, "")):
                    self.define(Type(name))
        for name, (declared, value) in self.p.consts.items():
            with self.within(self.p.modules.get(name, "")):
                ty = self.expr(value, self.resolve(declared, value))
            if ty.name not in CPP or value.tag not in {"int", "float", "bool"}:
                fail("E-CONST", "A constant is one scalar literal.", value)
        concrete = [f for f in self.p.functions if not f.generics or f.bindings]
        for f in concrete:
            self.signature(f)
        for f in concrete:  # Generic instances are appended, and checked, at their first use.
            self.function(f)
        instantiated = {f.source_name for f in self.p.functions if f.bindings}
        for f in [f for f in self.p.functions if f.generics and not f.bindings]:
            self.p.functions.remove(f)
            if f.name not in instantiated:
                if all(k == "nat" for _, k in f.generics) and not f.module and not f.owner:
                    fail("E-UNINSTANTIATED", f"Static function {f.name} has no family; "
                         "unused templates are not silently ignored.")  # fmt: skip
                self.unchecked.append(f.name)
        effects = fixed_point(self)
        audit(self, effects)
        kernels = [(f.name, True, f) for f in self.p.functions if f.kernel]
        for callee, device, node in [*self.lane_calls, *kernels]:
            allowed = PURE if device else LANE_SAFE
            reach = ("read:", "write:") if callee in {k for k, _, _ in kernels} else ("read:",)
            excess = sorted(x for x in effects[callee] if x not in allowed and not x.startswith(reach))
            if excess:
                fail("E-PARALLEL-CALL", f"A lane cannot call {callee}: it may {', '.join(excess)}.", node)
            todo = [callee] if device else []
            while todo:  # Everything a device lane reaches is compiled for the device as well.
                name = todo.pop()
                if name not in self.device_functions:
                    self.device_functions.add(name)
                    todo += self.calls[name]
        return {
            n: {
                "effects": sorted(effects[n]),
                "calls": sorted(self.calls[n]),
                "syntactic_check_sites": self.checks[n],
                "heap_allocations": sum(x["kind"] == "buffer" for x in self.resources[n]),
                "allocation_count_kind": "syntactic-sites-not-dynamic-bound",
                "local_storage": self.resources[n],
                "implicit_synchronization": 0,
                "status": "prototype-checked-not-proved",
            }
            for n in effects
        }

    def signature(self, f: Function):
        """Resolve a concrete signature in its own module, once, before any call site needs it."""
        if f.name in self.signed:
            return
        self.signed.add(f.name)
        tenv = dict(f.bindings)
        with self.within(f.module, tenv):
            if f.owner:
                tenv["Self"] = self.resolve(f.owner[1], f)
            seen: dict[str, Type] = {}
            for i, (n, declared) in enumerate(f.params):
                ty = self.resolve(declared, f)
                if n in seen or ty == VOID:
                    fail("E-PARAM", f"Invalid or duplicate parameter {n}.", f)
                if is_view(ty) and ty.extent.isdigit() and int(ty.extent) > 2**63 - 1:
                    fail("E-EXTENT", "Static extent exceeds bootstrap bound.", f)
                later = f.extern and (ty.extent, USIZE) in f.params  # C puts the length where it likes.
                if is_view(ty) and not ty.extent.isdigit() and not later and seen.get(ty.extent) != USIZE:
                    fail("E-EXTENT", "Dynamic extent must name an earlier immutable usize parameter.", f)
                seen[n] = ty
                f.params[i] = (n, ty)
            f.ret = self.resolve(f.ret, f)
        if any(t.name in PINNED and t.mode == "value" for t in [f.ret, *(t for _, t in f.params)]):
            fail("E-PINNED", "Tickets, atomics and mutexes cannot be passed or returned by value; borrow them.", f)
        if f.ret.mode != "value":
            fail("E-ESCAPE", "Borrowed view returns are not in the native subset.", f)
        if f.extern and f.effects is None:
            fail("E-EXTERN-EFFECTS", "An extern declaration states its effects; its body is not visible.", f)

    def function(self, f: Function):
        self.signature(f)
        outer, self.s = self.s, Scope(f, dict(f.bindings), module=f.module, device_depth=int(f.kernel))
        self.call_edges[f.name], self.resources[f.name] = [], []
        if f.owner:
            self.tenv["Self"] = self.resolve(f.owner[1], f)
        for n, ty in f.params:
            self.env[n] = Binding(ty)
            if is_view(ty):
                self.effects.add("ffi_precondition")
                self.guard("view_entry")
            if ty.name in self.p.enums and ty.mode == "value":
                self.guard("enum_entry")
        for name, value in f.bindings.items():
            if isinstance(value, int):
                if name in self.env:
                    fail("E-DUPLICATE", "Static binder shadows a parameter.", f)
                self.env[name] = Binding(USIZE, constant=value)
        if f.extern:
            self.effects |= {"ffi:" + (f.symbol or f.name.rsplit(".", 1)[-1]), *(f.effects or ())}
            self.effects |= {("write:" if t.mode == "rw" else "read:") + n for n, t in f.params if t.mode != "value"}
        elif not self.block(f.body) and f.ret != VOID:
            fail("E-RETURN", f"Not all paths of {f.name} return.", f)
        borrowed = {n for n, t in f.params if t.mode != "value"}
        self.local_effects[f.name] = {exposed(e, borrowed) for e in self.effects}
        self.calls[f.name], self.checks[f.name] = self.callset, self.counts
        self.s = outer

    # Statements --------------------------------------------------------------------------------

    def effect(self, name: str):
        self.effects.add(name)

    def guard(self, kind: str):
        self.effects.add("trap")
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def expect(self, got: Type, want: Type, e: Any):
        if got != want:
            fail("E-TYPE-MISMATCH", f"Expected {want.display()}, got {got.display()}.", e,
                 expected_type=want.display(), actual_type=got.display())  # fmt: skip

    def bind(self, name: str, binding: Binding, node: Any, message: str | None = None):
        if name in self.env:
            fail("E-SHADOW", message or f"{name} is already bound; shadowing is forbidden in this subset.", node)
        binding.depth = self.loop_depth
        self.env[name] = binding
        self.moved.discard(name)
        self.deferred.discard(name)

    def leaks(self, names, node: Any):
        for n in names:
            if self.kind(self.env[n].ty) == "linear" and n not in self.moved | self.deferred:
                fail("E-LINEAR-LEAK", f"{n} is linear: consume it, or defer its consumer, on every path.", node)

    def block(self, ss: list[Stmt]) -> Any:
        saved, deferred, returned = dict(self.env), set(self.deferred), False
        for s in ss:
            if returned:
                fail("E-UNREACHABLE", "Statement after unconditional return.", s)
            returned = self.stmt(s)
        if not returned:
            self.leaks(set(self.env) - set(saved), ss[-1] if ss else self.f)
        self.moved |= (self.deferred - deferred) & set(saved)  # Its cleanup has now run: gone for good.
        self.leases = {t: held for t, held in self.leases.items() if t in saved}
        self.env, self.deferred = saved, deferred
        return returned

    def stmt(self, s: Stmt) -> Any:
        """False when control falls through, True after a return, "jump" after break or continue."""
        handler = getattr(self, "s_" + s.tag, None)
        if handler is None:
            fail("E-INTERNAL", f"Unknown statement {s.tag}.", s)
        return handler(s) or False

    def s_buffer(self, s: Stmt):
        if s.name in self.env:
            fail("E-SHADOW", "Local owner name is already bound.", s)
        element = self.resolve(s.ty, s)
        if element == VOID:
            fail("E-OWNER-ELEMENT", "Local buffers hold values; void has none.", s)
        if self.kind(element) == "linear":
            fail("E-LINEAR-STORAGE", "Zeroed storage cannot hold linear values: a zero would be a forged one.", s)
        extent = s.exprs[0]
        self.expr(extent, USIZE)
        if isinstance(extent.ref, Expr):  # A named constant is its literal, here and in the extent identity.
            extent = s.exprs[0] = extent.ref
        if extent.tag not in {"name", "int"}:
            fail("E-OWNER-EXTENT", "Bind a computed capacity to an immutable usize first.", extent)
        if extent.tag == "name" and self.env[extent.val].mutable:
            fail("E-OWNER-EXTENT", "A buffer capacity must be immutable.", extent)
        if extent.tag == "int" and int(extent.val) > 2**63 - 1:
            fail("E-OWNER-EXTENT", "Capacity exceeds the native object limit.", extent)
        resource = {"name": s.name, "kind": s.tag, "element": element.display(), "capacity": extent.val,
                    "initialization": "zeroed", "release": "lexical-on-normal-exit", "line": s.line}  # fmt: skip
        if s.ty.place != "host":
            resource["placement"] = s.ty.place
        if s.tag == "stack":
            if extent.tag != "int":
                fail("E-STACK-EXTENT", "Stack storage needs a literal capacity.", extent)
            resource["bytes"] = int(extent.val) * self.sizeof(element)
            prior = sum(x.get("bytes", 0) for x in self.resources[self.f.name])
            if prior + resource["bytes"] > 65536:
                fail("E-STACK-LIMIT", "Explicit stack declarations total at most 65536 bytes per function.", extent)
        # The extent is a literal or immutable scalar, so this identity holds for the whole borrow.
        place = s.ref = s.ty.place
        s.ty = element
        self.bind(s.name, Binding(Type(element.name, "rw", extent.val, element.args, place)), s)
        self.resources[self.f.name].append(resource)
        self.effect("zero_init")
        if s.tag == "stack" and place != "host":
            fail("E-PLACE", "Stack storage lives where its code runs; it takes no placement.", s)
        if s.tag == "buffer" and self.device_depth:
            fail("E-PLACEMENT", "A device lane cannot allocate; declare the buffer outside the region.", s)
        if s.tag == "buffer":
            self.effects |= {"alloc", "free"} if place == "host" else {"gpu_alloc", "gpu_free"}
            self.guard("allocation")
        else:
            self.effect("stack_storage")

    s_stack = s_buffer

    def s_let(self, s: Stmt):
        if s.name in self.env:
            fail("E-SHADOW", f"{s.name} is already bound; shadowing is forbidden in this subset.", s)
        self.spawning = s.name if s.exprs[0].tag == "spawn" else ""
        ty = self.expr(s.exprs[0], self.resolve(s.ty, s) if s.ty else None)
        if ty == VOID or (ty.mode != "value" and s.exprs[0].tag != "str"):
            fail("E-VIEW-ALIAS", "Local view aliases and void values are outside this subset.", s)
        s.ty = ty
        self.bind(s.name, Binding(ty, s.tag == "reg"), s)

    s_reg = s_let

    def s_compact(self, s: Stmt):
        out, hi, pred, value = s.exprs
        if s.name in self.env or s.binder in self.env or s.name == s.binder:
            fail("E-SHADOW", "Collector names must be fresh and distinct.", s)
        target = self.env.get(out.val)
        if self.lanes:  # Every lane would fill the same prefix.
            fail("E-PARALLEL-NEST", "A collector is a whole-array form; it cannot run inside a lane.", s)
        if target is None or not is_view(target.ty) or target.ty.mode != "rw":
            fail("E-WRITE-LEASE", "Compaction target must be a direct rw parameter.", out)
        self.expr(out, consume=False)
        self.expr(hi, USIZE)
        if self.extent_of(hi) != target.ty.extent:
            fail("E-COLLECT-CAPACITY", "Compaction requires iteration extent equal to output capacity.", hi)
        s.ref = "device" if target.ty.place == "device" else "host"
        outer, self.device_depth = self.device_depth, int(s.ref == "device")
        self.env[s.binder] = Binding(USIZE)
        self.expr(pred, BOOL)
        self.expr(value, target.ty.value)
        self.device_depth = outer
        if s.ref == "device":
            self.effect("par:device")

        def mentions(e: Expr) -> bool:
            return (e.tag == "name" and e.val == out.val) or any(mentions(x) for x in e.args)

        if mentions(pred) or mentions(value):
            fail("E-COLLECT-SELF-READ", "Collector predicate/projection cannot read its output.", s)
        del self.env[s.binder]
        self.env[s.name] = Binding(USIZE)
        self.effect("write:" + out.val)
        self.counts["bounded_collectors"] = self.counts.get("bounded_collectors", 0) + 1

    def s_assign(self, s: Stmt):
        self.expr(s.exprs[1], self.place(s.exprs[0], write=True))

    def s_break(self, s: Stmt):
        if not self.loop_depth:
            fail("E-LOOP-CONTROL", s.tag + " requires an enclosing loop.", s)
        self.leaks((n for n, b in self.env.items() if b.depth >= self.loop_depth), s)
        return "jump"  # Ends this lexical block but not the function: its moves still matter afterwards.

    s_continue = s_break

    def s_return(self, s: Stmt):
        ret, outer = self.closure or (self.f.ret, set())
        if self.lanes and not self.closure:
            fail("E-PARALLEL-CONTROL", "A lane cannot return from the enclosing function.", s)
        if ret == VOID:
            if s.exprs:
                fail("E-RETURN", "Void function cannot return a value.", s)
        elif not s.exprs:
            fail("E-RETURN", "Missing return value.", s)
        else:
            self.expr(s.exprs[0], ret)
        self.leaks(set(self.env) - outer, s)
        return True

    def branches(self, node: Any, runs: list) -> Any:
        """Alternatives start from one ownership state; a linear value must agree across them."""
        before, outcomes = set(self.moved), []
        for run in runs:
            self.moved = set(before)
            outcomes.append((run(), self.moved))
        falls = [moved for ended, moved in outcomes if not ended]
        everywhere: set[str] = set.intersection(*falls) if falls else set()
        for n in set().union(*falls) - everywhere:
            if n in self.env and self.kind(self.env[n].ty) == "linear":
                fail("E-LINEAR-BRANCH", f"{n} is consumed on some paths only.", node)
        # A branch that returned cannot reach what follows; one that jumped (break/continue) can.
        self.moved = set().union(before, *(moved for ended, moved in outcomes if ended is not True))
        ends = [ended for ended, _ in outcomes]
        return all(ends) and (True if all(e is True for e in ends) else "jump")

    def s_if(self, s: Stmt):
        self.expr(s.exprs[0], BOOL)
        both = self.branches(s, [lambda: self.block(s.body), lambda: self.block(s.other)])
        return both if s.other else False

    def s_match(self, s: Stmt):
        ty = self.expr(s.exprs[0])
        layout = self.layouts.get(ty)
        if ty.mode != "value" or not isinstance(layout, dict):
            fail("E-MATCH-TYPE", "match requires a declared enum or tagged sum.", s)
        given = [a.variant.rsplit(".", 1)[1] if self.qualify(a.variant.rsplit(".", 1)[0], self.types) == ty.name
                 else a.variant for a in s.arms]  # fmt: skip
        if len(set(given)) != len(given):
            fail("E-MATCH-DUPLICATE", "A variant may appear only once.", s)
        if set(given) != set(layout):
            fail("E-MATCH-COVERAGE", "Every variant must have exactly one arm.", s,
                 missing_variants=sorted(f"{ty.name}.{v}" for v in set(layout) - set(given)),
                 unknown_variants=sorted(v if "." in v else f"{ty.name}.{v}" for v in set(given) - set(layout)))  # fmt: skip

        def arm_body(arm, payload):
            if bool(arm.binder) != (payload is not None):
                fail("E-MATCH-BINDING", "A payload arm binds exactly one value; a nullary arm binds none.", arm)
            if arm.binder:
                self.bind(arm.binder, Binding(payload), arm, "Payload binder must be fresh.")
            returned = self.block(arm.body)
            if arm.binder:
                if not returned:
                    self.leaks([arm.binder], arm)
                del self.env[arm.binder]
            return returned

        s.ref = given
        result = self.branches(s, [lambda a=a, v=v: arm_body(a, layout[v]) for a, v in zip(s.arms, given, strict=True)])
        self.guard("tag")
        return result

    def loop(self, s: Stmt):
        outer, before = set(self.env), set(self.moved)
        self.loop_depth += 1
        self.block(s.body)
        self.loop_depth -= 1
        repeated = (self.moved - before) & outer
        if repeated:
            fail("E-MOVE-IN-LOOP", f"{sorted(repeated)[0]} would be moved once per iteration.", s)

    def s_while(self, s: Stmt):
        self.expr(s.exprs[0], BOOL)
        self.effect("diverge")
        self.loop(s)

    def s_for(self, s: Stmt):
        self.expr(s.exprs[0], USIZE)
        self.expr(s.exprs[1], USIZE)
        self.bind(s.name, Binding(USIZE), s, f"Loop binder {s.name} already exists.")
        self.loop(s)
        del self.env[s.name]

    def region(self, s: Stmt, exprs: list[Expr], run) -> Any:
        """Check a lane body; the placement of the views it indexes decides where it runs."""
        if self.lanes or self.f.kernel:
            fail("E-PARALLEL-NEST", "A lane cannot start another parallel region.", s)

        def places(e: Expr) -> set[str]:
            mine = {self.env[root(e).val].ty.place} if e.tag == "index" and root(e).val in self.env else set()
            return mine.union(*(places(a) for a in e.args))

        def scan(ss: list[Stmt]) -> set[str]:
            found = set().union(*(places(e) for x in ss for e in x.exprs))
            return found.union(*(scan(x.body) | scan(x.other) | scan([b for a in x.arms for b in a.body]) for x in ss))

        target = "device" if "device" in scan(s.body) | set().union(*(places(e) for e in exprs)) else "host"
        self.bind(s.binder or s.name, Binding(USIZE), s)
        binder = s.binder or s.name
        saved = self.lanes, self.device_depth, self.loop_depth, set(self.moved)
        self.lanes, self.device_depth, self.loop_depth = (
            Lanes(binder, set(self.env) - {binder}),
            int(target == "device"),
            0,
        )
        result = run()
        if (self.moved - saved[3]) & self.lanes.outer:
            fail("E-MOVE-IN-LOOP", "An outer owner would be moved once per lane.", s)
        written = {name for name, _, write, _ in self.lanes.accesses if write}
        for name, at_binder, _, node in self.lanes.accesses:
            if name in written and not at_binder:
                fail(
                    "E-PARALLEL-RACE",
                    f"{name} is written by lanes, so every lane may touch only {name}[{binder}].",
                    node,
                )
        self.lanes, self.device_depth, self.loop_depth = saved[:3]
        del self.env[binder]
        if s.tag == "parallel" or target == "device":  # A host reduction is an ordinary in-order fold.
            self.effect("par:" + target)
            self.counts["parallel_regions"] = self.counts.get("parallel_regions", 0) + 1
        s.ref = target
        return result

    def s_parallel(self, s: Stmt):
        self.expr(s.exprs[0], USIZE)
        self.region(s, [], lambda: self.block(s.body))

    def s_reduce(self, s: Stmt):
        hi, value = s.exprs
        self.expr(hi, USIZE)
        declared = self.resolve(s.ty, s) if s.ty else None
        ty = self.region(s, [value], lambda: self.expr(value, declared))
        wanted = UNSIGNED if s.op in WRAPPING or s.op in {"&", "|", "^"} else INT if s.op in {"min", "max"} else FLOAT
        if ty.mode != "value" or ty.name not in wanted:
            fail("E-REDUCE-OP", f"reduce {s.op} combines {sorted(wanted)[0]}-like scalars in an unspecified order; "
                 "checked integer + has no order-independent trap.", s)  # fmt: skip
        s.ty = ty
        self.bind(s.name, Binding(ty), s)

    def s_expr(self, s: Stmt):
        ty = self.expr(s.exprs[0])
        if s.exprs[0].tag not in {"call", "try"}:
            fail("E-DISCARD", "Only calls may be used as discarded expression statements.", s)
        if ty != VOID:
            fail("E-DISCARD", "Nonvoid result must be bound or returned.", s)

    def s_block(self, s: Stmt):
        return self.block(s.body)

    def s_unsafe(self, s: Stmt):
        self.unsafe_depth += 1
        self.counts["unsafe_blocks"] = self.counts.get("unsafe_blocks", 0) + 1
        returned = self.block(s.body)
        self.unsafe_depth -= 1
        return returned

    def s_defer(self, s: Stmt):
        """A visible cleanup: checked here, run at every normal exit of the enclosing block."""
        inner = s.body[0]
        self.host_only(s, "defer schedules a host call")
        if inner.tag != "expr" or inner.exprs[0].tag != "call":
            fail("E-DEFER", "defer schedules exactly one call.", s)
        before, leases = set(self.moved), dict(self.leases)
        self.stmt(inner)
        self.deferred |= self.moved - before
        self.moved, self.leases = before, leases  # The call runs at block exit; until then nothing is returned.

    # Places and expressions --------------------------------------------------------------------

    def extent_of(self, e: Expr) -> str | None:
        """The name/literal identity of an extent expression, or None when it has none."""
        if e.tag == "name" and e.val not in self.env:  # A named constant is its literal.
            const = self.qualify(e.val, self.p.consts)
            return self.p.consts[const][1].val if const else e.val
        if e.tag in {"name", "int"}:
            return e.val
        if e.tag == "call" and e.val == "len" and len(e.args) == 1 and root(e.args[0]).tag == "name":
            ty = e.args[0].ty or self.expr(e.args[0], consume=False)
            return ty.extent if is_view(ty) else f"len({self.identity(e.args[0])})" if ty.name == "Buf" else None
        return None

    def writable(self, e: Expr) -> bool:
        b = self.env.get(root(e).val) if root(e).tag == "name" else None
        return b is not None and (b.ty.mode == "rw" or (b.ty.mode == "value" and b.mutable))

    def place(self, e: Expr, write: bool = False) -> Type:
        """Type a storage location without consuming it; `write` demands mutability."""
        if e.tag == "name":
            b = self.env.get(e.val)
            if write and (b is None or is_view(b.ty) or not self.writable(e)):
                fail("E-IMMUTABLE", f"{e.val} is not a mutable local.", e)
            if write and b.ty.mode == "rw":
                self.effect("write:" + e.val)
            if write or (b is not None and not is_view(b.ty) and b.ty.name not in {"Buf", "Array"}):
                self.leased(e.val, "rw" if write else "ro", e)
            if write and self.lanes and e.val in self.lanes.outer:
                fail(
                    "E-PARALLEL-WRITE",
                    f"Every lane would write {e.val}; write element [{self.lanes.binder}] or reduce.",
                    e,
                )
            return self.expr(e, consume=False)
        if e.tag == "index":
            if write and not self.writable(e):
                fail("E-WRITE-LEASE", "Indexed assignment requires a direct rw slice parameter.", e)
            if write:
                self.effect("write:" + root(e).val)
            e.ty = self.e_index(e, None, read=not write)
            return e.ty
        if e.tag == "field":
            self.place(e.args[0], write)
            return self.expr(e, consume=False)
        fail("E-LVALUE", "Assignment requires a mutable variable, field, or rw element.", e)

    def expr(self, e: Expr, expected: Type | None = None, consume: bool = True) -> Type:
        if id(e) in self.early:
            ty = e.ty
        else:
            self.nodes += 1
            if self.nodes > MAX_NODES:
                fail("E-AST-LIMIT", "Expanded typechecking exceeds 200000 expression visits.", e)
            handler = getattr(self, "e_" + e.tag, None)
            if handler is None:
                fail("E-INTERNAL", f"Unknown expression {e.tag}.", e)
            ty = e.ty = handler(e, expected)
        if consume and ty.mode == "value" and self.kind(ty) != "copy":
            self.consume(e)
        if expected:
            self.expect(ty, expected, e)
        if self.capture_sites and e.start >= 0:
            self.sites.append({
                "symbol": self.f.name, "start": e.start, "end": e.end, "tag": e.tag, "type": ty.display(),
                "expected_type": expected.display() if expected else None,
                "bindings": {n: {"type": b.ty.display(), "mutable": b.mutable, "constant": b.constant}
                             for n, b in self.env.items()},
            })  # fmt: skip
        return ty

    def peek(self, e: Expr) -> Type:
        """Type an argument before its call is resolved; the call will not evaluate it again."""
        ty = self.expr(e, consume=False)
        self.early[id(e)] = e
        return ty

    def host_only(self, node: Any, what: str):
        if self.device_depth:
            fail("E-PLACEMENT", f"{what}; a device lane cannot use it.", node)

    def stable(self, name: str) -> bool:
        """A name whose value cannot change while it is in scope: a parameter, a let, a loop binder."""
        return name in self.env and not self.env[name].mutable and self.env[name].ty.mode == "value"

    def where(self, e: Expr) -> str:
        return path(e, self.stable)

    def identity(self, e: Expr) -> str:
        """Which storage an extent belongs to: exact for a literal or stable index, unique otherwise."""
        if e.tag == "index":
            i, self.unique = e.args[1], self.unique + 1
            key = i.val if i.tag == "int" or (i.tag == "name" and self.stable(i.val)) else f"?{self.unique}"
            return f"{self.identity(e.args[0])}[{key}]"
        return self.identity(e.args[0]) + "." + e.val if e.tag == "field" else e.val

    def leased(self, place: str, mode: str, node: Any):
        """While a task holds a borrow, nobody else may write it, or touch it if the task writes it."""
        for ticket, held in self.leases.items():
            if any(overlaps(place, p) and "rw" in (mode, m) for p, m in held):
                fail("E-LEASED", f"{place} is lent to task {ticket} until wait({ticket}).", node)

    def consume(self, e: Expr):
        """An owner used as a value moves; its name is dead afterwards."""
        waited = e.ty.name == "Ticket" and self.spawning == "<wait>"
        if e.tag in {"name", "field", "index"} and e.ty.name in PINNED and not waited:
            fail("E-PINNED", f"{e.ty.display()} cannot move; wait() a ticket, borrow an atomic or mutex.", e)
        if e.tag == "name":
            self.leased(e.val, "rw", e)
            if e.val in self.moved | self.deferred:
                fail("E-MOVED", f"{e.val} was already moved or scheduled for cleanup.", e)
            if self.env[e.val].ty.mode != "value":
                fail("E-MOVE-BORROW", f"{e.val} is borrowed; take() or swap() its contents instead.", e)
            self.moved.add(e.val)
            e.ref = "move"
        elif e.tag == "index" or (e.tag == "field" and not isinstance(e.ref, tuple)):  # Enum.None is a value.
            fail("E-PARTIAL-MOVE", "An owner cannot be moved out of a place; use take() or swap().", e)

    def e_int(self, e: Expr, expected: Type | None) -> Type:
        ty = expected if expected and expected.mode == "value" and expected.name in NUMERIC else Type("u64")
        n = int(e.val)
        if ty.name in WIDTH:
            if n >= 2 ** (WIDTH[ty.name] - (ty.name in SIGNED)):
                fail("E-LITERAL-RANGE", f"Literal is not representable in {ty.name}.", e)
        elif n > 2**53:
            fail("E-LITERAL-RANGE", "Large integer-to-float literals require a checked explicit conversion.", e)
        return ty

    def e_float(self, e: Expr, expected: Type | None) -> Type:
        ty = expected if expected and expected.name in FLOAT else Type("f64")
        v = float(e.val)
        if not math.isfinite(v) or (ty.name == "f32" and abs(v) > 3.4028234663852886e38):
            fail("E-LITERAL-RANGE", "Nonfinite/overflowing floating literal.", e)
        return ty

    def e_bool(self, e: Expr, expected: Type | None) -> Type:
        return BOOL

    def e_str(self, e: Expr, expected: Type | None) -> Type:
        return Type("u8", "ro", str(len(e.val.encode("latin-1", "replace"))))

    def e_name(self, e: Expr, expected: Type | None) -> Type:
        b = self.env.get(e.val)
        if b is None:
            const = self.qualify(e.val, self.p.consts, node=e)
            value = self.function_value(e, expected) if const is None and expected and expected.name == "fn" else None
            if value:
                return value
            if const is None:
                fail("E-UNBOUND", f"Unbound name {e.val}.", e, available_names=sorted(self.env),
                     expected_type=expected.display() if expected else None)  # fmt: skip
            e.ref = self.p.consts[const][1]
            with self.within(self.p.modules.get(const, "")):
                return self.resolve(self.p.consts[const][0], e)
        if e.val in self.moved:
            fail("E-MOVED", f"{e.val} was moved.", e)
        if not is_view(b.ty) and b.ty.name not in {"Buf", "Array"}:  # Arrays are checked per element or part.
            self.leased(e.val, "ro", e)
        e.ref = "mut" if b.mutable or b.ty.mode == "rw" else b.constant
        if b.ty.mode != "value" and not b.ty.extent:  # A single borrow reads through to its value.
            self.effect("read:" + e.val)
            return b.ty.value
        return b.ty

    def e_index(self, e: Expr, expected: Type | None, read: bool = True) -> Type:
        value = self.function_value(e, expected) if expected and expected.name == "fn" else None
        if value:
            return value
        if len(e.args) != 2:
            fail("E-INDEX", "An index has exactly one position.", e)
        a, i = e.args
        ty = self.expr(a, consume=False)
        if not is_view(ty) and ty.name not in {"Buf", "Array"}:
            fail("E-INDEX", "Only views can be indexed.", e)
        outer = self.lanes.outer if self.lanes else {n for n, _ in self.f.params}
        private = self.device_depth and root(a).tag == "name" and root(a).val not in outer
        if (ty.place == "device") != bool(self.device_depth) and ty.place != "unified" and not private:
            fail(
                "E-PLACEMENT",
                f"{ty.place} memory is not addressable from {'device' if self.device_depth else 'host'} code.",
                e,
            )
        self.expr(i, USIZE)
        self.guard("bounds")
        if read and root(a).tag == "name":
            self.effect("read:" + root(a).val)
        if self.lanes and root(a).val in self.lanes.outer:
            self.lanes.accesses.append((root(a).val, i.tag == "name" and i.val == self.lanes.binder, not read, e))
        if root(a).tag == "name":
            self.leased(self.where(e), "ro" if read else "rw", e)
        return ty.value if is_view(ty) else ty.args[0]

    def e_field(self, e: Expr, expected: Type | None) -> Type:
        enum = self.named_type(e.args[0])
        if enum is not None:
            return self.variant(enum, e.val, [], e, expected)
        const = self.qualify(path(e), self.p.consts, node=e) if root(e).val not in self.env else None
        if const:
            e.tag, e.val, e.args = "name", path(e), []
            return self.e_name(e, expected)
        at = self.expr(e.args[0], consume=False)
        layout = self.layouts.get(at)
        if at.mode != "value" or not isinstance(layout, list):
            fail("E-FIELD", "Field access requires a record.", e)
        if e.val not in dict(layout):
            fail("E-FIELD", f"Unknown field {e.val}.", e)
        return dict(layout)[e.val]

    def named_type(self, e: Expr) -> Type | None:
        """`Enum`, `module.Enum` or `Enum[args]` in expression position, unless a local hides it."""
        args: tuple = ()
        if e.tag == "index":
            e, args = e.args[0], tuple(self.type_argument(a) for a in e.args[1:])
        if root(e).tag != "name" or root(e).val in self.env or "[" in path(e):
            return None
        name = self.qualify(path(e), self.p.sums, self.p.enums, node=e)
        return Type(name, args=args) if name else None

    def type_argument(self, e: Expr) -> Any:
        if e.tag == "int":
            return int(e.val)
        if e.tag == "index":
            return Type(path(e.args[0]), args=tuple(self.type_argument(a) for a in e.args[1:]))
        return Type(path(e))

    def variant(self, enum: Type, variant: str, args: list[Expr], e: Expr, expected: Type | None) -> Type:
        variants = dict(self.p.sums.get(enum.name) or [(v, None) for v in self.p.enums[enum.name]])
        if variant not in variants:
            fail("E-ENUM-VARIANT", f"Unknown {enum.name}.{variant}.", e, available_variants=list(variants))
        if len(args) != (1 if variants[variant] else 0):
            fail("E-SUM-ARITY", "Constructor arguments must match the declared payload.", e)
        generics = [g for g, _ in self.p.generics.get(enum.name, [])]
        if generics and not enum.args:  # Infer from the expected type, else from the payload.
            bound: dict[str, Any] = {}
            if expected and expected.name == enum.name:
                bound = dict(zip(generics, expected.args, strict=True))
            elif args:
                with self.within(self.p.modules.get(enum.name, "")):
                    self.unify(variants[variant], self.peek(args[0]), bound, set(generics))
            if set(bound) != set(generics):
                fail("E-INFER", f"Cannot infer the type arguments; write {enum.name}[...].{variant}.", e)
            enum = Type(enum.name, args=tuple(bound[g] for g in generics))
        ty = self.resolve(enum, e)
        if args:
            self.expr(args[0], self.layouts[ty][variant])
        e.ref = ("variant", list(variants).index(variant))
        return ty

    def e_lambda(self, e: Expr, expected: Type | None) -> Type:
        """A closure exists only as a `ro<fn(...)>` argument, so it can never outlive what it captures."""
        f: Function = e.ref
        if expected is None or expected.name != "fn" or expected.mode != "ro" or self.device_depth:
            fail("E-CLOSURE", "A closure is written directly as an argument to a ro<fn(...)> parameter on the host.", e)
        saved = dict(self.env), self.closure, self.loop_depth, set(self.moved)
        f.params = [(n, self.resolve(t, e)) for n, t in f.params]
        f.ret = self.resolve(f.ret, e)
        self.expect(Type("fn", "ro", args=(*(t for _, t in f.params), f.ret)), expected, e)
        self.closure, self.loop_depth = (f.ret, set(self.env)), 0
        for n, t in f.params:
            self.bind(n, Binding(t), e)
        if not self.block(f.body) and f.ret != VOID:
            fail("E-RETURN", "Not all paths of the closure return.", e)
        if (self.moved - saved[3]) & set(saved[0]):
            fail("E-MOVE-IN-LOOP", "A closure may run many times; it cannot move an outer owner.", e)
        self.env, self.closure, self.loop_depth = saved[:3]
        return expected

    def function_value(self, e: Expr, want: Type) -> Type | None:
        """A declared function named where a fn value is expected; it counts as called here."""
        named, targs = (e.args[0], e.args[1:]) if e.tag == "index" else (e, [])
        free = root(named).tag == "name" and root(named).val not in self.env
        name = self.qualify(path(named), self.fs, node=e) if free else None
        if name is None:
            return None
        g = self.fs[name]
        if targs and g.generics:  # ascending[u64]
            bound = dict(
                zip((n for n, _ in g.generics), (self.static(self.type_argument(a), e) for a in targs), strict=False)
            )
            g = self.instantiate(g, bound, e)
            e.args = []
        name = g.name
        self.signature(g)
        if (g.generics and not g.bindings) or g.extern or any(t.mode != "value" for _, t in g.params):
            fail("E-FN-TYPE", f"{name} cannot be a function value: only plain functions of values qualify.", e)
        self.call_edges[self.f.name].append((name, {}))
        self.callset.add(name)
        self.address_taken.add(name)
        e.ref, e.tag = g, "function"
        return Type("fn", want.mode, args=(*(t for _, t in g.params), g.ret))

    def lend(self, a: Expr, mode: str, borrows: list[tuple[str, str]]) -> str:
        """A named place lent to a call: leased places and lanes object here; returns its root name."""
        if root(a).tag != "name" or root(a).val not in self.env:
            return ""
        self.leased(self.where(a), mode, a)
        borrows.append((self.where(a), mode))
        if self.lanes and root(a).val in self.lanes.outer:
            self.lanes.accesses.append((root(a).val, False, mode == "rw", a))
        return root(a).val

    def disjoint(self, borrows: list[tuple[str, str]], node: Any):
        for i, (place, m) in enumerate(borrows):
            if any(overlaps(place, other) and "rw" in (m, k) for other, k in borrows[i + 1 :]):
                fail("E-ALIAS", "A mutable view cannot be passed to overlapping call arguments.", node)

    def indirect(self, e: Expr, target: Binding, args: list[Expr]) -> Type:
        self.host_only(e, "A function value is a host code pointer")
        *params, ret = target.ty.args
        if len(args) != len(params):
            fail("E-ARITY", f"{e.val} expects {len(params)} arguments.", e)
        borrows: list[tuple[str, str]] = []
        for a, want in zip(args, params, strict=True):
            if want.mode == "value":
                self.expr(a, want)
                continue
            if want.mode == "rw" and not self.writable(a):
                fail("E-WRITE-LEASE", "A mutable borrow needs a mutable local or an rw borrow.", a)
            self.expect(self.expr(a, consume=False).value, want.value, a)
            lent = self.lend(a, want.mode, borrows)  # No callee row exists to rename: charge the caller now.
            self.effects |= {("write:" if want.mode == "rw" else "read:") + lent} if lent else set()
        self.disjoint(borrows, e)
        self.effect("indirect_call")
        if target.ty.mode == "value":
            self.guard("callable")
        e.ref = ("indirect", target.ty)
        return ret

    def shared(self, e: Expr, n: str, ty: Type, args: list[Expr]) -> Type:
        """Interior mutability, and only here: every atomic access names its memory order."""
        self.host_only(e, "Atomics and mutexes are host objects")
        e.ref = ("shared", ty)
        if ty.name == "Mutex":
            if n != "with" or len(args) != 1 or args[0].tag != "lambda":
                fail("E-CALLEE", "A mutex has one operation: m.with(|state:rw<T>| { ... }).", e)
            ret = self.resolve(args[0].ref.ret, e)
            self.expr(args[0], Type("fn", "ro", args=(Type(ty.args[0].name, "rw", args=ty.args[0].args), ret)))
            self.effect("lock")
            return ret
        order = Type(self.qualify("Order", self.p.enums) or "Order")
        if n == "compare_exchange":
            wanted = [ty.args[0], ty.args[0], order, order]
        elif n in ATOMIC_OPS:
            wanted = [ty.args[0]] * ATOMIC_OPS[n] + [order]
        else:
            fail("E-CALLEE", f"An atomic offers {', '.join(ATOMIC_OPS)} and compare_exchange.", e)
        if len(args) != len(wanted):
            fail("E-ARITY", f"{n} takes {len(wanted) - 1} value(s) and an explicit memory order.", e)
        if n == "compare_exchange" and not self.writable(args[0]):
            fail("E-WRITE-LEASE", "compare_exchange updates its expected value; pass a mutable local.", args[0])
        for a, want in zip(args, wanted, strict=True):
            self.expect(self.place(a, write=True), want, a) if n == "compare_exchange" and a is args[0] else self.expr(
                a, want
            )
        self.effect("atomic")
        return BOOL if n == "compare_exchange" else VOID if n == "store" else ty.args[0]

    def e_spawn(self, e: Expr, expected: Type | None) -> Type:
        """`let t = spawn f(args);` runs f on its own thread; t lends f's borrows until wait(t)."""
        call, name = e.args[0], self.spawning
        if name in ("", "<wait>") or call.tag != "call" or self.lanes or self.closure:
            fail("E-SPAWN", "Write `let t = spawn f(args);` in a function body; a task is always named.", e)
        self.spawning = ""
        result = self.expr(call)
        if not isinstance(call.ref, Function) or any(a.tag == "lambda" for a in call.args):
            fail("E-SPAWN", "spawn runs a declared function, and a closure cannot follow it to another thread.", e)
        self.leases[name] = self.borrowed
        self.effect("spawn")
        return Type("Ticket", args=(result,))

    def e_try(self, e: Expr, expected: Type | None) -> Type:
        """`try x` yields the success payload or returns the failure from the enclosing function."""
        ty = self.expr(e.args[0])
        ret, outer = self.closure or (self.f.ret, set())
        if self.lanes and not self.closure:
            fail("E-PARALLEL-CONTROL", "A lane cannot return from the enclosing function.", e)
        layout, target = self.layouts.get(ty), self.layouts.get(ret)
        if not isinstance(layout, dict) or len(layout) != 2 or ty.name in self.p.enums:
            fail("E-TRY", "try needs a sum of exactly two variants: success first, failure second.", e)
        failure = list(layout.values())[1]
        fits = isinstance(target, dict) and len(target) == 2 and ret.name not in self.p.enums
        if not fits or list(target.values())[1] != failure:
            fail("E-TRY", f"try returns the failure of {ty.display()}, which {ret.display()} cannot carry.", e)
        self.leaks(set(self.env) - outer, e)
        e.ref = (*layout, ret, list(target)[1])
        return next(iter(layout.values())) or VOID

    def e_unary(self, e: Expr, expected: Type | None) -> Type:
        ty = self.expr(e.args[0], BOOL if e.val == "!" else expected)
        if ty.mode != "value":
            fail("E-OPERATOR", "Unary operator on a view.", e)
        if e.val == "!":
            self.expect(ty, BOOL, e)
        elif e.val == "~":
            if ty.name not in UNSIGNED:
                fail("E-OPERATOR", "Bitwise complement requires an unsigned integer.", e)
        elif ty.name in SIGNED:
            self.guard("overflow")
        elif ty.name not in FLOAT:
            fail("E-OPERATOR", "Negation requires a signed integer or floating value.", e)
        return ty

    def e_binary(self, e: Expr, expected: Type | None) -> Type:
        (a, b), op = e.args, e.val
        logical, compare = op in {"&&", "||"}, op in COMPARISONS
        hint = BOOL if logical else (None if compare else expected)
        # Literals adapt to their nonliteral peer; there is no general implicit conversion.
        if a.tag in {"int", "float"} and b.tag not in {"int", "float"}:
            right = self.expr(b, hint)
            left = self.expr(a, right)
        else:
            left = self.expr(a, hint)
            right = self.expr(b, left)
        self.expect(right, left, e)
        if left.mode != "value":
            fail("E-OPERATOR", "View operators are not implicit loops.", e)
        if logical:
            self.expect(left, BOOL, e)
        elif compare:
            if left.name not in SCALAR and not (op in {"==", "!="} and left.name in self.p.enums):
                fail("E-OPERATOR", "Comparison requires scalars or equality on enums.", e)
            return BOOL
        elif op in {"&", "|", "^"}:
            if left.name not in UNSIGNED:
                fail("E-OPERATOR", "Bitwise operation requires unsigned scalars.", e)
        elif left.name in INT:
            self.guard("division" if op in {"/", "%"} else "overflow")
        elif left.name not in FLOAT or op == "%":
            fail("E-OPERATOR", f"{op} not defined on {left.name}.", e)
        return left

    # Calls -------------------------------------------------------------------------------------

    def e_call(self, e: Expr, expected: Type | None) -> Type:
        n, args = e.val, e.args
        targs = tuple(e.ref or ()) if n == "Dyn" else tuple(self.static(a, e) for a in e.ref or ())
        receiver = None
        if "." in n and n.split(".")[0] in self.env:  # value.method(...) on a named place
            *names, n = n.split(".")
            receiver = Expr("name", names[0], [], e.line, e.col)
            for name in names[1:]:
                receiver = Expr("field", name, [receiver], e.line, e.col)
            args = e.args = [receiver, *args]
        elif n.startswith("."):
            n, receiver = n[1:], args[0]
        elif "." in n:
            enum = self.qualify(n.rsplit(".", 1)[0], self.p.sums, node=e)
            if enum:
                return self.variant(Type(enum, args=targs), n.rsplit(".", 1)[1], args, e, expected)
        e.val = n
        shared = self.peek(receiver) if receiver is not None else VOID
        if shared.name in {"Atomic", "Mutex"}:
            return self.shared(e, n, shared, args[1:])
        home = self.p.modules.get(shared.name, "") if receiver is not None else ""
        with self.within(home or self.module):  # A method is found in its receiver's home module first.
            method = self.qualify(n, self.fs, node=e) if home else None
        if method and self.p.modules.get(method, "") not in ("", self.module) and method not in self.p.public:
            fail("E-PRIVATE", f"{method} is private to module {self.p.modules[method]}.", e)
        if method:
            return self.invoke(e, self.fs[method], args, targs, expected)
        if n in self.env and self.env[n].ty.name == "fn":
            return self.indirect(e, self.env[n], args)
        if (n in BUILTINS and n not in self.fs) or n in NUMERIC or n in INTRINSIC_TYPES:
            e.ref = ("builtin", targs)
            return self.builtin(e, n, args, targs, expected)
        record = self.qualify(n, self.p.records, node=e)
        if record:
            return self.construct(e, record, args, targs, expected)
        name = self.qualify(n, self.fs, node=e)
        f = self.fs[name] if name else self.trait_member(e, n, args)
        if f is not None and isinstance(e.ref, tuple) and e.ref[0] == "dispatch":
            return f.ret
        if f is None:
            fail("E-CALLEE", "Qualified calls are declared tagged-sum constructors, not methods." if "." in n
                 else f"Unknown callable {n}; arbitrary C++ names are not allowed.", e)  # fmt: skip
        return self.invoke(e, f, args, targs, expected)

    def trait_member(self, e: Expr, n: str, args: list[Expr]) -> Function | None:
        """Static dispatch of a trait member on the type of its Self argument."""
        prefix, _, short = n.rpartition(".")
        for trait, members in self.p.traits.items():
            hidden = self.p.modules.get(trait, "") not in ("", self.module) and trait not in self.p.public
            if hidden or (prefix and self.qualify(prefix, self.p.traits) != trait):
                continue
            for declared in members:
                position = next((i for i, (_, t) in enumerate(declared.params) if t.name == "Self"), None)
                if declared.name == short and position is not None and position < len(args):
                    if self.peek(args[position]).value in (
                        Type("dyn", args=(Type(trait),)),
                        Type("Dyn", args=(Type(trait),)),
                    ):
                        return self.dispatch(e, trait, declared, position, args)
                    found = self.implementation(trait, self.peek(args[position]).value)
                    if short in found:
                        return found[short]
                    fail("E-TRAIT-IMPL", f"{args[position].ty.display()} does not implement {trait}.", e)
        return None

    def implementors(self, trait: str) -> dict[Type, dict[str, Function]]:
        """Every concrete `impl trait for T`, with resolved signatures: what a dyn call may reach."""
        found: dict[Type, dict[str, Function]] = {}
        for f in list(self.fs.values()):
            if f.owner and not f.generics:
                with self.within(f.module):
                    if self.qualify(f.owner[0], self.p.traits) == trait:
                        self.signature(f)
                        found.setdefault(self.resolve(f.owner[1]), {})[f.name.rsplit(".", 1)[1]] = f
        return found

    def dispatch(self, e: Expr, trait: str, member: Function, position: int, args: list[Expr]) -> Function:
        """An indirect call through the vtable; its row is the join of every implementation's."""
        self.host_only(e, "A dynamic reference points at a host table")
        if len(args) != len(member.params):
            fail("E-ARITY", f"{member.name} expects {len(member.params)} arguments.", e)
        if member.params[position][1].mode == "rw" and not self.writable(args[position]):
            fail("E-WRITE-LEASE", f"{member.name} writes its receiver; it needs an rw<dyn {trait}> reference.", e)
        with self.within(self.p.modules.get(trait, ""), {"Self": Type("dyn", args=(Type(trait),))}):
            for i, (a, (_, declared)) in enumerate(zip(args, member.params, strict=True)):
                if i != position:
                    if declared.mode != "value" or declared.name == "Self":
                        fail(
                            "E-DYN",
                            f"{trait}.{member.name} is not dyn-compatible: only its receiver may be a borrow or Self.",
                            e,
                        )
                    self.expr(a, self.resolve(declared, a))
            ret = self.resolve(member.ret, e)
        receiver = root(args[position]).val
        targets = [members[member.name] for members in self.implementors(trait).values()]
        for target in targets:
            self.call_edges[self.f.name].append((target.name, {target.params[position][0]: receiver}))
            self.callset.add(target.name)
        self.effect("dispatch")
        index = [m.name for m in self.p.traits[trait]].index(member.name)
        e.ref = ("dispatch", trait, index, position, [t.name for t in targets])
        e.ty = ret
        return Function("", [], ret, [])

    def implementation(self, trait: str, self_type: Type) -> dict[str, Function]:
        """The members of `impl trait for T` whose target pattern matches the concrete type."""
        found = {}
        for f in list(self.fs.values()):
            if f.owner and not f.bindings:
                with self.within(f.module):
                    if self.qualify(f.owner[0], self.p.traits) == trait and self.unify(
                        f.owner[1], self_type, {}, {g for g, _ in f.generics}
                    ):
                        found[f.name.rsplit(".", 1)[1]] = f
        return found

    def unify(self, pattern: Any, actual: Any, bound: dict[str, Any], generics: set[str]) -> bool:
        """Bind generic names in `pattern` so that its value part equals `actual`."""
        actual = actual.value if isinstance(actual, Type) else actual
        if isinstance(pattern, Type) and pattern.name in generics and not pattern.args:
            return bound.setdefault(pattern.name, actual) == actual
        if not isinstance(pattern, Type) or not isinstance(actual, Type):
            return pattern == actual
        known = pattern.name in CPP or pattern.name in INTRINSIC_TYPES
        name = pattern.name if known else self.qualify(pattern.name, self.types)
        if name != actual.name or len(pattern.args) != len(actual.args):
            return False
        return all(self.unify(p, a, bound, generics) for p, a in zip(pattern.args, actual.args, strict=True))

    def open(self, declared: Any, generics: set[str], bound: dict[str, Any]) -> bool:
        """Does this declared type still mention an unbound generic parameter?"""
        if not isinstance(declared, Type):
            return False
        if declared.name in generics and not declared.args:
            return declared.name not in bound
        return any(self.open(a, generics, bound) for a in declared.args)

    def instantiate(self, template: Function, bound: dict[str, Any], node: Any) -> Function:
        values = [bound.get(g) for g, _ in template.generics]
        if None in values:
            missing = ", ".join(g for g, _ in template.generics if g not in bound)
            fail("E-INFER", f"Cannot infer {missing} of {template.name}; write {template.name}[...](...).", node)
        name = f"{template.name}[{', '.join(v.display() if isinstance(v, Type) else str(v) for v in values)}]"
        if name not in self.fs:
            for (g, constraint), value in zip(template.generics, values, strict=True):
                if (constraint == "nat") != isinstance(value, int):
                    fail(
                        "E-GENERIC-KIND",
                        f"{g} of {template.name} is a {'natural' if constraint == 'nat' else 'type'}.",
                        node,
                    )
                for wanted in constraint.split("+") if constraint not in {"nat", "type"} else []:
                    with self.within(template.module):
                        trait = self.qualify(wanted, self.p.traits, node=node)
                    if trait is None or not self.implementation(trait, value):
                        fail("E-TRAIT-IMPL", f"{value.display()} does not implement {wanted}.", node)
            if len(self.p.functions) >= MAX_FUNCTIONS:
                fail("E-EXPANSION-LIMIT", "Expanded program exceeds 2048 functions.", node)
            f = copy.deepcopy(template)
            f.name, f.bindings = (name, dict(zip((g for g, _ in template.generics), values, strict=True)))
            self.fs[name] = f
            self.p.functions.append(f)
            self.function(f)
        return self.fs[name]

    def infer(self, f: Function, args: list[Expr], targs: tuple, expected: Type | None, node: Any) -> dict:
        """Bind a template's generics from explicit arguments, Self, argument types, then the result."""
        names = [g for g, _ in f.generics]
        generics, bound = set(names), dict(zip(names, targs, strict=False))
        with self.within(f.module, {}):
            if f.owner:
                self.unify(f.owner[1], self.peek(args[0]), bound, generics)
            ordered = sorted(zip(args, f.params, strict=True), key=lambda x: x[0].tag in {"int", "float"})
            for a, (_, declared) in ordered:  # Literals adapt after the other arguments bind generics.
                if self.open(declared, generics, bound):
                    actual = self.peek(root(a) if a.tag == "slice" else a)
                    element = actual if not declared.extent or is_view(actual) else actual.args[0]
                    if not self.unify(declared, element, bound, generics):
                        fail("E-TYPE-MISMATCH", f"{actual.display()} does not fit {declared.display()}.", a)
            if expected:
                self.unify(f.ret, expected, bound, generics)
        return bound

    def invoke(self, e: Expr, f: Function, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
        if len(args) != len(f.params):
            fail("E-ARITY", f"{e.val} expects {len(f.params)} arguments.", e)
        if f.extern and not self.unsafe_depth:
            fail("E-UNSAFE", f"{f.name} is foreign; call it inside an unsafe block.", e)
        if f.kernel and not self.device_depth:
            fail("E-PLACEMENT", f"{f.name} is a kernel: it runs in device lanes, not in host code.", e)
        if f.generics and not f.bindings:
            f = self.instantiate(f, self.infer(f, args, targs, expected, e), e)
        subst = dict(zip((n for n, _ in f.params), args, strict=True))
        borrows: list[tuple[str, str]] = []
        mapping: dict[str, str] = {}
        for a, (name, want) in zip(args, f.params, strict=True):
            if want.name == "fn" and (a.tag == "lambda" or root(a).val not in self.env):
                self.expr(a, want)  # A closure or a declared function: nothing of the caller is borrowed.
                mapping[name] = ""
                continue
            if want.mode == "value":
                if self.kind(self.expr(a, want)) != "copy" and root(a).tag == "name":
                    borrows.append((self.where(a), "rw"))  # The callee may release it while a view is live.
                continue
            named = root(a).tag == "name" and root(a).val in self.env
            if want.name == "dyn" and self.peek(a).name != "dyn":
                boxed = self.peek(a).value == Type("Dyn", args=want.args)
                members = self.implementors(want.args[0].name).get(self.peek(a).value)
                if not boxed and (members is None or len(members) != len(self.p.traits[want.args[0].name])):
                    fail("E-TRAIT-IMPL", f"{a.ty.display()} does not implement {want.args[0].name}.", a)
                if not named or (want.mode == "rw" and not self.writable(a)):
                    fail("E-WRITE-LEASE", "A dynamic reference borrows a named place (mutable for rw).", a)
                inner = Expr(a.tag, a.val, a.args, a.line, a.col, a.ty, a.start, a.end, a.ref)
                a.tag, a.args, a.ty = "coerce", [inner], want
                a.ref = None if boxed else [members[m.name] for m in self.p.traits[want.args[0].name]]
                self.early[id(a)] = a
                self.leased(self.where(inner), want.mode, a)
                borrows.append((self.where(inner), want.mode))
                mapping[name] = root(inner).val
                continue
            if not named and (want.mode == "rw" or (want.extent and a.tag != "str")):
                fail("E-CALL-VIEW", "Only direct view parameters may be passed.", a)
            if want.extent:
                actual = self.view_argument(a)
                extent = want.extent if want.extent.isdigit() else self.extent_of(subst[want.extent])
                if a.tag == "slice":  # A part is guarded dynamically, so its extent may be any expression.
                    a.ref, extent = subst.get(want.extent, want.extent), actual.extent
                if extent is None:
                    fail("E-CALL-SHAPE", "View extent must be a name, literal or len(view).", subst[want.extent])
                mode = "rw" if actual.mode == "rw" and want.mode == "ro" else want.mode
                self.expect(actual, Type(want.name, mode, extent, want.args, want.place), a)
            else:
                actual = self.expr(a, consume=False)
                if want.mode == "rw" and not self.writable(a):
                    fail("E-WRITE-LEASE", "A mutable borrow needs a mutable local or an rw borrow.", a)
                self.expect(actual.value, want.value, a)
            mapping[name] = self.lend(a, want.mode, borrows)
            if named and want.mode == "rw" and self.env[root(a).val].ty.mode == "value":
                self.effect("write:" + root(a).val)
        self.disjoint(borrows, e)
        self.call_edges[self.f.name].append((f.name, mapping))
        self.callset.add(f.name)
        self.borrowed = borrows
        if self.lanes or self.f.kernel:
            self.lane_calls.append((f.name, bool(self.device_depth), e))
        e.ref = f
        return f.ret

    def view_argument(self, a: Expr) -> Type:
        """A view, local owner, Buf, Array, part or string passed where an array borrow is expected."""
        if a.tag == "slice":
            base, lo, hi = a.args
            ty = self.view_argument(base)
            self.expr(lo, USIZE)
            self.expr(hi, USIZE)
            self.guard("bounds")
            a.ty = Type(ty.name, ty.mode, "part", ty.args, ty.place)
            return a.ty
        ty = self.expr(a, consume=False)
        if is_view(ty) or ty.name not in {"Buf", "Array"}:
            return ty
        mode = "rw" if self.writable(a) else "ro"
        extent = f"len({self.identity(a)})" if ty.name == "Buf" else str(ty.args[1])
        return Type(ty.args[0].name, mode, extent, ty.args[0].args)

    def construct(self, e: Expr, record: str, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
        fields = self.p.records[record]
        if len(fields) != len(args):
            fail("E-ARITY", f"Record {record} expects {len(fields)} fields in declaration order.", e)
        names = [g for g, _ in self.p.generics.get(record, [])]
        bound: dict[str, Any] = dict(zip(names, targs, strict=False))
        if names and not bound and expected and expected.name == record:
            bound = dict(zip(names, expected.args, strict=True))
        home = self.p.modules.get(record, "")
        ordered = sorted(zip(args, fields, strict=True), key=lambda x: x[0].tag in {"int", "float"})
        for a, (_, declared) in ordered:  # Literals adapt after the other fields bind generics.
            with self.within(home, {}):
                if self.open(declared, set(names), bound) and not self.unify(declared, self.peek(a), bound, set(names)):
                    fail("E-INFER", f"Cannot infer the type arguments of {record}; write {record}[...](...).", a)
        for a, (_, declared) in zip(args, fields, strict=True):
            with self.within(home, dict(bound)):
                want = self.resolve(declared, a)
            self.expr(a, want)
        ty = self.resolve(Type(record, args=tuple(bound[g] for g in names)), e)
        e.ref = ("record", ty)
        return ty

    def builtin(self, e: Expr, n: str, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
        def arity(count: int, message: str):
            if len(args) != count:
                fail("E-ARITY", message, e)

        if n == "len":
            if len(args) != 1 or root(args[0]).tag != "name" or args[0].tag == "slice":
                fail("E-LEN", "len takes one direct borrowed view or local buffer.", e)
            ty = self.expr(args[0], consume=False)
            if not is_view(ty) and ty.name not in {"Buf", "Array"}:
                fail("E-LEN", "len requires an array view.", e)
            return USIZE
        if n in NUMERIC:
            arity(1, "Scalar conversion takes one argument.")
            src = self.expr(args[0])
            if src.mode != "value" or src.name not in NUMERIC:
                fail("E-CAST", "Conversion requires numeric scalar.", e)
            if n in INT:  # Narrowing, and float to integer (truncation toward zero), are range checked.
                self.guard("conversion")
            return Type(n)
        if n in {"mmio_read", "mmio_write", "asm"}:  # Target access: audited, never silently safe.
            if not self.unsafe_depth:
                fail("E-UNSAFE", f"{n} touches the machine directly; use it inside an unsafe block.", e)
            self.effect("asm" if n == "asm" else "mmio")
            if n == "asm":
                if len(args) != 1 or args[0].tag != "str":
                    fail("E-ARITY", "asm takes one string literal of target instructions.", e)
                return VOID
            ty = self.resolve(targs[0], e) if len(targs) == 1 else expected
            if ty is None or ty.name not in UNSIGNED:
                fail("E-INFER", f"Write {n}[u8|u16|u32|u64] with the register width.", e)
            arity(1 + (n == "mmio_write"), f"{n} takes an address" + (" and a value." if n == "mmio_write" else "."))
            self.expr(args[0], USIZE)
            if n == "mmio_write":
                self.expr(args[1], ty)
            e.ref = ("builtin", (ty,))
            return ty if n == "mmio_read" else VOID
        if n == "transfer":  # The only way elements cross a placement boundary; extents agree by identity.
            arity(2, "transfer takes a destination and a source view.")
            if self.lanes:
                fail("E-PARALLEL-NEST", "transfer moves a whole array; it cannot run inside a lane.", e)
            dst, src = (self.view_argument(a) for a in args)
            if not is_view(dst) or not is_view(src) or dst.mode != "rw" or root(args[0]).tag != "name":
                fail("E-WRITE-LEASE", "transfer needs an rw destination view and a source view.", e)
            self.expect(Type(src.name, "rw", src.extent, src.args, dst.place), dst, e)
            ends = ["h" if t.place in HOST_VISIBLE else "d" for t in (src, dst)]
            self.effects |= {f"transfer:{ends[0]}2{ends[1]}", "write:" + root(args[0]).val}
            if root(args[1]).tag == "name":
                self.effect("read:" + root(args[1]).val)
            return VOID
        if n == "wait":  # Completion is the only thing that returns a task's borrows.
            arity(1, "wait takes one ticket.")
            self.spawning = "<wait>"
            ticket = self.expr(args[0])
            self.spawning = ""
            if ticket.name != "Ticket" or args[0].tag != "name":
                fail("E-TYPE-MISMATCH", "wait takes the name of a ticket.", e)
            self.leases.pop(args[0].val, None)
            self.effect("join")
            return ticket.args[0]
        if n in {"Atomic", "Mutex"}:  # Shared state is declared in place and reached through ro borrows.
            ty = self.resolve(Type(n, args=targs), e) if targs else expected
            if ty is None or ty.name != n or (n == "Atomic" and ty.args[0].name not in INT | {"bool"}):
                fail("E-INFER", f"Write {n}[T](initial); an atomic holds an integer or bool.", e)
            arity(1, f"{n} takes its initial value.")
            self.expr(args[0], ty.args[0])
            return ty
        if n in {"take", "swap"}:  # The only ways to move an owner out of a place.
            arity(1 if n == "take" else 2, f"{n} takes {'one place' if n == 'take' else 'two places'}.")
            types = [self.place(a, write=True) for a in args]
            if n == "take" and self.kind(types[0]) == "linear":
                fail("E-LINEAR-STORAGE", "take would leave a forged linear value behind; swap two places instead.", e)
            self.effects |= {"read:" + root(a).val for a in args}
            self.expect(types[-1], types[0], e)
            return types[0] if n == "take" else VOID
        if n == "Dyn":
            ty = self.resolve(Type("Dyn", args=targs), e) if targs else expected
            if ty is None or ty.name != "Dyn":
                fail("E-INFER", "Write Dyn[Trait](value).", e)
            arity(1, "Dyn takes the value it will own.")
            members = self.implementors(ty.args[0].name).get(self.expr(args[0]).value)
            if members is None or len(members) != len(self.p.traits[ty.args[0].name]):
                fail("E-TRAIT-IMPL", f"{args[0].ty.display()} does not implement {ty.args[0].name}.", e)
            for member in members.values():  # Whoever holds the value may dispatch to these.
                self.callset.add(member.name)
            self.effects |= {"alloc", "free"}
            self.guard("allocation")
            e.ref = ("builtin", [members[m.name] for m in self.p.traits[ty.args[0].name]])
            return ty
        if n in {"Buf", "Array"}:  # Zero-initialized owners: Buf[T](n) on the heap, Array[T, N]() inline.
            ty = self.resolve(Type(n, args=targs), e) if targs else expected
            if ty is None or ty.name != n:
                fail("E-INFER", f"Write {n}[...] with its type arguments.", e)
            arity(n == "Buf", f"{n} takes {'one capacity' if n == 'Buf' else 'no arguments'}.")
            self.effect("zero_init")
            if n == "Buf":
                self.expr(args[0], USIZE)
                self.effects |= {"alloc", "free"}
                self.guard("allocation")
            return ty
        if n not in BUILTINS:
            fail("E-CALLEE", f"{n} is a type, not a callable.", e)
        arity(2, f"{n} takes two arguments.")
        a, b = args
        shift = n in {"shl_wrap", "shr"}
        if a.tag == "int" and b.tag != "int" and not shift:
            t = self.expr(b, expected)
            self.expr(a, t)
        else:
            t = self.expr(a, expected)
            self.expr(b, USIZE if shift else t)
        if n in WRAPPING:
            if t.mode != "value" or t.name not in UNSIGNED:
                fail("E-WRAP-TYPE", "Wrapping/bit shift operations require unsigned integers.", e)
            if shift:
                self.guard("shift")
        elif t.name not in INT or t.mode != "value":
            fail("E-MINMAX", "Bootstrap min/max are integer-only; floating NaN semantics must be explicit.", e)
        return t

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

from .syntax import (
    BOOL, CPP, FLOAT, INT, MAX_FUNCTIONS, MAX_NODES, NUMERIC, SCALAR, SIGNED, UNSIGNED, USIZE, VOID,
    WIDTH, Expr, Function, Program, Stmt, Type, fail,
)  # fmt: skip

WRAPPING = {"add_wrap", "sub_wrap", "mul_wrap", "shl_wrap", "shr"}
BUILTINS = WRAPPING | {"min", "max", "len", "take", "swap"}
INTRINSIC_TYPES = {"Buf": 1, "Array": 2, "fn": None, "dyn": 1}
KINDS = ["copy", "affine", "linear"]
PURE = {"trap", "diverge", "local_read", "local_write", "stack_storage", "zero_init", "ffi_precondition"}
COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}


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


@dataclass
class Binding:
    ty: Type
    mutable: bool = False
    constant: int | None = None
    depth: int = 0  # Loop depth at declaration: an outer owner cannot be moved inside a loop.


SCOPED = frozenset(Scope.__dataclass_fields__)


def is_view(ty: Type) -> bool:
    return ty.mode != "value" and ty.extent != ""


def root(e: Expr) -> Expr:
    while e.tag in {"field", "index", "slice"}:
        e = e.args[0]
    return e


def path(e: Expr) -> str:
    """A syntactic place identity for alias checks: a.b, a[], a[lo..hi]."""
    if e.tag == "field":
        return path(e.args[0]) + "." + e.val
    if e.tag == "index":
        return path(e.args[0]) + "[]"
    if e.tag == "slice":
        bounds = (a.val if a.tag in {"name", "int"} else "?" for a in e.args[1:])
        return path(e.args[0]) + "[" + "..".join(bounds) + "]"
    return e.val


def overlaps(a: str, b: str) -> bool:
    """Two parts of one array are disjoint only when they visibly share a boundary."""
    (base_a, _, part_a), (base_b, _, part_b) = a.partition("["), b.partition("[")
    if base_a == base_b and ".." in part_a and ".." in part_b:
        (lo_a, hi_a), (lo_b, hi_b) = part_a[:-1].split(".."), part_b[:-1].split("..")
        return "?" in (lo_a, hi_a, lo_b, hi_b) or not (hi_a == lo_b or hi_b == lo_a)
    return base_a == base_b or base_a.startswith(base_b + ".") or base_b.startswith(base_a + ".")


class Checker:
    def __init__(self, program: Program, capture_sites: bool = False):
        self.p = program
        self.capture_sites = capture_sites
        self.sites: list[dict[str, Any]] = []
        self.fs = {f.name: f for f in program.functions}
        self.types = {**program.records, **program.enums, **program.sums}
        for name in [*self.fs, *self.types, *program.consts, *program.traits]:
            if name in CPP or name in BUILTINS or name in INTRINSIC_TYPES:
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
        self.nodes = 0

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
            base = Type(name, args=args)
            self.define(base, node)
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
            ranks = [KINDS.index(self.kind(t)) for t in (parts or ty.args[:1] if ty.name != "Buf" else [])]
            own = 2 if "linear" in self.p.attributes.get(ty.name, ()) else int(ty.name in {"Buf", "dyn"})
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
            if not self.p.generics.get(name):
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
        effects = self.fixed_point()
        self.audit(effects)
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
        if f.ret.mode != "value":
            fail("E-ESCAPE", "Borrowed view returns are not in the native subset.", f)
        if f.extern and f.effects is None:
            fail("E-EXTERN-EFFECTS", "An extern declaration states its effects; its body is not visible.", f)

    def function(self, f: Function):
        self.signature(f)
        outer, self.s = self.s, Scope(f, dict(f.bindings), module=f.module)
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
            self.effects |= {"ffi:" + f.name.rsplit(".", 1)[-1], *(f.effects or ())}
            self.effects |= {("write:" if t.mode == "rw" else "read:") + n for n, t in f.params if t.mode != "value"}
        elif not self.block(f.body) and f.ret != VOID:
            fail("E-RETURN", f"Not all paths of {f.name} return.", f)
        borrowed = {n for n, t in f.params if t.mode != "value"}
        self.local_effects[f.name] = {self.exposed(e, borrowed) for e in self.effects}
        self.calls[f.name], self.checks[f.name] = self.callset, self.counts
        self.s = outer

    @staticmethod
    def exposed(effect: str, borrowed: set[str]) -> str:
        """Private storage keeps its cost visible without exporting a local name."""
        kind, _, name = effect.partition(":")
        return "local_" + kind if kind in {"read", "write"} and name not in borrowed else effect

    def fixed_point(self) -> dict[str, set[str]]:
        """Least fixed point of E_f = L_f + divergence + renamed callee footprints."""
        effects = {n: set(es) for n, es in self.local_effects.items()}
        for n in effects:
            todo, visited = list(self.calls[n]), set()
            while todo:
                q = todo.pop()
                if q == n:
                    effects[n].add("diverge")
                    break
                if q not in visited:
                    visited.add(q)
                    todo.extend(self.calls[q])
        universe = {x for es in effects.values() for x in es if not x.startswith(("read:", "write:"))}
        borrowed = {f.name: {n for n, t in f.params if t.mode != "value"} for f in self.p.functions}
        # A recursive argument permutation can need more sweeps than there are functions.
        for _ in range(1 + sum(2 * len(f.params) + len(universe) for f in self.p.functions)):
            changed = False
            for n in effects:
                before = len(effects[n])
                for callee, mapping in self.call_edges[n]:
                    for effect in tuple(effects[callee]):
                        kind, _, formal = effect.partition(":")
                        if kind in {"read", "write"}:
                            if formal not in mapping:
                                fail("E-INTERNAL", "Unmapped callee memory footprint.")
                            if not mapping[formal]:
                                continue  # A temporary or static argument has no caller footprint.
                            effect = self.exposed(kind + ":" + mapping[formal], borrowed[n])
                        effects[n].add(effect)
                changed |= len(effects[n]) != before
            if not changed:
                break
        else:
            fail("E-EFFECT-LIMIT", "Effect fixed point exceeded its finite universe.")
        for f in self.p.functions:
            if f.effects is not None and not f.extern:
                allowed = set(f.effects) | (PURE if "pure" in f.effects else set())
                reads = "pure" in f.effects
                excess = {e for e in effects[f.name] if e not in allowed and not (reads and e.startswith("read:"))}
                if excess:
                    fail("E-EFFECT-CEILING", f"{f.name} exceeds its declared effects.", f, added_effects=sorted(excess))
        return effects

    def audit(self, effects: dict[str, set[str]]):
        """Unspecified C++ operand order must not reorder observable writes or allocation."""

        def expr(e: Expr, at_root: bool = True):
            callee = e.ref.name if e.tag == "call" and isinstance(e.ref, Function) else None
            if callee in effects and not at_root:
                if any(x.startswith("write:") or x in {"alloc", "free"} for x in effects[callee]):
                    fail("E-EFFECT-ORDER", "Bind a writing call to its own statement before using its result.", e)
            for child in e.args:
                expr(child, False)

        def block(ss: list[Stmt]):
            for s in ss:
                for i, e in enumerate(s.exprs):
                    expr(e, s.tag != "compact" and not (s.tag == "assign" and i == 0))
                block(s.body)
                block(s.other)
                for arm in s.arms:
                    block(arm.body)

        for f in self.p.functions:
            block(f.body)

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

    def block(self, ss: list[Stmt]) -> bool:
        saved, deferred, returned = dict(self.env), set(self.deferred), False
        for s in ss:
            if returned:
                fail("E-UNREACHABLE", "Statement after unconditional return.", s)
            returned = self.stmt(s)
        if not returned:
            self.leaks(set(self.env) - set(saved), ss[-1] if ss else self.f)
        self.env, self.deferred = saved, deferred
        return returned

    def stmt(self, s: Stmt) -> bool:
        handler = getattr(self, "s_" + s.tag, None)
        if handler is None:
            fail("E-INTERNAL", f"Unknown statement {s.tag}.", s)
        return bool(handler(s))

    def s_buffer(self, s: Stmt):
        if s.name in self.env:
            fail("E-SHADOW", "Local owner name is already bound.", s)
        element = self.resolve(s.ty, s)
        if element == VOID:
            fail("E-OWNER-ELEMENT", "Local buffers hold values; void has none.", s)
        extent = s.exprs[0]
        self.expr(extent, USIZE)
        if extent.tag not in {"name", "int"}:
            fail("E-OWNER-EXTENT", "Bind a computed capacity to an immutable usize first.", extent)
        if extent.tag == "name" and self.env[extent.val].mutable:
            fail("E-OWNER-EXTENT", "A buffer capacity must be immutable.", extent)
        if extent.tag == "int" and int(extent.val) > 2**63 - 1:
            fail("E-OWNER-EXTENT", "Capacity exceeds the native object limit.", extent)
        resource = {"name": s.name, "kind": s.tag, "element": element.display(), "capacity": extent.val,
                    "initialization": "zeroed", "release": "lexical-on-normal-exit", "line": s.line}  # fmt: skip
        if s.tag == "stack":
            if extent.tag != "int":
                fail("E-STACK-EXTENT", "Stack storage needs a literal capacity.", extent)
            resource["bytes"] = int(extent.val) * self.sizeof(element)
            prior = sum(x.get("bytes", 0) for x in self.resources[self.f.name])
            if prior + resource["bytes"] > 65536:
                fail("E-STACK-LIMIT", "Explicit stack declarations total at most 65536 bytes per function.", extent)
        # The extent is a literal or immutable scalar, so this identity holds for the whole borrow.
        place = s.ty.place
        s.ty = element
        self.bind(s.name, Binding(Type(element.name, "rw", extent.val, element.args, place)), s)
        self.resources[self.f.name].append(resource)
        self.effect("zero_init")
        if s.tag == "buffer":
            self.effects |= {"alloc", "free"}
            self.guard("allocation")
        else:
            self.effect("stack_storage")

    s_stack = s_buffer

    def s_let(self, s: Stmt):
        if s.name in self.env:
            fail("E-SHADOW", f"{s.name} is already bound; shadowing is forbidden in this subset.", s)
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
        if target is None or not is_view(target.ty) or target.ty.mode != "rw":
            fail("E-WRITE-LEASE", "Compaction target must be a direct rw parameter.", out)
        self.expr(out, consume=False)
        self.expr(hi, USIZE)
        if self.extent_of(hi) != target.ty.extent:
            fail("E-COLLECT-CAPACITY", "Compaction requires iteration extent equal to output capacity.", hi)
        self.env[s.binder] = Binding(USIZE)
        self.expr(pred, BOOL)
        self.expr(value, target.ty.value)

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
        return True  # Ends this lexical block, not necessarily the function.

    s_continue = s_break

    def s_return(self, s: Stmt):
        if self.f.ret == VOID:
            if s.exprs:
                fail("E-RETURN", "Void function cannot return a value.", s)
        elif not s.exprs:
            fail("E-RETURN", "Missing return value.", s)
        else:
            self.expr(s.exprs[0], self.f.ret)
        self.leaks(self.env, s)
        return True

    def branches(self, node: Any, runs: list) -> bool:
        """Alternatives start from one ownership state; a linear value must agree across them."""
        before, outcomes = set(self.moved), []
        for run in runs:
            self.moved = set(before)
            outcomes.append((run(), self.moved))
        live = [moved for returned, moved in outcomes if not returned]
        for n in set().union(*live) - set.intersection(*live or [set()]):
            if n in self.env and self.kind(self.env[n].ty) == "linear":
                fail("E-LINEAR-BRANCH", f"{n} is consumed on some paths only.", node)
        self.moved = set().union(before, *(moved for _, moved in outcomes))
        return all(returned for returned, _ in outcomes)

    def s_if(self, s: Stmt):
        self.expr(s.exprs[0], BOOL)
        both = self.branches(s, [lambda: self.block(s.body), lambda: self.block(s.other)])
        return bool(s.other) and both

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

    def s_expr(self, s: Stmt):
        ty = self.expr(s.exprs[0])
        if s.exprs[0].tag != "call":
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
        if inner.tag != "expr" or inner.exprs[0].tag != "call":
            fail("E-DEFER", "defer schedules exactly one call.", s)
        before = set(self.moved)
        self.stmt(inner)
        self.deferred |= self.moved - before
        self.moved = before

    # Places and expressions --------------------------------------------------------------------

    def extent_of(self, e: Expr) -> str | None:
        """The name/literal identity of an extent expression, or None when it has none."""
        if e.tag in {"name", "int"}:
            return e.val
        if e.tag == "call" and e.val == "len" and len(e.args) == 1 and root(e.args[0]).tag == "name":
            ty = e.args[0].ty or self.expr(e.args[0], consume=False)
            return ty.extent if is_view(ty) else f"len({path(e.args[0])})" if ty.name == "Buf" else None
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

    def consume(self, e: Expr):
        """An owner used as a value moves; its name is dead afterwards."""
        if e.tag == "name":
            if e.val in self.moved | self.deferred:
                fail("E-MOVED", f"{e.val} was already moved or scheduled for cleanup.", e)
            if self.env[e.val].ty.mode != "value":
                fail("E-MOVE-BORROW", f"{e.val} is borrowed; take() or swap() its contents instead.", e)
            self.moved.add(e.val)
            e.ref = "move"
        elif e.tag in {"field", "index"}:
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
            if const is None:
                fail("E-UNBOUND", f"Unbound name {e.val}.", e, available_names=sorted(self.env),
                     expected_type=expected.display() if expected else None)  # fmt: skip
            e.ref = self.p.consts[const][1]
            with self.within(self.p.modules.get(const, "")):
                return self.resolve(self.p.consts[const][0], e)
        if e.val in self.moved:
            fail("E-MOVED", f"{e.val} was moved.", e)
        e.ref = b.constant
        if b.ty.mode != "value" and not b.ty.extent:  # A single borrow reads through to its value.
            self.effect("read:" + e.val)
            return b.ty.value
        return b.ty

    def e_index(self, e: Expr, expected: Type | None, read: bool = True) -> Type:
        if len(e.args) != 2:
            fail("E-INDEX", "An index has exactly one position.", e)
        a, i = e.args
        ty = self.expr(a, consume=False)
        if not is_view(ty) and ty.name not in {"Buf", "Array"}:
            fail("E-INDEX", "Only views can be indexed.", e)
        if (ty.place == "device") != bool(self.device_depth) and ty.place != "unified":
            fail(
                "E-PLACEMENT",
                f"{ty.place} memory is not addressable from {'device' if self.device_depth else 'host'} code.",
                e,
            )
        self.expr(i, USIZE)
        self.guard("bounds")
        if read and root(a).tag == "name":
            self.effect("read:" + root(a).val)
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
        targs = tuple(self.static(a, e) for a in e.ref or ())
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
        if n in BUILTINS or n in NUMERIC or n in INTRINSIC_TYPES:
            e.ref = ("builtin", targs)
            return self.builtin(e, n, args, targs, expected)
        record = self.qualify(n, self.p.records, node=e)
        if record:
            return self.construct(e, record, args, targs, expected)
        home = self.p.modules.get(self.peek(receiver).name, "") if receiver else ""
        with self.within(home or self.module):  # A method is found in its receiver's home module.
            name = self.qualify(n, self.fs, node=e) if home else None
        name = name or self.qualify(n, self.fs, node=e)
        f = self.fs[name] if name else self.trait_member(e, n, args)
        if f is None:
            fail("E-CALLEE", "Qualified calls are declared tagged-sum constructors, not methods." if "." in n
                 else f"Unknown callable {n}; arbitrary C++ names are not allowed.", e)  # fmt: skip
        return self.invoke(e, f, args, targs, expected)

    def trait_member(self, e: Expr, n: str, args: list[Expr]) -> Function | None:
        """Static dispatch of a trait member on the type of its Self argument."""
        prefix, _, short = n.rpartition(".")
        for trait, members in self.p.traits.items():
            if prefix and self.qualify(prefix, self.p.traits) != trait:
                continue
            for declared in members:
                position = next((i for i, (_, t) in enumerate(declared.params) if t.name == "Self"), None)
                if declared.name == short and position is not None and position < len(args):
                    found = self.implementation(trait, self.peek(args[position]).value)
                    if short in found:
                        return found[short]
                    fail("E-TRAIT-IMPL", f"{args[position].ty.display()} does not implement {trait}.", e)
        return None

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
                if constraint not in {"nat", "type"}:
                    with self.within(template.module):
                        trait = self.qualify(constraint, self.p.traits, node=node)
                    if trait is None or not self.implementation(trait, value):
                        fail("E-TRAIT-IMPL", f"{value.display()} does not implement {constraint}.", node)
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
        if f.generics and not f.bindings:
            f = self.instantiate(f, self.infer(f, args, targs, expected, e), e)
        subst = dict(zip((n for n, _ in f.params), args, strict=True))
        borrows: list[tuple[str, str]] = []
        mapping: dict[str, str] = {}
        for a, (name, want) in zip(args, f.params, strict=True):
            if want.mode == "value":
                self.expr(a, want)
                continue
            named = root(a).tag == "name" and root(a).val in self.env
            if not named and (want.mode == "rw" or (want.extent and a.tag != "str")):
                fail("E-CALL-VIEW", "Only direct view parameters may be passed.", a)
            if want.extent:
                actual = self.view_argument(a)
                extent = want.extent if want.extent.isdigit() else self.extent_of(subst[want.extent])
                if extent is None:
                    fail("E-CALL-SHAPE", "View extent must be a name, literal or len(view).", subst[want.extent])
                if a.tag == "slice":  # A part carries a dynamic extent guard, not a static identity.
                    a.ref, extent = subst.get(want.extent, want.extent), actual.extent
                mode = "rw" if actual.mode == "rw" and want.mode == "ro" else want.mode
                self.expect(actual, Type(want.name, mode, extent, want.args, want.place), a)
            else:
                actual = self.expr(a, consume=False)
                if want.mode == "rw" and not self.writable(a):
                    fail("E-WRITE-LEASE", "A mutable borrow needs a mutable local or an rw borrow.", a)
                self.expect(actual.value, want.value, a)
            mapping[name] = root(a).val if named else ""
            if named:
                borrows.append((path(a), want.mode))
                if want.mode == "rw" and self.env[root(a).val].ty.mode == "value":
                    self.effect("write:" + root(a).val)
        for i, (a, m) in enumerate(borrows):
            if any(overlaps(a, b) and "rw" in (m, k) for b, k in borrows[i + 1 :]):
                fail("E-ALIAS", "A mutable view cannot be passed to overlapping call arguments.", e)
        self.call_edges[self.f.name].append((f.name, mapping))
        self.callset.add(f.name)
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
        extent = f"len({path(a)})" if ty.name == "Buf" else str(ty.args[1])
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
            if src.name in FLOAT and n in INT:
                fail("E-CAST", "Float-to-integer conversion is not in the bootstrap subset.", e)
            if src.name in INT and n in INT:
                self.guard("conversion")
            return Type(n)
        if n in {"take", "swap"}:  # The only ways to move an owner out of a place.
            arity(1 if n == "take" else 2, f"{n} takes {'one place' if n == 'take' else 'two places'}.")
            types = [self.place(a, write=True) for a in args]
            self.effects |= {"read:" + root(a).val for a in args}
            self.expect(types[-1], types[0], e)
            return types[0] if n == "take" else VOID
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
        if a.tag == "int" and b.tag != "int" and not shift and n in WRAPPING:
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

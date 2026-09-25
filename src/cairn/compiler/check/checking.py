"""Names, types, generic instances and the walk over every body; the rules sit beside it.

Generic functions and types are instantiated on demand and every instance is
checked as ordinary monomorphic code. Borrows are second class (parameters and
call arguments only), so no lifetime annotations exist; owners are affine and
`linear` values must be consumed exactly once. Nothing here is mechanically proved.

The Checker holds the program-wide tables and the per-function Scope; the rules are
functions in statements.py, expressions.py, calls.py, places.py and concurrency.py,
bound as methods in the class, so each lives in the file that owns its subject.
"""

from __future__ import annotations

from contextlib import contextmanager
from operator import attrgetter
from typing import Any

from ..cooperative import cooperative
from ..device import launches
from ..plans import implementations
from ..primitives import machine
from ..primitives.builtins import SOFT, TABLE
from ..primitives.wide import CACHES
from ..syntax.tree import (
    CPP,
    INTRINSIC_TYPES,
    MAX_NODES,
    STORAGE,
    USIZE,
    VOID,
    WIDTH,
    Diagnostic,
    Expr,
    Function,
    Program,
    Stmt,
    Type,
    fail,
    is_view,
)
from . import calls, concurrency, expressions, places, refusals, statements
from .concurrency import ORDERS, PINNED
from .constants import constant
from .effects import DEVICE_SAFE, LANE_SAFE, audit, exposed, fixed_point
from .places import FORGED
from .scope import SCOPED, Binding, Block, Lanes, Scope
from .traits import KINDS, connect_dispatches, hold_impls, satisfies

BUILTIN_ENUMS = ("Order", "Cache")  # declared for every program, and defined only where a module declares its own


def parts(layout: Any) -> list[Type]:
    """What a record's fields or a sum's payloads hold by value."""
    return [t for _, t in layout] if isinstance(layout, list) else [t for t in (layout or {}).values() if t]


class Checker:
    # Declared for readers and type checkers; the values live in the current Scope (see `scoped` below).
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
    coop: Block | None
    closure: tuple[Function, set[str]] | None
    leases: dict[str, list[tuple[str, str]]]
    before: dict[str, set[str]]
    spawning: str
    touched: list[tuple[str, str, bool, Any]] | None
    facts: list[tuple[str, str, int]]
    values: dict[str, tuple[Any, Any]]
    cited: list[tuple[str, str, int]]
    discharged: dict[str, int]
    # The rules live one module per subject, each a function taking the checker as `c`, and are bound here as methods
    # so that mypy checks every call; a statement or expression tag dispatches to `s_<tag>` or `e_<tag>`.
    s_buffer, s_stack, s_let, s_reg, s_unpack = (statements.s_buffer, statements.s_buffer, statements.s_let,
                                                 statements.s_let, statements.s_unpack)  # fmt: skip
    s_compact, s_assign, s_break, s_continue = (statements.s_compact, statements.s_assign, statements.s_break,
                                                statements.s_break)  # fmt: skip
    s_return, s_if, s_match, s_while, s_for = (statements.s_return, statements.s_if, statements.s_match,
                                               statements.s_while, statements.s_for)  # fmt: skip
    s_expr, s_block, s_unsafe, s_defer = statements.s_expr, statements.s_block, statements.s_unsafe, statements.s_defer
    s_asm = machine.s_asm
    leaving, branches, loop = statements.leaving, statements.branches, statements.loop

    extent_of, declared_extent, writable = places.extent_of, places.declared_extent, places.writable
    place, stable, where, identity = places.place, places.stable, places.where, places.identity
    leased, capture, consume, intact = places.leased, places.capture, places.consume, places.intact
    lend, disjoint, carried = places.lend, places.disjoint, places.carried

    s_parallel, s_reduce, s_scan, region, host_only = (concurrency.s_parallel, concurrency.s_reduce,
                                                       concurrency.s_scan, concurrency.region,
                                                       concurrency.host_only)  # fmt: skip
    e_spawn, shared, lane_callee, plans = (concurrency.e_spawn, concurrency.shared, concurrency.lane_callee,
                                           concurrency.plans)  # fmt: skip
    judge_lane_callbacks, s_submit = concurrency.judge_lane_callbacks, concurrency.s_submit
    s_blocks, s_shared, s_barrier, s_warp_reduce, s_pipeline = (cooperative.s_blocks, cooperative.s_shared,
                                                                cooperative.s_barrier, cooperative.s_warp_reduce,
                                                                cooperative.s_pipeline)  # fmt: skip

    e_int, e_float, e_bool, e_str, e_name = (expressions.e_int, expressions.e_float, expressions.e_bool,
                                             expressions.e_str, expressions.e_name)  # fmt: skip
    e_slice, e_index, e_field, e_lambda, e_try = (expressions.e_slice, expressions.e_index, expressions.e_field,
                                                  expressions.e_lambda, expressions.e_try)  # fmt: skip
    e_unary, e_binary, named_type, type_argument = (expressions.e_unary, expressions.e_binary, expressions.named_type,
                                                    expressions.type_argument)  # fmt: skip
    variant, function_value, bare, unexpected = (expressions.variant, expressions.function_value, expressions.bare,
                                                 expressions.unexpected)  # fmt: skip

    e_call, invoke, indirect, repeatable = calls.e_call, calls.invoke, calls.indirect, calls.repeatable
    view_argument, construct, establish = calls.view_argument, calls.construct, calls.establish

    refusing, rest, verdict = refusals.refusing, refusals.rest, refusals.verdict

    def __init__(self, program: Program, capture_sites: bool = False, every: bool = False):
        self.p = program
        # With `every`, a refusal is kept and the check goes on: (about, whether it read callees' rows, it, its key).
        self.refusals: list[tuple[str, bool, Diagnostic, tuple]] | None = [] if every else None
        self.unjudged: set[str] = set()  # functions a refusal left without a verdict
        self.abandoned: BaseException | None = None  # what ended such a check early, when it was not a refusal
        self.capture_sites = capture_sites
        self.sites: list[dict[str, Any]] = []
        self.fs = {f.name: f for f in program.functions}
        program.enums.setdefault("Order", ORDERS)
        program.enums.setdefault("Cache", CACHES)  # the hints of a wide load or store (wide.py)
        self.types = {**program.records, **program.enums, **program.sums}
        for name in [*self.fs, *self.types, *program.consts, *program.traits]:
            if name in CPP or name in set(TABLE) - SOFT or name in INTRINSIC_TYPES:
                fail("E-BUILTIN-NAME", f"Cannot redefine builtin {name}.")
        for module, name in program.uses:  # An imported name may not hide one the module declares.
            if f"{module}.{name}" in program.modules:
                fail("E-DUPLICATE", f"import ({name}) collides with {module}.{name}; drop one or use the qualified "
                     "name.")  # fmt: skip
        self.aliases: dict[str, dict[str, str]] = {}
        for importer, target, alias in program.imports:
            self.aliases.setdefault(importer, {})[alias] = target
        self.layouts: dict[Type, Any] = {}  # Concrete records and sums, dependencies first.
        self.kinds: dict[Type, str] = {}
        self.frees: dict[Type, bool] = {}
        self.early: dict[int, Expr] = {}  # Arguments typed ahead of their call for dispatch.
        self.s = Scope(Function("", [], VOID, []))
        self.signed: set[str] = set()
        self.local_effects: dict[str, set[str]] = {}
        self.calls: dict[str, set[str]] = {}
        self.checks: dict[str, dict[str, int]] = {}
        self.discharges: dict[str, dict[str, int]] = {}
        self.call_edges: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.resources: dict[str, list[dict[str, Any]]] = {}
        self.numerics: dict[str, list[dict[str, Any]]] = {}  # each rounding the source wrote (builtins.contract)
        self.unchecked: list[str] = []
        self.lane_calls: list[tuple[str, bool, Expr, str]] = []  # (callee, on the device, the call, its caller)
        self.judging = ""  # The function a whole-program rule is looking at: whom a failure there is about.
        # (argument, caller's parameters, callee, formal, caller) for every fn argument.
        self.fn_sites: list[tuple[Expr, set[str], str, str, str]] = []
        self.impls: dict[tuple[str, Type], dict[str, Function] | None] = {}
        self.hostish: dict[str, str] = {}  # function -> the first host-only construct in its body
        self.bounds: dict[str, tuple[list[str], str]] = {}  # witness type -> (traits it promises, its kind)
        self.folded: dict[str, Any] = {}  # constant -> its value
        self.dispatches: list[tuple] = []
        self.borrowed: list[tuple[str, str]] = []
        self.device_functions: set[str] = set()
        self.rows: dict[str, set[str]] = {}  # every function's row, once the judge has settled them
        self.address_taken: set[str] = set()
        self.nodes = self.unique = 0
        self.reaching = 0  # Depth inside a field path: its base is reached, not read whole.
        self.alternatives: dict[str, list[str]] = {}  # reference -> its implementations (implementations.py)
        self.selected: dict[str, str] = {}  # reference -> the implementation a plan runs

    def __setstate__(self, state: dict[str, Any]) -> None:
        """A copy of a finished check (compiler/compilations.py) keys what was typed ahead by the copied expressions."""
        self.__dict__.update(state)
        self.early = {id(e): e for e in self.early.values()}

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

    @contextmanager
    def within(self, module: str, tenv: dict[str, Any] | None = None):
        """Temporarily resolve names as another module (and generic instance) would."""
        saved = self.module, self.tenv
        self.module, self.tenv = module, self.tenv if tenv is None else tenv
        try:
            yield
        finally:
            self.module, self.tenv = saved

    def resolve(self, ty: Type, node=None) -> Type:
        """Substitute generic parameters, qualify names and instantiate generic layouts."""
        if ty.name in self.tenv and not ty.args:
            base = self.tenv[ty.name]
            if not isinstance(base, Type):
                fail("E-TYPE", f"{ty.name} is a static natural, not a type.", node)
        elif ty.name in self.bounds:  # A witness is already a type.
            base = ty.value
        elif ty.name == "Dyn" and len(ty.args) == 1 and isinstance(ty.args[0], Type):
            trait = self.qualify(ty.args[0].name, self.p.traits, node=node)
            if trait is None:
                fail("E-DYN", f"Dyn[...] owns a value behind a trait; {ty.args[0].name} is not a trait.", node)
            return Type("Dyn", ty.mode, ty.extent, (Type(trait),), ty.place)
        elif ty.name == "dyn":
            trait = self.qualify(ty.args[0].name, self.p.traits, node=node)
            if trait is None or ty.mode == "value" or ty.extent:
                fail("E-DYN", "Write ro<dyn Trait> or rw<dyn Trait>: a dynamic interface is a borrowed fat "
                     "reference.", node)  # fmt: skip
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
            for (g, constraint), value in zip(self.p.generics.get(name, []), args, strict=False):
                for wanted in constraint.split("+") if constraint not in {"nat", "type"} else []:
                    broken = satisfies(self, value, wanted, self.p.modules.get(name, ""), node)
                    if broken:
                        code = "E-TRAIT-IMPL" if broken.startswith("does not implement") else "E-BOUND"
                        fail(code, f"{value.display()} {broken}; {name} needs [{g}:{constraint}].", node)
            self.define(base, node)
            if name in {"Buf", "Array"} and self.kind(args[0]) == "linear":
                fail("E-LINEAR-STORAGE", FORGED, node)
            if name == "Group" and self.kind(args[0]) == "linear":
                fail("E-LINEAR-STORAGE", "A group drops every result nobody collects; a linear one cannot be.", node)
        if ty.mode == "value":
            return base
        if base == VOID:
            fail("E-TYPE", "A slice cannot contain void.", node)
        bound = self.tenv.get(ty.extent)  # rw<u64>[K] of a [K:nat] instance, or of a constant, is a static extent.
        named = (
            self.qualify(ty.extent, self.p.consts, node=node)
            if ty.extent and not ty.extent.isdigit() and bound is None
            else None
        )
        bound = constant(self, named, []) if named else bound
        static = isinstance(bound, int) and not isinstance(bound, bool)
        return Type(base.name, ty.mode, str(bound) if static else ty.extent, base.args, ty.place)

    def static(self, argument: Any, node=None) -> Any:
        """A type argument: a natural (literal or bound static name) or a type."""
        if isinstance(argument, Type) and not argument.args and isinstance(self.tenv.get(argument.name), int):
            return self.tenv[argument.name]
        named = isinstance(argument, Type) and not argument.args and argument.name not in self.tenv
        const = self.qualify(argument.name, self.p.consts, node=node) if named else None
        if const and type(constant(self, const, [])) is int:  # `Array[u64, N]`, `scale[N](x)`: a constant natural.
            return constant(self, const, [])
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
                self.carriers(ty.name, layout, node)
            else:
                variants = self.p.sums.get(ty.name) or [(v, None) for v in self.p.enums[ty.name]]
                if not variants or len({v for v, _ in variants}) != len(variants):
                    fail("E-ENUM", f"Invalid enum {ty.name}.", node)
                layout = {v: t and self.member(t, "E-SUM-PAYLOAD", "Payloads") for v, t in variants}
        del self.layouts[ty]
        self.layouts[ty] = layout

    def carriers(self, record: str, layout: list[tuple[str, Type]], node=None):
        """`struct Chart { rows:usize; price:Buf[f64][rows]; }` declares that `price` holds `rows` elements.

        The name is an earlier `usize` field of the same record, and the field that declares it is a `Buf`,
        so every value of the record answers `len(c.price)` with `c.rows` and a call needs no part."""
        order, held = [n for n, _ in layout], dict(layout)
        for carrier, extent in self.p.field_extents.get(record, {}).items():
            if held[carrier].name != "Buf":
                fail("E-EXTENT", f"A declared extent belongs to a Buf field; {carrier} is "
                     f"{held[carrier].display()}.", node)  # fmt: skip
            if extent not in held or order.index(extent) >= order.index(carrier):
                fail("E-EXTENT", f"{carrier} names {extent} as its extent; that is not an earlier field "
                     f"of {record}.", node)  # fmt: skip
            if held[extent] != USIZE:
                fail("E-EXTENT", f"A field extent is a usize field; {extent} is {held[extent].display()}.", node)
        if record in self.p.lends:  # `lends data[0..len];`: a Buf field and two bounds each call reads again.
            carrier, lo, hi = self.p.lends[record]
            if carrier not in held or held[carrier].name != "Buf":
                shown = held[carrier].display() if carrier in held else "no field of it"
                fail("E-LENDS", f"{record} lends {carrier}, which is {shown}; a record lends a Buf field.", node)
            for bound in (lo, hi):
                if not bound.isdigit() and held.get(bound) != USIZE:
                    fail("E-LENDS", f"A lent view's bounds are literals or usize fields of {record}; {bound} is "
                         f"{held[bound].display() if bound in held else 'no field of it'}.", node)  # fmt: skip
            if lo.isdigit() and hi.isdigit() and int(lo) > int(hi):
                fail("E-LENDS", f"{record} lends {carrier}[{lo}..{hi}], which ends before it begins.", node)

    def participates(self, ty: Type, name: str) -> str:
        """Which declared field extent of `ty` the field `name` takes part in: its carrier, or the empty string."""
        declared = self.p.field_extents.get(ty.name, {}) if ty.mode == "value" else {}
        return name if name in declared else next((c for c, e in declared.items() if e == name), "")

    def member(self, declared: Type, code: str, what: str) -> Type:
        if declared.mode != "value" or declared == VOID:
            fail(code, f"{what} must be value types, never borrows or void.")
        ty = self.resolve(declared)
        if ty.name in PINNED:
            fail("E-PINNED", f"{ty.display()} lives where it is declared; share it by ro borrow.")
        inline = ty
        while inline.name == "Array":
            inline = inline.args[0]
        if inline in self.layouts and self.layouts[inline] is None:
            fail(code, f"{what} cannot contain their own type by value; reach it through a Buf.")
        return ty

    def kind(self, ty: Type) -> str:
        """copy < affine (moves, implicit release) < linear (must be consumed exactly once)."""
        if ty.mode != "value" or ty.name in CPP or ty.name == "fn" or ty.name in self.p.enums:
            return "copy"
        if ty.name in self.bounds:  # A witness: exactly as owning as its template's bounds allow.
            return self.bounds[ty.name][1]
        if ty not in self.kinds:
            linear = "linear" in self.p.attributes.get(ty.name, ()) or ty.name in {"Ticket", "Group", "IoRing"}
            own = 2 if linear else int(ty.name in {"Buf", "dyn", "Dyn", "Atomic", "Mutex"})
            layout = self.layouts.get(ty, {} if ty.name in INTRINSIC_TYPES else None)
            if layout is None:  # Asked while its own definition is open: it reaches itself through a Buf.
                return KINDS[max(own, 1)]
            self.kinds[ty] = "affine"
            inline = parts(layout) or (ty.args[:1] if ty.name == "Array" else [])
            self.kinds[ty] = KINDS[max([own, *(KINDS.index(self.kind(t)) for t in inline)])]
        return self.kinds[ty]

    def releases(self, ty: Type) -> bool:
        """Does dropping a value of this type hand storage back? `Buf` and `Dyn` do, and so does anything
        holding one by value. This is not `kind`: `linear struct Lease { id:u64 }` moves and is consumed
        exactly once, and releases nothing."""
        if ty.mode != "value" or ty.name in CPP or ty.name == "fn" or ty.name in self.p.enums:
            return False
        if ty.name in {"Buf", "Dyn"}:
            return True
        if ty.name in self.bounds:  # A witness stands for every argument its bounds allow; only a copy owns nothing.
            return self.bounds[ty.name][1] != "copy"
        if ty not in self.frees:
            layout = self.layouts.get(ty, {} if ty.name in INTRINSIC_TYPES else None)
            if layout is None:  # Asked while its own definition is open: it reaches itself through a Buf.
                return True
            self.frees[ty] = False
            inline = parts(layout) or (ty.args[:1] if ty.name in {"Array", "Mutex"} else [])
            self.frees[ty] = any(self.releases(t) for t in inline)
        return self.frees[ty]

    def sizeof(self, ty: Type) -> int:
        """A conservative byte size (8-byte alignment) for the explicit stack budget."""
        if ty.name in STORAGE:
            return (1 + STORAGE[ty.name][0] + STORAGE[ty.name][1]) // 8
        if ty.name in CPP:
            return 1 if ty.name == "bool" else WIDTH.get(ty.name, 32 if ty.name == "f32" else 64) // 8
        if ty.name == "Array":
            return self.sizeof(ty.args[0]) * ty.args[1]
        layout = self.layouts.get(ty)
        return 8 * isinstance(layout, dict) + sum(-(-self.sizeof(t) // 8) * 8 for t in parts(layout)) or 16

    # Whole program -----------------------------------------------------------------------------

    def prepare(self) -> list[Function]:
        """Types, constants and every concrete signature: what any body needs before it can be checked.

        Where every refusal is reported, each kind is checked whole, and a refused one ends the check after it: what
        names a refused declaration would be judged against half of it."""
        for name in self.types:
            if not self.p.generics.get(name) and (name not in BUILTIN_ENUMS or name in self.p.modules):
                with self.refusing(name), self.within(self.p.modules.get(name, "")):
                    self.define(Type(name))
        for name in [] if self.refusals else list(self.p.consts):
            with self.refusing(name):
                constant(self, name, [])
            if self.refusals:  # one constant is folded again inside another, so only the first refusal is its own
                break
        concrete = [f for f in self.p.functions if not f.generics or f.bindings]
        for f in [] if self.refusals else concrete:
            with self.refusing(f.name):
                self.signature(f)
        if self.refusals:
            self.unjudged.update(f.name for f in concrete)
            return []
        hold_impls(self, concrete)
        return concrete

    def bodies(self):
        for f in self.prepare():  # Generic instances are appended, and checked, at their first use.
            with self.refusing(f.name):
                try:
                    self.function(f)
                except Diagnostic as error:  # The position is the recipe's: say which derivation this copy came from.
                    error.data.update({"derived": f.source_name} if f.source_name.startswith("derive ") else {})
                    inner = self.s.f.module  # the innermost body checked, as an instance is checked inside its caller
                    if inner in self.p.sources and "module" not in error.data:
                        error.data["module"] = inner  # its line counts in that library module's own file
                    raise

    def judge(self) -> dict[str, set[str]]:
        """The rules that need every row: ceilings, operand order, and what a lane may reach."""
        instantiated = {f.source_name for f in self.p.functions if f.bindings}
        for f in [f for f in self.p.functions if f.generics and not f.bindings]:
            self.p.functions.remove(f)
            if f.name not in instantiated:
                if all(k == "nat" for _, k in f.generics) and not f.module and not f.owner:
                    fail("E-UNINSTANTIATED", f"Static function {f.name} has no family; "
                         "unused templates are not silently ignored.")  # fmt: skip
                self.unchecked.append(f.name)
        connect_dispatches(self)
        effects = fixed_point(self)
        effects = effects if self.refusals else implementations.joined(self, effects)
        audit(self, effects)
        if self.refusals:  # a refused row may be what refuses the rules below
            return effects
        self.judge_lane_callbacks(effects)
        kernels = [(f.name, True, f, f.name) for f in self.p.functions if f.kernel]
        for callee, device, node, caller in [*self.lane_calls, *kernels]:
            self.judging = caller
            allowed = DEVICE_SAFE if device else LANE_SAFE | {"dispatch", "indirect_call"}  # Targets' rows join in.
            # A callee writes only through what it was lent, and the lane's race rule judged each lent place where
            # the lane lent it: its own element, its own block, or nothing lanes write.
            reach = ("read:", "write:") if not device or callee in {k for k, *_ in kernels} else ("read:",)
            excess = sorted(x for x in effects[callee] if x not in allowed and not x.startswith(reach))
            if excess:
                fail("E-PARALLEL-CALL", f"A lane cannot call {callee}: it may {', '.join(excess)}.", node)
            todo = [callee] if device else []
            while todo:  # Everything a device lane reaches is compiled for the device as well, so it is device code.
                name = todo.pop()
                if name not in self.device_functions:
                    self.device_functions.add(name)
                    todo += self.calls[name]
                    views = [n for n, t in self.fs[name].params if is_view(t) and t.place in ("host", "pinned")]
                    if name in self.hostish or views:
                        fail("E-PLACEMENT", f"A device lane reaches {name}, where "
                             f"{self.hostish.get(name) or views[0] + ' is a host view'}.", node)  # fmt: skip
        return effects

    def check(self) -> dict[str, Any]:
        try:
            self.bodies()
            if self.refusals:
                self.rest()
            else:
                implementations.check(self)
                planned = concurrency.plans(self)
                effects = self.rows = self.judge()
        except refusals.Stopped:
            self.unjudged.update(self.fs)
        if self.refusals:
            raise self.verdict()
        concurrency.fusions(self, effects)
        tests = {f.name for f in self.p.functions if f.test}  # `cairn test` runs them; no other build holds one
        return {
            n: {
                **({"test": True} if n in tests else {}),
                "effects": sorted(effects[n]),
                "calls": sorted(self.calls[n]),
                "syntactic_check_sites": self.checks[n],
                "discharged_check_sites": self.discharges[n],
                **({"plan": planned[n]} if n in planned else {}),
                **({"numerics": self.numerics[n]} if self.numerics.get(n) else {}),
                **implementations.receipt(self, n),
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
            fail("E-PINNED", "Tickets, groups, atomics and mutexes cannot be passed or returned by value.", f)
        # A lease lent through a borrow would end at return.
        if any(t.name in {"Ticket", "Group"} for _, t in f.params):
            fail("E-PINNED", "A ticket or a group is used only where it is declared: its leases end at wait().", f)
        if f.ret.mode != "value":
            fail("E-ESCAPE", "Borrowed view returns are not in the native subset.", f)
        if f.extern and f.effects is None:
            fail("E-EXTERN-EFFECTS", "An extern declaration states its effects; its body is not visible.", f)

    def function(self, f: Function):
        self.signature(f)
        # A generic instance is checked where its call sits, which may be inside a field path; its own body is not.
        outer, reaching = (self.s, self.reaching)
        self.s, self.reaching = Scope(f, dict(f.bindings), module=f.module, device_depth=int(f.kernel)), 0
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
            if f.launch:  # a vendored CUDA kernel host code launches
                launches.check_launch(self, f)
        elif not self.block(f.body) and f.ret != VOID:
            fail("E-RETURN", f"Not all paths of {f.name} return.", f)
        if not f.extern:  # An owner it was given, and did not pass on, is released where the function ends.
            self.released([n for n, t in f.params if t.mode == "value"])
        borrowed = {n for n, t in f.params if t.mode != "value"}
        self.local_effects[f.name] = {exposed(e, borrowed) for e in self.effects}
        self.calls[f.name], self.checks[f.name], self.discharges[f.name] = self.callset, self.counts, self.discharged
        self.s, self.reaching = outer, reaching

    # The walk ----------------------------------------------------------------------------------

    def effect(self, name: str):
        self.effects.add(name)

    def guard(self, kind: str, *effects: str):
        """A check site that traps when it fails, and whatever else the construct it guards costs."""
        self.effects |= {"trap", *effects}
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def expect(self, got: Type, want: Type, e: Any, declared: Type | None = None):
        if got != want:
            want = declared or want
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

    def released(self, names):
        """Charge `free` where storage goes back: the scope still holding an owner runs its release."""
        if any(n not in self.moved | self.deferred and self.releases(self.env[n].ty) for n in names):
            self.effect("free")

    def block(self, ss: list[Stmt]) -> Any:
        saved, deferred, returned, known = dict(self.env), set(self.deferred), False, len(self.facts)
        for s in ss:
            if returned:
                fail("E-UNREACHABLE", "Statement after unconditional return.", s)
            returned = self.stmt(s)
        if not returned:
            self.leaks(set(self.env) - set(saved), ss[-1] if ss else self.f)
        self.released(set(self.env) - set(saved))
        self.moved |= (self.deferred - deferred) & set(saved)  # Its cleanup has now run: gone for good.
        self.leases = {t: held for t, held in self.leases.items() if t in saved}
        self.env, self.deferred = saved, deferred
        del self.facts[known:]  # What this block learned named what it bound or what it tested.
        return returned

    def stmt(self, s: Stmt) -> Any:
        """False when control falls through, True after a return, "jump" after break or continue."""
        handler = getattr(self, "s_" + s.tag, None)
        if handler is None:
            fail("E-INTERNAL", f"Unknown statement {s.tag}.", s)
        return handler(s) or False

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
            e.established, e.proof = False, None  # Only what this check establishes, where it stands, removes a guard.
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


def scoped(name: str) -> property:
    """A Scope field, read and written through the checker as `self.env`. A property is found at once, where a
    __getattr__ ran only after each failed lookup."""

    def put(checker: Checker, value: Any) -> None:
        setattr(checker.s, name, value)

    return property(attrgetter(f"s.{name}"), put)


if _clash := SCOPED & set(vars(Checker)):  # A property would replace a method of the same name unseen.
    raise TypeError(f"Checker defines {sorted(_clash)}, which name fields of Scope.")
for _name in SCOPED:
    setattr(Checker, _name, scoped(_name))

"""The symbolic translator: a function body as SMT terms over its parameters, path by path.

`Formula` holds the declarations and the inputs of one query; `Symbolic` walks a body, splitting on
every branch, unrolling loops to `MAX_UNROLL` and recording what would still be running as a
residual obligation. Storage behind a view is an SMT array read and written inside its window.
"""

from __future__ import annotations

from ..compiler.cairnc import SIGNED, WIDTH, Expr, Function, Stmt, Type
from ..compiler.tree import BOOL, FLOAT, NUMERIC, USIZE, VOID, is_view
from .scalar_values import (
    FORMATS,
    MAX_PATHS,
    MAX_TERMS,
    MAX_UNROLL,
    TAG,
    ZERO,
    Frame,
    Loop,
    Source,
    Term,
    Unsupported,
    Window,
    arrayed,
    bounds,
    components,
    conj,
    constant,
    declared,
    disj,
    extend,
    extent_of,
    integral,
    ite,
    kinds,
    lifted,
    neg,
    numeral,
    real,
    same,
    shifted,
    sort,
)


class Formula:
    def __init__(self, params: list[tuple[str, Type]], source: Source):
        self.source = source
        self.declarations: list[str] = []
        self.definitions: list[str] = []
        self.cache: dict[tuple[str, str], str] = {}
        self.inputs: dict[str, Term] = {}
        self.variables: dict[str, str] = {}  # The solver reads a float input as its bit pattern.
        self.floating = self.arrays = False
        self.storages = 0
        for i, (name, ty) in enumerate(params):
            parts = []
            for j, leaf in enumerate(source.leaves(ty)):
                n = f"arg_{i}_{j}"
                if is_view(ty):  # Storage the caller lends: one array per component of its element.
                    self.declarations.append(f"(declare-const {n} {arrayed(leaf)})")
                    parts.append(n)
                    continue
                self.declarations.append(f"(declare-const {n} {declared(leaf)})")
                self.variables[n] = leaf.name
                parts.append(self.bind(lifted(n, leaf), sort(leaf)) if leaf.name in FLOAT else n)
            self.track(ty)
            self.arrays = self.arrays or is_view(ty)
            window = Window(ZERO, extent_of(self.inputs, ty), self.storage()) if is_view(ty) else None
            self.inputs[name] = Term(ty, tuple(parts), window=window)

    def storage(self) -> int:
        """A fresh identity for one array: a parameter lends one, and every local owner is its own."""
        self.storages += 1
        return self.storages

    def track(self, ty: Type):
        """Remember that a float exists, so the query names a logic whose theory has one."""
        self.floating = self.floating or any(leaf.name in FLOAT for leaf in self.source.leaves(ty))

    def bind(self, expression: str, kind: str) -> str:
        if expression in {"true", "false"}:
            return expression
        key = (kind, expression)
        if key in self.cache:
            return self.cache[key]
        if len(self.definitions) >= MAX_TERMS:
            raise Unsupported("Symbolic term budget exceeded.")
        n = f"term_{len(self.definitions)}"
        self.definitions.append(f"(define-fun {n} () {kind} {expression})")
        self.cache[key] = n
        return n

    def term(self, ty: Type, value: str, defined: str = "true") -> Term:
        self.track(ty)
        return Term(ty, (self.bind(value, sort(ty)),), self.bind(defined, "Bool"))

    def make(self, ty: Type, parts: tuple[str, ...], defined: str = "true", window: Window | None = None) -> Term:
        self.track(ty)
        return Term(ty, parts, self.bind(defined, "Bool"), window)

    @property
    def logic(self) -> str | None:
        """The solver to build: storage needs the array-aware bit-blaster, floats the general one."""
        return None if self.floating else "QF_AUFBV" if self.arrays else None

    def text(self, assertion: str) -> str:
        declared_logic = "ALL" if self.floating or self.arrays else "QF_BV"
        head = [f"(set-logic {declared_logic})", *self.declarations, *self.definitions]
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

    def empty(self, item: Type) -> tuple[str, ...]:
        """Zero-initialized storage of `item` elements: one constant array per component."""
        self.q.arrays = True
        return tuple(f"((as const {arrayed(t)}) {z})"
                     for t, z in zip(self.source.leaves(item), self.zeros(item), strict=True))  # fmt: skip

    def at(self, base: Term, index: str) -> tuple[str, ...]:
        """The components of the element storage holds at `index` of a view's window."""
        return tuple(f"(select {p} {shifted(base.window.offset, index)})" for p in base.parts)

    def expr(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        self.tick()
        if e.ty is None:
            raise Unsupported("An untyped expression cannot be modeled.")
        ty = e.ty
        if ty.name != "Buf":  # An owner is storage, not a value: `leaves` refuses it wherever one is stored.
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
        if e.tag == "slice":
            return self.part(e, env, inner)
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
        if n == "Array":  # An inline array is a value; `Buf` and `stack` are storage.
            return self.q.make(e.ty, self.zeros(e.ty))
        if n == "len":
            return self.q.term(USIZE, self.hold(args[0]).extent, ok)
        if n == "Buf":  # Zeroed heap storage; allocation failure is outside this model.
            window = Window(ZERO, args[0].value, self.q.storage())
            return Term(e.ty, self.empty(e.ty.args[0]), self.q.bind(ok, "Bool"), window)
        n = e.ref.name if isinstance(e.ref, Function) else n  # The callee the checker resolved.
        if n in self.source.functions:
            return self.apply(n, e, args, env, frame, ok)
        raise Unsupported("Call is outside the supported value fragment.")

    def hold(self, base: Term) -> Window:
        """Where a term's elements live, or a refusal: an inline array value is not storage."""
        if base.window is None:
            raise Unsupported("Only views and local storage are lent, sliced and measured in this model.")
        return base.window

    def counted(self, base: Term) -> int:
        """How many elements an inline array value holds."""
        array = self.source.elements(base.ty)
        if array is None:
            raise Unsupported("Only views, local storage and inline arrays hold elements in this model.")
        return array[1]

    def apply(self, name: str, e: Expr, args: list[Term], env: dict[str, Term], frame: Frame, ok: str) -> Term:
        """A call, with what it wrote through each rw parameter stored back into the place that lent it."""
        f = self.source.functions[name]
        lent = [a.window.root for a in args if a.window is not None]
        shared = (a for a, (_, t) in zip(args, f.params, strict=True) if a.window and t.mode == "rw")
        if any(lent.count(a.window.root) > 1 for a in shared):
            raise Unsupported("Two views of one array passed to one call are not modeled.")
        places = [a for a, (_, t) in zip(e.args, f.params, strict=True) if t.mode == "rw"]
        value, written = self.invoke(name, args, frame.stack, conj(frame.path, ok))
        for a, final in zip(places, written, strict=True):
            ok = conj(ok, self.store(a, env, frame, final))
        return self.q.make(value.ty, value.parts, conj(ok, value.defined))

    def compose(self, ty: Type, index: int, payload: Term | None) -> Term:
        """A sum value: the tag beside every payload, the inactive ones zeroed as the emitter zeroes them."""
        layout = self.source.layout(ty)
        parts = [constant(index, TAG.name)]
        for i, t in enumerate(layout.values()):
            if t is not None:
                parts += list(payload.parts if i == index and payload else self.zeros(t))
        return self.q.make(ty, tuple(parts), payload.defined if payload else "true")

    def element(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        """A guarded read of one element of storage or of an inline array."""
        base = self.expr(e.args[0], env, frame)
        if len(e.args) != 2:
            raise Unsupported("An index has exactly one position in this model.")
        index = self.expr(e.args[1], env, frame)
        item = self.source.item(base.ty)
        leaves = self.source.leaves(item)
        if base.window is not None:
            inside = f"(bvult {index.value} {base.window.extent})"
            parts = tuple(self.q.bind(x, sort(t)) for x, t in zip(self.at(base, index.value), leaves, strict=True))
            return self.q.make(item, parts, conj(base.defined, index.defined, inside))
        count = self.counted(base)
        inside = f"(bvult {index.value} {constant(count, index.ty.name)})" if count else "false"
        picked = []
        for k, leaf in enumerate(leaves):
            chosen = base.parts[max(count - 1, 0) * len(leaves) + k] if count else self.zeros(item)[k]
            for i in reversed(range(count - 1)):
                chosen = ite(same(index.value, constant(i, index.ty.name)), base.parts[i * len(leaves) + k], chosen)
            picked.append(self.q.bind(chosen, sort(leaf)))
        return self.q.make(item, tuple(picked), conj(base.defined, index.defined, inside))

    def part(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        """`x[lo..hi]` passed to a callee: one guard (lo <= hi <= len, and hi - lo is the extent it wants)."""
        base = self.expr(e.args[0], env, frame)
        window = self.hold(base)
        lo, hi = (self.expr(a, env, frame) for a in e.args[1:])
        span = self.q.bind(f"(bvsub {hi.value} {lo.value})", sort(USIZE))
        guard = conj(f"(bvule {lo.value} {hi.value})", f"(bvule {hi.value} {window.extent})")
        if isinstance(e.ref, Expr):  # The extent the callee declares, as the caller's argument reads it.
            guard = conj(guard, same(span, self.expr(e.ref, env, frame).value))
        elif str(e.ref).isdigit():
            guard = conj(guard, same(span, constant(int(e.ref), "usize")))
        defined = conj(base.defined, lo.defined, hi.defined, guard)
        shift = self.q.bind(shifted(window.offset, lo.value), sort(USIZE))
        return Term(e.ty, base.parts, self.q.bind(defined, "Bool"), Window(shift, span, window.root))

    def propagate(self, e: Expr, env: dict[str, Term], frame: Frame) -> Term:
        """`try x`: the success payload, or a return of the failure the enclosing result carries."""
        inner = self.expr(e.args[0], env, frame.at(frame.path))
        ok, err, target, carrier = e.ref
        layout = self.source.layout(inner.ty)
        failed = neg(same(inner.parts[0], constant(0, TAG.name)))
        payload = layout[err] and self.q.make(layout[err], inner.parts[slice(*self.source.span(inner.ty, err))])
        index = list(self.source.layout(target)).index(carrier)
        failure = (conj(frame.path, inner.defined, failed), self.compose(target, index, payload), self.outs(env, frame))
        frame.returns.append(failure)
        good = inner.parts[slice(*self.source.span(inner.ty, ok))] if layout[ok] else ()
        return self.q.make(e.ty, good, conj(inner.defined, neg(failed)))

    def outs(self, env: dict[str, Term], frame: Frame) -> tuple[Term, ...]:
        """What the rw parameters hold where a path leaves the function."""
        return tuple(env[n] for n in frame.outs)

    def invoke(self, name: str, args: list[Term], stack: tuple[str, ...] = (), path: str = "true") -> tuple:
        """A call: the value it returns, and what each rw parameter holds when it does.

        The caller's path enters the body, so a callee's loop is bounded by what reaches it.
        """
        if name in stack:
            raise Unsupported("Recursive functions are not modeled.")
        if len(stack) > 24:
            raise Unsupported("Call-depth limit exceeded.")
        f = self.source.functions[name]
        if f.static:
            raise Unsupported("Static parameters are outside the modeled fragment.")
        self.source.leaves(f.ret)
        env = {n: Term(t.value, a.parts) for (n, t), a in zip(f.params, args, strict=True) if not is_view(t)}
        for (n, t), a in zip(f.params, args, strict=True):  # A view is bounded by the extent its callee declares.
            if is_view(t):
                lends = self.hold(a)
                env[n] = Term(t, a.parts, window=Window(lends.offset, extent_of(env, t), lends.root))
        frame = Frame((*stack, name), [], outs=tuple(n for n, t in f.params if t.mode == "rw"))
        rest = self.block(f.body, [(path, env)], frame, None)
        if rest and f.ret != VOID:
            raise Unsupported("A function fell through without returning.")
        for reached, inside in rest:  # A void function returns by reaching its end.
            frame.returns.append((reached, self.q.make(VOID, ()), self.outs(inside, frame)))
        value = self.zeros(f.ret)
        finals = [list(x.parts) for x in self.outs(env, frame)]
        for path, v, written in reversed(frame.returns):
            value = tuple(ite(path, x, y) for x, y in zip(v.parts, value, strict=True))
            for k, out in enumerate(written):
                finals[k] = [ite(path, x, y) for x, y in zip(out.parts, finals[k], strict=True)]
        parts = tuple(self.q.bind(x, sort(t)) for x, t in zip(value, self.source.leaves(f.ret), strict=True))
        held = []
        for seed, final in zip(self.outs(env, frame), finals, strict=True):
            joined = tuple(self.q.bind(x, k) for x, k in zip(final, kinds(self.source, seed), strict=True))
            held.append(Term(seed.ty, joined, window=seed.window))
        reached = self.q.bind(disj(*(path for path, _, _ in frame.returns)), "Bool")
        return self.q.make(f.ret, parts, reached), tuple(held)

    def store(self, target: Expr, env: dict[str, Term], frame: Frame, value: Term) -> str:
        """Assign into a place; returns what the assignment needs in order to happen."""
        if target.tag == "name":
            held = env[target.val]
            env[target.val] = Term(held.ty, value.parts, window=held.window)
            return "true"
        base = self.expr(target.args[0], env, frame)
        if target.tag == "slice":  # Writing through a part writes the storage it narrows.
            return conj(base.defined, self.store(target.args[0], env, frame, Term(base.ty, value.parts)))
        if target.tag == "field":
            lo, hi = self.source.span(base.ty, target.val)
            whole = Term(base.ty, base.parts[:lo] + value.parts + base.parts[hi:])
            return conj(base.defined, self.store(target.args[0], env, frame, whole))
        if target.tag != "index":
            raise Unsupported("Only names, fields and array elements are assigned in this model.")
        index = self.expr(target.args[1], env, frame)
        leaves = self.source.leaves(self.source.item(base.ty))
        if base.window is not None:
            place = shifted(base.window.offset, index.value)
            parts = tuple(self.q.bind(f"(store {p} {place} {x})", arrayed(t))
                          for p, x, t in zip(base.parts, value.parts, leaves, strict=True))  # fmt: skip
            inside = conj(base.defined, index.defined, f"(bvult {index.value} {base.window.extent})")
            return conj(inside, self.store(target.args[0], env, frame, Term(base.ty, parts, window=base.window)))
        count = self.counted(base)
        updated = list(base.parts)
        for i in range(count):
            hit = same(index.value, constant(i, index.ty.name))
            for k, leaf in enumerate(leaves):
                at = i * len(leaves) + k
                updated[at] = self.q.bind(ite(hit, value.parts[k], base.parts[at]), sort(leaf))
        inside = conj(base.defined, index.defined, f"(bvult {index.value} {constant(count, index.ty.name)})")
        return conj(inside, self.store(target.args[0], env, frame, Term(base.ty, tuple(updated))))

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
                    env[s.name] = Term(v.ty, v.parts, window=v.window)
                    next_states.append((conj(path, v.defined), env))
                elif s.tag == "assign":
                    v = self.expr(s.exprs[1], env, here)
                    guard = self.store(s.exprs[0], env, frame.at(path), v)
                    next_states.append((conj(path, v.defined, guard), env))
                elif s.tag in {"stack", "buffer"} and s.ref == "host":
                    count = self.expr(s.exprs[0], env, here)  # Zeroed; a failed allocation is outside this model.
                    held = Type(s.ty.name, "rw", s.exprs[0].val, s.ty.args, "host")
                    where = Window(ZERO, count.value, self.q.storage())
                    env[s.name] = Term(held, self.empty(s.ty), window=where)
                    next_states.append((conj(path, count.defined), env))
                elif s.tag == "return" and len(s.exprs) == 1:
                    v = self.expr(s.exprs[0], env, here)
                    frame.returns.append((conj(path, v.defined), v, self.outs(env, frame)))
                elif s.tag == "return":
                    frame.returns.append((path, self.q.make(VOID, ()), self.outs(env, frame)))
                elif s.tag in {"compact", "reduce"}:
                    next_states.extend(self.fold(s, path, env, frame))
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
            held = states[0][1][n]
            parts = list(states[-1][1][n].parts)
            for p, inside in reversed(states[:-1]):
                parts = [ite(p, a, b) for a, b in zip(inside[n].parts, parts, strict=True)]
            sorts = kinds(self.source, held)
            joined = tuple(self.q.bind(x, k) for x, k in zip(parts, sorts, strict=True))
            env[n] = Term(held.ty, joined, window=held.window)
        return [(self.q.bind(disj(*(p for p, _ in states)), "Bool"), env)]

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
                survivors.append((self.q.bind(conj(held, neg(c.value)), "Bool"), inside))
                entering.append((self.q.bind(conj(held, c.value), "Bool"), dict(inside)))
            if step == MAX_UNROLL:
                self.residual = disj(self.residual, *(p for p, _ in entering))
                break
            loop = Loop()
            states = self.merge(self.block(s.body, entering, frame, loop) + loop.continues, keep)
            survivors += loop.breaks
            if s.tag == "for":
                for _, inside in states:
                    i, whole = inside[binder], numeral(inside[binder].value)
                    up = f"(bvadd {i.value} {constant(1, i.ty.name)})"
                    inside[binder] = Term(i.ty, (constant(whole + 1, i.ty.name) if whole is not None else up,))
        return self.merge(survivors, keep - {binder})

    def reduction(self, op: str, ty: Type, a: Term, b: Term) -> Term:
        """One step of the emitted host fold. Every operator offered here is order independent."""
        if op in {"+", "add_wrap", "mul_wrap"}:
            kept = {"+": "+", "add_wrap": "+", "mul_wrap": "*"}[op]
            return self.arithmetic(ty, kept, a, b, checked=op == "+")
        both = conj(a.defined, b.defined)
        if op in {"&", "|", "^"}:
            return self.q.term(ty, f"({ {'&': 'bvand', '|': 'bvor', '^': 'bvxor'}[op] } {a.value} {b.value})", both)
        if op in {"min", "max"}:
            cmp = f"(bv{'s' if ty.name in SIGNED else 'u'}le {a.value} {b.value})"
            return self.q.term(ty, ite(cmp, a.value, b.value) if op == "min" else ite(cmp, b.value, a.value), both)
        raise Unsupported(f"reduce {op} is not modeled.")

    def seeded(self, op: str, ty: Type) -> Term:
        """What the emitted fold starts from; a float total would depend on the order it is taken in."""
        if ty.name in FLOAT:
            raise Unsupported("A float reduction combines in an unspecified order; it is not modeled.")
        lo, hi = bounds(ty.name)
        return self.q.term(ty, constant({"mul_wrap": 1, "&": hi, "min": hi, "max": lo}.get(op, 0), ty.name))

    def fold(self, s: Stmt, path: str, env: dict[str, Term], frame: Frame) -> list:
        """`reduce` and `compact`: one in-order pass over 0..hi, unrolled under the loop budget."""
        if s.ref != "host":
            raise Unsupported("Only host reductions and collectors are modeled.")
        collect = s.tag == "compact"
        count = self.expr(s.exprs[1] if collect else s.exprs[0], env, frame.at(path))
        ok = conj(path, count.defined)
        target = self.expr(s.exprs[0], env, frame.at(ok)) if collect else None
        acc = self.q.term(USIZE, ZERO) if collect else self.seeded(s.op, s.ty)
        for step in range(MAX_UNROLL):
            live = self.q.bind(conj(ok, f"(bvult {constant(step, 'usize')} {count.value})"), "Bool")
            inner = {**env, s.binder: self.q.term(USIZE, constant(step, "usize"))}
            if not collect:
                v = self.expr(s.exprs[1], inner, frame.at(live))
                total = self.reduction(s.op, s.ty, acc, v)
                ok = conj(ok, ite(live, conj(v.defined, total.defined), "true"))
                acc = self.q.term(s.ty, ite(live, total.value, acc.value))
                continue
            keep = self.expr(s.exprs[2], inner, frame.at(live))
            taken = self.q.bind(conj(live, keep.value), "Bool")
            v = self.expr(s.exprs[3], inner, frame.at(taken))  # The projection runs only where it is selected.
            ok = conj(ok, ite(live, keep.defined, "true"), ite(taken, v.defined, "true"))
            # The store is the emitter's one unchecked write: used <= step < count, and count is the capacity.
            place = shifted(self.hold(target).offset, acc.value)
            parts = tuple(
                self.q.bind(ite(taken, f"(store {p} {place} {x})", p), arrayed(t))
                for p, x, t in zip(target.parts, v.parts, components(self.source, target), strict=True)
            )
            target = Term(target.ty, parts, window=target.window)
            acc = self.q.term(USIZE, ite(taken, f"(bvadd {acc.value} {constant(1, 'usize')})", acc.value))
        self.residual = disj(self.residual, conj(ok, f"(bvult {constant(MAX_UNROLL, 'usize')} {count.value})"))
        env = dict(env)
        env[s.name] = acc
        return [(conj(ok, self.store(s.exprs[0], env, frame.at(ok), target) if collect else "true"), env)]

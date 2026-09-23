"""Expression rules: literals, names, indexing, fields, variants, closures, `try` and operators."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from . import facts
from .builtins import TABLE
from .places import field_path, path
from .scope import Binding
from .traits import instantiate, unify
from .tree import (
    BOOL,
    FLOAT,
    INT,
    INTRINSIC_TYPES,
    NUMERIC,
    SCALAR,
    SIGNED,
    STORAGE,
    UNSIGNED,
    USIZE,
    VOID,
    WIDTH,
    Expr,
    Function,
    Type,
    fail,
    is_view,
    root,
)

if TYPE_CHECKING:
    from .checking import Checker

COMPARISONS = {"==", "!=", "<", "<=", ">", ">="}


def stored(name: str) -> str:
    """What to write instead of arithmetic, a comparison or a literal of a storage float."""
    return f"{name} is a storage float: widen it with f32(x) to compute, and round back with {name}(y) or quantize."


def unrounded(e: Expr, expected: Type | None, written: str) -> None:
    """A literal never becomes a storage float by itself: the rounding is written, `f16(1.5)`."""
    if expected and expected.mode == "value" and expected.name in STORAGE:
        fail("E-TYPE-MISMATCH", f"A literal is an f32 or an f64; write {expected.name}({written}) to round it.", e)


def e_int(c: Checker, e: Expr, expected: Type | None) -> Type:
    unrounded(e, expected, e.val + ".0")
    ty = expected if expected and expected.mode == "value" and expected.name in NUMERIC else Type("u64")
    n = int(e.val)
    if ty.name in WIDTH:
        if n >= 2 ** (WIDTH[ty.name] - (ty.name in SIGNED)):
            fail("E-LITERAL-RANGE", f"Literal is not representable in {ty.name}.", e)
    elif n > 2**53:
        fail("E-LITERAL-RANGE", "Large integer-to-float literals require a checked explicit conversion.", e)
    return ty


def e_float(c: Checker, e: Expr, expected: Type | None) -> Type:
    unrounded(e, expected, e.val)
    ty = expected if expected and expected.name in FLOAT else Type("f64")
    v = float(e.val)
    if not math.isfinite(v) or (ty.name == "f32" and abs(v) > 3.4028234663852886e38):
        fail("E-LITERAL-RANGE", "Nonfinite/overflowing floating literal.", e)
    return ty


def e_bool(c: Checker, e: Expr, expected: Type | None) -> Type:
    return BOOL


def e_str(c: Checker, e: Expr, expected: Type | None) -> Type:
    c.host_only(e, "A string literal is host memory")
    return Type("u8", "ro", str(len(e.val.encode("latin-1", "replace"))))


def e_name(c: Checker, e: Expr, expected: Type | None) -> Type:
    sum_ = bare(c, e.val, expected, e)
    if sum_:  # Checked, and lowered, as the qualified `Option.None` it stands for.
        e.tag, e.args = "field", [Expr("name", sum_.name, [], e.line, e.col)]
        return c.variant(sum_, e.val, [], e, expected)
    b = c.env.get(e.val)
    if b is None:
        const = c.qualify(e.val, c.p.consts, node=e)
        value = c.function_value(e, expected) if const is None and expected and expected.name == "fn" else None
        if value:
            return value
        if const is None and e.val == "_":
            fail("E-UNBOUND", "_ binds nothing, so nothing can read it: name the value to use it.", e)
        if const is None:
            fail("E-UNBOUND", f"Unbound name {e.val}.{unexpected(c, e.val)}", e, available_names=sorted(c.env),
                 expected_type=expected.display() if expected else None)  # fmt: skip
        e.ref = c.p.consts[const][1]
        with c.within(c.p.modules.get(const, "")):
            return c.resolve(c.p.consts[const][0], e)
    if e.val in c.moved:
        fail("E-MOVED", f"{e.val} was moved.", e)
    if c.device_depth and c.lanes and e.val in c.lanes.outer and c.kind(b.ty.value) != "copy":
        c.host_only(e, f"{e.val} owns host memory, and a lane copies the values it captures")
    if not is_view(b.ty) and b.ty.name not in {"Buf", "Array"} and not c.reaching:
        c.leased(e.val, "ro", e)  # Arrays are checked per element or part, a record per field reached.
    e.ref = "mut" if b.mutable or b.ty.mode == "rw" else b.constant
    if b.ty.mode != "value" and not b.ty.extent:  # A single borrow reads through to its value.
        c.effect("read:" + e.val)
        return b.ty.value
    return b.ty


def e_slice(c: Checker, e: Expr, expected: Type | None) -> Type:
    fail("E-VIEW-ALIAS", "A part xs[lo..hi] is a borrow: it exists only as a view argument of a call.", e)


def e_index(c: Checker, e: Expr, expected: Type | None, read: bool = True) -> Type:
    value = c.function_value(e, expected) if expected and expected.name == "fn" else None
    if value:
        return value
    if len(e.args) != 2:
        fail("E-INDEX", "An index has exactly one position.", e)
    a, i = e.args
    ty = c.expr(a, consume=False)
    if not is_view(ty) and ty.name not in {"Buf", "Array"}:
        fail("E-INDEX", "Only views can be indexed.", e)
    outer = c.lanes.outer if c.lanes else {n for n, _ in c.f.params}
    private = c.device_depth and root(a).tag == "name" and root(a).val not in outer
    if (ty.place == "device") != bool(c.device_depth) and ty.place != "unified" and not private:
        fail("E-PLACEMENT", f"{ty.place} memory is not addressable from {'device' if c.device_depth else 'host'} "
             "code.", e)  # fmt: skip
    c.expr(i, USIZE)
    c.guard("bounds")
    facts.discharge(c, e, "bounds", facts.index(c, e))
    if read and root(a).tag == "name":
        c.effect("read:" + root(a).val)
    if c.lanes and root(a).val in c.lanes.outer:
        c.lanes.accesses.append((root(a).val, facts.window(c, i, c.lanes.binder), not read, e))
    if root(a).tag == "name":
        c.leased(c.where(e), "ro" if read else "rw", e)
    return ty.value if is_view(ty) else ty.args[0]


def e_field(c: Checker, e: Expr, expected: Type | None) -> Type:
    enum = c.named_type(e.args[0])
    if enum is not None:
        return c.variant(enum, e.val, [], e, expected)
    const = c.qualify(path(e), c.p.consts, node=e) if root(e).val not in c.env else None
    if const:
        e.tag, e.val, e.args = "name", path(e), []
        return c.e_name(e, expected)
    outer, reached = not c.reaching, field_path(e) and root(e).val in c.env
    c.reaching += reached
    at = c.expr(e.args[0], consume=False)
    c.reaching -= reached
    layout = c.layouts.get(at)
    if at.mode != "value" or not isinstance(layout, list):
        fail("E-FIELD", "Field access requires a record.", e)
    if c.p.modules.get(at.name, "") not in ("", c.module) and at.name not in c.p.public:
        fail("E-PRIVATE", f"{at.name} is private to module {c.p.modules[at.name]}; so are its fields.", e)
    if e.val not in dict(layout):
        fail("E-FIELD", f"Unknown field {e.val}.", e, available_fields=[k for k, _ in layout])
    if reached and outer:  # Reaching a field reads its own header: a lease of its elements does not forbid it.
        c.leased(c.where(e), "ro", e, elements=False)
    return dict(layout)[e.val]


def named_type(c: Checker, e: Expr) -> Type | None:
    """`Enum`, `module.Enum` or `Enum[args]` in expression position, unless a local hides it."""
    args: tuple = ()
    if e.tag == "index":
        e, args = e.args[0], tuple(c.type_argument(a) for a in e.args[1:])
    if root(e).tag != "name" or root(e).val in c.env or "[" in path(e):
        return None
    name = c.qualify(path(e), c.p.sums, c.p.enums, node=e)
    return Type(name, args=args) if name else None


def type_argument(c: Checker, e: Expr) -> Any:
    if e.tag == "int":
        return int(e.val)
    if e.tag == "index":
        return Type(path(e.args[0]), args=tuple(c.type_argument(a) for a in e.args[1:]))
    return Type(path(e))


def bare(c: Checker, name: str, expected: Type | None, e: Expr) -> Type | None:
    """`None`, `Some(x)`, `Ok(v)`: a variant written without its type is one of the sum or enum the context expects,
    and only when nothing else of that name is visible. A name that could mean both is refused, never guessed."""
    if expected is None or expected.mode != "value" or "." in name:
        return None
    if name not in [v for v, _ in c.p.sums.get(expected.name, [])] + list(c.p.enums.get(expected.name, [])):
        return None
    tables = {"a constant": c.p.consts, "a function": c.fs, "a type": c.types}
    other = "a local" if name in c.env else "a builtin" if name in TABLE or name in INTRINSIC_TYPES else None
    other = other or next((what for what, table in tables.items() if c.qualify(name, table)), None)
    short = expected.name.rsplit(".", 1)[-1]
    if other:
        fail("E-VARIANT-AMBIGUOUS", f"{name} is both {short}.{name} and {other}; write {short}.{name}.", e)
    home = c.p.modules.get(expected.name, "")
    if home not in ("", c.module) and expected.name not in c.p.public:
        fail("E-PRIVATE", f"{expected.name} is private to module {home}; so are its variants.", e)
    return Type(expected.name, args=expected.args)


def adapts(c: Checker, e: Expr) -> bool:
    """A literal, or a bare name that can only be a variant: either takes its type from the operand beside it."""
    if e.tag in {"int", "float"}:
        return True
    name = e.val if e.tag in {"name", "call"} and not e.ref else "."
    if "." in name or name in c.env or name in TABLE or any(c.qualify(name, t) for t in (c.p.consts, c.fs, c.types)):
        return False
    return any(name in dict(vs) for vs in c.p.sums.values()) or any(name in vs for vs in c.p.enums.values())


def unexpected(c: Checker, name: str) -> str:
    """Why a bare variant did not resolve: nothing here expects its sum, so it has to say which one."""
    sums = [s for s, vs in c.p.sums.items() if name in dict(vs)] + [s for s, vs in c.p.enums.items() if name in vs]
    if not sums:
        return ""
    return f" {name} is a variant of {sums[0].rsplit('.', 1)[-1]}: nothing here expects that sum, so qualify it."


def variant(c: Checker, enum: Type, variant: str, args: list[Expr], e: Expr, expected: Type | None) -> Type:
    variants = dict(c.p.sums.get(enum.name) or [(v, None) for v in c.p.enums[enum.name]])
    if variant not in variants:
        fail("E-ENUM-VARIANT", f"Unknown {enum.name}.{variant}.", e, available_variants=list(variants))
    if len(args) != (1 if variants[variant] else 0):
        fail("E-SUM-ARITY", "Constructor arguments must match the declared payload.", e)
    generics = [g for g, _ in c.p.generics.get(enum.name, [])]
    if generics and not enum.args:  # Infer from the expected type, else from the payload.
        bound: dict[str, Any] = {}
        if expected and expected.name == enum.name:
            bound = dict(zip(generics, expected.args, strict=True))
        elif args:
            with c.within(c.p.modules.get(enum.name, "")):
                unify(c, variants[variant], c.peek(args[0]), bound, set(generics))
        if set(bound) != set(generics):
            fail("E-INFER", f"Cannot infer the type arguments; write {enum.name}[...].{variant}.", e)
        enum = Type(enum.name, args=tuple(bound[g] for g in generics))
    ty = c.resolve(enum, e)
    if args:
        c.expr(args[0], c.layouts[ty][variant])
    e.ref = ("variant", list(variants).index(variant))
    return ty


def e_lambda(c: Checker, e: Expr, expected: Type | None) -> Type:
    """A closure exists only as a `ro<fn(...)>` argument, so it can never outlive what it captures."""
    f: Function = e.ref
    if expected is None or expected.name != "fn" or expected.mode != "ro" or c.device_depth:
        fail("E-CLOSURE", "A closure is written directly as an argument to a ro<fn(...)> parameter on the host.", e)
    saved = dict(c.env), c.closure, c.loop_depth, set(c.moved), c.effects, c.callset
    f.params = [(n, c.resolve(t, e)) for n, t in f.params]
    f.ret = c.resolve(f.ret, e)
    c.expect(Type("fn", "ro", args=(*(t for _, t in f.params), f.ret)), expected, e)
    f.captures, f.row = [], (set(), set())
    c.closure, c.loop_depth, (c.effects, c.callset) = (f, set(c.env)), 0, f.row
    for n, t in f.params:
        c.bind(n, Binding(t), e)
    if not c.block(f.body) and f.ret != VOID:
        fail("E-RETURN", "Not all paths of the closure return.", e)
    c.released([n for n, _ in f.params])
    if (c.moved - saved[3]) & set(saved[0]):
        fail("E-MOVE-IN-LOOP", "A closure may run many times; it cannot move an outer owner.", e)
    c.env, c.closure, c.loop_depth, _, c.effects, c.callset = saved
    c.effects |= f.row[0]
    c.callset |= f.row[1]
    for place, mode in f.captures:  # A closure written inside a closure captures for both.
        c.capture(place, mode)
    return expected


def function_value(c: Checker, e: Expr, want: Type) -> Type | None:
    """A declared function named where a fn value is expected; it counts as called here."""
    named, targs = (e.args[0], e.args[1:]) if e.tag == "index" else (e, [])
    free = root(named).tag == "name" and root(named).val not in c.env
    name = c.qualify(path(named), c.fs, node=e) if free else None
    if name is None:
        return None
    g = c.fs[name]
    if targs and g.generics:  # ascending[u64]
        bound = dict(zip((n for n, _ in g.generics), (c.static(c.type_argument(a), e) for a in targs), strict=False))
        g = instantiate(c, g, bound, e)
        e.args = []
    name = g.name
    c.signature(g)
    if (g.generics and not g.bindings) or g.extern or g.kernel or any(t.mode != "value" for _, t in g.params):
        fail("E-FN-TYPE", f"{name} cannot be a function value: only plain host functions of values qualify.", e)
    c.call_edges[c.f.name].append((name, {}))
    c.callset.add(name)
    c.address_taken.add(name)
    e.ref, e.tag = g, "function"
    return Type("fn", want.mode, args=(*(t for _, t in g.params), g.ret))


def e_try(c: Checker, e: Expr, expected: Type | None) -> Type:
    """`try x` yields the success payload or returns the failure from the enclosing function."""
    ty = c.expr(e.args[0])
    ret, outer = c.leaving(e)
    layout, target = c.layouts.get(ty), c.layouts.get(ret)
    if not isinstance(layout, dict) or len(layout) != 2 or ty.name in c.p.enums:
        fail("E-TRY", "try needs a sum of exactly two variants: success first, failure second.", e)
    failure = list(layout.values())[1]
    fits = isinstance(target, dict) and len(target) == 2 and ret.name not in c.p.enums
    if not fits or list(target.values())[1] != failure:
        fail("E-TRY", f"try returns the failure of {ty.display()}, which {ret.display()} cannot carry.", e)
    c.leaks(set(c.env) - outer, e)
    e.ref = (*layout, ret, list(target)[1])
    return next(iter(layout.values())) or VOID


def e_unary(c: Checker, e: Expr, expected: Type | None) -> Type:
    ty = c.expr(e.args[0], BOOL if e.val == "!" else expected)
    if ty.mode != "value":
        fail("E-OPERATOR", "Unary operator on a view.", e)
    if ty.name in STORAGE:
        fail("E-OPERATOR", stored(ty.name), e)
    if e.val == "!":
        c.expect(ty, BOOL, e)
    elif e.val == "~":
        if ty.name not in UNSIGNED:
            fail("E-OPERATOR", "Bitwise complement requires an unsigned integer.", e)
    elif ty.name in SIGNED:
        c.guard("overflow")
    elif ty.name not in FLOAT:
        fail("E-OPERATOR", "Negation requires a signed integer or floating value.", e)
    return ty


def e_binary(c: Checker, e: Expr, expected: Type | None) -> Type:
    (a, b), op = e.args, e.val
    logical, compare = op in {"&&", "||"}, op in COMPARISONS
    hint = BOOL if logical else (None if compare else expected)
    # Literals and bare variants adapt to their peer; there is no general implicit conversion.
    if logical:  # The right side runs only when the left said `&&` true or `||` false, so it knows that much.
        left, known = c.expr(a, hint), len(c.facts)
        facts.assume(c, a, op == "&&", origin=("left", e, op == "&&"))
        right = c.expr(b, left)
        del c.facts[known:]
    elif adapts(c, a) and not adapts(c, b):
        right = c.expr(b, hint)
        left = c.expr(a, right)
    else:
        left = c.expr(a, hint)
        right = c.expr(b, left)
    c.expect(right, left, e)
    if left.mode != "value":
        fail("E-OPERATOR", "View operators are not implicit loops.", e)
    if left.name in STORAGE:
        fail("E-OPERATOR", stored(left.name), e)
    if logical:
        c.expect(left, BOOL, e)
    elif compare:
        if left.name not in SCALAR and not (op in {"==", "!="} and left.name in c.p.enums):
            fail("E-OPERATOR", "Comparison requires scalars or equality on enums.", e)
        return BOOL
    elif op in {"&", "|", "^"}:
        if left.name not in UNSIGNED:
            fail("E-OPERATOR", "Bitwise operation requires unsigned scalars.", e)
    elif left.name in INT:
        c.guard("division" if op in {"/", "%"} else "overflow")
        if op in {"+", "-"} and left == USIZE and not facts.discharge(c, e, "overflow", facts.arithmetic(c, e)):
            facts.discharge(c, e, "overflow", e.span is not None, ("span", e.span))  # What a part's guard covers.
    elif left.name not in FLOAT or op == "%":
        fail("E-OPERATOR", f"{op} not defined on {left.name}.", e)
    return left

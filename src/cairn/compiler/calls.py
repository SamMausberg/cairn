"""Calls: declared functions and generic instances, function values, arguments, records and their extents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import facts, implementations, layouts, pipelines, rings
from .builtins import SOFT, TABLE, WRAPPING
from .scope import Binding
from .syntax import copied, lent_part
from .traits import infer, instantiate, trait_member, unbound, unify, vtable
from .tree import INTRINSIC_TYPES, NUMERIC, USIZE, VISIBLE_AS, VOID, Expr, Function, Type, fail, is_view, root

if TYPE_CHECKING:
    from .checking import Checker


REPEATABLE = {"len", "min", "max", *WRAPPING, *NUMERIC}  # Calls a guard may name twice: they only compute.
COMPUTES = {*REPEATABLE, "abs", "ceil", "floor", "sqrt", "trunc", "to_bits"}  # a statement of one does nothing
PASSED = {"name", "field", "slice", "str"}  # What a view argument is written as.


def indirect(c: Checker, e: Expr, target: Binding, args: list[Expr]) -> Type:
    c.host_only(e, "A function value is a host code pointer")
    *params, ret = target.ty.args
    if len(args) != len(params):
        fail("E-ARITY", f"{e.val} expects {len(params)} arguments.", e)
    borrows: list[tuple[str, str]] = []
    for a, want in zip(args, params, strict=True):
        if want.mode == "value":
            c.expr(a, want)
            continue
        if want.mode == "rw" and not c.writable(a):
            fail("E-WRITE-LEASE", "A mutable borrow needs a mutable local or an rw borrow.", a)
        c.expect(c.expr(a, consume=False).value, want.value, a)
        lent = c.lend(a, want.mode, borrows)  # No callee row exists to rename: charge the caller now.
        c.effects |= {("write:" if want.mode == "rw" else "read:") + lent} if lent else set()
    c.disjoint(borrows, e)
    if c.lanes:
        c.lane_callee(e)
    c.effect("indirect_call")
    if target.ty.mode == "value":
        c.guard("callable")
    e.ref = ("indirect", target.ty)
    return ret


def e_call(c: Checker, e: Expr, expected: Type | None) -> Type:
    sum_ = c.bare(e.val, expected, e) if not e.ref else None  # `Some(x)` where an Option is expected
    if sum_:
        return c.variant(sum_, e.val, e.args, e, expected)
    named = c.tenv.get(e.val)
    if isinstance(named, Type) and not e.ref:  # `T(x)` converts or constructs at this instance's T.
        e.val, e.ref = named.name, named.args or None
    n, args = e.val, e.args
    targs = tuple(e.ref or ()) if n == "Dyn" else tuple(c.static(a, e) for a in e.ref or ())
    receiver = None
    if "." in n and n.split(".")[0] in c.env:  # value.method(...) on a named place
        *names, n = n.split(".")
        receiver = Expr("name", names[0], [], e.line, e.col)
        for name in names[1:]:
            receiver = Expr("field", name, [receiver], e.line, e.col)
        args = e.args = [receiver, *args]
    elif n.startswith("."):
        n, receiver = n[1:], args[0]
    elif "." in n:
        enum = c.qualify(n.rsplit(".", 1)[0], c.p.sums, node=e)
        if enum:
            return c.variant(Type(enum, args=targs), n.rsplit(".", 1)[1], args, e, expected)
        if (answer := layouts.method(c, e, n, args)) is not None:  # `T.at(r, c)`: compiler/layouts.py
            return answer
    e.val = n
    shared = c.peek(receiver) if receiver is not None else VOID
    if shared.name in {"Atomic", "Mutex"}:
        return c.shared(e, n, shared, args[1:])
    if shared.name == "IoRing":
        return rings.method(c, e, n, args[1:])
    if receiver is not None and receiver.tag == "name" and c.coop is not None and receiver.val in c.coop.pipelines:
        return pipelines.method(c, e, receiver.val, n, args[1:])  # a cooperative region's stages
    home = c.p.modules.get(shared.name, "") if receiver is not None else ""
    with c.within(home or c.module):  # A method is found in its receiver's home module first.
        method = c.qualify(n, c.fs, node=e) if home else None
    if method and c.p.modules.get(method, "") not in ("", c.module) and method not in c.p.public:
        fail("E-PRIVATE", f"{method} is private to module {c.p.modules[method]}.", e)
    if method:
        return c.invoke(e, c.fs[method], args, targs, expected)
    if n in c.env and c.env[n].ty.name == "fn":
        return c.indirect(e, c.env[n], args)
    if n in TABLE and not (n in SOFT and c.qualify(n, c.fs)):
        e.ref = ("builtin", targs)
        return TABLE[n][0](c, e, args, targs, expected)
    if n in INTRINSIC_TYPES:
        fail("E-CALLEE", f"{n} is a type, not a callable.", e)
    record = c.qualify(n, c.p.records, node=e)
    if record:
        return c.construct(e, record, args, targs, expected)
    name = c.qualify(n, c.fs, node=e)
    f = c.fs[name] if name else trait_member(c, e, n, args)
    if f is not None and isinstance(e.ref, tuple) and e.ref[0] == "dispatch":
        return f.ret
    if f is None:
        fail("E-CALLEE", "Qualified calls are declared tagged-sum constructors, not methods." if "." in n
             else f"Unknown callable {n}; arbitrary C++ names are not allowed.{c.unexpected(n)}", e)  # fmt: skip
    return c.invoke(e, f, args, targs, expected)


def unwritten(e: Expr) -> Expr:
    """A copy of what was written, with no source span (so no site, hover or edit slot) and none of the checker's
    annotations: an unchecked node keeps the explicit type arguments its parser left in `ref`."""
    return Expr(e.tag, e.val, [unwritten(a) for a in e.args], e.line, e.col, ref=None if e.ty else e.ref)


def measured(a: Expr) -> Expr:
    """What an omitted extent is: `len(v)` of the view argument that names it, or `hi - lo` of a part."""
    if a.tag == "slice":
        return Expr("binary", "-", [unwritten(a.args[2]), unwritten(a.args[1])], a.line, a.col)
    return Expr("call", "len", [unwritten(a)], a.line, a.col)


def extents(f: Function) -> list[str]:
    """The usize parameters of `f` that a later view parameter names as its extent: a call may leave them out."""
    views = [t.extent for _, t in f.params if is_view(t)]
    return [] if f.extern else [n for n, t in f.params if t.name == "usize" and t.mode == "value" and n in views]


def elaborate(f: Function, args: list[Expr]) -> None:
    """`checksum(frame)` for `checksum(n:usize, bytes:ro<u8>[n])`: a call that leaves out every extent parameter
    gets each from the first view argument that names it, and the call is rewritten in place, so everything after
    the checker sees the explicit form. The other views are held to that extent as if it had been written."""
    implied = extents(f)
    if not implied or len(args) != len(f.params) - len(implied):
        return
    given = iter(list(args))
    written = {n: next(given) for n, _ in f.params if n not in implied}
    if any(is_view(t) and written[n].tag not in PASSED for n, t in f.params if n in written):
        return  # `dot(len(a), a)` wrote one extent and forgot a view: that is an arity error, said as one.
    first = {t.extent: written[n] for n, t in reversed(f.params) if is_view(t)}  # the earliest view wins
    args[:] = [written[n] if n in written else measured(first[n]) for n, _ in f.params]


def lent(c: Checker, f: Function, args: list[Expr]) -> None:
    """`io.print(out)` for `print(n:usize, text:ro<u8>[n])`: a record that lends a view (`lends data[0..len];`),
    named where an array view is expected, is that part written out, `out.data[0..out.len]`, before anything else
    reads the call. The part pays its guard, lends its elements and is held to every alias and lease rule, as the
    part a person would write is; the bounds are read again at every call, so nothing about them is assumed."""
    implied = extents(f)
    params = f.params if len(args) == len(f.params) else [(n, t) for n, t in f.params if n not in implied]
    if len(params) != len(args):
        return
    for k, (a, (_, want)) in enumerate(zip(args, params, strict=True)):
        if not want.extent or a.tag not in {"name", "field", "index"} or root(a).val not in c.env:
            continue
        counted, discharged = dict(c.counts), dict(c.discharged)
        ty = c.expr(copied(a), consume=False)  # a look at its type only: the guards it counted are not emitted
        for kept, saved in ((c.counts, counted), (c.discharged, discharged)):
            kept.clear()
            kept.update(saved)
        if not is_view(ty) and ty.name in c.p.lends:
            args[k] = lent_part(a, c.p.lends[ty.name])


def same(a: Expr, b: Expr) -> bool:
    """Whether two expressions are written alike."""
    return (a.tag, a.val, len(a.args)) == (b.tag, b.val, len(b.args)) and all(map(same, a.args, b.args))


def spanned(args: list[Expr]):
    """Mark an argument written `hi - lo` over the bounds of a part among the same arguments, as an omitted extent
    is. The part evaluates those bounds under their own guards and traps when lo > hi, all before the callee runs,
    so that argument's usize `+` and `-` need no guard of their own (`e_binary`, with the proof ("span", part))."""
    for a in args:
        part = next((p for p in args if p.tag == "slice" and a.tag == "binary" and a.val == "-"
                     and same(a.args[0], p.args[2]) and same(a.args[1], p.args[1])), None)  # fmt: skip
        stack = [a] if part is not None else []
        while stack:
            node = stack.pop()
            node.span = part
            stack += node.args


def invoke(c: Checker, e: Expr, f: Function, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    if f.implements is not None:
        implementations.called(c, f, e)
    if c.p.lends:
        lent(c, f, args)
    elaborate(f, args)
    spanned(args)
    if len(args) != len(f.params):
        implied = extents(f)
        fewer = f", or {len(f.params) - len(implied)} leaving out the extents {', '.join(implied)}" if implied else ""
        fail("E-ARITY", f"{e.val} expects {len(f.params)} arguments{fewer}.", e)
    if f.extern and not c.unsafe_depth:
        fail("E-UNSAFE", f"{f.name} is foreign; call it inside an unsafe block.", e)
    if f.kernel and not c.device_depth:
        fail("E-PLACEMENT", f"{f.name} is a kernel: it runs in device lanes, not in host code.", e)
    if f.generics and not f.bindings:
        f = instantiate(c, f, infer(c, f, args, targs, expected, e), e)
    subst = dict(zip((n for n, _ in f.params), args, strict=True))
    borrows: list[tuple[str, str]] = []
    closures: list[list[tuple[str, str]]] = []
    mapping: dict[str, str] = {}
    for a, (name, want) in zip(args, f.params, strict=True):
        if want.name == "fn":  # If the callee's lanes call it, what is written here is judged here.
            c.fn_sites.append((a, {n for n, _ in c.f.params}, f.name, name, c.f.name))
            if c.lanes and a.tag == "name" and a.val in c.env:  # Handed on from a lane, it runs in that lane.
                c.lane_callee(a)
        if want.name == "fn" and (a.tag == "lambda" or root(a).val not in c.env):
            c.expr(a, want)  # A declared function borrows nothing; a closure borrows what it captures.
            closures += [a.ref.captures] if a.tag == "lambda" else []
            mapping[name] = ""
            continue
        if want.mode == "value":
            if c.kind(c.expr(a, want)) != "copy" and root(a).tag == "name":
                borrows.append((c.where(a), "rw"))  # The callee may release it while a view is live.
            mapping[name] = a.val if a.tag == "name" and a.val in dict(c.f.params) else ""
            continue
        named = root(a).tag == "name" and root(a).val in c.env
        if want.name == "dyn" and c.peek(a).name != "dyn":
            boxed = c.peek(a).value == Type("Dyn", args=want.args)
            table = None if boxed else vtable(c, want.args[0].name, c.peek(a).value, a)
            if not named or (want.mode == "rw" and not c.writable(a)):
                fail("E-WRITE-LEASE", "A dynamic reference borrows a named place (mutable for rw).", a)
            inner = Expr(a.tag, a.val, a.args, a.line, a.col, a.ty, a.start, a.end, a.ref)
            c.intact(inner, want.mode, False)
            a.tag, a.args, a.ty = "coerce", [inner], want
            a.ref = table
            c.early[id(a)] = a
            c.leased(c.where(inner), want.mode, a)
            borrows.append((c.where(inner), want.mode))
            mapping[name] = root(inner).val
            continue
        if not named and (want.mode == "rw" or (want.extent and a.tag != "str")):
            fail("E-CALL-VIEW", "Only direct view parameters may be passed.", a)
        if want.extent:
            actual = c.view_argument(a)
            extent = want.extent if want.extent.isdigit() else c.extent_of(subst[want.extent])
            if a.tag == "slice":  # A part is guarded dynamically, so its extent may be any repeatable expression.
                a.ref, extent = subst.get(want.extent, want.extent), actual.extent
                a.ref = c.repeatable(a.ref) if isinstance(a.ref, Expr) else a.ref
            if extent is None:
                fail("E-CALL-SHAPE", "View extent must be a name, literal or len(view).", subst[want.extent])
            mode = "rw" if actual.mode == "rw" and want.mode == "ro" else want.mode
            place = actual.place if (actual.place, want.place) in VISIBLE_AS else want.place
            asked = Type(want.name, want.mode, extent, want.args, want.place)
            c.expect(actual, Type(want.name, mode, extent, want.args, place), a, asked)
        else:
            actual = c.expr(a, consume=False)
            if want.mode == "rw" and not c.writable(a):
                fail("E-WRITE-LEASE", "A mutable borrow needs a mutable local or an rw borrow.", a)
            c.expect(actual.value, want.value, a)
        mapping[name] = c.lend(a, want.mode, borrows, bool(want.extent))
        if named and want.mode == "rw" and c.env[root(a).val].ty.mode == "value":
            c.effect("write:" + root(a).val)
    for a in args:  # Every extent is known now, so a part whose bounds the facts settle loses its guard.
        if a.tag == "slice":
            facts.discharge(c, a, "bounds", facts.part(c, a))
    c.disjoint(borrows, e, *closures)
    c.call_edges[c.f.name].append((f.name, mapping))
    c.callset.add(f.name)
    c.borrowed = borrows
    if c.lanes or c.f.kernel:
        c.lane_calls.append((f.name, bool(c.device_depth), e, c.f.name))
    e.ref = f
    return f.ret


def repeatable(c: Checker, e: Expr) -> Expr:
    """A part's bounds and extent are named again by its guard, so they are written from what a second look
    cannot change or pay for twice: names, literals, fields, elements, operators, `len` and scalar arithmetic.
    A call is bound to a name first, as a computed capacity is (E-OWNER-EXTENT)."""
    plain = e.tag in {"name", "int", "field", "index", "binary", "unary"} or (e.tag == "call" and e.val in REPEATABLE)
    if not plain:
        fail("E-CALL-SHAPE", "A part's bounds and extent are names, literals and arithmetic; bind a call first.", e)
    for a in e.args:
        c.repeatable(a)
    return e


def view_argument(c: Checker, a: Expr) -> Type:
    """A view, local owner, Buf, Array, part or string passed where an array borrow is expected."""
    if a.tag == "slice":
        base, lo, hi = a.args
        ty = c.view_argument(base)
        c.expr(c.repeatable(lo), USIZE)
        c.expr(c.repeatable(hi), USIZE)
        c.guard("bounds")
        a.ty = Type(ty.name, ty.mode, "part", ty.args, ty.place)
        return a.ty
    ty = c.expr(a, consume=False)
    if is_view(ty) or ty.name not in {"Buf", "Array"}:
        return ty
    mode = "rw" if c.writable(a) else "ro"
    extent = c.declared_extent(a) or (f"len({c.identity(a)})" if ty.name == "Buf" else str(ty.args[1]))
    return Type(ty.args[0].name, mode, extent, ty.args[0].args)


def construct(c: Checker, e: Expr, record: str, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    fields = c.p.records[record]
    if len(fields) != len(args):
        fail("E-ARITY", f"Record {record} expects {len(fields)} fields in declaration order.", e)
    names = [g for g, _ in c.p.generics.get(record, [])]
    bound: dict[str, Any] = dict(zip(names, targs, strict=False))
    if names and not bound and expected and expected.name == record:
        bound = dict(zip(names, expected.args, strict=True))
    home = c.p.modules.get(record, "")
    ordered = sorted(zip(args, fields, strict=True), key=lambda x: x[0].tag in {"int", "float"})
    for a, (_, declared) in ordered:  # Literals adapt after the other fields bind generics.
        with c.within(home, {}):
            if unbound(c, declared, set(names), bound) and not unify(c, declared, c.peek(a), bound, set(names)):
                fail("E-INFER", f"Cannot infer the type arguments of {record}; write {record}[...](...).", a)
    for a, (_, declared) in zip(args, fields, strict=True):
        with c.within(home, dict(bound)):
            want = c.resolve(declared, a)
        c.expr(a, want)
    c.establish(record, args, fields)
    ty = c.resolve(Type(record, args=tuple(bound[g] for g in names)), e)
    e.ref = ("record", ty)
    return ty


def establish(c: Checker, record: str, args: list[Expr], fields: list[tuple[str, Type]]):
    """A declared field extent holds of every value ever built, and nothing checks it at run time, so a
    constructor writes the carrier inline as `Buf[T](e)` with the extent identity the extent field is given.
    Every other way to a record preserves it: a move copies both halves, `take` and `swap` exchange whole
    places, and zeroed storage is `rows` of zero beside an empty `Buf`."""
    at = {n: i for i, (n, _) in enumerate(fields)}
    for carrier, extent in c.p.field_extents.get(record, {}).items():
        a, size = args[at[carrier]], c.extent_of(args[at[extent]])
        inline = a.tag == "call" and a.val == "Buf" and len(a.args) == 1
        if size is None or not inline or c.extent_of(a.args[0]) != size:
            fail("E-EXTENT-FIELD", f"{carrier} holds {extent} elements: build it here as Buf[T](n) on "
                 f"the same n that {extent} is given.", a)  # fmt: skip

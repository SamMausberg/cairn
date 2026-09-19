"""Traits, bounds and generic instances: who implements what, what a parameter promises, and the one place an
instance is made. Functions over the checker, like effects.py and builtins.py; checking.py calls them by name.
"""

from __future__ import annotations

import copy
import itertools
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .effects import allowed, fixed_point
from .syntax import (
    CPP,
    FLOAT,
    INT,
    INTRINSIC_TYPES,
    MAX_FUNCTIONS,
    NUMERIC,
    SCALAR,
    SIGNED,
    UNSIGNED,
    Diagnostic,
    Expr,
    Function,
    Type,
    fail,
    is_view,
)

if TYPE_CHECKING:
    from .checking import Checker

KINDS = ["copy", "affine", "linear"]
PENDING: Any = object()  # An implementation that is being decided: a bound that asks for it has found a cycle.
# What a generic parameter may promise besides traits: the most its kind can be, or a closed class of scalars.
CLASSES = {"integer": INT, "unsigned": UNSIGNED, "signed": SIGNED, "float": FLOAT, "numeric": NUMERIC, "scalar": SCALAR}


def satisfies(c: Checker, value: Type, wanted: str, module: str, node: Any) -> str:
    """ "" if `value` keeps the promise `wanted` (a trait, a kind bound or a scalar class), else why not."""
    with c.within(module):
        trait = c.qualify(wanted, c.p.traits, node=node)
    if trait:
        return "" if implemented(c, trait, value, node) else f"does not implement {wanted}"
    if wanted in KINDS[:2]:
        fits = KINDS.index(c.kind(value)) <= KINDS.index(wanted)
        return "" if fits else f"is {c.kind(value)}, not {wanted}"
    plain = value.mode == "value" and not value.args and value.name in CLASSES.get(wanted, ())
    return "" if plain else f"is not {wanted}" if wanted in CLASSES else f"does not implement {wanted}"


def unify(c: Checker, pattern: Any, actual: Any, bound: dict[str, Any], generics: set[str]) -> bool:
    """Bind generic names in `pattern` so that its value part equals `actual`."""
    actual = actual.value if isinstance(actual, Type) else actual
    if isinstance(pattern, Type) and pattern.name in generics and not pattern.args:
        return bound.setdefault(pattern.name, actual) == actual
    if not isinstance(pattern, Type) or not isinstance(actual, Type):
        return pattern == actual
    known = pattern.name in CPP or pattern.name in INTRINSIC_TYPES
    name = pattern.name if known else c.qualify(pattern.name, c.types)
    if name != actual.name or len(pattern.args) != len(actual.args):
        return False
    return all(unify(c, p, a, bound, generics) for p, a in zip(pattern.args, actual.args, strict=True))


def unbound(c: Checker, declared: Any, generics: set[str], bound: dict[str, Any]) -> bool:
    """Does this declared type still mention an unbound generic parameter?"""
    if not isinstance(declared, Type):
        return False
    if declared.name in generics and not declared.args:
        return declared.name not in bound
    return any(unbound(c, a, generics, bound) for a in declared.args)


def instantiate(c: Checker, template: Function, bound: dict[str, Any], node: Any) -> Function:
    values = [bound.get(g) for g, _ in template.generics]
    if None in values:
        missing = ", ".join(g for g, _ in template.generics if g not in bound)
        fail("E-INFER", f"Cannot infer {missing} of {template.name}; write {template.name}[...](...).", node)
    name = f"{template.name}[{', '.join(v.display() if isinstance(v, Type) else str(v) for v in values)}]"
    if name not in c.fs:
        for (g, constraint), value in zip(template.generics, values, strict=True):
            if (constraint == "nat") != isinstance(value, int):
                fail(
                    "E-GENERIC-KIND",
                    f"{g} of {template.name} is a {'natural' if constraint == 'nat' else 'type'}.",
                    node,
                )
            for wanted in constraint.split("+") if constraint not in {"nat", "type"} else []:
                broken = satisfies(c, value, wanted, template.module, node)
                if broken:  # The caller learns which promise failed, not which line of the body did.
                    code = "E-TRAIT-IMPL" if broken.startswith("does not implement") else "E-BOUND"
                    fail(code, f"{value.display()} {broken}; {template.name} needs [{g}:{constraint}].", node)
        if len(c.p.functions) >= MAX_FUNCTIONS:
            fail("E-EXPANSION-LIMIT", "Expanded program exceeds 2048 functions.", node)
        f = copy.deepcopy(template)
        f.name, f.bindings = (name, dict(zip((g for g, _ in template.generics), values, strict=True)))
        c.fs[name] = f
        c.p.functions.append(f)
        c.function(f)
    return c.fs[name]


def infer(c: Checker, f: Function, args: list[Expr], targs: tuple, expected: Type | None, node: Any) -> dict:
    """Bind a template's generics from explicit arguments, Self, argument types, then the result."""
    names = [g for g, _ in f.generics]
    generics, bound = set(names), dict(zip(names, targs, strict=False))

    def bind(pattern: Any, actual: Any) -> bool:  # The pattern is the template's text; an argument is the caller's,
        with c.within(f.module, {}):  # typed where it is written, with the caller's names and the caller's privacy.
            return unify(c, pattern, actual, bound, generics)

    if f.owner:
        bind(f.owner[1], c.peek(args[0]))
    ordered = sorted(zip(args, f.params, strict=True), key=lambda x: x[0].tag in {"int", "float"})
    for a, (_, declared) in ordered:  # Typed arguments bind first, then the expected result, then literals.
        if expected and a.tag in {"int", "float"}:
            bind(f.ret, expected)
        if declared.extent in generics and declared.extent not in bound and a.tag != "slice":
            seen = c.peek(a)  # `say("text")` tells `[N:nat]` its N, and so does a fixed array.
            extent = seen.extent if is_view(seen) else str(seen.args[1]) if seen.name == "Array" else ""
            bound.update({declared.extent: int(extent)} if extent.isdigit() else {})
        if unbound(c, declared, generics, bound):
            actual = c.peek(a.args[0] if a.tag == "slice" else a)  # A part has the element type of its base.
            element = actual if not declared.extent or is_view(actual) else actual.args[0]
            if not bind(declared, element):
                fail("E-TYPE-MISMATCH", f"{actual.display()} does not fit {declared.display()}.", a)
    if expected:
        bind(f.ret, expected)
    return bound


def trait_member(c: Checker, e: Expr, n: str, args: list[Expr]) -> Function | None:
    """A trait member called by name: dispatch is on the type of its Self argument, static unless that is dyn."""
    prefix, _, short = n.rpartition(".")
    named, found = [], []
    for trait, members in c.p.traits.items():
        hidden = c.p.modules.get(trait, "") not in ("", c.module) and trait not in c.p.public
        if hidden or (prefix and c.qualify(prefix, c.p.traits) != trait):
            continue
        for declared in members:
            position = next((i for i, (_, t) in enumerate(declared.params) if t.name == "Self"), None)
            if declared.name == short and position is not None and position < len(args):
                receiver = c.peek(args[position]).value
                dynamic = receiver in (Type("dyn", args=(Type(trait),)), Type("Dyn", args=(Type(trait),)))
                named.append(trait)
                if dynamic or implemented(c, trait, receiver, e):
                    found.append((trait, declared, position, dynamic))
    if len(found) > 1:
        fail("E-TRAIT-AMBIGUOUS", f"{short} is a member of {' and '.join(t for t, *_ in found)}; "
             f"write {found[0][0]}.{short}(...).", e)  # fmt: skip
    if named and not found:
        fail("E-TRAIT-IMPL", f"{args[0].ty.display() if args else n} does not implement {' or '.join(named)}.", e)
    if not found:
        return None
    trait, declared, position, dynamic = found[0]
    if dynamic:
        return dispatch(c, e, trait, declared, position, args)
    return implemented(c, trait, c.peek(args[position]).value, e)[short]


def implemented(c: Checker, trait: str, target: Type, node: Any = None) -> dict[str, Function] | None:
    """The one implementation of a trait for a concrete type, or None: every declared member, conforming
    to its declaration with Self := target. A generic impl is instantiated; two matching impls are an error."""
    if (trait, target) not in c.impls and trait in c.bounds.get(target.name, ((),))[0]:
        promised = {}
        for m in c.p.traits[trait]:
            with c.within(c.p.modules.get(trait, ""), {"Self": target}):
                params = [(n, c.resolve(t, m)) for n, t in m.params]
                promised[m.name] = Function(f"{trait}.{target.name}.{m.name}", params, c.resolve(m.ret, m), [])
            name = promised[m.name].name  # It may do what its trait's ceiling allows; with none, nobody can say what.
            row = {f"bound:{trait}.{m.name}"} if m.effects is None else allowed(m.effects) - {"pure"}
            c.fs[name], c.local_effects[name], c.calls[name], c.call_edges[name] = (promised[m.name], row, set(), [])
            c.signed.add(name)
        c.impls[trait, target] = promised
    if c.impls.get((trait, target)) is PENDING:
        fail("E-TRAIT-OVERLAP", f"Whether {target.display()} implements {trait} depends on itself: the bound of a "
             "generic impl asks the question that impl answers.", node)  # fmt: skip
    if (trait, target) not in c.impls:
        c.impls[trait, target] = PENDING
        matches: dict[str, tuple[Function, dict[str, Any]]] = {}
        for f in [f for f in list(c.fs.values()) if f.owner and not f.bindings]:
            bound: dict[str, Any] = {}
            with c.within(f.module):
                if c.qualify(f.owner[0], c.p.traits) != trait:
                    continue
                if not unify(c, f.owner[1], target, bound, {g for g, _ in f.generics}):
                    continue
            promises = [
                (bound[g], w) for g, c in f.generics if g in bound and c not in {"nat", "type"} for w in c.split("+")
            ]
            if any(satisfies(c, value, wanted, f.module, node) for value, wanted in promises):
                continue  # `impl[T:integer] Ord for T` is an impl for the integers, not for everything.
            if any(other.block != f.block for other, _ in matches.values()):
                fail("E-TRAIT-OVERLAP", f"Two impls of {trait} match {target.display()}: one Self type means "
                     "one implementation.", node)  # fmt: skip
            matches[f.name.rsplit(".", 1)[1]] = (f, bound)
        # A member whose body uses its own trait on its own type finds the template while its instance is made.
        c.impls[trait, target] = {short: f for short, (f, _) in matches.items()} or None
        found = {short: instantiate(c, f, bound, node) if f.generics and set(bound) == {g for g, _ in f.generics}
                 else f for short, (f, bound) in matches.items()}  # fmt: skip
        declared = {m.name: m for m in c.p.traits[trait]}
        if found and set(found) != set(declared):
            fail("E-TRAIT-IMPL", f"impl {trait} for {target.display()} defines {', '.join(sorted(found))}; "
                 f"the trait declares {', '.join(sorted(declared))}.", next(iter(found.values())))  # fmt: skip
        for short, f in found.items():
            if not f.generics or f.bindings:
                c.signature(f)
                rename = dict(zip((n for n, _ in f.params), (n for n, _ in declared[short].params), strict=False))
                got = [Type(t.name, t.mode, rename.get(t.extent, t.extent), t.args, t.place) for _, t in f.params]
                with c.within(c.p.modules.get(trait, ""), {"Self": target}):
                    want = [c.resolve(t, f) for _, t in declared[short].params]
                    if [*got, f.ret] != [*want, c.resolve(declared[short].ret, f)]:
                        fail("E-TRAIT-IMPL", f"{f.name} does not match {trait}.{short}"
                             f"({', '.join(t.display() for t in want)}).", f)  # fmt: skip
                ceiling = declared[short].effects  # A member's ceiling is part of what a bound on its trait promises.
                if ceiling is not None and f.effects is not None and not allowed(f.effects) <= allowed(ceiling):
                    fail("E-TRAIT-IMPL", f"{f.name} declares effects that {trait}.{short} does not allow.", f)
                f.effects = ceiling if f.effects is None else f.effects
        c.impls[trait, target] = found or None
    return c.impls[trait, target]


def dispatch(c: Checker, e: Expr, trait: str, member: Function, position: int, args: list[Expr]) -> Function:
    """An indirect call through the vtable; its row is the join of every implementation's."""
    c.host_only(e, "A dynamic reference points at a host table")
    if len(args) != len(member.params):
        fail("E-ARITY", f"{member.name} expects {len(member.params)} arguments.", e)
    if member.params[position][1].mode == "rw" and not c.writable(args[position]):
        fail("E-WRITE-LEASE", f"{member.name} writes its receiver; it needs an rw<dyn {trait}> reference.", e)

    def selfless(t: Any) -> bool:
        return not isinstance(t, Type) or (t.name != "Self" and all(selfless(a) for a in t.args))

    receiver = c.lend(args[position], member.params[position][1].mode, [])  # Leases and lanes see it.
    callbacks = []
    with c.within(c.p.modules.get(trait, ""), {"Self": Type("dyn", args=(Type(trait),))}):
        for i, (a, (_, declared)) in enumerate(zip(args, member.params, strict=True)):
            if i != position:
                if declared.mode != "value" or not selfless(declared) or not selfless(member.ret):
                    fail("E-DYN", f"{trait}.{member.name} is not dyn-compatible: only its receiver may be "
                         "a borrow or mention Self.", e)  # fmt: skip
                callbacks += [(a, i)] if c.expr(a, c.resolve(declared, a)).name == "fn" else []
        if not selfless(member.ret):
            fail("E-DYN", f"{trait}.{member.name} returns Self, which a dynamic reference cannot name.", e)
        ret = c.resolve(member.ret, e)
    targets: list[str] = []  # Filled once every implementation is known: a later coercion may add one.
    c.dispatches.append((c.f.name, {n for n, _ in c.f.params}, trait, member.name, position, receiver,
                            targets, callbacks, e if c.lanes else None, c.callset))  # fmt: skip
    c.effect("dispatch")
    index = [m.name for m in c.p.traits[trait]].index(member.name)
    e.ref = ("dispatch", trait, index, position, targets)
    e.ty = ret
    return Function("", [], ret, [])


def vtable(c: Checker, trait: str, value: Type, node: Any) -> list[Function]:
    """The found a value of this type puts behind `dyn trait`, in declaration order."""
    found = implemented(c, trait, value, node)
    if found is None or any(m.generics and not m.bindings for m in found.values()):
        fail("E-TRAIT-IMPL", f"{value.display()} does not implement {trait} with concrete found.", node)

    def selfless(t: Any) -> bool:
        return not isinstance(t, Type) or (t.name != "Self" and all(selfless(a) for a in t.args))

    for m in c.p.traits[trait]:  # The static table has a slot for every member, called or not.
        receiver = next((i for i, (_, t) in enumerate(m.params) if t.name == "Self"), None)
        rest = [t for i, (_, t) in enumerate(m.params) if i != receiver]
        if receiver is None or not selfless(m.ret) or any(t.mode != "value" or not selfless(t) for t in rest):
            fail("E-DYN", f"{trait}.{m.name} is not dyn-compatible: only its receiver may be a borrow or "
                 "mention Self.", node)  # fmt: skip
    c.callset |= {m.name for m in found.values()}  # The static table reaches them, called here or not.
    return [found[m.name] for m in c.p.traits[trait]]


def hold_impls(c: Checker, concrete: list[Function]):
    """Every impl is held to its trait, used or not; a generic one must be determined by its Self type."""

    def mentioned(t: Any) -> set[str]:
        return {t.name}.union(*(mentioned(a) for a in t.args)) if isinstance(t, Type) else set()

    for f in [f for f in c.p.functions if f.owner and f.generics and not f.bindings]:
        with c.within(f.module):
            trait = c.qualify(f.owner[0], c.p.traits, node=f)
        stated = next((m for m in c.p.traits.get(trait, []) if m.name == f.name.rsplit(".", 1)[1]), None)
        loose = {g for g, _ in f.generics} - mentioned(f.owner[1]) - {g for g, _ in (stated.generics if stated else [])}
        if stated is None or loose:  # Its instances are made from the Self type alone.
            fail("E-TRAIT-IMPL", f"{f.name}: a generic impl states its trait's members, and every generic "
                 f"parameter appears in its Self type{' (not ' + ', '.join(sorted(loose)) + ')' if loose else ''}.", f)  # fmt: skip
    for f in [f for f in concrete if f.owner]:
        with c.within(f.module):
            trait = c.qualify(f.owner[0], c.p.traits, node=f)
            if trait is None:
                fail("E-TRAIT-IMPL", f"{f.owner[0]} is not a trait this module can name.", f)
            implemented(c, trait, c.resolve(f.owner[1], f), f)


def connect_dispatches(c: Checker):
    """Draw each dynamic call's edges once every implementation is known: a later coercion may add one."""
    for caller, parameters, trait, name, position, receiver, targets, callbacks, lane, row in c.dispatches:
        for (owner, held), members in c.impls.items():  # A witness is never the value behind a dynamic reference.
            real = owner == trait and members and not held.name.startswith("?")
            if real and not (members[name].generics and not members[name].bindings):
                target = members[name]
                targets.append(target.name)
                c.calls[caller].add(target.name)
                row.add(target.name)  # Inside a closure this is the closure's own row.
                passed = {target.params[i][0]: a.val if a.tag == "name" and a.val in parameters else ""
                          for a, i in callbacks}  # fmt: skip
                c.call_edges[caller].append((target.name, {target.params[position][0]: receiver, **passed}))
                c.lane_calls += [(target.name, False, lane, caller)] if lane else []
                c.fn_sites += [(a, parameters, target.name, target.params[i][0], caller) for a, i in callbacks]


def certify(make: Callable[[], Checker]) -> tuple[Checker, dict[str, str]]:
    """Check each generic function once against what its bounds promise. A parameter bounded by a scalar
    class is checked at every type of that class; any other is an opaque witness that implements exactly the
    traits named and is as owning as the bounds allow (linear when they say nothing: the strictest kind), whose
    members may do what their trait's ceiling allows. "ok" means every instance whose arguments satisfy the
    bounds checks too, bodies and whole-program rules alike; otherwise the entry names the first thing the
    template needed beyond its bounds, and it is still checked per instance, as before. A template that fails is
    left out and the rest are judged again on a fresh checker (`make`), so one verdict never colours another."""
    failed: dict[str, str] = {}
    while True:
        c, current = make(), ""
        templates = [f for f in c.p.functions if f.generics and not f.bindings]
        try:
            c.bodies()
            for f in [f for f in templates if f.name not in failed]:
                current = f.name
                choices: list[list[Any]] = []
                for g, constraint in f.generics:
                    words = [] if constraint == "type" else constraint.split("+")
                    classes = [CLASSES[w] for w in words if w in CLASSES]
                    if constraint == "nat":
                        choices.append([])
                    elif classes:
                        choices.append([Type(n) for n in sorted(set.intersection(*classes))])
                    else:
                        witness = Type(f"?{f.name}.{g}")
                        with c.within(f.module):
                            traits = [t for t in (c.qualify(w, c.p.traits) for w in words) if t]
                        kind = next((w for w in KINDS[:2] if w in words), "linear")
                        c.bounds[witness.name] = (traits, kind)
                        choices.append([witness])
                # A natural's witnesses are its families' instances, which `bodies` has checked.
                family = [g for g in c.p.functions if g.source_name == f.name and g.bindings]
                naturals = all(kind == "nat" for _, kind in f.generics)
                if (not all(choices) and not (family and naturals)) or math.prod(map(len, choices)) > 128:
                    failed[f.name] = (
                        "too many scalar cases" if all(choices) else "no family gives its natural a witness"
                    )
                    continue
                for values in itertools.product(*choices) if all(choices) else []:
                    instantiate(c, f, dict(zip((g for g, _ in f.generics), values, strict=True)), f)
            current = ""
            c.judge()
            return c, {f.name: failed.get(f.name, "ok") for f in templates}
        except Diagnostic as e:  # The strictest witness is the usual reason: say which parameter promised nothing.
            at = c.fs.get(c.judging) or getattr(c.s, "f", None)  # What a whole-program rule, or a body check, was at.
            blamed = current or (at.source_name if at is not None and at.bindings else "")
            f = next((f for f in templates if f.name == blamed and blamed not in failed), None)
            if f is None:  # The program itself does not check, so nothing more can be decided: unknown, never ok.
                unknown = f"unknown: the program does not check ({e.data['code']})"
                return c, {f.name: failed.get(f.name, unknown) for f in templates}
            free = [g for g, k in f.generics if k != "nat" and not {*KINDS, *CLASSES} & set(k.split("+"))]
            failed[blamed] = f"{e.data['code']}: {e.data['message']}"
            if free and e.data["code"].startswith(("E-LINEAR", "E-MOVE", "E-PARTIAL-MOVE")):
                failed[blamed] += (
                    f" {', '.join(free)} may be linear here; [{free[0]}:affine] or [{free[0]}:copy] promises more."
                )
            if "bound:" in str(e.data):
                failed[blamed] += (
                    " A bound promises only what its trait declares: give that member a ceiling (`-> u64 pure;`)."
                )


def described(make: Callable[[], Checker]) -> tuple[Checker, dict[str, str], dict[str, set[str]]]:
    """Verdicts as `certify`, and the effect row of every function: a concrete one's own, a template's at its
    witnesses, where `bound:Trait.member` stands for whatever that member of its argument does."""
    c, verdicts = certify(make)
    rows = fixed_point(c)
    for name in [name for name, verdict in verdicts.items() if verdict == "ok"]:
        witnessed = [rows[n] for n in rows if n.startswith(name + "[")]
        rows[name] = set().union(*witnessed) if witnessed else set()
    return c, verdicts, rows

"""Alternative implementations: a function keeps one reference definition beside separately written ones.

    fn total(n:usize, xs:ro<u64>[n]) -> u64 { ... }                                    // the reference
    fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 { ... }
    plan total use total_by4;

An implementation has exactly its reference's signature (E-IMPL-SIGNATURE), so its input domain is the one the
reference's entry guards admit. Its row stays inside the reference's ceiling: the declared one, or the reference's
own row when none is declared (E-IMPL-EFFECT). It writes no rounding the reference does not write (E-IMPL-NUMERICS).
`when` is a condition over the value parameters that cannot trap (E-IMPL-WHEN), so where it is false the reference
runs and every input the reference admits is still admitted. Only a test block calls an implementation by name, and
an implementation never reaches its reference, which could dispatch back to it (E-IMPL-CALL).

Nothing runs an implementation unless a plan names it (`plan total use total_by4;`, E-IMPL-USE), and then the
reference's body starts with the dispatch: the condition tested at entry, or nothing when there is no condition. A
call whose arguments make the condition a constant that folds to true (`total(8, xs)`) calls the implementation
directly, the condition established at compile time.
`needs(cp_async)` names the device features an implementation's code needs, from the device-target table
(projects/target.py), and only an implementation that runs device code may name one. A selection adds them to what the
program asks of its device target, and a build whose target lacks one is refused (E-IMPL-TARGET, `targeted`) rather
than falling back to the reference: a mismatch is refused, never approximated.

A reference's row is the join of its own and every implementation's, whichever one a plan selects, so choosing an
implementation changes no row. Each implementation has an identity, the digest of the two declarations as written
(`Implements.identity`), which a validation record and a candidate history key on.

An implementation may take natural parameters, and then says which values each takes:

    fn total_by[K:nat](n:usize, xs:ro<u64>[n]) -> u64 implements total when n % K == 0 tune K in [2, 4, 8] { ... }
    plan total use total_by[8];

Each combination of the listed values is an instance, `total_by[8]`, made and held to every rule above whether or not
a plan selects it, so each instance the search may try is one the checker accepted; one that fails is refused with
its own code and named. A value outside the list, a parameter with no list, or a selection without values is
E-IMPL-PARAM. An instance's identity is the declarations' identity with its values, and the list itself is not part
of it, so listing another value leaves every other instance's validation current.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from typing import TYPE_CHECKING, Any

from .effects import allowed, fixed_point
from .scope import Binding, Scope
from .tree import (
    BOOL,
    FLOAT,
    INT,
    SCALAR,
    USIZE,
    VOID,
    WIDTH,
    Diagnostic,
    Expr,
    Function,
    Stmt,
    clone,
    fail,
    is_view,
    nested,
)

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

COMPARISONS = {"==", "!=", "<", "<=", ">", ">=", "&&", "||", "&", "|", "^"}
TOTAL_CALLS = {"min", "max", "add_wrap", "sub_wrap", "mul_wrap"}  # calls of values that never trap
WHEN = (
    "A when is a condition over the value parameters that cannot trap: comparisons, && || !, & | ^ ~, min, max, "
    "the wrapping forms, / or % by a nonzero literal or natural parameter, shr or shl_wrap by one below the width, "
    "len of a view parameter, literals, constants and natural parameters."
)


MAX_INSTANCES = 16  # instances one parameterized implementation lists, all its parameters' values combined


def check(c: Checker) -> None:
    """Every declaration, its condition, and every `plan f use g;`: what needs only the checked bodies."""
    for f in list(c.p.functions):
        if f.implements is not None and f.generics and not f.bindings:
            parameterized(c, f)
        elif f.implements is not None and not f.bindings:
            declared(c, f)
    for module, written, chosen, token in c.p.selections:
        select(c, module, written, chosen, token)


def parameterized(c: Checker, f: Function) -> None:
    """Make every instance the `tune` clause lists, and hold each to every rule an implementation keeps."""
    clause = f.implements
    assert clause is not None
    if f.kernel or f.owner or any(kind != "nat" for _, kind in f.generics):
        what = "a kernel" if f.kernel else "a trait member" if f.owner else "generic over types"
        fail("E-IMPLEMENTS", f"{f.name} is {what}; an implementation is an ordinary function with a body, and its "
             "parameters in brackets are naturals.", f)  # fmt: skip
    values = listed(f)
    combos = list(itertools.product(*values.values()))
    if len(combos) > MAX_INSTANCES:
        fail("E-IMPL-PARAM", f"{f.name} lists {len(combos)} instances; an implementation lists at most "
             f"{MAX_INSTANCES}, all its parameters' values combined.", f)  # fmt: skip
    from .traits import instantiate

    for combo in combos:
        bound = dict(zip(values, combo, strict=True))
        name = instance(f.name, combo)
        try:
            made = instantiate(c, f, bound, f)
            assert made.implements is not None
            made.implements.identity = hashlib.sha256(f"{clause.identity}\0{local(name)}".encode()).hexdigest()
            declared(c, made)
        except Diagnostic as error:  # say which instance: the position is the template's text
            error.data["message"] = f"{local(name)}: {error.data['message']}"
            error.data.setdefault("instance", name)
            raise


def instance(template: str, values: tuple[int, ...] | list[int]) -> str:
    """The name of an instance, as a generic instance is named: `total_by[8]`, `tiled[4, 2]`."""
    return f"{template}[{', '.join(str(v) for v in values)}]"


def local(name: str) -> str:
    """A name without its module, an instance's values kept: `m.total_by[8]` is `total_by[8]`."""
    base, bracket, rest = name.partition("[")
    return base.rsplit(".", 1)[-1] + bracket + rest


def listed(f: Function) -> dict[str, tuple[int, ...]]:
    """Each natural parameter of `f` and the values its `tune` clause lists, in the parameters' order."""
    clause = f.implements
    assert clause is not None
    names = [g for g, _ in f.generics]
    given = dict(clause.tune)
    if not clause.tune:
        fail("E-IMPL-PARAM", f"{f.name} takes {', '.join(names)}; list the values each takes, as in "
             f"tune {names[0]} in [4, 8], and a plan selects one instance, as in {local(f.name)}[8].", f)  # fmt: skip
    for name, values in clause.tune:
        if name not in names or [n for n, _ in clause.tune].count(name) > 1:
            fail("E-IMPL-PARAM", f"tune lists the values of each natural parameter of {f.name} once; "
                 f"{name} is {'listed twice' if name in names else 'not one of ' + ', '.join(names)}.", f)  # fmt: skip
        if not values or len(set(values)) != len(values) or max(values) >= 2**32:
            fail("E-IMPL-PARAM", f"The values of {name} are a nonempty list of distinct naturals below 2^32.", f)
    if missing := [n for n in names if n not in given]:
        fail("E-IMPL-PARAM", f"tune lists no values for {', '.join(missing)} of {f.name}.", f)
    return {n: given[n] for n in names}


def admitted(template: Function, bound: dict[str, Any], node: Any) -> None:
    """An instance of an implementation made anywhere, such as a test block's `total_by[3](...)`: only one its
    `tune` clause lists."""
    if any(kind != "nat" for _, kind in template.generics):
        return  # refused as generic over types where the declarations are checked
    values = listed(template)
    for name, value in bound.items():
        if value not in values.get(name, ()):
            fail("E-IMPL-PARAM", f"{name} = {value} is not a value {local(template.name)} lists; {name} is one of "
                 f"{', '.join(map(str, values[name]))}.", node)  # fmt: skip


def declared(c: Checker, f: Function) -> None:
    clause = f.implements
    assert clause is not None
    generic = f.generics and not f.bindings
    what = "generic" if generic else "a kernel" if f.kernel else "a trait member" if f.owner else ""
    if what:
        fail("E-IMPLEMENTS", f"{f.name} is {what}; an implementation is an ordinary function with a body.", f)
    if clause.tune and not f.bindings:
        fail("E-IMPL-PARAM", f"{f.name} takes no natural parameter for tune to list values of; declare one, as in "
             f"fn {f.name}[K:nat](...).", f)  # fmt: skip
    with c.within(f.module):
        name = c.qualify(clause.reference, c.fs, node=f)
    if name is None:
        fail("E-IMPLEMENTS", f"{f.name} implements {clause.reference}, and no function of that name is visible.", f)
    ref = c.fs[name]
    if c.p.modules.get(name, ref.module) != c.p.modules.get(f.name, f.module):
        fail("E-IMPLEMENTS", f"{f.name} implements {name} of another module; declare it beside its reference.", f)
    if ref.implements is not None:
        fail("E-IMPLEMENTS", f"{name} is itself an implementation of {ref.implements.reference}; implement "
             f"{ref.implements.reference}.", f)  # fmt: skip
    what = ("generic" if ref.generics and not ref.bindings else "foreign" if ref.extern else "a kernel" if ref.kernel
            else "a test" if ref.test else "a trait member" if ref.owner else "")  # fmt: skip
    if what:
        fail("E-IMPLEMENTS", f"{name} is {what}; only an ordinary function has implementations.", f)
    theirs, mine = [(n, t) for n, t in ref.params], [(n, t) for n, t in f.params]
    if theirs != mine or ref.ret != f.ret:
        fail("E-IMPL-SIGNATURE", f"{f.name} is ({shown(f)}), and {name} is ({shown(ref)}): an implementation has "
             "exactly its reference's parameters, types, extents, placements and result.", f,
             expected=shown(ref), actual=shown(f))  # fmt: skip
    from ..projects.target import FEATURES  # the device-target table: what a target can provide

    for feature in clause.needs:
        if feature not in FEATURES:
            fail("E-IMPLEMENTS", f"needs names device features {f.name}'s code uses, from {', '.join(FEATURES)}; "
                 f"{feature} is none of them.", f)  # fmt: skip
    if clause.when is not None:
        condition(c, f, clause.when)
    c.alternatives.setdefault(name, []).append(f.name)


def shown(f: Function) -> str:
    ps = ", ".join(f"{n}:{t.display()}" for n, t in f.params)
    return ps + ("" if f.ret == VOID else f") -> {f.ret.display()}")


def condition(c: Checker, f: Function, e: Expr) -> None:
    """Type `e` as a bool over `f`'s parameters, in a scope of its own, and hold it to the total grammar."""
    saved, sites = c.s, c.capture_sites
    c.s, c.capture_sites = Scope(f, dict(f.bindings), module=f.module), False  # not a site an edit may replace
    try:
        for n, ty in f.params:
            c.env[n] = Binding(ty)
        for n, value in f.bindings.items():  # an instance's naturals, as its body sees them
            c.env[n] = Binding(USIZE, constant=value)
        c.expr(e, BOOL)
    finally:
        c.s, c.capture_sites = saved, sites
    params = dict(f.params)
    total(c, e, params)
    if not any(x.tag == "name" and x.val in params for x in walk(e)):  # a constant condition: decided here
        from .constants import fold

        if not fold(c, fixed(e), BOOL, []):
            fail("E-IMPL-WHEN", f"The condition of {f.name} is false for every input, so it never runs.", e)


def static(e: Expr) -> int | None:
    """The value of a name that is one of an instance's naturals, as its checked condition or body holds it."""
    return e.ref if e.tag == "name" and isinstance(e.ref, int) and not isinstance(e.ref, bool) else None


def fixed(e: Expr) -> Expr:
    """`e` with an instance's naturals written as the literals they are, as constant folding reads a condition."""
    out = clone(e)
    for x in walk(out):
        if (value := static(x)) is not None:
            x.tag, x.val, x.args = "int", str(value), []
    return out


def total(c: Checker, e: Expr, params: dict) -> None:
    """Refuse anything in a condition that could trap or read memory."""
    ok = e.tag in {"int", "float", "bool"}
    if e.tag == "name":
        ty = params.get(e.val)
        ok = (ty is not None and ty.mode == "value" and ty.name in SCALAR) or (
            ty is None and (isinstance(e.ref, Expr) or static(e) is not None)
        )
    elif e.tag == "unary":
        ok = e.val in {"!", "~"} or (e.val == "-" and e.ty is not None and e.ty.name in FLOAT)
    elif e.tag == "binary":
        left = e.args[0].ty
        divisor = literal(e.args[1], params)
        ok = (e.val in COMPARISONS or (left is not None and left.name in FLOAT)
              or (e.val in {"/", "%"} and divisor is not None and divisor > 0))  # fmt: skip
    elif e.tag == "call" and e.val == "len":
        ok = len(e.args) == 1 and e.args[0].tag == "name" and is_view(params.get(e.args[0].val, VOID))
    elif e.tag == "call" and e.val in {"shr", "shl_wrap"}:
        width = WIDTH.get(e.ty.name, 0) if e.ty else 0
        shift = literal(e.args[1], params) if len(e.args) == 2 else None
        ok = shift is not None and shift < width
    elif e.tag == "call":
        ok = e.val in TOTAL_CALLS and e.ty is not None and e.ty.name in INT
    if not ok:
        fail("E-IMPL-WHEN", WHEN, e)
    if not (e.tag == "call" and e.val == "len"):
        for a in e.args:
            total(c, a, params)


def literal(e: Expr, params: dict) -> int | None:
    """An integer literal, or an instance's natural, whose value the condition may divide or shift by."""
    if e.tag == "int":
        return int(e.val, 0)
    return static(e) if e.val not in params else None


def walk(e: Expr):
    yield e
    for a in e.args:
        yield from walk(a)


def select(c: Checker, module: str, written: str, chosen: str, token: Any) -> None:
    base, bracket, rest = chosen.partition("[")
    with c.within(module):
        name = c.qualify(written, c.fs, node=token)
        impl = c.qualify(base, c.fs, node=token)
    if name is None:
        fail("E-IMPL-USE", f"plan {written} use {chosen}; names no function {written}.", token)
    g = c.fs.get(impl) if impl else None
    if g is not None and g.implements is not None and g.generics and not g.bindings:  # parameterized
        values = [int(v) for v in rest.rstrip("]").split(",") if v.strip()]
        names = [n for n, _ in g.generics]
        if len(values) != len(names):
            example = instance(base, [listed(g)[n][0] for n in names])
            fail("E-IMPL-PARAM", f"{base} takes {', '.join(names)}; select one instance, as in plan {written} use "
                 f"{example};.", token)  # fmt: skip
        admitted(g, dict(zip(names, values, strict=True)), token)
        impl = instance(g.name, values)
    elif bracket and g is not None and g.implements is not None:
        fail("E-IMPL-PARAM", f"{base} takes no natural parameters; select it as plan {written} use {base};.", token)
    offered = c.alternatives.get(name, [])
    if impl not in offered:
        these = f"its implementations are {', '.join(offered)}" if offered else f"{name} has no implementation"
        fail("E-IMPL-USE", f"{chosen} is not an implementation of {name}; {these}.", token)
    if name in c.selected:
        fail("E-IMPL-USE", f"{name} already runs {c.selected[name]}; a plan selects one implementation.", token)
    c.selected[name] = impl


def within(effect: str, ceiling: set[str], pure: bool) -> bool:
    return effect in ceiling or (pure and effect.startswith("read:"))


def joined(c: Checker, effects: dict[str, set[str]]) -> dict[str, set[str]]:
    """Hold each implementation's row, reach and roundings to its reference, then join it into the reference's row
    and into every caller's, so the row is the same whichever implementation runs."""
    if not c.alternatives:
        return effects
    for name, impls in c.alternatives.items():
        ref = c.fs[name]
        ceiling = allowed(ref.effects) if ref.effects is not None else effects[name]
        pure = ref.effects is not None and "pure" in ref.effects
        mine = roundings(c, name)
        for impl in impls:
            c.judging = impl
            try:
                kept(c, name, impl, effects, ceiling, pure, mine)
            except Diagnostic as error:  # each message names the implementation; an instance is also data
                if c.fs[impl].bindings:
                    error.data.setdefault("instance", impl)
                raise
    for name, impls in c.alternatives.items():
        for impl in impls:
            c.calls[name].add(impl)
            c.call_edges[name].append((impl, {n: n for n, _ in c.fs[name].params}))
    return fixed_point(c)


def kept(c: Checker, name: str, impl: str, effects: dict[str, set[str]], ceiling: set[str], pure: bool,
         mine: set[str]) -> None:  # fmt: skip
    """The rules that need every row: `impl` stays inside `name`'s ceiling and roundings, and never reaches it."""
    ref = c.fs[name]
    if excess := sorted(e for e in effects[impl] if not within(e, ceiling, pure)):
        said = (
            f"which {name}'s ceiling does not allow"
            if ref.effects is not None
            else f"which {name} does not; {name} declares no ceiling, so its own row is the ceiling"
        )
        fail("E-IMPL-EFFECT", f"{impl} may {', '.join(excess)}, {said}.", c.fs[impl], added_effects=excess)
    if name in reached(c, impl):
        fail("E-IMPL-CALL", f"{impl} reaches {name}, its reference, which can dispatch back to it; call a helper "
             "both share.", c.fs[impl])  # fmt: skip
    clause = c.fs[impl].implements
    if clause is not None and clause.needs and requires(c, c.fs[impl], effects)["target"] != "device":
        fail("E-IMPLEMENTS", f"{impl} needs {', '.join(clause.needs)}, device features, and runs no device code.",
             c.fs[impl])  # fmt: skip
    if extra := sorted(roundings(c, impl) - mine):
        fail("E-IMPL-NUMERICS", f"{impl} rounds where {name} does not ({'; '.join(extra)}); an implementation keeps "
             "its reference's numerical contract.", c.fs[impl])  # fmt: skip


def reached(c: Checker, start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo += c.calls.get(n, ())
    return seen


def roundings(c: Checker, name: str) -> set[str]:
    """Every rounding the source writes where `name` and what it reaches run, as the receipt states each one, and
    every float reduction or scan whose lanes combine in an unspecified order."""
    out = set()
    for n in reached(c, name):
        out |= {json.dumps({k: v for k, v in r.items() if k != "line"}, sort_keys=True) for r in c.numerics.get(n, [])}
        for s in statements(c.fs[n].body if n in c.fs else []):
            if s.tag in {"reduce", "scan"} and s.ref == "device" and s.ty is not None and s.ty.name in FLOAT:
                out.add(json.dumps({"op": f"{s.tag} {s.op}", "order": "unspecified", "type": s.ty.name}))
    return out


def statements(ss: list[Stmt]):
    for s in ss:
        yield s
        yield from statements(nested(s))


def called(c: Checker, f: Function, e: Expr) -> None:
    """A call or a function value naming an implementation: only a test block may, to compare it with its
    reference; everything else reaches it through the reference and a plan."""
    if f.implements is not None and not c.f.test:
        ref, name = f.implements.reference, local(f.name)
        if f.generics and not f.bindings and f.implements.tune:  # a parameterized one: select an instance
            name = instance(name, [values[0] for _, values in f.implements.tune])
        fail("E-IMPL-CALL", f"{local(f.name)} is an implementation of {ref}: call {ref}, and select it with plan "
             f"{ref} use {name};.", e)  # fmt: skip


def receipt(c: Checker, name: str) -> dict[str, Any]:
    """What the receipt of `name` says about implementations: those of a reference and the one a plan selected, or
    the reference of an implementation."""
    f = c.fs.get(name)
    if f is not None and f.implements is not None:
        return {"implements": next(r for r, impls in c.alternatives.items() if name in impls), **parameters(f)}
    if name not in c.alternatives:
        return {}
    found = {}
    for impl in c.alternatives[name]:
        g = c.fs[impl]
        clause = g.implements
        assert clause is not None
        found[impl] = {
            "identity": clause.identity,
            "when": written(g) or "always",
            "applies": "tested at entry" if params_named(g) else "always",
            **parameters(g),
            **({"needs": list(clause.needs)} if clause.needs else {}),
            "requires": requires(c, g),
        }
    return {"implementations": found, **({"runs": c.selected[name]} if name in c.selected else {})}


def parameters(f: Function) -> dict[str, Any]:
    """What an instance of a parameterized implementation adds to its receipt: its template and its values."""
    if not f.bindings:
        return {}
    return {"instance_of": f.source_name, "parameters": dict(f.bindings)}


def written(f: Function) -> str:
    """The condition as written, an instance's naturals written as the values they have in it."""
    text = f.implements.text if f.implements else ""
    for name, value in f.bindings.items():
        text = re.sub(rf"\b{re.escape(name)}\b", str(value), text)
    return text


def params_named(f: Function) -> bool:
    e = f.implements.when if f.implements else None
    return e is not None and any(x.tag == "name" and x.val in dict(f.params) for x in walk(e))


def requires(c: Checker, f: Function, rows: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """What running `f` takes from the machine, as its checked body says: where it runs, whose threads, and the
    storage it declares."""
    row = (rows or c.rows).get(f.name, set())
    device = any(e == "par:device" or e.startswith(("transfer:", "gpu_")) for e in row)
    device = device or any(is_view(t) and t.place == "device" for _, t in f.params)
    local = c.resources.get(f.name, [])
    return {
        "target": "device" if device else "host",
        "host_lanes": "par:host" in row,
        "tasks": "spawn" in row,
        "heap_allocation_sites": sum(x["kind"] == "buffer" for x in local),
        "stack_bytes": sum(x.get("bytes", 0) for x in local),
    }


def lower(g: Emitter, f: Function) -> None:
    """At the head of a reference a plan points at an implementation: run it where its condition holds."""
    from .codegen import bare

    chosen = g.c.selected.get(f.name)
    if chosen is None:
        return
    impl = g.c.fs[chosen]
    when = impl.implements.when if impl.implements else None
    test = bare(g.expr(when)) if when is not None and params_named(impl) else "true"  # `if ((a == b))` warns
    args = ", ".join(f"std::move(v_{n})" if t.mode == "value" and not g.trivial(t) else f"v_{n}" for n, t in f.params)
    call = f"{g.callee(impl)}({args})"
    g.put(f"if ({test}) {{ {call}; return; }}" if f.ret == VOID else f"if ({test}) return {call};")
    for feature in impl.implements.needs if impl.implements else ():
        g.feature(feature)  # what the program now asks of its device target


def direct(g: Emitter, e: Expr, f: Function) -> Function:
    """The function a call of `f` reaches: the selected implementation itself when the arguments its condition reads
    are literals or constants that make it true, so the test is decided here; otherwise `f`, which tests it on entry."""
    from .constants import fold

    chosen = g.c.selected.get(f.name)
    impl = g.c.fs.get(chosen) if chosen else None
    when = impl.implements.when if impl is not None and impl.implements is not None else None
    if impl is None or when is None or len(e.args) != len(f.params):
        return f
    given = {n: a for (n, _), a in zip(f.params, e.args, strict=True)}
    named = {x.val for x in walk(when) if x.tag == "name" and x.val in given}
    known = {n: given[n] if given[n].tag == "int" else given[n].ref for n in named}
    if not named or not all(isinstance(v, Expr) and v.tag in {"int", "unary"} for v in known.values()):
        return f
    decided = fixed(when)
    for x in walk(decided):
        if x.tag == "name" and x.val in known:
            x.tag, x.val, x.args = known[x.val].tag, known[x.val].val, clone(known[x.val].args)
    try:
        holds = fold(g.c, decided, BOOL, []) is True
    except (Diagnostic, ValueError):  # a condition folding cannot decide, such as one that calls min, is tested
        return f
    return impl if holds else f


def targeted(functions: dict[str, Any], device: Any) -> None:
    """A build's check of every selected implementation against its device target (projects/target.py), before the
    program's own features are: E-IMPL-TARGET names the implementation and the feature the target lacks."""
    for name, receipt in functions.items():
        chosen = receipt.get("runs")
        needs = receipt.get("implementations", {}).get(chosen, {}).get("needs", []) if chosen else []
        if lacking := [n for n in needs if not device.provides(n)]:
            raise Diagnostic("E-IMPL-TARGET", f"plan {name} use {chosen}; selects an implementation that needs "
                             f"{', '.join(lacking)}, which {device.name} does not provide. Select another, or build for "
                             "a target that provides it; the reference is not run in its place.",
                             target=device.name, features=lacking)  # fmt: skip

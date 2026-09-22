"""Statement rules: declarations, assignment, control flow, `match`, loops, `defer` and collectors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import facts
from .places import settle
from .scope import Binding
from .tree import BOOL, USIZE, VOID, Expr, Stmt, Type, fail, is_view

if TYPE_CHECKING:
    from .checking import Checker


def s_buffer(c: Checker, s: Stmt):
    if s.name in c.env:
        fail("E-SHADOW", "Local owner name is already bound.", s)
    element = c.resolve(s.ty, s)
    if element == VOID:
        fail("E-OWNER-ELEMENT", "Local buffers hold values; void has none.", s)
    if c.kind(element) == "linear":
        fail("E-LINEAR-STORAGE", "Zeroed storage cannot hold linear values: a zero would be a forged one.", s)
    extent = s.exprs[0]
    c.expr(extent, USIZE)
    if isinstance(extent.ref, Expr):  # A named constant is its literal, here and in the extent identity.
        extent = s.exprs[0] = extent.ref
    if extent.tag not in {"name", "int"}:
        fail("E-OWNER-EXTENT", "Bind a computed capacity to an immutable usize first.", extent)
    if extent.tag == "name" and c.env[extent.val].mutable:
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
        resource["bytes"] = int(extent.val) * c.sizeof(element)
        prior = sum(x.get("bytes", 0) for x in c.resources[c.f.name])
        if prior + resource["bytes"] > 65536:
            fail("E-STACK-LIMIT", "Explicit stack declarations total at most 65536 bytes per function.", extent)
    # The extent is a literal or immutable scalar, so this identity holds for the whole borrow.
    place = s.ref = s.ty.place
    s.ty = element
    c.bind(s.name, Binding(Type(element.name, "rw", extent.val, element.args, place)), s)
    c.resources[c.f.name].append(resource)
    c.effect("zero_init")
    if s.tag == "stack" and place != "host":
        fail("E-PLACE", "Stack storage lives where its code runs; it takes no placement.", s)
    if s.tag == "buffer" and c.device_depth:
        fail("E-PLACEMENT", "A device lane cannot allocate; declare the buffer outside the region.", s)
    if s.tag == "buffer":
        c.guard("allocation", *(("alloc", "free") if place == "host" else ("gpu_alloc", "gpu_free")))
    else:
        c.effect("stack_storage")


def s_let(c: Checker, s: Stmt):
    if s.name in c.env:
        fail("E-SHADOW", f"{s.name} is already bound; shadowing is forbidden in this subset.", s)
    c.spawning = s.name if s.exprs[0].tag == "spawn" else ""
    ty = c.expr(s.exprs[0], c.resolve(s.ty, s) if s.ty else None)
    if ty == VOID or (ty.mode != "value" and s.exprs[0].tag != "str"):
        fail("E-VIEW-ALIAS", "Local view aliases and void values are outside this subset.", s)
    s.ty = ty
    c.bind(s.name, Binding(ty, s.tag == "reg"), s)
    if s.tag == "let":
        facts.defined(c, s.name, s.exprs[0], origin=("let", s))


def s_unpack(c: Checker, s: Stmt):
    """`let Conn(sock, sent) = c;` consumes a record and binds every field: the dual of constructing it, and the
    way out for an owner or a linear value kept inside one. Only its own module takes a linear record apart."""
    ty = c.expr(s.exprs[0])
    record, layout = c.qualify(s.name, c.p.records, node=s), c.layouts.get(ty)
    if ty.mode != "value" or ty.name != record or not isinstance(layout, list) or len(layout) != len(s.other_names):
        fail("E-UNPACK", f"let {s.name}(...) takes a {s.name} value apart: one name per field, in order.", s)
    home, linear = c.p.modules.get(ty.name, ""), "linear" in c.p.attributes.get(ty.name, ())
    if home != c.module and (linear or ty.name not in c.p.public):
        fail("E-PRIVATE", f"{ty.name} is {'linear' if linear else 'private'}: only module {home} takes it apart.", s)
    s.ty, s.ref = ty, layout
    for name, (_, held) in zip(s.other_names, layout, strict=True):
        c.bind(name.val, Binding(held, s.op == "reg"), name)


def s_compact(c: Checker, s: Stmt):
    out, hi, pred, value = s.exprs
    if s.name in c.env or s.binder in c.env or s.name == s.binder:
        fail("E-SHADOW", "Collector names must be fresh and distinct.", s)
    target = c.env.get(out.val)
    if c.lanes:  # Every lane would fill the same prefix.
        fail("E-PARALLEL-NEST", "A collector is a whole-array form; it cannot run inside a lane.", s)
    if target is None or not is_view(target.ty) or target.ty.mode != "rw":
        fail("E-WRITE-LEASE", "Compaction target must be a direct rw parameter.", out)
    c.expr(out, consume=False)
    c.expr(hi, USIZE)
    if c.extent_of(hi) != target.ty.extent:
        fail("E-COLLECT-CAPACITY", "Compaction requires iteration extent equal to output capacity.", hi)
    c.lend(out, "rw", [])

    def mentions(e: Expr) -> bool:
        return (e.tag == "name" and e.val == out.val) or any(mentions(x) for x in e.args)

    def body():  # The projection runs only for an index the predicate kept, on the host and on the device.
        c.expr(pred, BOOL)
        known = len(c.facts)
        facts.assume(c, pred, origin=("predicate", s))
        c.expr(value, target.ty.value)
        del c.facts[known:]

    if mentions(pred) or mentions(value):
        fail("E-COLLECT-SELF-READ", "Collector predicate/projection cannot read its output.", s)
    if target.ty.place == "device":  # The predicate and projection run as device lanes.
        c.region(s, [], body, "device")
    else:
        c.env[s.binder], s.ref, known = Binding(USIZE), "host", len(c.facts)
        facts.binder(c, s.binder, None, hi, origin=("binder", s))
        body()
        del c.env[s.binder], c.facts[known:]
    c.env[s.name] = Binding(USIZE)
    # The certificates' last: what was kept fits the capacity.
    facts.binder(c, s.name, None, hi, strict=False, origin=("kept", s))
    c.effect("write:" + out.val)
    c.counts["bounded_collectors"] = c.counts.get("bounded_collectors", 0) + 1


def s_assign(c: Checker, s: Stmt):
    ty = c.place(s.exprs[0], write=True)
    if c.releases(ty):  # Whatever the place held is released where the new value lands.
        c.effect("free")
    c.expr(s.exprs[1], ty)


def s_break(c: Checker, s: Stmt):
    if not c.loop_depth:
        fail("E-LOOP-CONTROL", s.tag + " requires an enclosing loop.", s)
    c.leaks((n for n, b in c.env.items() if b.depth >= c.loop_depth), s)
    return "jump"  # Ends this lexical block but not the function: its moves still matter afterwards.


def leaving(c: Checker, node: Any) -> tuple[Type, set[str]]:
    """What `return` and `try` leave: the closure being written, else the function; never a lane."""
    if c.lanes and c.closure is c.lanes.home:
        fail("E-PARALLEL-CONTROL", "A lane cannot return from the enclosing function.", node)
    return (c.closure[0].ret, c.closure[1]) if c.closure else (c.f.ret, set())


def s_return(c: Checker, s: Stmt):
    ret, outer = c.leaving(s)
    if ret == VOID:
        if s.exprs:
            fail("E-RETURN", "Void function cannot return a value.", s)
    elif not s.exprs:
        fail("E-RETURN", "Missing return value.", s)
    else:
        c.expr(s.exprs[0], ret)
    c.leaks(set(c.env) - outer, s)
    c.released(set(c.env) - outer)  # Leaving here drops everything this scope still holds.
    return True


def branches(c: Checker, node: Any, runs: list) -> Any:
    """Alternatives start from one ownership state; a linear value must agree across them. What a path
    that returns did (a move, a wait that ended a lease) says nothing about the paths that go on."""
    before, held, outcomes = set(c.moved), (c.leases, c.before), []
    for run in runs:
        c.moved, c.leases, c.before = set(before), dict(held[0]), dict(held[1])
        outcomes.append((run(), c.moved, c.leases, c.before))
    falls = [moved for ended, moved, *_ in outcomes if not ended]
    everywhere: set[str] = set.intersection(*falls) if falls else set()
    for n in set().union(*falls) - everywhere:
        if n in c.env and c.kind(c.env[n].ty) == "linear":
            fail("E-LINEAR-BRANCH", f"{n} is consumed on some paths only.", node)
    # A branch that returned cannot reach what follows; one that jumped (break/continue) can.
    onward = [o for o in outcomes if o[0] is not True]
    c.moved = set().union(before, *(moved for _, moved, *_ in onward))
    c.leases = settle([o[2] for o in onward]) if onward else held[0]
    c.before = {t: earlier for o in onward for t, earlier in o[3].items()} if onward else held[1]
    ends = [ended for ended, *_ in outcomes]
    return all(ends) and (True if all(e is True for e in ends) else "jump")


def s_if(c: Checker, s: Stmt):
    cond, ends = s.exprs[0], []
    c.expr(cond, BOOL)

    def arm(body: list[Stmt], truth: bool) -> Any:
        known = len(c.facts)
        facts.assume(c, cond, truth, origin=("arm", s, truth))
        ends.append(c.block(body))
        del c.facts[known:]
        return ends[-1]

    both = c.branches(s, [lambda: arm(s.body, True), lambda: arm(s.other, False)])
    if bool(ends[0]) != bool(ends[1]):  # One arm leaves, so the rest of the block runs after the other.
        facts.assume(c, cond, bool(ends[1]), origin=("exit", s, bool(ends[1])))
    return both if s.other else False


def s_match(c: Checker, s: Stmt):
    ty = c.expr(s.exprs[0])
    layout = c.layouts.get(ty)
    if ty.mode != "value" or not isinstance(layout, dict):
        fail("E-MATCH-TYPE", "match requires a declared enum or tagged sum.", s)
    given = [a.variant.rsplit(".", 1)[1] if "." in a.variant and c.qualify(a.variant.rsplit(".", 1)[0], c.types)
             == ty.name else a.variant for a in s.arms]  # fmt: skip  # A bare arm names a variant of the subject.
    home = c.p.modules.get(ty.name, "")
    if home not in ("", c.module) and ty.name not in c.p.public and any("." not in a.variant for a in s.arms):
        fail("E-PRIVATE", f"{ty.name} is private to module {home}; so are its variants.", s)
    for arm in s.arms:  # An arm that names nothing visible is that fault, not a gap in the coverage.
        head = arm.variant.rsplit(".", 1)[0]
        if "." in arm.variant and c.qualify(head, c.types) is None:
            fail("E-UNBOUND", f"{head} is not a type this module can name; import it, or write "
                 f"{ty.name}.{arm.variant.rsplit('.', 1)[1]}.", arm)  # fmt: skip
    if len(set(given)) != len(given):
        fail("E-MATCH-DUPLICATE", "A variant may appear only once.", s)
    if set(given) != set(layout):
        missing = sorted(f"{ty.name}.{v}" for v in set(layout) - set(given))
        fail("E-MATCH-COVERAGE", "Every variant must have exactly one arm" +
             (f"; missing {', '.join(missing)}." if missing else "."), s, missing_variants=missing,
             unknown_variants=sorted(v if "." in v else f"{ty.name}.{v}" for v in set(given) - set(layout)))  # fmt: skip

    def arm_body(arm, payload):
        if bool(arm.binder) != (payload is not None):
            fail("E-MATCH-BINDING", "A payload arm binds exactly one value; a nullary arm binds none.", arm)
        if arm.binder:
            c.bind(arm.binder, Binding(payload), arm, "Payload binder must be fresh.")
        returned = c.block(arm.body)
        if arm.binder:
            if not returned:
                c.leaks([arm.binder], arm)
            c.released([arm.binder])
            del c.env[arm.binder]
        return returned

    s.ref = given
    result = c.branches(s, [lambda a=a, v=v: arm_body(a, layout[v]) for a, v in zip(s.arms, given, strict=True)])
    c.guard("tag")
    return result


def loop(c: Checker, s: Stmt):
    outer, before, held, around = set(c.env), set(c.moved), dict(c.leases), c.touched
    c.loop_depth, c.touched = c.loop_depth + 1, []
    c.block(s.body)
    c.loop_depth, touched, c.touched = c.loop_depth - 1, c.touched, around
    repeated = (c.moved - before) & outer
    if repeated:
        fail("E-MOVE-IN-LOOP", f"{sorted(repeated)[0]} would be moved once per iteration.", s)
    c.carried(held, touched)
    if around is not None:  # An inner loop runs inside every iteration of the one around it.
        around.extend(touched)


def s_while(c: Checker, s: Stmt):
    c.expr(s.exprs[0], BOOL)
    c.effect("diverge")
    c.loop(s)


def s_for(c: Checker, s: Stmt):
    c.expr(s.exprs[0], USIZE)
    c.expr(s.exprs[1], USIZE)
    c.bind(s.name, Binding(USIZE), s, f"Loop binder {s.name} already exists.")
    known = len(c.facts)
    facts.binder(c, s.name, s.exprs[0], s.exprs[1], origin=("binder", s))
    c.loop(s)
    del c.env[s.name], c.facts[known:]


def s_expr(c: Checker, s: Stmt):
    ty = c.expr(s.exprs[0])
    if s.exprs[0].tag not in {"call", "try"}:
        fail("E-DISCARD", "Only calls may be used as discarded expression statements.", s)
    if ty != VOID:
        fail("E-DISCARD", "Nonvoid result must be bound or returned.", s)


def s_block(c: Checker, s: Stmt):
    return c.block(s.body)


def s_unsafe(c: Checker, s: Stmt):
    c.unsafe_depth += 1
    c.counts["unsafe_blocks"] = c.counts.get("unsafe_blocks", 0) + 1
    returned = c.block(s.body)
    c.unsafe_depth -= 1
    return returned


def s_defer(c: Checker, s: Stmt):
    """A visible cleanup: checked here, run at every normal exit of the enclosing block."""
    inner = s.body[0]
    c.host_only(s, "defer schedules a host call")
    if inner.tag != "expr" or inner.exprs[0].tag != "call":
        fail("E-DEFER", "defer schedules exactly one call.", s)
    before, leases = set(c.moved), dict(c.leases)
    c.stmt(inner)
    c.deferred |= c.moved - before
    c.moved, c.leases = before, leases  # The call runs at block exit; until then nothing is returned.

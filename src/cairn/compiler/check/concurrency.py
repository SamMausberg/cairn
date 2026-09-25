"""Tasks and tickets, lanes and regions, atomics and mutexes, and where code may run (placement)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..plans import chunks, fusion, staging
from ..primitives import atomics, wide
from ..primitives.builtins import WRAPPING, crossing
from ..syntax.tree import (
    BOOL,
    FLOAT,
    INT,
    UNSIGNED,
    USIZE,
    VOID,
    Expr,
    Function,
    Stmt,
    Type,
    fail,
    is_view,
    nested,
    root,
)
from . import facts
from .effects import DEVICE_SAFE, LANE_SAFE
from .scope import Binding, Lanes

if TYPE_CHECKING:
    from .checking import Checker


# Declared and borrowed in place; never stored, passed or returned.
PINNED = {"Ticket", "Group", "IoRing", "Atomic", "Mutex"}
ORDERS = ["relaxed", "acquire", "release", "acquire_release", "seq_cst"]
UPDATES = ("store", "swap", "fetch_add", "fetch_sub", "fetch_and", "fetch_or", "fetch_xor")
ATOMIC_OPS = {"load": 0, **dict.fromkeys(UPDATES, 1)}  # How many values each operation takes before its order.


def host_only(c: Checker, node: Any, what: str):
    """Refuse a host construct in device code, and remember it for functions a device lane turns out to reach."""
    if c.device_depth:
        fail("E-PLACEMENT", f"{what}; a device lane cannot use it.", node)
    c.hostish.setdefault(c.f.name, what[0].lower() + what[1:])


def region(c: Checker, s: Stmt, exprs: list[Expr], run, target: str = "") -> Any:
    """Check a lane body; the placement of the views it indexes decides where it runs."""
    if c.lanes or c.f.kernel:
        fail("E-PARALLEL-NEST", "A lane cannot start another parallel region.", s)

    def places(e: Expr) -> set[str]:
        base = e if e.tag == "index" else e.args[0] if e.tag == "call" and e.val in wide.NAMES and e.args else None
        mine = {c.env[root(base).val].ty.place} if base is not None and root(base).val in c.env else set()
        return mine.union(*(places(a) for a in e.args))

    def scan(ss: list[Stmt]) -> set[str]:
        found = set().union(*(places(e) for x in ss for e in x.exprs))
        return found.union(*(scan(nested(x)) for x in ss))

    found = scan(s.body) | set().union(*(places(e) for e in exprs))
    target = target or ("device" if "device" in found else "host")
    binder, known = s.binder or s.name, len(c.facts)
    c.bind(binder, Binding(USIZE), s)
    count = s.exprs[1 if s.tag in {"compact", "scan"} else 0]
    facts.binder(c, binder, None, count, origin=("binder", s))  # Each lane's index is below it.
    saved = c.lanes, c.device_depth, c.loop_depth, set(c.moved), c.effects
    c.lanes, c.device_depth = Lanes(binder, set(c.env) - {binder}, c.closure), int(target == "device")
    c.loop_depth, c.effects = 0, set()
    result = run()
    if (c.moved - saved[3]) & c.lanes.outer:
        fail("E-MOVE-IN-LOOP", "An outer owner would be moved once per lane.", s)
    allowed = DEVICE_SAFE if target == "device" else LANE_SAFE | {"indirect_call", "dispatch"}  # Judged at calls.
    excess = sorted(x for x in c.effects if x not in allowed and not x.startswith(("read:", "write:", "lane:")))
    if excess:  # A lane's own row obeys the rule its callees obey.
        fail("E-PARALLEL-CALL", f"A {target} lane cannot {', '.join(excess)}.", s)
    saved[4].update(c.effects)
    updated = dict(c.lanes.atomics)  # arrays the lanes update atomically, which they touch no other way
    for name, _, _, node in c.lanes.accesses:
        if name in updated:
            atomics.mixed(name, updated[name], node, "read or written plainly in the same region")
    written = {name for name, _, write, _ in c.lanes.accesses if write} | set(updated)
    touched = sorted({name for name, *_ in c.lanes.accesses} | set(updated))  # What a queued region holds.
    c.borrowed = [(name + "[]", "rw" if name in written else "ro") for name in touched]
    strides: dict[str, int] = {}
    for name, stride, _, node in c.lanes.accesses:  # Lanes' blocks of one stride are disjoint; of two, they meet.
        if name in written and (stride is None or strides.setdefault(name, stride) != stride):
            fail("E-PARALLEL-RACE", f"{name} is written by lanes, so every lane may touch only {name}[{binder}], "
                 f"or only its own block {name}[{binder} * S + j] with j below one constant S.", node)  # fmt: skip
    s.block = max((stride or 1 for _, stride, _, _ in c.lanes.accesses), default=1)  # A lane costs its block.
    s.touched = tuple((name, stride, write) for name, stride, write, _ in c.lanes.accesses)  # what fusion reads
    s.touched += tuple((name, None, True) for name in updated)  # an atomic update is no lane's own element
    c.lanes, c.device_depth, c.loop_depth, _, c.effects = saved
    del c.env[binder], c.facts[known:]
    if s.tag != "parallel" and target == "device":  # The runtime's scan and reduction need device scratch.
        c.effects |= {"gpu_alloc", "gpu_free"}
    if s.tag == "parallel" or target == "device" or s.pooled:  # A host reduction folds in order unless pooled.
        c.effect("par:" + target)
        c.counts["parallel_regions"] = c.counts.get("parallel_regions", 0) + 1
    s.ref = target
    return result


# Every item a plan may set: the regions it schedules, its least and greatest value, and what it must be a multiple of.
# grain and lanes split a host region over the pool; block, per_lane and unroll shape a device region's launch;
# fuse runs adjacent regions of either kind as one (compiler/plans/fusion.py).
PLAN_ITEMS = {
    "grain": ("host", 1, 2**63 - 1, 1),  # indices one claim covers at least
    "lanes": ("host", 1, 1024, 1),  # lanes a region engages at most
    "block": ("device", 32, 1024, 32),  # threads in one block: whole warps
    "per_lane": ("device", 1, 65536, 1),  # indices each thread runs before the grid wraps
    "unroll": ("device", 1, 32, 1),  # passes of a thread's index loop the compiler unrolls
    "vector": ("device", 2, 16, 1),  # adjacent indices a lane runs over one W-wide chunk of each array (chunks.py)
    "stage": ("device", 1, 32, 1),  # how far either side of its indices a block's shared tile reaches (staging.py)
    "fuse": ("either", 2, 16, 1),  # adjacent regions, over one extent, that run as one traversal at most
}
POWERS = {"vector"}  # items that are also a power of two: a chunk is one access, and accesses are powers of two


def planned_functions(c: Checker, module: str, name: str) -> list[Function]:
    """The function `plan name` written in `module` names, with every instance of it."""
    with c.within(module):
        target = c.qualify(name, c.fs)
    return [f for f in c.p.functions if target in (f.name, f.source_name)]


def plans(c: Checker) -> dict[str, dict[str, int]]:
    """`plan f { grain G; lanes L; }` chooses how f's host regions are claimed: at least G indices at a time, on at
    most L lanes; `block B; per_lane K; unroll U; vector W;` how f's device regions launch: B threads a block, a
    grid that gives each thread K indices, its loop unrolled U times, W adjacent indices a lane over one chunk of
    each array it touches only at [i] (compiler/plans/chunks.py). Lanes are race free, every index runs exactly once and a
    region finishes before the next statement, so a plan only picks one of the schedules the region already allows;
    it changes no result and no effect row. What each function got."""
    chosen: dict[str, dict[str, int]] = {}
    for module, name, items, token in c.p.plans:
        for item, value in items.items():
            if item not in PLAN_ITEMS:
                fail("E-PLAN", f"A plan sets {', '.join(PLAN_ITEMS)}; {item} is none of them.", token)
            _, least, most, step = PLAN_ITEMS[item]
            if not least <= value <= most or value % step:
                multiple = f", a multiple of {step}" if step > 1 else ""
                fail("E-PLAN", f"{item} runs from {least} to {most}{multiple}; {value} is outside.", token)
            if item in POWERS and value & (value - 1):
                fail("E-PLAN", f"{item} is a power of two from {least} to {most}; {value} is not one.", token)
        planned = planned_functions(c, module, name)
        regions = [s for f in planned for s in walk(f.body) if s.tag == "parallel"]
        if not regions or any(f.name in chosen for f in planned):
            fail("E-PLAN", f"plan {name} must name a function with a parallel region, once.", token)
        for where in ("host", "device"):
            given = {k: v for k, v in items.items() if PLAN_ITEMS[k][0] == where}
            regions = [s for f in planned for s in walk(f.body) if s.tag == "parallel" and s.ref == where]
            if given and not regions:
                fail("E-PLAN", f"{', '.join(given)} schedule{'s' * (len(given) == 1)} a {where} parallel region, and "
                     f"{name} has none.", token)  # fmt: skip
            for s in regions:
                if where == "host":
                    s.plan = (given.get("grain", 0), given.get("lanes", 0))
                else:
                    s.launch = (given.get("block", 0), given.get("per_lane", 0), given.get("unroll", 0))
        for s in (s for f in planned for s in walk(f.body) if s.tag == "parallel"):
            s.fuse = items.get("fuse", 0)
            if "vector" in items and s.ref == "device":
                chunks.vectored(c, s, items["vector"], token)
            if "stage" in items and s.ref == "device":
                staging.staged(c, s, items["stage"], token)
        chosen |= {f.name: dict(items) for f in planned}
    return chosen


def fusions(c: Checker, rows: dict[str, set[str]]):
    """`fuse K` needs regions it can join once every row is known: adjacent, over one extent, lanes touching what
    they share only at their own index, and bodies that cannot trap, loop or be seen (compiler/plans/fusion.py)."""
    for module, name, items, token in c.p.plans:
        if "fuse" not in items:
            continue
        planned = planned_functions(c, module, name)
        if not any(fusion.chains(ss, rows) for f in planned for ss in fusion.lists(f.body)):
            fail("E-PLAN", f"fuse joins adjacent parallel regions over one extent whose bodies cannot trap, loop or be "
                 f"seen, and whose shared arrays each lane touches only at its own index; {name} has no two such "
                 "regions side by side.", token)  # fmt: skip


def walk(ss: list[Stmt]):
    for s in ss:
        yield s
        yield from walk(nested(s))


def s_parallel(c: Checker, s: Stmt, queued: bool = False):
    if s.other_names and not queued:
        fail("E-SPAWN", "after orders queued work: write `let t = spawn parallel ... after ... { }`.", s)
    c.expr(s.exprs[0], USIZE)
    c.region(s, [], lambda: c.block(s.body))


def combining(c: Checker, s: Stmt, ty: Type, code: str):
    """The operators a reduction and a scan share: every one associative on what it takes, and a checked + only
    over unsigned integers, where no partial result overflows unless the whole does."""
    wanted = UNSIGNED if s.op in WRAPPING or s.op in {"&", "|", "^"} else INT if s.op in {"min", "max"} else FLOAT
    if s.op == "+" and ty.name in UNSIGNED:  # No partial sum of naturals overflows unless the total does.
        wanted = UNSIGNED
        c.guard("overflow")
    if ty.mode != "value" or ty.name not in wanted:
        takes = {id(UNSIGNED): "unsigned integers", id(INT): "integers"}.get(id(wanted), "floats")
        fail(code, f"{s.tag} {s.op} takes {takes}{' and unsigned integers' * (s.op == '+')}, not "
             f"{ty.display()}: lanes combine in an unspecified order, and only unsigned + has an order-independent "
             "trap (signed + and integer * do not).", s)  # fmt: skip


def s_reduce(c: Checker, s: Stmt):
    hi, value = s.exprs
    c.expr(hi, USIZE)
    declared = c.resolve(s.ty, s) if s.ty else None
    ty = c.region(s, [value], lambda: c.expr(value, declared))
    combining(c, s, ty, "E-REDUCE-OP")
    if s.pooled and s.ref == "host" and ty.name in FLOAT:
        fail("E-REDUCE-ORDER", f"reduce {s.op} parallel adds {ty.name} in blocks on the lane pool, and floating "
             "addition in another order gives another answer: fold with for, or write the blocks yourself.", s)  # fmt: skip
    s.ty = ty
    c.bind(s.name, Binding(ty), s)


def s_scan(c: Checker, s: Stmt):
    """`scan op [exclusive] out for i in n yield v`: out[i] is op over the yields up to i (before i when exclusive)
    and the total is op over all of them; each yield runs once, and may read out only at its own element."""
    out, hi, value, store = s.exprs
    if s.binder in c.env or s.name in c.env or s.name == s.binder:
        fail("E-SHADOW", "A scan's total and its index need names of their own.", s)
    target = c.env.get(out.val)
    if target is None or not is_view(target.ty) or target.ty.mode != "rw":
        fail("E-SCAN-TARGET", f"scan writes {out.val}[{s.binder}] for every {s.binder}: name an rw view or a "
             "buffer the function holds.", out)  # fmt: skip
    c.expr(out, consume=False)
    c.expr(hi, USIZE)
    if c.extent_of(hi) != target.ty.extent:
        fail("E-SCAN-EXTENT", f"scan writes {out.val}[{s.binder}] for every {s.binder} it counts, so {out.val} "
             f"must hold exactly that many elements; its extent is {target.ty.extent}.", hi)  # fmt: skip
    declared = c.resolve(s.ty, s) if s.ty else target.ty.value
    if declared != target.ty.value:
        c.expect(declared, target.ty.value, s)

    def lanes() -> Type:  # The yield first, as at run time, then the store it feeds.
        ty = c.expr(value, target.ty.value)
        c.place(store, write=True)
        return ty

    ty = c.region(s, [value, store], lanes, "device" if target.ty.place == "device" else "")
    combining(c, s, ty, "E-SCAN-OP")
    if ty.name in FLOAT and (s.pooled or s.ref == "device"):
        fail("E-SCAN-ORDER", f"scan {s.op} over {ty.name} combines in the written order only: a {'device' if s.ref == 'device' else 'pooled'} "
             "scan associates in blocks, and floating arithmetic in another order gives another answer.", s)  # fmt: skip
    s.ty = ty
    if s.name:
        c.bind(s.name, Binding(ty), s)


def judge_lane_callbacks(c: Checker, effects: dict[str, set[str]]):
    """What a callee's lanes will call (`lane:f` in its row) is judged where it was written: a closure
    may not write what it captured, and nothing it does may exceed what a lane may do."""
    for a, parameters, callee, formal, caller in c.fn_sites:
        c.judging = caller
        if "lane:" + formal in effects[callee] and not (a.tag == "name" and a.val in parameters):
            closure = a.ref if a.tag == "lambda" else None
            if closure:
                rows = [closure.row[0], *(effects[g] for g in closure.row[1])]
            elif a.tag == "function":
                rows = [effects[a.ref.name]]
            else:  # A stored fn value is some function whose address was taken.
                shape = a.ty.args
                rows = [effects[g] for g in c.address_taken if (*(t for _, t in c.fs[g].params), c.fs[g].ret) == shape]
            allowed = LANE_SAFE | {"dispatch"}  # A dispatch's targets are in the row beside it.
            wrong = {x for row in rows for x in row if x not in allowed and not x.startswith(("read:", "write:"))}
            wrong |= {"write:" + place for place, mode in (closure.captures if closure else []) if mode == "rw"}
            if wrong:
                fail("E-PARALLEL-CALL", f"{callee} calls {formal} from parallel lanes, where it cannot "
                     f"{', '.join(sorted(wrong))}.", a)  # fmt: skip


def lane_callee(c: Checker, e: Expr):
    """A function value used inside a lane must be a parameter: `lane:f` tells whoever passes it to answer for it."""
    if e.val not in dict(c.f.params):
        fail("E-PARALLEL-CALL", "A lane calls declared functions and fn parameters, which their writer answers for.", e)
    c.effect("lane:" + e.val)


def shared(c: Checker, e: Expr, n: str, ty: Type, args: list[Expr]) -> Type:
    """Interior mutability, and only here: every atomic access names its memory order."""
    c.host_only(e, "Atomics and mutexes are host objects")
    e.ref = ("shared", ty)
    if ty.name == "Mutex":
        if n != "with" or len(args) != 1 or args[0].tag != "lambda":
            fail("E-CALLEE", "A mutex has one operation: m.with(|state:rw<T>| { ... }).", e)
        ret = c.resolve(args[0].ref.ret, e)
        c.expr(args[0], Type("fn", "ro", args=(Type(ty.args[0].name, "rw", args=ty.args[0].args), ret)))
        c.disjoint([(c.where(e.args[0]), "rw")], e, args[0].ref.captures)  # Locking it again would trap.
        c.effect("lock")
        return ret
    order = Type(c.qualify("Order", c.p.enums) or "Order")
    if n == "compare_exchange":
        wanted = [ty.args[0], ty.args[0], order, order]
    elif n in ATOMIC_OPS:
        wanted = [ty.args[0]] * ATOMIC_OPS[n] + [order]
    else:
        fail("E-CALLEE", f"An atomic offers {', '.join(ATOMIC_OPS)} and compare_exchange.", e)
    if len(args) != len(wanted):
        fail("E-ARITY", f"{n} takes {len(wanted) - 1} value(s) and an explicit memory order.", e)
    if n == "compare_exchange" and not c.writable(args[0]):
        fail("E-WRITE-LEASE", "compare_exchange updates its expected value; pass a mutable local.", args[0])
    for a, want in zip(args, wanted, strict=True):
        c.expect(c.place(a, write=True), want, a) if n == "compare_exchange" and a is args[0] else c.expr(a, want)
    c.effect("atomic")
    return BOOL if n == "compare_exchange" else VOID if n == "store" else ty.args[0]


def s_submit(c: Checker, s: Stmt):
    """`spawn f(args) into g;` runs f on its own thread and hands the task to the group g instead of naming
    a ticket; g holds what the task borrows until wait(g), and collect(g) yields results as they finish."""
    binding = c.env.get(s.name)
    if binding is None or binding.ty.name != "Group":
        fail("E-TYPE-MISMATCH", f"into names a group declared in this function; {s.name} is not one.", s)
    if s.name in c.moved:
        fail("E-MOVED", f"{s.name} was moved.", s)
    c.spawning = s.name
    c.expr(s.exprs[0])


def e_spawn(c: Checker, e: Expr, expected: Type | None) -> Type:
    """`let t = spawn f(args);` runs f on its own thread; `spawn parallel ...` and `spawn transfer(...)` queue
    device work on a stream of their own. Either way t lends what the work borrows until wait(t)."""
    region, name = e.ref if isinstance(e.ref, Stmt) else None, c.spawning
    call, after = (None, e.args) if region else (e.args[0], e.args[1:])
    if name in ("", "<wait>") or (call is not None and call.tag != "call") or c.lanes or c.closure:
        fail("E-SPAWN", "Write `let t = spawn f(args);` in a function body; a task is always named.", e)
    c.spawning = ""
    queued = region is not None or (call.val == "transfer" and c.qualify("transfer", c.fs) is None)
    if e.val == "into" and queued:
        fail("E-SPAWN", "A group holds tasks that run declared functions; queued device work keeps its ticket.", e)
    earlier: set[str] = set()
    for ticket in after:  # Work queued after a ticket's work may touch what that ticket holds.
        if c.before.get(ticket.val) is None or ticket.val in c.moved:
            fail("E-SPAWN", f"after names live tickets of queued device work; {ticket.val} is not one.", ticket)
        earlier |= {ticket.val} | c.before[ticket.val]
    held, c.leases = c.leases, {t: places for t, places in c.leases.items() if t not in earlier}
    if region is not None:
        c.s_parallel(region, queued=True)
    result = VOID if region is not None else c.expr(call)
    c.leases = held
    if queued:
        if (region.ref != "device") if region is not None else crossing(call.args[1].ty, call.args[0].ty) == "h2h":
            fail("E-SPAWN", "Only device work is queued; spawn a function to run host work as a task.", e)
        c.before[name] = earlier
    elif (
        after
        or not isinstance(call.ref, Function)
        or any(t.name == "fn" and t.mode != "value" for _, t in call.ref.params)
    ):
        fail("E-SPAWN", "spawn runs a declared function (a closure cannot follow it to another thread); "
             "only queued device work is ordered with after.", e)  # fmt: skip
    if e.val == "into":  # The group holds every lease until wait(g); which task finished is never known here.
        group = c.env[name]
        c.expect(result, group.ty.args[0], e)
        # An owner passed by value is the task's now: the spawner cannot reach it, and a later binding of its name
        # is another object, so it is not lent, and a loop may move a fresh one in each time round.
        lent = [(p, mode) for p, mode in c.borrowed if re.split(r"[.\[]", p, maxsplit=1)[0] not in c.moved]
        again = [p for p, mode in lent if mode == "rw"] if c.loop_depth > group.depth else []
        if again:
            fail("E-LEASED", f"{again[0].removesuffix('[]')} is lent to {name} until wait({name}), and the next "
                 "iteration would lend it again.", e)  # fmt: skip
        c.leases[name] = [*c.leases.get(name, ()), *lent]  # A new list: a sibling path shares the old one.
        c.effect("spawn")
        c.guard("submit")  # A full group traps rather than growing.
        return VOID
    c.leases[name] = c.borrowed
    c.effect("spawn")
    e.val = "queue" if queued else ""
    return Type("Ticket", args=(result,), place="device" if queued else "host")

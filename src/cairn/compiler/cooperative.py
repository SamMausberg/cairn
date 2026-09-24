"""Cooperative regions: `blocks b in G threads t in T { ... }`, blocks of threads that share arrays and meet at
barriers, beside `parallel`, whose lanes share nothing.

    blocks b in g threads t in 256 {           // g blocks, each of 256 threads; b and t are usize
      shared partial:u64[256] = zeroed;        // one array per block, every thread's
      partial[t] = x[b * 256 + t];
      barrier;                                 // every thread of the block is here before any goes on
      ...
    }

Up to three names a side, fastest first (`blocks bx, by in gx, gy threads tx, ty in 32, 8`). The grid's extents are
any usize values, their product the number of blocks; the thread extents are constants whose product is a whole
number of warps, 32 to 1024, and thread `(tx, ty)` is thread `tx + 32 * ty` of its block. A `shared` array is declared
directly in the body with a constant length, zeroed at the start of every block; all of a block's arrays together hold
at most 48 KiB. The region runs on the device when a view it indexes is `@device`, and on host threads otherwise.

Who reaches a statement together is decided before the body is checked (`reach`): the whole block where every
condition it sits under has one value in the block (it depends on block names, values from outside and constants
only), each warp whole where a condition also depends on warp-wide names (`ty` when `tx` counts 32, or `t / 32`),
and single threads otherwise. A barrier needs the whole block (E-COOP-BARRIER): a thread that skipped it would leave
the others waiting. A warp operation, `shuffle`, `shuffle_xor`, `shuffle_down` and `reduce OP warp yield v`, needs
each warp whole (E-COOP-WARP). `participation(c, node)` gives the answer to any rule, and `collective` refuses.

What threads may touch is two rules. Between two barriers no two threads of a block touch one element of a shared
array where either writes (compiler/phases.py: E-COOP-CONFLICT, E-COOP-UNORDERED, E-COOP-REUSE, E-COOP-UNDECIDED),
and every element of an array from outside is written by at most one thread of one block, and read by another only
if nobody writes it (compiler/footprints.py: E-COOP-GLOBAL). Everything else a lane may not do, a thread may not do.

On the device the region is one kernel launch: a block per grid block (strided when the grid passes 65535), its
arrays in static shared memory, `barrier` as `__syncthreads()`, the warp operations as `__shfl_*_sync` over the
whole warp, on the calling thread's execution context, returning once its stream has run the region, as `parallel`
does. On the host every block's threads are real threads meeting
at a `std::barrier`, two blocks at a time (runtime/cairn_coop.hpp), so the thread sanitizer sees the phase rule hold
on real runs. Its cost row: `par:device` or `par:host`, `zero_init` for its arrays, and `trap` for its guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import atomics, block_run, execution, footprints, fragments, phases, pipelines, wide
from .constants import constant
from .effects import DEVICE_SAFE, LANE_SAFE
from .footprints import natural
from .pipelines import lower_pipeline, s_pipeline  # noqa: F401  (the checker and the emitter bind them from here)
from .scope import Binding, Block, Lanes
from .tree import (
    FLOAT,
    INT,
    NUMERIC,
    SCALAR,
    STORAGE,
    UNSIGNED,
    USIZE,
    Diagnostic,
    Expr,
    Stmt,
    Type,
    fail,
    is_view,
    nested,
    root,
)

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter

BLOCK, WARP, THREAD = 0, 1, 2
WIDTHS = ("block", "warp", "thread")
WARP_SIZE = 32
SHARED_LIMIT = 48 * 1024  # static shared memory a block may hold on every NVIDIA device since compute 2.0
ALIGN = 128  # where each shared array starts: tensor-core fragments load from 32 bytes on (compiler/fragments.py)
SHUFFLES = {"shuffle", "shuffle_xor", "shuffle_down"}
SHARED_STATE = {"Atomic", "Mutex"}  # what threads handed the same one may still see differently
# The primitives that write an argument; every other one takes its arguments by value or ro (builtins.TABLE).
WRITING = {"take", "swap", "transfer", "mma_store", "store_wide", *atomics.NAMES}


# Who reaches a statement together ---------------------------------------------------------------------------------


class Reach:
    """For every statement and call of a cooperative body, the widest group that reaches it together, and the
    condition that narrows it: the whole block, each warp whole, or single threads."""

    def __init__(self, c: Checker, block: Block, stages: set[str]):
        self.c, self.block, self.stages = c, block, stages  # stages: the pipelines the body declares
        # The body's `let mut` locals, the only names a thread's write can make differ: nothing outside the region is
        # assigned in it, and the phase rule keeps each element of a shared array to one writer between barriers.
        self.mutable: set[str] = set()
        whole = block.extents[0] % WARP_SIZE == 0
        self.values: dict[str, tuple[int, Any]] = dict.fromkeys(block.grid, (BLOCK, None))
        for i, name in enumerate(block.threads):
            self.values[name] = (WARP if i and whole else THREAD, None)
        self.private: set[str] = set()
        self.escapes: list[list[tuple[int, Any]]] = []

    def constant(self, e: Expr) -> int | None:
        if e.tag == "int":
            return int(e.val)
        if e.tag == "name" and e.val not in self.values:
            name = self.c.qualify(e.val, self.c.p.consts)
            value = constant(self.c, name, []) if name else None
            return value if isinstance(value, int) and not isinstance(value, bool) else None
        return None

    def value(self, e: Expr) -> tuple[int, Any]:
        """How widely one value is shared: BLOCK, WARP or THREAD, with the expression that makes it so."""
        if e.tag == "name":
            return self.values.get(e.val, (BLOCK, None))
        if self.warp_wide(e):
            return WARP, e
        if e.tag == "call" and (e.val in SHUFFLES or e.val in atomics.NAMES or self.unshared(e)):
            return THREAD, e  # another thread's value, or an element's old value, which each thread sees apart
        if e.tag == "index" and root(e).tag == "name" and root(e).val in self.private:
            return THREAD, e
        if e.tag == "lambda":
            return THREAD, e
        found: tuple[int, Any] = (BLOCK, None)
        parts = list(e.args)
        if e.tag == "call" and "." in e.val:  # a method's receiver is one of its values too
            parts.append(Expr("name", e.val.split(".")[0], line=e.line, col=e.col))
        for a in parts:
            level = self.value(a)
            if level[0] > found[0]:
                found = level if level[1] is not None else (level[0], a)
        return found

    def warp_wide(self, e: Expr) -> bool:
        """`t / 32k` is a warp's number, and `t < 32k` or `t >= 32k` holds for whole warps, where t is the fastest
        thread name and its extent is whole warps."""
        if e.tag != "binary" or len(e.args) != 2 or self.block.extents[0] % WARP_SIZE:
            return False
        a, b = e.args
        op = e.val
        if a.tag != "name" or a.val != self.block.threads[0]:
            a, b, op = b, a, {"<": ">", ">": "<", "<=": ">=", ">=": "<="}.get(op, "")
        k = self.constant(b) if a.tag == "name" and a.val == self.block.threads[0] else None
        if k is None:
            return False
        edge = {"/": k, "<": k, ">=": k, "<=": k + 1, ">": k + 1}.get(op)
        return edge is not None and edge % WARP_SIZE == 0 and (op != "/" or k > 0)

    def note(self, node: Any, at: tuple[int, Any]):
        old = self.block.reach.get(id(node))
        if old is None or at[0] > old[0]:
            self.block.reach[id(node)] = at

    def unshared(self, e: Expr) -> bool:
        """A call whose result may differ between threads that pass it the same values: one handed an atomic or a
        mutex from outside the region, or a function value, which computes whatever it closes over. Typed assembly
        runs only in the region's own body, where `asm` makes its outputs each thread's own."""
        head = e.val.split(".")[0]
        named = [head, *(root(a).val for a in e.args if root(a).tag == "name")]
        if any(n in self.c.env and self.c.env[n].ty.name in SHARED_STATE for n in named):
            return True
        return "." not in e.val and (e.val in self.values or e.val in self.c.env)

    def calls(self, e: Expr, at: tuple[int, Any]):
        """Note every call of an expression as reached at `at`, the right side of `&&` and `||` only where the left
        side lets it run, and what each call may write."""
        if e.tag == "lambda":
            self.run(e.ref.body, (THREAD, e))  # it may run in any thread: whatever it assigns differs
            return
        if e.tag == "binary" and e.val in {"&&", "||"}:
            self.calls(e.args[0], at)
            self.calls(e.args[1], widest(at, self.cause(e.args[0])))
            return
        if e.tag == "call":
            self.note(e, at)
            for a in e.args:  # how widely each argument is shared, for a warp operation that takes one per warp
                self.block.levels[id(a)] = widest(self.block.levels.get(id(a), (BLOCK, None)), self.value(a))
            self.lends(e, at)
        for a in e.args:
            self.calls(a, at)

    def lends(self, e: Expr, at: tuple[int, Any]):
        """A call leaves in what it may write whatever each thread's call put there: its rw arguments, every
        argument of a primitive that writes one or of a callee the checker cannot see into, and a method's
        receiver."""
        from .builtins import TABLE

        head, _, method = e.val.rpartition(".")
        if head in self.stages or e.val in SHUFFLES:
            return
        try:
            found = None if head else self.c.qualify(e.val, self.c.fs)
        except Diagnostic:
            found = None
        if found is not None:
            written = [a for a, (_, t) in zip(e.args, self.c.fs[found].params, strict=False) if t.mode == "rw"]
        elif (method or e.val) in TABLE and (method or e.val) not in WRITING:
            written = []
        else:
            written = list(e.args)
            if head:
                written.append(Expr("name", head.split(".")[0], line=e.line, col=e.col))
        level = widest(at, self.value(e))
        for a in written:
            self.assigned(a, widest(level, self.value(a)))

    def assigned(self, target: Expr, level: tuple[int, Any]):
        """`target` now holds a value as widely shared as `level`: its root local, through fields and elements."""
        name = root(target)
        if name.tag == "name" and name.val in self.mutable:
            self.values[name.val] = widest(self.values.get(name.val, (BLOCK, None)), level)

    def run(self, ss: list[Stmt], at: tuple[int, Any]):
        for s in ss:
            self.stmt(s, at)

    def stmt(self, s: Stmt, at: tuple[int, Any]):
        self.note(s, at)
        for e in s.exprs:
            self.calls(e, at)
        tag = s.tag
        if tag == "reg" or (tag == "unpack" and s.op == "reg"):
            self.mutable |= {s.name} if tag == "reg" else {n.val for n in s.other_names}
        if tag in {"let", "reg"}:
            level = self.value(s.exprs[0])
            self.values[s.name] = widest(level, at) if tag == "reg" else level
        elif tag == "warp_reduce":
            level = self.value(s.exprs[0])
            self.values[s.name] = (min(level[0], WARP), level[1])
        elif tag == "unpack":
            for n in s.other_names:
                self.values[n.val] = self.value(s.exprs[0])
        elif tag == "stack":
            self.private.add(s.name)
        elif tag == "assign":  # an element written at an index that differs makes the whole local differ
            self.assigned(s.exprs[0], widest(self.value(s.exprs[0]), self.value(s.exprs[1]), at))
        elif tag == "asm":  # machine state the checker cannot see, such as %laneid
            for name, *_ in s.assembly.outputs:
                self.values[name] = (THREAD, s)
            for effect in s.assembly.effects:
                if effect.startswith("write:"):
                    self.assigned(Expr("name", effect.partition(":")[2], line=s.line, col=s.col), (THREAD, s))
        elif tag == "if":
            inner = widest(at, self.cause(s.exprs[0]))
            self.run(s.body, inner)
            self.run(s.other, inner)
        elif tag == "match":
            inner = widest(at, self.cause(s.exprs[0]))
            for arm in s.arms:
                self.values[arm.binder] = self.value(s.exprs[0])
                self.run(arm.body, inner)
        elif tag in {"for", "while"}:
            self.loop(s, at)
        elif tag in {"break", "continue"} and self.escapes:
            self.escapes[-1].append(at)
        elif tag in {"block", "unsafe", "defer"}:
            self.run(s.body, at)
        elif tag in {"blocks", "parallel"}:
            self.run(s.body, (THREAD, s))

    def cause(self, e: Expr) -> tuple[int, Any]:
        level = self.value(e)
        return level[0], level[1] if level[1] is not None else e

    def loop(self, s: Stmt, at: tuple[int, Any]):
        """A loop runs its body until nothing it assigns reaches further, and a break or continue that fewer threads
        reach than the loop's body narrows the whole body: after it, those threads are in another iteration."""
        leaving = next(iter(jumps(s.body)), None)
        if leaving is not None and block_run.holds_barrier(s.body, self.stages):
            fail("E-COOP-BARRIER", f"A loop that holds a barrier runs every iteration whole in every thread; the "
                 f"{leaving.tag} at line {leaving.line} would take threads past it.", leaving)  # fmt: skip
        while True:  # levels only rise, so this reaches its fixed point
            before = {name: level for name, (level, _) in self.values.items()}
            inner = at
            for e in s.exprs:
                inner = widest(inner, self.cause(e))
            if s.tag == "for" and s.name != "_":
                self.values[s.name] = (inner[0], inner[1])
            self.escapes.append([])
            self.run(s.body, inner)
            for escape in self.escapes.pop():
                if escape[0] > inner[0]:
                    inner = escape
                    self.run(s.body, inner)
            if s.tag == "while":  # its condition runs again in every thread still inside
                for e in s.exprs:
                    self.calls(e, inner)
            if {name: level for name, (level, _) in self.values.items()} == before:
                break


def jumps(ss: list[Stmt]) -> list[Stmt]:
    """The break and continue statements that leave or restart this loop, not a loop inside it."""
    found = []
    for s in ss:
        if s.tag in {"break", "continue"}:
            found.append(s)
        elif s.tag not in {"for", "while"}:
            found += jumps(nested(s))
    return found


def widest(*levels: tuple[int, Any]) -> tuple[int, Any]:
    return max(levels, key=lambda x: x[0])


def participation(c: Checker, node: Any) -> str | None:
    """Who reaches `node` together inside the cooperative region being checked: "block" (every thread of its block),
    "warp" (each warp whole) or "thread"; None outside a cooperative region."""
    if c.coop is None:
        return None
    return WIDTHS[c.coop.reach.get(id(node), (THREAD, None))[0]]


def collective(c: Checker, node: Any, width: int, what: str, code: str):
    """Refuse `what` unless every thread of each block (width BLOCK) or each warp (WARP) reaches it together."""
    if c.coop is None or (c.closure is not None and c.lanes is not None and c.closure is not c.lanes.home):
        fail(code, f"{what} joins the threads of a cooperative block; write it inside `blocks ... threads ... {{ }}`.",
             node)  # fmt: skip
    level, why = c.coop.reach.get(id(node), (THREAD, None))
    if level <= width:
        return
    group = "every thread of the block" if width == BLOCK else "every thread of a warp"
    line = getattr(why, "line", 0) if why is not None else 0
    reason = f"the condition at line {line}" if line else "a condition"
    fail(code, f"{what} must be reached by {group} together, and {reason} differs from "
         f"{'thread to thread' if level == THREAD else 'warp to warp'}: a thread that skips it leaves the others "
         f"waiting. Move it out from under that condition.", node)  # fmt: skip


# The region ---------------------------------------------------------------------------------------------------------


def s_blocks(c: Checker, s: Stmt):
    if c.lanes or c.f.kernel or c.coop is not None:
        fail("E-PARALLEL-NEST", "A cooperative region cannot start inside a lane, a kernel or another region.", s)
    count = int(s.op)
    names = [n.val for n in s.other_names]
    grid, threads = names[:count], names[count:]
    if not 1 <= len(grid) <= 3 or not 1 <= len(threads) <= 3 or len(set(names)) != len(names):
        fail("E-COOP-SHAPE", "A region names one to three block names and one to three thread names, each once.", s)
    for e in s.exprs[:count]:
        c.expr(e, USIZE)
    extents = []
    for e in s.exprs[count:]:
        c.expr(e, USIZE)
        value = natural(e) or 0
        if value < 1:
            fail("E-COOP-SHAPE", "A thread extent is a positive literal or constant: a block's shape is fixed when it "
                 "is compiled.", e)  # fmt: skip
        extents.append(value)
    total = 1
    for k in extents:
        total *= k
    if total % WARP_SIZE or not WARP_SIZE <= total <= 1024:
        fail("E-COOP-SHAPE", f"A block runs whole warps, 32 to 1024 threads, a multiple of 32; {' x '.join(map(str, extents))} "
             f"is {total}.", s)  # fmt: skip
    device = "device" in placements(c, s.body)
    block = Block(grid, threads, extents, device, top={id(x) for x in s.body})
    held = {x.name: x.tag for x in s.body if x.tag in {"shared", "pipeline"}}
    outside = {n: "outside" for n, b in c.env.items() if is_view(b.ty) or b.ty.name in {"Buf", "Array"}}
    closures(c, s.body, {**held, **outside})
    reach = Reach(c, block, {n for n, k in held.items() if k == "pipeline"})
    reach.run(s.body, (BLOCK, None))
    for name, node in zip(names, s.other_names, strict=True):
        c.bind(name, Binding(USIZE), node)
    known = len(c.facts)
    from . import facts

    for name, e in zip(names, s.exprs, strict=True):
        facts.binder(c, name, None, e, origin=("binder", s))
    saved = c.lanes, c.coop, c.device_depth, c.loop_depth, set(c.moved), c.effects
    c.lanes = Lanes(threads[0], set(c.env) - set(names), c.closure)
    c.coop, c.device_depth, c.loop_depth, c.effects = block, int(device), 0, set()
    c.block(s.body)
    lanes = c.lanes
    if (c.moved - saved[4]) & lanes.outer:
        fail("E-MOVE-IN-LOOP", "An outer owner would be moved once per thread.", s)
    allowed = DEVICE_SAFE if device else LANE_SAFE | {"indirect_call", "dispatch"}
    excess = sorted(x for x in c.effects if x not in allowed and not x.startswith(("read:", "write:", "lane:")))
    if excess:
        fail("E-PARALLEL-CALL", f"A {'device' if device else 'host'} thread cannot {', '.join(excess)}.", s)
    saved[5].update(c.effects)
    walker = footprints.Globals(c, lanes.outer)
    extent_polys = [walker.poly(e, {}) for e in s.exprs[:count]]
    pipelines.check(c, s, block)
    phases.check(c, s, block, extent_polys)
    footprints.check(c, s, block, lanes.outer, extent_polys)
    c.lanes, c.coop, c.device_depth, c.loop_depth, _, c.effects = saved
    for name in names:
        del c.env[name]
    del c.facts[known:]
    target = "device" if device else "host"
    c.effect("par:" + target)
    if len(grid) > 1:
        c.guard("grid")  # the product of the grid's extents is checked
    if block.shared or block.pipelines:
        c.effect("zero_init")  # a block's shared memory, its stages included, is zeroed where it starts
    c.counts["cooperative_regions"] = c.counts.get("cooperative_regions", 0) + 1
    c.resources[c.f.name].append({"name": "blocks", "kind": "blocks", "threads": total, "shared_bytes": block.bytes,
                                  "placement": target, "line": s.line})  # fmt: skip
    s.ref = block


def closures(c: Checker, ss: list[Stmt], held: dict[str, str]):
    """Refuse a closure in the body that names a shared array, a pipeline or an array from outside the region: it may
    be called in any phase and in any thread, so neither the phase rule nor the global rule can place what it
    touches."""

    def names(e: Expr) -> set[str]:
        found = {e.val} if e.tag == "name" else {e.val.split(".")[0]} if e.tag == "call" else set()
        if e.tag == "lambda":
            found |= inside(e.ref.body)
        return found.union(*(names(a) for a in e.args))

    def inside(body: list[Stmt]) -> set[str]:
        return set().union(*(names(e) for s in body for e in s.exprs), *(inside(nested(s)) for s in body))

    def walk(e: Expr):
        if e.tag == "lambda":
            named = sorted(inside(e.ref.body) & set(held))
            if named:
                what = {"shared": "the shared array", "pipeline": "the pipeline", "outside": "the array"}[
                    held[named[0]]
                ]
                fail("E-COOP-UNDECIDED", f"A closure in a cooperative region names {what} {named[0]}; it may run in "
                     "any phase and any thread, so the checker cannot tell which of its elements it touches when. "
                     "Write the access in the region's body.", e)  # fmt: skip
        for a in e.args:
            walk(a)

    for s in ss:
        for e in s.exprs:
            walk(e)
        closures(c, nested(s), held)


def placements(c: Checker, ss: list[Stmt]) -> set[str]:
    """Where the views a body indexes, or moves a fragment through, live."""

    def places(e: Expr) -> set[str]:
        mine = {c.env[root(e).val].ty.place} if e.tag == "index" and root(e).val in c.env else set()
        if (
            e.tag == "call"
            and e.val in {*fragments.OPERATIONS, *wide.NAMES}
            and e.args
            and root(e.args[0]).val in c.env
        ):
            mine.add(c.env[root(e.args[0]).val].ty.place)  # a fragment's tile, or a wide access's array
        return mine.union(*(places(a) for a in e.args))

    found: set[str] = set()
    for s in ss:
        found |= set().union(*(places(e) for e in s.exprs)) | placements(c, nested(s))
    return found


def s_shared(c: Checker, s: Stmt):
    """`shared tile:f32[N] = zeroed;`: one array per block, zeroed where the block starts, every thread's."""
    if c.coop is None or id(s) not in c.coop.top:
        fail("E-COOP-SHARED", "A shared array is declared directly in the body of a cooperative region, where every "
             "thread of the block declares it together.", s)  # fmt: skip
    element = c.resolve(s.ty, s)
    if element.mode != "value" or element.name not in SCALAR | set(STORAGE):
        fail("E-COOP-SHARED", f"A shared array holds scalars; {element.display()} is not one.", s)
    c.expr(s.exprs[0], USIZE)
    e = s.exprs[0]
    size = natural(e) or 0
    if size < 1:
        fail("E-COOP-SHARED", "A shared array's length is a positive literal or constant.", e)
    width = c.sizeof(element) * size
    offset = c.coop.bytes
    c.coop.bytes += -(-width // ALIGN) * ALIGN  # the next array starts on ALIGN bytes too
    if c.coop.bytes > SHARED_LIMIT:
        fail("E-COOP-SHARED", f"A block's shared arrays hold at most {SHARED_LIMIT} bytes; with {s.name} they hold "
             f"{c.coop.bytes}.", s)  # fmt: skip
    c.coop.shared[s.name] = (element, size, offset)
    place = "device" if c.coop.device else "host"
    c.bind(s.name, Binding(Type(element.name, "rw", str(size), element.args, place)), s)
    c.effect("zero_init")
    c.resources[c.f.name].append({"name": s.name, "kind": "shared", "element": element.display(), "capacity": size,
                                  "bytes": width, "initialization": "zeroed", "line": s.line})  # fmt: skip
    s.ref = (element, size, offset)


def s_barrier(c: Checker, s: Stmt):
    collective(c, s, BLOCK, "barrier", "E-COOP-BARRIER")


def s_warp_reduce(c: Checker, s: Stmt):
    """`let total = reduce OP warp yield v;`: OP over v of the 32 threads of the warp, which every one of them gets.
    The order is fixed, halves then quarters down to neighbours (a butterfly), the same on the host and the device."""
    collective(c, s, WARP, f"reduce {s.op} warp", "E-COOP-WARP")
    declared = c.resolve(s.ty, s) if s.ty else None
    ty = c.expr(s.exprs[0], declared)
    wanted = UNSIGNED if s.op in {"add_wrap", "mul_wrap", "&", "|", "^"} else INT if s.op in {"min", "max"} else FLOAT
    if s.op == "+" and ty.name in UNSIGNED:  # no partial sum of naturals overflows unless the total does
        wanted = UNSIGNED
        c.guard("overflow")
    if ty.mode != "value" or ty.name not in wanted:
        fail("E-REDUCE-OP", f"reduce {s.op} warp takes {'unsigned integers' if wanted is UNSIGNED else 'integers' if wanted is INT else 'floats'}"
             f"{' and unsigned integers' * (s.op == '+' and wanted is FLOAT)}, not {ty.display()}.", s)  # fmt: skip
    s.ty = ty
    c.bind(s.name, Binding(ty), s)


def check_shuffle(c: Checker, e: Expr, args: list[Expr], targs: tuple, expected: Type | None) -> Type:
    """`shuffle(v, lane)`, `shuffle_xor(v, mask)` and `shuffle_down(v, delta)`: v as another thread of the warp
    holds it, every thread of the warp taking part. The lane, mask or delta is below 32, a guard."""
    from .builtins import arity

    arity(e, args, 2, f"{e.val} takes a value and a lane: {e.val}(v, k).")
    collective(c, e, WARP, e.val, "E-COOP-WARP")
    ty = c.expr(args[0], expected if expected is not None and expected.name in NUMERIC | {"bool"} else None)
    if ty.mode != "value" or ty.name not in NUMERIC | {"bool"}:
        fail("E-TYPE-MISMATCH", f"{e.val} moves an integer, a float or a bool between threads, not {ty.display()}.", e)
    c.expr(args[1], USIZE)
    c.guard("lane")
    return ty


def lower_shuffle(g: Emitter, e: Expr) -> str:
    return f"cr_blk.{e.val}({g.expr(e.args[0])}, {g.expr(e.args[1])})"


# Lowering -----------------------------------------------------------------------------------------------------------


def lower_blocks(g: Emitter, s: Stmt, es: list[str]):
    """One launch for the device, or one team of host threads per block; the body is the same lambda either way."""
    block: Block = s.ref
    count = len(block.grid)
    g.need("cairn_gpu.hpp" if block.device else "cairn_parallel.hpp")
    g.need("cairn_coop.hpp")

    def body():
        grid = [f"cr_g{k}" for k in range(count)]
        named = "[[maybe_unused]] const std::size_t"  # a body need not use every name; nvcc would refuse it unused
        if count == 1:
            g.put(f"{named} v_{block.grid[0]} = cr_b;")
        else:
            below = "cr_b"
            for k, name in enumerate(block.grid):
                g.put(f"{named} v_{name} = {below} % {grid[k]};" if k < count - 1 else f"{named} v_{name} = {below};")
                below = f"({below} / {grid[k]})"
        rest = "cr_t"
        for k, (name, extent) in enumerate(zip(block.threads, block.extents, strict=True)):
            last = k == len(block.threads) - 1
            g.put(f"{named} v_{name} = {rest if last else f'{rest} % {extent}'};")
            rest = f"{rest} / {extent}"
        g.block(s.body)

    context = "Device" if block.device else "Host"
    head = f"(cr::coop::{context}& cr_blk, std::size_t cr_b, std::size_t cr_t)"
    entry = "launch" if block.device else "run"

    def whole():
        total = es[0]
        if count > 1:
            for k in range(count):
                g.put(f"const std::size_t cr_g{k} = {es[k]};")
            total = "cr_g0"
            for k in range(1, count):
                total = f"cr::mul<std::size_t>({total}, cr_g{k})"  # the number of blocks is checked
        lanes = g.inner(lambda: f"[=] CR_DEVICE{head}" if block.device else f"[&]{head} noexcept", body)
        context = f"{execution.CONTEXT}, " if block.device else ""  # the thread's execution context
        g.put(f"cr::coop::{entry}<{block.count}, {block.bytes}>({context}{total}, {lanes});")

    g.nest("{", whole)


def lower_shared(g: Emitter, s: Stmt, es: list[str]):
    element, _, offset = s.ref
    ty = g.type(element)
    g.put(f"[[maybe_unused]] {ty}* const v_{s.name} = reinterpret_cast<{ty}*>(cr_blk.shared + {offset});")


def lower_barrier(g: Emitter, s: Stmt, es: list[str]):
    g.put("cr_blk.sync();")


def lower_warp_reduce(g: Emitter, s: Stmt, es: list[str]):
    ty = g.type(s.ty)
    if s.op in {"min", "max"}:
        combine = f"a {'<' if s.op == 'min' else '>'} b ? a : b"
    elif s.op == "+" and s.ty.name in UNSIGNED:
        combine = f"cr::add<{ty}>(a, b)"  # checked: it traps if the warp's total overflows, whatever the order
    elif s.op in {"add_wrap", "mul_wrap"}:
        combine = f"cr::{s.op}<{ty}>(a, b)"
    else:
        combine = f"static_cast<{ty}>(a {s.op} b)"
    g.put(f"const {ty} v_{s.name} = cr_blk.reduce({es[0]}, []({ty} a, {ty} b) {{ return {combine}; }});")

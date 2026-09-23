"""Pipeline stages in a cooperative region: shared memory a block fills asynchronously from an outside array, one
stage while its threads read another, with each stage's state known at every statement.

    pipeline tiles:u64[256] depth 2;           // two stages of 256, beside the block's shared arrays
    tiles.fill(x, start, count);               // the next free stage starts to receive x[start .. start + count]
    tiles.wait();                              // the oldest stage in flight has landed, in every thread: readable
    let v = tiles[t];                          // reads the readable stage
    tiles.release();                           // its readers are done with it once the next barrier passes

A stage goes available -> transfer in flight -> readable -> consumers in flight -> available: `fill` takes the next
available stage in ring order, `wait` makes the oldest stage in flight readable and is a barrier, `release` hands the
readable stage to its consumers' last reads, and the next barrier (or `wait`) makes it available again. The checker
follows these states through the body in order, the same in every thread, since every operation is one the whole
block reaches together (E-COOP-BARRIER otherwise). It refuses:

- a read of `tiles` where no stage is readable, a `release` with nothing readable, and a `wait` with nothing in
  flight (E-STAGE-UNREADY): the transfer may not have landed;
- a `fill` with no available stage, naming the stage still being read and the reads that may still be running, and a
  `wait` while a stage is still readable (E-STAGE-BUSY);
- a loop whose body leaves the pipeline in another state than it found it, and an `if` whose arms leave it in two
  (E-STAGE-LOOP): a loop of unknown count has to work from every iteration.

`fill` copies `count` elements, at most the stage's length, and zeroes the rest of the stage; `start + count` must lie
within `x`, or it traps. A pipeline's depth is a constant of the declaration, so raising it changes three things and
nothing else: the shared memory a block holds (depth times the stage's bytes, in the receipt's `local_storage` and in
the kernel's static shared memory), how many stages a prologue may fill ahead, and how many transfers a `wait` leaves
in flight (`cp.async.wait_group N`, N the stages still in flight after it, which the checker counts). The program
does not restate a lifetime.

On the device a fill is one `cp.async` of each element by the thread that owns it, committed as one group; a wait is
`cp.async.wait_group` then `__syncthreads`. On the host a fill is each thread's plain copy of its elements and a wait
is the block's barrier, so a read the checker let through before its fill had landed would race with that copy under
the thread sanitizer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .footprints import natural
from .tree import USIZE, VOID, Expr, Stmt, Type, fail, is_view, nested

if TYPE_CHECKING:
    from .checking import Checker
    from .codegen import Emitter
    from .scope import Block

ELEMENTS = {"u32": 4, "i32": 4, "f32": 4, "u64": 8, "i64": 8, "f64": 8}  # what one cp.async of an element moves
DEPTHS = range(1, 9)
OPERATIONS = {"fill": 3, "wait": 0, "release": 0}


@dataclass
class Pipeline:
    element: Type
    size: int
    depth: int
    offset: int


def s_pipeline(c: Checker, s: Stmt):
    """`pipeline tiles:u64[256] depth 2;` declares `depth` stages of 256 elements in the block's shared memory."""
    from .cooperative import SHARED_LIMIT

    if c.coop is None or id(s) not in c.coop.top:
        fail("E-COOP-SHARED", "A pipeline is declared directly in the body of a cooperative region, where every "
             "thread of the block declares it together.", s)  # fmt: skip
    element = c.resolve(s.ty, s)
    if element.mode != "value" or element.name not in ELEMENTS:
        fail("E-COOP-SHARED", f"A pipeline stage holds 4- or 8-byte scalars, each one asynchronous copy: "
             f"{', '.join(ELEMENTS)}; {element.display()} is not one.", s)  # fmt: skip
    for e in s.exprs:
        c.expr(e, USIZE)
    size, depth = (natural(e) or 0 for e in s.exprs)
    if size < 1 or depth not in DEPTHS:
        fail("E-COOP-SHARED", f"A pipeline's length is a positive constant and its depth a constant from 1 to 8; "
             f"{s.name} has {size} and {depth}.", s)  # fmt: skip
    stage = -(-size * ELEMENTS[element.name] // 16) * 16
    offset = c.coop.bytes
    c.coop.bytes += stage * depth
    if c.coop.bytes > SHARED_LIMIT:
        fail("E-COOP-SHARED", f"A block's shared arrays and stages hold at most {SHARED_LIMIT} bytes; with {s.name}'s "
             f"{depth} stages of {stage} bytes they hold {c.coop.bytes}.", s)  # fmt: skip
    c.coop.pipelines[s.name] = Pipeline(element, size, depth, offset)
    place = "device" if c.coop.device else "host"
    from .scope import Binding

    c.bind(s.name, Binding(Type(element.name, "ro", str(size), element.args, place)), s)
    c.resources[c.f.name].append({"name": s.name, "kind": "pipeline", "element": element.display(), "capacity": size,
                                  "stages": depth, "bytes": stage * depth, "line": s.line})  # fmt: skip
    s.ref = c.coop.pipelines[s.name]


def method(c: Checker, e: Expr, name: str, op: str, args: list[Expr]) -> Type:
    """`tiles.fill(x, start, count)`, `tiles.wait()` and `tiles.release()`: operations of the whole block."""
    from .cooperative import BLOCK, collective

    pipeline = c.coop.pipelines[name] if c.coop is not None else None
    if pipeline is None or op not in OPERATIONS:
        fail("E-CALLEE", f"A pipeline offers {', '.join(OPERATIONS)}.", e)
    if len(args) != OPERATIONS[op]:
        fail("E-ARITY", "fill takes an array, where to start in it and how many elements to copy; wait and release "
             "take nothing.", e)  # fmt: skip
    collective(c, e, BLOCK, f"{name}.{op}", "E-COOP-BARRIER")
    if op == "fill":
        source = c.view_argument(args[0])
        if not is_view(source) or source.value != pipeline.element:
            fail("E-TYPE-MISMATCH", f"{name} holds {pipeline.element.display()}; fill it from a view of them, not "
                 f"{source.display()}.", args[0])  # fmt: skip
        c.lend(args[0], "ro", [])
        c.expr(args[1], USIZE)
        c.expr(args[2], USIZE)
        c.guard("stage")  # count at most the stage's length, and start + count within the source
    e.ref = ("stage", name, op)
    return VOID


# The states ---------------------------------------------------------------------------------------------------------


@dataclass
class State:
    flight: list[Any]  # the fills in flight, oldest first
    readable: Any = None  # the wait that made a stage readable
    reads: list[Any] = field(default_factory=list)  # the reads of the readable stage so far
    draining: list[tuple[Any, list[Any]]] = field(default_factory=list)  # (the release, its stage's reads)

    def key(self) -> tuple[int, bool, int]:
        return len(self.flight), self.readable is not None, len(self.draining)

    def copy(self) -> State:
        return State(list(self.flight), self.readable, list(self.reads), [(r, list(x)) for r, x in self.draining])


class Stages:
    """Follow every pipeline of a region through its body, in order, as every thread of a block runs it."""

    def __init__(self, pipelines: dict[str, Pipeline]):
        self.pipelines = pipelines
        self.states = {name: State([]) for name in pipelines}

    def expr(self, e: Expr):
        if e.tag == "lambda":
            return
        for a in e.args:
            self.expr(a)
        if e.tag == "index" and e.args[0].tag == "name" and e.args[0].val in self.pipelines:
            state = self.states[e.args[0].val]
            if state.readable is None:
                self.unready(e.args[0].val, e, f"{e.args[0].val}[...] at line {e.line} reads")
            state.reads.append(e)
        if e.tag == "call" and isinstance(e.ref, tuple) and e.ref[0] == "stage":
            self.operate(e)

    def unready(self, name: str, node: Any, what: str):
        state = self.states[name]
        if state.flight:
            why = f"the stage filled at line {state.flight[0].line} may still be in flight: wait for it first"
        elif state.draining:
            why = f"the last readable stage was released at line {state.draining[-1][0].line}"
        else:
            why = "no stage has been filled and waited for"
        fail("E-STAGE-UNREADY", f"{what} a stage of {name} that is not readable: {why}.", node)

    def operate(self, e: Expr):
        _, name, op = e.ref[:3]
        state, pipeline = self.states[name], self.pipelines[name]
        if op == "fill":
            available = pipeline.depth - len(state.flight) - (state.readable is not None) - len(state.draining)
            if available <= 0:
                if state.draining:
                    release, reads = state.draining[0]
                    lines = sorted({r.line for r in reads})
                    shown = ", ".join(f"line {x}" for x in lines) or "no line"
                    fail("E-STAGE-BUSY", f"{name}.fill at line {e.line} would refill the stage of {name} released at "
                         f"line {release.line} while other threads may still be reading it ({name}[...] at {shown}). "
                         f"Put a barrier after line {release.line} and before line {e.line} runs.", e,
                         stage_reads=lines)  # fmt: skip
                if state.readable is not None:
                    fail("E-STAGE-BUSY", f"{name}.fill at line {e.line} has no stage to fill: the readable one, made "
                         f"so at line {state.readable.line}, is still held; release it first.", e)  # fmt: skip
                fail("E-STAGE-BUSY", f"{name}.fill at line {e.line} has no stage to fill: all {pipeline.depth} are in "
                     f"flight; wait for one first.", e)  # fmt: skip
            state.flight.append(e)
        elif op == "wait":
            if state.readable is not None:
                fail("E-STAGE-BUSY", f"{name}.wait at line {e.line} would make a second stage readable while the one "
                     f"made so at line {state.readable.line} is held; release it first.", e)  # fmt: skip
            if not state.flight:
                self.unready(name, e, f"{name}.wait at line {e.line} waits for")
            state.flight.pop(0)
            state.readable, state.reads = e, []
            self.barrier()
            left = len(state.flight) if len(e.ref) < 4 else min(e.ref[3], len(state.flight))
            e.ref = (*e.ref[:3], left)  # the transfers this wait leaves in flight, the fewest it ever finds
        else:
            if state.readable is None:
                self.unready(name, e, f"{name}.release at line {e.line} releases")
            state.draining.append((e, state.reads))
            state.readable, state.reads = None, []

    def barrier(self):
        """Every thread has passed its reads of a released stage: it is available again."""
        for state in self.states.values():
            state.draining = []

    def stmts(self, ss: list[Stmt]):
        for s in ss:
            self.stmt(s)

    def stmt(self, s: Stmt):
        if s.tag == "barrier":
            return self.barrier()
        if s.tag in {"for", "while"}:
            for e in s.exprs:
                self.expr(e)
            return self.loop(s)
        for e in s.exprs:
            self.expr(e)
        if s.tag == "if":
            before = {k: v.copy() for k, v in self.states.items()}
            self.stmts(s.body)
            then = self.states
            self.states = before
            self.stmts(s.other)
            for name in self.states:
                if then[name].key() != self.states[name].key():
                    fail("E-STAGE-LOOP", f"The two ways through the if at line {s.line} leave {name} in two states: "
                         "every thread must find each stage where the other way leaves it.", s)  # fmt: skip
                self.states[name].reads += then[name].reads
            return None
        if s.tag == "match":
            before = {k: v.copy() for k, v in self.states.items()}
            ends = []
            for arm in s.arms:
                self.states = {k: v.copy() for k, v in before.items()}
                self.stmts(arm.body)
                ends.append(self.states)
            self.states = ends[0] if ends else before
            for end in ends[1:]:
                for name in self.states:
                    if end[name].key() != self.states[name].key():
                        fail("E-STAGE-LOOP", f"The arms of the match at line {s.line} leave {name} in different "
                             "states.", s)  # fmt: skip
            return None
        return self.stmts(nested(s))

    def loop(self, s: Stmt):
        """A loop of a known count runs its iterations until the states repeat; any other must leave every pipeline
        as it found it, since it may run any number of times, none included."""
        count = counted(s)
        if count is not None:
            for _ in range(count):
                before = {k: v.key() for k, v in self.states.items()}
                self.stmts(s.body)
                if {k: v.key() for k, v in self.states.items()} == before:
                    break
            return
        entry = {k: v.key() for k, v in self.states.items()}
        self.stmts(s.body)
        after = {k: v.key() for k, v in self.states.items()}
        if after != entry:
            self.stmts(s.body)  # a second iteration names what the first left behind, where it can
        for name in self.states:
            if after[name] != entry[name]:
                was, now = entry[name], after[name]
                fail("E-STAGE-LOOP", f"The loop at line {s.line} leaves {name} with {now[0]} transfer(s) in flight"
                     f"{', one stage readable' if now[1] else ''} and {now[2]} stage(s) being released, and entered "
                     f"it with {was[0]}{', one readable' if was[1] else ''} and {was[2]}: each iteration must leave "
                     "the pipeline as it found it, as a fill and a wait each turn do.", s)  # fmt: skip


def counted(s: Stmt) -> int | None:
    """How many times a `for` over constant bounds runs; None for anything else."""
    if s.tag != "for":
        return None
    lo, hi = (natural(e) for e in s.exprs[:2])
    return max(hi - lo, 0) if lo is not None and hi is not None else None


def check(c: Checker, s: Stmt, block: Block):
    """Follow every pipeline's stages through the region; the waits learn how many transfers they leave in flight."""
    if block.pipelines:
        Stages(block.pipelines).stmts(s.body)


# Lowering -----------------------------------------------------------------------------------------------------------


def lower_pipeline(g: Emitter, s: Stmt, es: list[str]):
    pipeline: Pipeline = s.ref
    ty = g.type(pipeline.element)
    g.put(f"cr::coop::Stages<{ty}, {pipeline.size}, {pipeline.depth}> cr_stages_{s.name}(cr_blk.shared + "
          f"{pipeline.offset});")  # fmt: skip
    g.put(f"const {ty}* v_{s.name} = nullptr;  // the readable stage, once a wait has made one so")


def lower(g: Emitter, e: Expr) -> str:
    _, name, op = e.ref[:3]
    stages = f"cr_stages_{name}"
    if op == "fill":
        data, count = g.pointer(e.args[1])
        return f"{stages}.fill(cr_blk, {data}, {count}, {g.expr(e.args[2])}, {g.expr(e.args[3])})"
    if op == "wait":  # a wait the states never reached leaves nothing in flight
        return f"(v_{name} = {stages}.template wait<{e.ref[3] if len(e.ref) > 3 else 0}>(cr_blk))"
    return "static_cast<void>(0)"  # release: the next barrier makes the stage available, which the checker follows
